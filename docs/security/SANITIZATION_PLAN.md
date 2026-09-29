# Sanitization Plan

## Blockers for Public Release
1. job_agent/profile/profile.yaml contains real personal data
2. Git author email exposes identity
3. README claim of no personal data is false
4. jobs.sqlite3 tracked empty placeholder
5. Potential history contamination

## Steps

1. Remove personal profile
   - Move job_agent/profile/profile.yaml to local-only location outside repo
   - Replace with profile.yaml.example with synthetic data
   - Add job_agent/profile/profile.yaml to .gitignore

2. Sanitize git history
   - Author email: create new repo with neutral author or use git filter-repo to rewrite author metadata
   - Commit messages reviewed for PII

3. Clean tracked files
   - Remove jobs.sqlite3 from tracking: git rm --cached jobs.sqlite3, add to .gitignore
   - Ensure no .env files tracked

4. Update .gitignore
   - Ensure data/, Financial_data/, previous_project_and_raw_data/, Email_Outlook/, Workouts/, chat-export-*.json, *.sqlite3, *.db are ignored
   - Add job_agent/profile/profile.yaml

5. Replace real fixtures
   - Audit tests for real emails/names
   - Replace with synthetic [alice@example.invalid]

6. Documentation
   - Update README to accurately reflect privacy posture
   - Remove any references to real data

7. Verification
   - Run git log --all --full-history --name-only to confirm no sensitive blobs
   - Run secret scanner
   - Run PII scanner on tracked files

## Do NOT
- Do not rewrite history in place without backup
- Do not delete private data from working tree
- Do not publish PRIVATE_DATA_INVENTORY

## After Sanitization
Classification: PUBLIC-SAFE-AFTER-SANITIZATION
