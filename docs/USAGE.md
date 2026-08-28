# Personal AI — user guide and known limitations

This is a local-first personal assistant backed by a single local chat model
(`qwen3.5:9b`). It answers questions from your stored personal records
(conversation notes, watched-video and search history, URL visits).

## Two ways to use it

**CLI** (single question, prints the answer to stdout):

```
uv run python -m personal_ai.cli \
  --workspace /tmp/ws \
  --database /path/to/knowledge.db \
  "Your question here"
```

**HTTP API (Phase 30)** — an OpenAI-compatible service that uses the exact same
Agent, for Open WebUI or any OpenAI-compatible client:

```
uv run python -m personal_ai.server \
  --workspace /tmp/ws \
  --database /path/to/knowledge.db
```

Then `POST http://127.0.0.1:8000/v1/chat/completions` with an OpenAI-style
`messages` array. See [`OPEN_WEBUI.md`](OPEN_WEBUI.md) for endpoints, env vars,
and Open WebUI setup.

Add `--verbose` to the CLI to watch the agent pick evidence sources (tool calls,
rounds, timing). The final answer goes to stdout; progress goes to stderr. The
question-style guidance below applies to both the CLI and the API.

## How to ask good questions

The assistant is most reliable when a question targets a single, well-scoped
topic. Prefer these patterns:

- **Personal knowledge recall** — "What do I know about BCG?"
- **Activity recall** — "What did I search for recently?"
- **Temporal activity** — "What was my most recent career-related activity?"
- **Negative evidence** — "Do I have anything about fly fishing?" (It will
  say plainly when there is no record — it does not invent content.)
- **Activity summary** — "Summarize my activity from January 2026."
- **Watching/search history** — "What videos did I watch about job interviews?"

The assistant retrieves your actual records and answers from them. It does not
fabricate personal data (watched videos, searches, dates, or cross-source
connections). If an item is uncertain it will usually flag it as such.

## Known limitations

These come from the local 9B model, not from the retrieval system, and are
intentionally not papered over.

1. **Compound questions are unreliable.** A question spanning two evidence
   domains in one sentence — for example "What did I watch about BCG, and what
   do my notes say about it?" or "Compare what I know about BCG and McKinsey"
   — may retrieve both domains correctly but then answer only one, drift into
   a generic how-to guide, or include noise. **Split such questions** into one
   single-domain sub-question each:

   > What did I watch about BCG?
   >
   > What do my notes say about BCG?

2. **"Most recent / recent" is approximate.** The assistant returns a real
   recent record, but on different runs it may surface a different one — it
   does not always return the single newest record. Treat "most recent" answers
   as "a real, recent record," not a guarantee of recency ordering.

3. **Occasional blank or generic answers.** On some runs the model ends a
   turn without producing text (a silent blank after tool calls), or produces
   only a fragment. This is stochastic. **Retry the same question** — the next
   attempt is usually fine.

## What you can reliably rely on

- Grounded factual recall of your stored notes and history — no fabrication.
- Trustworthy "there is no record of X" answers across multiple sources.
- Temporal summaries with correct date bounds and real aggregate counts.
- Convergent answers with consistent latency for single-domain questions.

## What you should not rely on

- Unprompted open-ended synthesis across many unrelated documents.
- Compound multi-domain questions without splitting them.
- Deterministic answers: any given question may vary across runs.

Everything asserted about your records is grounded in what is stored; the
weakness is **omission or noise in synthesis, not invented facts**.
