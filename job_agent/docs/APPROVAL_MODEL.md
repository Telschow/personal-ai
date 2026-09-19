# APPROVAL_MODEL.md

Every action the agent could take that affects the user's real-world standing
(applications, publishing, spending) requires explicit human approval. This
document defines the model and the guarantees.

## Hard guarantees (enforced by tests + validator)

1. **Nothing is ever submitted automatically.** `application.auto_submit` and
   `projects.auto_publish` are rejected at config validation time — there is no
   configuration value that enables automatic submission, and no code path
   performs it.
2. **Applications require a human review package.** A future approval stage
   must show: the job, the tailored CV draft, the cover letter draft, and the
   evidence/provenance for every claim — before the user clicks approve.
3. **The approval is per-application, not per-session.** A once-approved tool
   cannot be reused to submit a different application.

## What is approved today

- **Nothing.** Slice 1 is discovery + evaluation only. `job-agent decision
  JOB_ID approve|reject` records the user's *opinion* of a job in
  `job_decisions` for later learning — it does not and cannot submit anything.

## What will require approval (future slices)

| Action                         | Approval surface                                |
|--------------------------------|-------------------------------------------------|
| Tailor CV for a specific job   | Review tailored CV diff + provenance            |
| Generate a cover letter        | Review letter                                   |
| Draft screening answers        | Review answers                                  |
| Submit an application          | Explicit per-application approval + audit trail |

## Design rules

- The decision/approval boundary is a single code path; nothing bypasses it.
- Approval inputs (job, drafts, evidence) must be reviewable in the human
  approval step; no blind signing off.
- Every approved/exposed action writes an audit event (who, what, when,
  which job, hash of the artifact) — never auto-approved in bulk.

## Non-goals

- No credential reuse across applications without explicit consent.
- No "approve once for all applications of this template" shortcuts.