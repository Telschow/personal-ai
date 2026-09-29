# Publication Threat Model

## Adversary Capability
Clone repo, inspect all files, git history, branches, tags, commit messages, metadata, run software, execute examples, inspect artifacts.

## Information Leakage Vectors

1. Tracked source code containing personal data
2. Git history containing deleted sensitive files
3. Commit messages/author metadata exposing identity
4. Configuration files with secrets
5. Test fixtures with real personal data
6. Documentation with personal examples
7. Generated artifacts committed (db, embeddings, logs)
8. Browser history / email exports in repo
9. File paths revealing username/machine
10. Embeddings/vector stores allowing reconstruction

## Current Findings

- job_agent/profile/profile.yaml tracked with name Daniel Telschow, location Munich, education, employment history at BMW/EDAG/TUMCREATE, salary constraints 120k-150k EUR -> SENSITIVE_PERSONAL
- Git author email daniel.telschow@hotmail.es visible in all commits -> PERSONAL
- jobs.sqlite3 empty file tracked at root -> UNKNOWN
- README claims repo contains no personal data, contradicted by profile.yaml
- previous_project_and_raw_data/ contains Chrome history with API keys, gitignored but present on FS
- Financial_data/ contains bank statements, gitignored
- data/ contains SQLite DBs with personal data, gitignored
- No secrets found in tracked source code
- No embeddings/vector stores tracked
- No .env files tracked except example

## Impact
Public release would expose identity, career history, salary expectations, location, employer history. Enables social engineering, targeted phishing, doxxing.

## Classification
NOT-SAFE-TO-PUBLISH in current form.
