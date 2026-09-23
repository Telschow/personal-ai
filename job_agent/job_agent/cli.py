"""CLI entry point for the personal job agent.

Subcommands:
    scan       Run a source or all sources (--dry-run/--no-llm/--source)
    profile    Display the active profile
    sources    list / check configured sources
    jobs       list jobs in the database
    fit        career fit intelligence for a stored job
    career     career document & evidence management (ingest/list, evidence list)
    tailor     proposal-only tailored CV for a stored job (--cv)
    digest     write the daily digest markdown
    stats      show aggregate statistics
    decision   record a human decision on a job
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from . import db
from .career import (
    FitWeights,
    NullCareerKnowledge,
    OllamaJsonClient,
    analyze_fit,
    build_knowledge,
    build_retrieval_plan,
    collect_evidence,
    compact_evidence,
    derive_career_profile,
    extract_job_attributes,
)
from .career.documents import DocumentError, ingest_document
from .career.evidence import CareerEvidence, VerificationLevel
from .career.reconcile import candidate_evidence_from_document, reconcile_document
from .catalog import load_catalog
from .config import Config, load_config
from .discovery import build_sources, list_configured_sources
from .discovery_search import WebSearchDiscovery, fetch_candidate_jobs, run_planned_discovery
from .logging_setup import configure_logging, get_logger, log_event
from .models import JobMatch, Score
from .observability import discovery_diagnostics, discovery_yield_report
from .pipeline import Provenance, ingest_global_jobs, run_lifecycle, run_sources
from .query_plan import build_query_plan, planned_queries
from .report import write_digest
from .scoring import scoring_policy_from_config

log = get_logger("cli")


def _load_profile(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def _db_path(args: argparse.Namespace, cfg: Config) -> str:
    return str(args.database) if getattr(args, "database", None) else cfg.database_path


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _script_jobs(rows: list[tuple]) -> list[JobMatch]:
    """Rebuild JobMatch objects from stored (job, total, decision, reasons)
    tuples produced by db.list_matched_jobs. No network involved."""
    matches: list[JobMatch] = []
    for job, total, decision, reasons in rows:
        score = Score(
            total=float(total),
            decision=decision,
            reasons=reasons or [],
            gaps=[],
            hard_fail=False,
            confidence=0.0,
            breakdown={},
        )
        matches.append(JobMatch(job=job, score=score))
    return matches


def cmd_scan(args: argparse.Namespace) -> int:
    cfg = load_config(path=args.config)
    profile = _load_profile(cfg.profile_path)
    policy = scoring_policy_from_config(cfg.model_dump())

    connection = db.connect(":memory:" if args.dry_run else _db_path(args, cfg))
    sources = build_sources(cfg, sources_filter=set(args.source or []))
    if not sources:
        print("No configured sources found. Use 'sources list' to inspect.")
        return 1

    start = time.monotonic()

    def _progress(source_name: str, fetched: int, total: int, elapsed: float) -> None:
        print(f"  {source_name}: +{fetched} candidates ({total} accepted) in {elapsed:.1f}s")

    seen: set[str] = set()
    for src in sources:
        result = run_sources(connection, [src], profile, policy, on_progress=_progress)
        seen |= result.jobs_seen

    if not args.no_global_search and cfg.search.global_enabled and not args.dry_run:
        print("Running global search discovery...")
        queries = planned_queries(cfg)
        engine = WebSearchDiscovery(max_results=cfg.search.results_per_query)
        urls = engine.candidate_urls(queries)
        jobs, search_errors = fetch_candidate_jobs(urls, max_pages=cfg.search.max_global_pages)
        log_event(log, "global_search_fetched", urls=len(urls), jobs=len(jobs), errors=len(search_errors))
        provenance = [Provenance(source_id="web_search", discovery_method="search_engine") for _ in jobs]
        result = ingest_global_jobs(connection, jobs, profile, policy, provenance=provenance)
        seen |= result.jobs_seen

    lifecycle = run_lifecycle(connection, cfg, seen)
    elapsed = time.monotonic() - start

    if args.dry_run:
        print(f"\n[Dry run] in-memory DB used; nothing persisted. Evaluated in {elapsed:.1f}s.")
    else:
        print(f"\nScan complete in {elapsed:.1f}s.")
        if lifecycle:
            print(f"  Lifecycle: {json.dumps(lifecycle, ensure_ascii=False)}")

    rows = db.list_matched_jobs(connection, limit=10)
    print(f"Stored matches (showing up to 10): {len(rows)}")
    for job, total, decision, _reasons in rows:
        print(f"  {job.id}  {job.title}  {job.company}  {total} ({decision})")
    return 0


def cmd_discover(args: argparse.Namespace) -> int:
    """Plan (deterministic, offline) or run (bounded, live) discovery."""
    cfg = load_config(path=args.config)

    if args.mode == "plan":
        plan = build_query_plan(
            cfg,
            limit_total=args.max_queries,
            limit_per_track=args.max_queries_per_track,
            limit_sources=args.max_sources_per_track,
        )
        if args.json:
            print(
                json.dumps(
                    {**plan.summary(), "queries": plan.audit_items()},
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return 0
        tracks = {item.track_id for item in plan.items}
        print(f"Discovery plan: {len(plan.queries())} queries across {len(tracks)} tracks")
        print(f"  locations: {', '.join(plan.locations)}")
        print(f"  budgets: {json.dumps(plan.budgets, sort_keys=True)}")
        for item in plan.items:
            print(f"  {item.query}  [{item.reason}]")
        return 0

    profile = _load_profile(cfg.profile_path)
    policy = scoring_policy_from_config(cfg.model_dump())
    connection = db.connect(":memory:" if args.dry_run else _db_path(args, cfg))
    start = time.monotonic()
    run_started_at = _now_iso()
    plan, jobs, provenance, search_errors, pacing_report = run_planned_discovery(
        cfg,
        max_pages=args.raw_limit or cfg.search.max_global_pages,
        max_results=cfg.career.discovery.max_results_per_query,
        limit_total=args.max_queries,
        limit_per_track=args.max_queries_per_track,
        limit_sources=args.max_sources_per_track,
    )
    elapsed_fetch = time.monotonic() - start
    result = ingest_global_jobs(connection, jobs, profile, policy, provenance=provenance, run_started_at=run_started_at)
    lifecycle = run_lifecycle(connection, cfg, result.jobs_seen)
    diag = discovery_diagnostics(connection)
    catalog = load_catalog(cfg.catalog_path_resolved())
    yield_report = discovery_yield_report(
        pacing_report=pacing_report,
        scan_result=result,
        lifecycle=lifecycle,
        qualifying_sources={e.source_id for e in catalog.enabled()},
    )

    if not args.dry_run:
        for pstat in pacing_report.providers:
            db.record_provider_run(
                connection,
                source_id=pstat["source_id"],
                provider=pstat["provider"],
                status=pstat["status"],
                requests=pstat["requests"],
                hits=pstat["hits"],
                candidate_jobs=pstat["candidate_jobs"],
                duplicates=pstat["duplicates"],
                errors=list(pstat["errors"]),
                latency_ms=pstat["latency_ms"],
            )
        provider_totals = yield_report.provider_totals
        db.record_discovery_run(
            connection,
            planned_queries=len(plan.queries()),
            candidates_found=len(jobs),
            jobs_persisted=sum(result.persisted_by_source.values()),
            jobs_from_providers=int(provider_totals.get("jobs_persisted", 0)),
            jobs_from_search=sum(result.persisted_by_source.values()) - int(provider_totals.get("jobs_persisted", 0)),
            provider_failures=int(provider_totals.get("failed", 0)),
            fetch_errors=len(search_errors),
            duration_ms=int(elapsed_fetch * 1000),
        )
        connection.commit()

    if args.json:
        print(
            json.dumps(
                {
                    **diag.to_dict(),
                    "plan": {**plan.summary(), "queries": plan.audit_items()},
                    "pacing": pacing_report.to_dict(),
                    "yield": yield_report.to_dict(),
                    "lifecycle": lifecycle,
                    "candidates_found": len(jobs),
                    "accepted": len(result.matches),
                    "duplicates": result.total_duplicates,
                    "fetch_errors": len(search_errors),
                    "fetch_seconds": round(elapsed_fetch, 2),
                },
                indent=2,
                ensure_ascii=False,
                default=str,
            )
        )
        return 0

    print(
        f"discovery: {len(plan.queries())} queries -> {len(jobs)} candidate pages "
        f"({len(search_errors)} fetch errors) in {elapsed_fetch:.1f}s"
    )
    totals = pacing_report.to_dict()["totals"]
    print(
        f"search pacing: {totals['attempted']} attempted, {totals['successful']} ok, "
        f"{totals['rate_limited']} rate-limited, {totals['failed']} failed, "
        f"{totals['paused_skipped']} paused-skipped, {totals['cost_seconds']}s pacing"
    )
    print(f"ingested: {len(jobs)} candidates, {len(result.matches)} accepted, {result.total_duplicates} duplicates")
    yt = yield_report.totals
    print(
        f"queried-vs-yielded: {yt['hits_returned']} hits -> {yt['candidate_pages']} candidate pages -> "
        f"{yt['jobs_parsed']} parsed -> {yt['jobs_persisted']} persisted"
    )
    print(
        f"dedup: {yt['duplicate_on_page']} on-page, {yt['duplicate_at_url']} at-url, "
        f"{yt['duplicate_at_db']} at-db, {yt['duplicate_workspace_merged']} workspace-merged, "
        f"{yt['previous_runs']} previous-runs"
    )
    if yield_report.zero_yield_sources:
        print(f"zero-yield sources: {', '.join(yield_report.zero_yield_sources)}")
    if yield_report.never_queried_sources:
        print(f"never-queried sources: {', '.join(yield_report.never_queried_sources)}")
    if yield_report.providers:
        pt = yield_report.provider_totals
        print(
            f"providers: {pt['sources']} sources, {pt['ok']} ok, {pt['zero_yield']} zero-yield, "
            f"{pt['failed']} failed, {pt['hits']} hits -> {pt['candidates']} candidates -> "
            f"{pt['jobs_persisted']} persisted"
        )
        for pstat in pacing_report.providers:
            print(
                f"  {pstat['source_id']:<24} {pstat['provider']:<10} {pstat['status']:<10} "
                f"{pstat['requests']}req {pstat['hits']}hits -> {pstat['candidate_jobs']} candidates "
                f"({pstat['latency_ms']}ms)"
            )
    print(f"jobs in db (sampled {diag.jobs_sampled}/{diag.jobs_total}):")
    for track, count in sorted(diag.jobs_by_career_track.items(), key=lambda kv: -kv[1]):
        print(f"  {track:<24} {count}")
    print("jobs by location tier:")
    for tier, count in sorted(diag.jobs_by_location_tier.items(), key=lambda kv: kv[0]):
        print(f"  {tier:<14} {count}")
    if args.dry_run:
        print("\n[Dry run] in-memory DB used; nothing persisted.")
    return 0


def cmd_profile(args: argparse.Namespace) -> int:
    cfg = load_config(path=args.config)
    p = Path(cfg.profile_path)
    if not p.exists():
        print(f"Profile not found: {p}")
        return 1
    print(p.read_text(encoding="utf-8"))
    return 0


def cmd_sources(args: argparse.Namespace) -> int:
    cfg = load_config(path=args.config)
    if args.command == "check":
        return _sources_check(cfg)
    inventory = list_configured_sources(cfg)
    if not inventory:
        print("No configured sources found (all example placeholders).")
        return 1
    print(f"{'Name':<28} {'Kind':<18} Target")
    print("-" * 72)
    for item in inventory:
        print(f"{item['name']:<28} {item['kind']:<18} {item['target']}")
    print(f"\nTotal: {len(inventory)}")
    return 0


def _sources_check(cfg) -> int:
    import httpx

    failures = 0
    for item in list_configured_sources(cfg):
        url = _source_probe_url(item)
        try:
            r = httpx.get(url, timeout=10.0, follow_redirects=True)
            ok = r.status_code // 100 == 2
        except Exception:  # noqa: BLE001
            ok, r = False, None
        status = "ok" if ok else "unreachable"
        if not ok:
            failures += 1
        print(f"  {item['name']:<28} {status}")
    if failures:
        print(f"\n{failures} source(s) unreachable. Check config.")
        return 1
    return 0


def _source_probe_url(item: dict) -> str:
    target = item.get("target", "")
    for prefix in ("url=", "site="):
        if target.startswith(prefix):
            raw = target[len(prefix) :]
            if prefix == "site=":
                return f"https://jobs.lever.co/{raw}"
            return raw
    if item["kind"] == "ats_board":
        return "https://boards-api.greenhouse.io/v1/boards/"
    return "https://example.com"


def cmd_jobs(args: argparse.Namespace) -> int:
    cfg = load_config(path=args.config)
    connection = db.connect(_db_path(args, cfg))
    jobs = db.get_jobs(connection, source=args.source, status=args.status)
    if not jobs:
        print("No jobs found.")
        return 0
    if args.json:
        data = [{**job.model_dump(mode="json", exclude={"description", "raw"}), "total": None} for job in jobs]
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
        return 0
    print(f"{'ID':<46} {'Title':<36} {'Company':<22} {'Status':<10}")
    print("-" * 114)
    for job in jobs[: args.limit]:
        print(f"{job.id:<46} {job.title[:36]:<36} {job.company[:22]:<22} {(job.status or 'active'):<10}")
    print(f"\n{len(jobs)} job(s); showing {min(len(jobs), args.limit)}")
    return 0


def cmd_digest(args: argparse.Namespace) -> int:
    cfg = load_config(path=args.config)
    connection = db.connect(_db_path(args, cfg))

    matches = _script_jobs(db.list_matched_jobs(connection, limit=200))
    decisions = db.recent_decisions(connection, limit=100)
    summary = {
        "Jobs stored": db.stats_counts(connection).get("total_jobs", 0),
        "Shortlisted": len(matches),
        "Decided": len(decisions),
    }
    path = write_digest(matches, cfg.digest.directory, summary=summary)
    print(f"Digest written to: {path}")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    cfg = load_config(path=args.config)
    connection = db.connect(_db_path(args, cfg))
    print(json.dumps(db.stats_counts(connection), ensure_ascii=False, indent=2))
    return 0


def cmd_decision(args: argparse.Namespace) -> int:
    cfg = load_config(path=args.config)
    connection = db.connect(_db_path(args, cfg))
    job = db.get_job(connection, args.job_id)
    if job is None:
        print(f"Job not found: {args.job_id}")
        return 1
    db.record_decision(connection, args.job_id, args.action, reason=args.note or "")
    connection.commit()
    print(f"Recorded decision: {args.job_id} → {args.action}")
    log_event(log, "decision_recorded", job_id=args.job_id, action=args.action)
    return 0


def cmd_fit(args: argparse.Namespace) -> int:
    """Career fit intelligence for a stored job (Slice 2).

    Deterministic assessment always runs; the LLM narrative is opt-in
    (``career.llm.enabled`` and not ``--no-llm``). A missing knowledge
    provider is an explicit degraded state, never a silent empty result.
    """
    cfg = load_config(path=args.config)
    connection = db.connect(_db_path(args, cfg))
    job = db.get_job(connection, args.job_id)
    if job is None:
        print(f"Job not found: {args.job_id}", file=sys.stderr)
        return 1

    profile = _load_profile(cfg.profile_path)
    career = derive_career_profile(profile)
    attrs = extract_job_attributes(job)

    share = cfg.career.knowledge
    knowledge = None
    knowledge_notes: list[str] = []
    if share.provider == "personal_ai":
        db_path = share.database_path or _parent_database_path()
        try:
            knowledge = build_knowledge(
                "personal_ai",
                database_path=db_path,
                memory_kinds=share.memory_kinds or None,
            )
        except Exception as exc:  # noqa: BLE001
            log_event(
                log,
                "career_knowledge_unavailable",
                provider=share.provider,
                reason=str(exc)[:120],
            )
            knowledge_notes.append("knowledge provider unavailable; assessment is based on profile evidence only")
    knowledge = knowledge or NullCareerKnowledge()
    plan = build_retrieval_plan(career, attrs)
    evidence = collect_evidence(
        profile,
        career,
        knowledge,
        plan,
        attrs,
        max_corpus=share.max_corpus_evidence,
        max_memory=share.max_memory_evidence,
    )

    assessment = analyze_fit(
        job,
        career,
        attrs,
        evidence,
        weights=FitWeights(**cfg.career.weights.model_dump()),
    )

    if knowledge_notes:
        assessment = assessment.model_copy(update={"risks": assessment.risks + knowledge_notes})

    narrative = None
    if cfg.career.llm.enabled and not getattr(args, "no_llm", False):
        narrative = _career_narrative(cfg, attrs, evidence)
        if narrative is not None:
            assessment = assessment.model_copy(update={"narrative": narrative})

    db.save_career_fit(connection, job.id, assessment)
    connection.commit()

    if getattr(args, "json", False):
        print(
            json.dumps(
                assessment.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        return 0

    print(f"{assessment.job_title} @ {assessment.company}")
    print(f"  current_fit:      {assessment.current_fit.score}")
    print(f"  career_upside:    {assessment.career_upside.score}")
    print(f"  evidence_coverage:{assessment.evidence_coverage}")
    print(f"  knowledge_sources: {', '.join(assessment.knowledge_sources) or 'none'}")
    if assessment.strengths:
        print("  strengths:")
        for s in assessment.strengths[:6]:
            print(f"    - {s.dimension} ({s.score:.3f}) {s.note}")
    if assessment.gaps:
        print("  gaps:")
        for g in assessment.gaps[:6]:
            print(f"    - {g.dimension}: {g.note}")
    if assessment.transferable_skills:
        print("  transferable skills:")
        for t in assessment.transferable_skills[:6]:
            print(f"    - {t.requirement} (level={t.level}, {len(t.evidence_ids)} evidence)")
    if assessment.positioning:
        print("  positioning:")
        for p in assessment.positioning[:5]:
            print(f"    - {p}")
    if assessment.risks:
        print("  risks:")
        for r in assessment.risks[:6]:
            print(f"    - {r}")
    if narrative is not None:
        print(f"  narrative: {narrative.relevance_blurb}")
    log_event(
        log,
        "fit_computed",
        job_id=job.id,
        current_fit=assessment.current_fit.score,
        career_upside=assessment.career_upside.score,
        evidence_count=len(evidence),
        llm_used=narrative is not None,
    )
    return 0


# ---------------------------------------------------------------------------
# Career documents + evidence (Slice 3)
# ---------------------------------------------------------------------------


def _load_career_profile(cfg) -> dict:
    return _load_profile(cfg.profile_path)


def _get_existing_evidence(conn) -> list[CareerEvidence]:
    rows = db.get_career_evidence(conn, limit=10_000)
    return [
        CareerEvidence(
            evidence_id=r["evidence_id"],
            claim=r["claim"],
            level=VerificationLevel(r["level"]),
            source=r["source"],
            source_type=r.get("source_type"),
            categories=json.loads(r["categories_json"] or "[]"),
            keywords=json.loads(r["keywords_json"] or "[]"),
            confidence=float(r["confidence"]),
            normalized_fact=r.get("normalized_fact"),
            authority=r.get("authority"),
        )
        for r in rows
    ]


def cmd_career_documents_ingest(args: argparse.Namespace) -> int:
    cfg = load_config(path=args.config)
    path = Path(args.path)
    if not path.is_file():
        print(f"Error: file not found: {path}", file=sys.stderr)
        return 1

    conn = db.connect(_db_path(args, cfg))
    try:
        doc = ingest_document(path)
    except DocumentError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if db.has_career_document(conn, doc.document_id):
        print(f"Document already ingested (id: {doc.document_id})")
        return 0

    db.save_career_document(conn, doc)

    candidates = candidate_evidence_from_document(doc)
    existing = _get_existing_evidence(conn)
    result = reconcile_document(doc, candidates, existing)

    added = db.save_career_evidence_many(conn, result.evidence)
    conn.commit()

    status = "conflicts" if result.conflict_count else "clean"
    db.save_career_reconciliation(
        conn,
        document_id=doc.document_id,
        total_facts=result.new_count + result.kept_count + result.conflict_count,
        exact_matches=result.kept_count,
        new_count=result.new_count,
        conflicts=result.conflict_count,
        status=status,
    )
    conn.commit()

    if getattr(args, "json", False):
        print(
            json.dumps(
                {
                    "document_id": doc.document_id,
                    "filename": doc.filename,
                    "new_evidence_saved": added,
                    "exact_matches": result.kept_count,
                    "conflicts": result.conflict_count,
                    "status": status,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0

    print(f"Document: {doc.filename}")
    print(f"  id: {doc.document_id}")
    print(f"  new evidence: {added}  exact matches: {result.kept_count}  conflicts: {result.conflict_count}")
    if status == "conflicts":
        print("  ⚠ conflicts detected — run 'career evidence list --conflicts' to review")
    return 0


def cmd_career_documents_list(args: argparse.Namespace) -> int:
    cfg = load_config(path=args.config)
    conn = db.connect(_db_path(args, cfg))
    rows = db.list_career_documents(conn, limit=args.limit)
    if getattr(args, "json", False):
        print(json.dumps(rows, indent=2, ensure_ascii=False, default=str))
        return 0
    if not rows:
        print("No career documents ingested.")
        return 0
    print(f"{'ID':<30} {'File':<32} {'Mime':<38}")
    print("-" * 100)
    for r in rows[: args.limit]:
        print(f"{r['document_id']:<30} {r['filename'][:32]:<32} {r['mime_type'][:38]:<38}")
    print(f"\n{len(rows)} document(s)")
    return 0


def cmd_career_evidence_list(args: argparse.Namespace) -> int:
    cfg = load_config(path=args.config)
    conn = db.connect(_db_path(args, cfg))
    rows = db.get_career_evidence(conn, limit=args.limit)

    if getattr(args, "json", False):
        print(json.dumps(rows, indent=2, ensure_ascii=False, default=str))
        return 0

    if not rows:
        print("No evidence stored.")
        return 0

    print(f"{'ID':<22} {'Level':<17} {'Source':<16} {'Claim'}")
    print("-" * 96)
    for r in rows[: args.limit]:
        print(f"{r['evidence_id'][:22]:<22} {r['level']:<17} {r['source'][:16]:<16} {r['claim'][:48]}")
    print(f"\n{len(rows)} evidence item(s)")
    if args.conflicts:
        recs = db.list_career_reconciliations(conn)
        flagged = [rc for rc in recs if rc["has_conflicts"]]
        if flagged:
            print(f"\n⚠ {len(flagged)} document(s) with unresolved conflicts:")
            for rc in flagged:
                print(f"  doc {rc['document_id']}  conflicts={rc['conflicts']}  (status: {rc['status']})")
        else:
            print("\nNo unresolved conflicts.")
    return 0


def cmd_career_artifacts_list(args: argparse.Namespace) -> int:
    """List proposal artifact versions for a job (append-only tape)."""
    cfg = load_config(path=args.config)
    connection = db.connect(_db_path(args, cfg))
    rows = db.list_career_artifacts(
        connection,
        args.job_id,
        version_from=args.version_from,
        version_to=args.version_to,
    )
    if getattr(args, "json", False):
        print(json.dumps(rows, indent=2, ensure_ascii=False, default=str))
        return 0
    if not rows:
        print(f"No proposal artifacts for job {args.job_id}.")
        return 0
    print(f"Proposal artifacts for job {args.job_id}:")
    print(f"{'v':<4} {'Status':<15} {'Source':<12} {'LLM':<4} {'Created':<20} ID")
    print("-" * 100)
    for r in rows:
        print(
            f"v{r['version']:<3} {r['status']:<15} {r['source']:<12} "
            f"{'yes' if r['llm_used'] else 'no':<4} {r['created_at']:<20} {r['artifact_id']}"
        )
    return 0


def cmd_career_artifacts_show(args: argparse.Namespace) -> int:
    """Show one artifact version (default latest) with its evidence trail."""
    cfg = load_config(path=args.config)
    connection = db.connect(_db_path(args, cfg))
    artifact = db.get_career_artifact(connection, args.job_id, version=args.version)
    if artifact is None:
        print(f"No artifact for job {args.job_id} (version={args.version}).", file=sys.stderr)
        return 1

    if getattr(args, "json", False):
        print(json.dumps(artifact, indent=2, ensure_ascii=False, default=str))
        return 0

    print(f"Artifact v{artifact['version']} for job {artifact['job_id']}")
    print(f"  status: {artifact['status']}  source: {artifact['source']}  llm_used: {bool(artifact['llm_used'])}")
    print(f"  created: {artifact['created_at']}")
    print(f"  headline: {artifact['headline']}")
    if artifact.get("summary"):
        print(f"\nSummary:\n{artifact['summary']}")
    if artifact.get("bullets"):
        print("\nBullets:")
        for b in artifact["bullets"]:
            print(f"  [{(b.get('evidence_id') or '')[:12]}] {b.get('text')}")
    return 0


def cmd_career_artifacts_diff(args: argparse.Namespace) -> int:
    """Diff two artifact versions (default latest two) — headline/summary/bullets/status."""
    cfg = load_config(path=args.config)
    connection = db.connect(_db_path(args, cfg))
    if args.from_version is None and args.to_version is None:
        versions = [r["version"] for r in db.list_career_artifacts(connection, args.job_id)]
        if len(versions) < 2:
            print(f"Need at least two artifact versions to diff (job {args.job_id}).", file=sys.stderr)
            return 1
        from_version, to_version = versions[-2], versions[-1]
    else:
        from_version = args.from_version
        to_version = args.to_version
        if from_version is None or to_version is None:
            print("Specify both --from-version and --to-version (or neither).", file=sys.stderr)
            return 1

    before = db.get_career_artifact(connection, args.job_id, version=from_version)
    after = db.get_career_artifact(connection, args.job_id, version=to_version)
    if before is None or after is None:
        print(f"Missing artifact version (from={from_version}, to={to_version}).", file=sys.stderr)
        return 1

    if getattr(args, "json", False):
        print(json.dumps({"from": before, "to": after}, indent=2, ensure_ascii=False, default=str))
        return 0

    print(f"Diff v{from_version} -> v{to_version} for job {args.job_id}")

    def _fmt(a: dict[str, Any] | None, key: str) -> str:
        return (a or {}).get(key) or ""

    if before["status"] != after["status"]:
        print(f"  status: {before['status']} -> {after['status']}")
    if _fmt(before, "headline") != _fmt(after, "headline"):
        print(f"  headline: '{_fmt(before, 'headline')}' -> '{_fmt(after, 'headline')}'")
    if _fmt(before, "summary") != _fmt(after, "summary"):
        print(
            f"  summary: v{from_version} length {len(_fmt(before, 'summary'))} -> "
            f"v{to_version} length {len(_fmt(after, 'summary'))}"
        )

    def _bullet_keys(a: dict[str, Any] | None) -> dict[str, str]:
        return {(b.get("evidence_id") or ""): (b.get("text") or "") for b in ((a or {}).get("bullets") or [])}

    b_bullets, a_bullets = _bullet_keys(before), _bullet_keys(after)
    added = {k: v for k, v in a_bullets.items() if k not in b_bullets}
    removed = {k: v for k, v in b_bullets.items() if k not in a_bullets}
    changed = {
        k: (b_bullets[k], a_bullets[k]) for k in b_bullets.keys() & a_bullets.keys() if b_bullets[k] != a_bullets[k]
    }
    if added:
        print("  added bullets:")
        for k, v in added.items():
            print(f"    [+] [{k[:12]}] {v}")
    if removed:
        print("  removed bullets:")
        for k, v in removed.items():
            print(f"    [-] [{k[:12]}] {v}")
    if changed:
        print("  changed bullets:")
        for k, (old, new) in changed.items():
            print(f"    [~] [{k[:12]}]")
            print(f"        - {old}")
            print(f"        + {new}")
    if not (added or removed or changed):
        print("  bullets: no change")
    return 0


def cmd_tailor(args: argparse.Namespace) -> int:
    cfg = load_config(path=args.config)
    connection = db.connect(_db_path(args, cfg))
    job = db.get_job(connection, args.job_id)
    if job is None:
        print(f"Job not found: {args.job_id}", file=sys.stderr)
        return 1

    profile = _load_career_profile(cfg)
    career = derive_career_profile(profile)
    attrs = extract_job_attributes(job)

    try:
        doc = ingest_document(Path(args.cv))
        db.save_career_document(connection, doc)
    except DocumentError as exc:
        print(f"Error loading CV: {exc}", file=sys.stderr)
        return 1

    candidates = candidate_evidence_from_document(doc)
    existing = _get_existing_evidence(connection)
    result = reconcile_document(doc, candidates, existing)
    db.save_career_evidence_many(connection, result.evidence)
    all_evidence = _get_existing_evidence(connection)
    db.save_career_reconciliation(
        connection,
        document_id=doc.document_id,
        total_facts=result.new_count + result.kept_count + result.conflict_count,
        exact_matches=result.kept_count,
        new_count=result.new_count,
        conflicts=result.conflict_count,
        status="conflicts" if result.conflict_count else "clean",
    )
    connection.commit()

    # LLM proposal (opt-in, fail-closed)
    client = None
    semantic_client = None
    llm_enabled = cfg.career.llm.enabled and not getattr(args, "no_llm", False)
    if llm_enabled:
        llm = cfg.career.llm
        llm_timeout = llm.timeout_seconds or cfg.llm.timeout_seconds
        from .career.cv_llm import OllamaCvClient
        from .career.llm_log import LlmCallLog

        llm_log = LlmCallLog()
        client = OllamaCvClient(
            llm.base_url or cfg.llm.base_url,
            llm.model or cfg.llm.model,
            temperature=llm.temperature,
            timeout=llm_timeout,
            call_log=llm_log,
        )
        if cfg.career.llm.semantic and not getattr(args, "no_semantic", False):
            from .career.llm import OllamaJsonClient

            semantic_client = OllamaJsonClient(
                llm.base_url or cfg.llm.base_url,
                llm.model or cfg.llm.model,
                temperature=llm.temperature,
                timeout=llm_timeout,
                call_log=llm_log,
            )

    from .career.tailoring import tailor

    tailoring = tailor(career, attrs, all_evidence, job_id=job.id, client=client, semantic_client=semantic_client)
    db.save_career_artifact(
        connection,
        tailoring.artifact,
        source=tailoring.source,
        llm_used=(tailoring.source == "llm"),
        mapping_json=json.dumps(
            [m.model_dump(mode="json") for m in tailoring.mapping],
            ensure_ascii=False,
        ),
        validation_json=json.dumps(
            [v.model_dump(mode="json") for v in tailoring.verdicts],
            ensure_ascii=False,
        ),
        positioning_json=json.dumps(
            tailoring.plan.model_dump(mode="json"),
            ensure_ascii=False,
        ),
    )
    connection.commit()

    if getattr(args, "json", False):
        print(
            json.dumps(
                {
                    "artifact_id": tailoring.artifact.artifact_id,
                    "job_id": job.id,
                    "headline": tailoring.artifact.headline,
                    "summary": tailoring.artifact.summary,
                    "bullets": [{"text": b.text, "evidence_id": b.evidence_id} for b in tailoring.artifact.bullets],
                    "status": tailoring.status.value,
                    "source": tailoring.source,
                    "note": tailoring.artifact.note,
                    "mapping": [m.model_dump(mode="json") for m in tailoring.mapping],
                    "validation": [v.model_dump(mode="json") for v in tailoring.verdicts],
                    "semantic": (
                        {
                            "applied": tailoring.semantic.applied,
                            "skipped": tailoring.semantic.skipped,
                        }
                        if tailoring.semantic is not None
                        else None
                    ),
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0

    print(f"{tailoring.artifact.headline}")
    print(f"Job:   {job.title} @ {job.company}")
    print(f"Source: {tailoring.source}  Status: {tailoring.status.value}")
    print(f"Note:   {tailoring.artifact.note}")
    if tailoring.semantic is not None:
        semantic_note = (
            "semantic mapping applied"
            if tailoring.semantic.applied
            else f"semantic mapping skipped ({tailoring.semantic.skipped})"
        )
        print(f"Semantic: {semantic_note}")
    if tailoring.artifact.summary:
        print(f"\n{tailoring.artifact.summary}")
    if tailoring.artifact.bullets:
        print("\nBullets:")
        for b in tailoring.artifact.bullets:
            print(f"  [{(b.evidence_id or '')[:12]}] {b.text}")
    print("\nRequirement coverage:")
    for m in tailoring.mapping:
        layer_marker = " (semantic)" if m.layer == "semantic" else ""
        print(f"  [{m.coverage.value:<11}] {m.requirement[:58]}  ids={','.join(m.evidence_ids[:4])}{layer_marker}")
    return 0


def _parent_database_path() -> str:
    env = __import__("os").environ.get("PERSONAL_AI_DATABASE", "")
    if env:
        return env
    # Default sibling layout: <repo>/data/personal-ai.db next to this repo root.
    return str(Path(__file__).resolve().parent.parent.parent / "data" / "personal-ai.db")


def _career_narrative(cfg: Config, attrs, evidence):
    llm = cfg.career.llm
    items, ids = compact_evidence(evidence)
    if not ids:
        return None
    base_url = llm.base_url or cfg.llm.base_url
    model = llm.model or cfg.llm.model
    llm_timeout = llm.timeout_seconds or cfg.llm.timeout_seconds
    client = OllamaJsonClient(base_url, model, temperature=llm.temperature, timeout=llm_timeout)
    try:
        return client.analyse_fit(
            attrs={"job_attributes": attrs.model_dump()},
            evidence_items=items,
            evidence_ids=ids,
        )
    except Exception:  # noqa: BLE001
        return None


def cmd_feedback_add(args: argparse.Namespace) -> int:
    """Add feedback for a job."""
    from .feedback import add_feedback
    
    cfg = load_config(path=args.config)
    connection = db.connect(_db_path(args, cfg))
    
    try:
        feedback_id = add_feedback(
            connection,
            job_id=args.job_id,
            label=args.label,
            note=args.note,
        )
        print(f"Feedback added for job {args.job_id}: {args.label}")
        if args.note:
            print(f"Note: {args.note}")
        return 0
    except Exception as e:
        print(f"Error adding feedback: {e}", file=sys.stderr)
        return 1
    finally:
        connection.close()


def cmd_feedback_review(args: argparse.Namespace) -> int:
    """Interactive feedback review."""
    from .feedback import get_labeled_job_ids, get_latest_feedback_for_job
    
    cfg = load_config(path=args.config)
    connection = db.connect(_db_path(args, cfg))
    
    try:
        labeled_jobs = get_labeled_job_ids(connection)
        all_jobs = db.list_jobs(connection, limit=1000)
        review_jobs = [j for j in all_jobs if j.id not in labeled_jobs]
        review_jobs = review_jobs[:args.limit]
        
        if not review_jobs:
            print("No jobs available for review.")
            return 0
        
        print(f"Starting feedback review for {len(review_jobs)} jobs...")
        
        for i, job in enumerate(review_jobs, 1):
            print(f"\nJob {i}/{len(review_jobs)}")
            print(f"Company: {job.company}")
            print(f"Title: {job.title}")
            print(f"Location: {job.location}")
            
            score = db.get_score(connection, job.id)
            if score:
                print(f"\nScore: {score.total}")
            
            while True:
                user_input = input("\nEnter label (s/i/m/n/r/y/l/c/d/x) or q to quit: ").strip().lower()
                if user_input in ('q', 'quit'):
                    print("Feedback review stopped.")
                    return 0
                elif user_input in ('s', 'i', 'm', 'n', 'r', 'y', 'l', 'c', 'd', 'x'):
                    label_map = {
                        's': 'strong_interest',
                        'i': 'interested',
                        'm': 'maybe',
                        'n': 'not_interested',
                        'r': 'wrong_role',
                        'y': 'wrong_seniority',
                        'l': 'wrong_location',
                        'c': 'wrong_compensation',
                        'd': 'wrong_domain',
                        'x': 'duplicate',
                    }
                    label = label_map[user_input]
                    note = input(f"Add note for {label} (optional): ").strip() or None
                    add_feedback(connection, job_id=job.id, label=label, note=note)
                    print(f"Feedback added: {label}")
                    break
                else:
                    print("Invalid input.")
        
        return 0
    except Exception as e:
        print(f"Error during feedback review: {e}", file=sys.stderr)
        return 1
    finally:
        connection.close()


def cmd_feedback_pending(args: argparse.Namespace) -> int:
    """Show pending feedback jobs."""
    from .feedback import get_labeled_job_ids
    
    cfg = load_config(path=args.config)
    connection = db.connect(_db_path(args, cfg))
    
    try:
        labeled_jobs = get_labeled_job_ids(connection)
        total_jobs = db.count_jobs(connection)
        
        print(f"Feedback status: {len(labeled_jobs)}/{total_jobs} jobs labeled")
        print(f"Pending jobs: {total_jobs - len(labeled_jobs)}")
        return 0
    except Exception as e:
        print(f"Error getting feedback status: {e}", file=sys.stderr)
        return 1
    finally:
        connection.close()


def cmd_feedback_summary(args: argparse.Namespace) -> int:
    """Show feedback summary."""
    from .feedback import get_feedback_summary
    
    cfg = load_config(path=args.config)
    connection = db.connect(_db_path(args, cfg))
    
    try:
        summary = get_feedback_summary(connection)
        print(f"Total feedback records: {summary['total_records']}")
        print(f"Unique jobs with feedback: {summary['unique_jobs']}")
        print("\nLabel distribution:")
        for label, count in summary['label_distribution'].items():
            print(f"  {label}: {count}")
        return 0
    except Exception as e:
        print(f"Error getting feedback summary: {e}", file=sys.stderr)
        return 1
    finally:
        connection.close()


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="personal-ai",
        description="Personal AI — local-first job discovery and evaluation agent",
    )
    ap.add_argument("--config", default=None, help="Path to config.yaml")
    ap.add_argument("--database", default=None, help="Override database path")
    sub = ap.add_subparsers(dest="command", help="Available commands")

    p_scan = sub.add_parser("scan", help="Scan configured sources and score jobs")
    p_scan.add_argument("--no-global-search", action="store_true", help="Disable search-engine discovery")
    p_scan.add_argument("--no-llm", action="store_true", help="Disable any LLM (already default in slice 1)")
    p_scan.add_argument("--source", action="append", help="Restrict to named source(s)")
    p_scan.add_argument(
        "--dry-run", action="store_true", help="Preview against an in-memory DB (no writes; skips global search)"
    )
    p_scan.set_defaults(func=cmd_scan)

    p_profile = sub.add_parser("profile", help="Show active profile")
    p_profile.set_defaults(func=cmd_profile)

    p_discover = sub.add_parser("discover", help="Plan (offline) or run (bounded, live) catalog-driven discovery")
    p_discover.add_argument("mode", nargs="?", choices=["plan", "run"], default="plan")
    p_discover.add_argument("--max-queries", type=int, default=None, help="Override total query budget")
    p_discover.add_argument("--max-queries-per-track", type=int, default=None)
    p_discover.add_argument("--max-sources-per-track", type=int, default=None)
    p_discover.add_argument("--raw-limit", type=int, default=None, help="Max candidate pages to fetch")
    p_discover.add_argument("--dry-run", action="store_true", help="Run against an in-memory DB (no writes)")
    p_discover.add_argument("--json", action="store_true")
    p_discover.set_defaults(func=cmd_discover)

    p_sources = sub.add_parser("sources", help="List or check configured sources")
    p_sources.add_argument("command", nargs="?", choices=["list", "check"], default="list")
    p_sources.set_defaults(func=cmd_sources)

    p_jobs = sub.add_parser("jobs", help="List jobs in the database")
    p_jobs.add_argument("--source", default=None, help="Filter by source name")
    p_jobs.add_argument("--status", default=None, help="Filter by status (active/closed/duplicate)")
    p_jobs.add_argument("--limit", type=int, default=100)
    p_jobs.add_argument("--json", action="store_true")
    p_jobs.set_defaults(func=cmd_jobs)

    p_digest = sub.add_parser("digest", help="Write the daily digest markdown")
    p_digest.set_defaults(func=cmd_digest)

    p_stats = sub.add_parser("stats", help="Show aggregate statistics")
    p_stats.set_defaults(func=cmd_stats)

    p_dec = sub.add_parser("decision", help="Record a human decision on a job")
    p_dec.add_argument("job_id")
    p_dec.add_argument("action", choices=["approve", "reject"])
    p_dec.add_argument("--note", default=None)
    p_dec.set_defaults(func=cmd_decision)

    p_feedback = sub.add_parser("feedback", help="Manage human feedback for calibration")
    p_feedback_sub = p_feedback.add_subparsers(dest="feedback_command", help="feedback subcommands")

    p_feedback_add = p_feedback_sub.add_parser("add", help="Add feedback for a job")
    p_feedback_add.add_argument("job_id")
    p_feedback_add.add_argument("label", choices=["strong_interest", "interested", "maybe", "not_interested", "wrong_role", "wrong_seniority", "wrong_location", "wrong_compensation", "wrong_domain", "duplicate", "irrelevant"])
    p_feedback_add.add_argument("--note", default=None)
    p_feedback_add.set_defaults(func=cmd_feedback_add)

    p_feedback_review = p_feedback_sub.add_parser("review", help="Interactive feedback review")
    p_feedback_review.add_argument("--limit", type=int, default=5, help="Number of jobs to review")
    p_feedback_review.set_defaults(func=cmd_feedback_review)

    p_feedback_pending = p_feedback_sub.add_parser("pending", help="Show pending feedback jobs")
    p_feedback_pending.set_defaults(func=cmd_feedback_pending)

    p_feedback_summary = p_feedback_sub.add_parser("summary", help="Show feedback summary")
    p_feedback_summary.set_defaults(func=cmd_feedback_summary)

    p_fit = sub.add_parser("fit", help="Career fit intelligence for a stored job")
    p_fit.add_argument("job_id")
    p_fit.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    p_fit.add_argument(
        "--no-llm",
        action="store_true",
        help="Disable the opt-in LLM narrative for this run",
    )
    p_fit.set_defaults(func=cmd_fit)

    p_career = sub.add_parser("career", help="Career document & evidence management (Slice 3)")
    p_career_sub = p_career.add_subparsers(dest="career_command", help="career subcommands")
    p_cdocs = p_career_sub.add_parser("documents", help="Manage ingested career documents")
    cdocs_sub = p_cdocs.add_subparsers(dest="documents_command", help="document subcommands")
    p_cdocs_ingest = cdocs_sub.add_parser("ingest", help="Ingest a CV/document into the evidence base")
    p_cdocs_ingest.add_argument("path")
    p_cdocs_ingest.add_argument("--json", action="store_true")
    p_cdocs_ingest.set_defaults(func=cmd_career_documents_ingest)
    p_cdocs_list = cdocs_sub.add_parser("list", help="List ingested career documents")
    p_cdocs_list.add_argument("--limit", type=int, default=100)
    p_cdocs_list.add_argument("--json", action="store_true")
    p_cdocs_list.set_defaults(func=cmd_career_documents_list)

    p_cev = p_career_sub.add_parser("evidence", help="Browse stored career evidence")
    cev_sub = p_cev.add_subparsers(dest="evidence_command", help="evidence subcommands")
    p_cev_list = cev_sub.add_parser("list", help="List stored evidence")
    p_cev_list.add_argument("--limit", type=int, default=100)
    p_cev_list.add_argument("--conflicts", action="store_true", help="Also show unresolved conflicts")
    p_cev_list.add_argument("--json", action="store_true")
    p_cev_list.set_defaults(func=cmd_career_evidence_list)

    p_cart = p_career_sub.add_parser("artifacts", help="Inspect the append-only tailored-proposal versions for a job")
    cart_sub = p_cart.add_subparsers(dest="artifacts_command", help="artifact subcommands")
    p_cart_list = cart_sub.add_parser("list", help="List artifact versions for a job")
    p_cart_list.add_argument("job_id")
    p_cart_list.add_argument("--version-from", dest="version_from", type=int, default=None)
    p_cart_list.add_argument("--version-to", dest="version_to", type=int, default=None)
    p_cart_list.add_argument("--json", action="store_true")
    p_cart_list.set_defaults(func=cmd_career_artifacts_list)
    p_cart_show = cart_sub.add_parser("show", help="Show one artifact version (default latest)")
    p_cart_show.add_argument("job_id")
    p_cart_show.add_argument("--version", type=int, default=None)
    p_cart_show.add_argument("--json", action="store_true")
    p_cart_show.set_defaults(func=cmd_career_artifacts_show)
    p_cart_diff = cart_sub.add_parser("diff", help="Diff latest two versions (or explicit versions)")
    p_cart_diff.add_argument("job_id")
    p_cart_diff.add_argument("--from-version", dest="from_version", type=int, default=None)
    p_cart_diff.add_argument("--to-version", dest="to_version", type=int, default=None)
    p_cart_diff.add_argument("--json", action="store_true")
    p_cart_diff.set_defaults(func=cmd_career_artifacts_diff)

    p_tailor = sub.add_parser("tailor", help="Produce a proposal-only tailored CV for a job")
    p_tailor.add_argument("job_id")
    p_tailor.add_argument("--cv", required=True, help="Path to the CV document to ground tailoring")
    p_tailor.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    p_tailor.add_argument("--no-llm", action="store_true", help="Disable the opt-in LLM proposal")
    p_tailor.add_argument(
        "--no-semantic",
        action="store_true",
        help="Disable the optional semantic mapping refinement (deterministic floor only)",
    )
    p_tailor.set_defaults(func=cmd_tailor)

    return ap


def main() -> int:
    return run(sys.argv[1:])


def run(argv: list[str]) -> int:
    configure_logging(verbosity=1)
    ap = build_parser()
    args = ap.parse_args(argv)

    if not hasattr(args, "func"):
        ap.print_help()
        return 0

    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        log_event(log, "cli_error", error=str(exc)[:300])
        return 1


if __name__ == "__main__":
    sys.exit(main())
