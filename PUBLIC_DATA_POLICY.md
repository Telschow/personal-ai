# Public Data Policy

## What May Be Committed

* Source code
* Documentation (Markdown, reST)
* Architecture diagrams (Mermaid, PNG, SVG)
* Synthetic test fixtures
* Example configuration files (.env.example, config.example.yaml)
* License files
* README and contributing guidelines
* CI/CD configuration
* Dockerfiles
* Build scripts

## What May NOT Be Committed

* Real personal documents (PDF, DOCX, TXT, etc.)
* Real email exports (MBOX, EML, etc.)
* Real chat exports (ChatGPT, Gemini, etc.)
* Real financial records (bank statements, CSVs, PDFs)
* Real medical records
* Real credentials, tokens, API keys
* Private databases (*.db, *.sqlite3, *.sqlite)
* Real vector stores or embeddings derived from personal data
* Private logs containing personal data
* Private paths or machine-specific configuration
* Environment files with real values (.env)
* SSH keys, private keys
* Browser history, cookies
* Any data that could reveal personal identity, health, finances, relationships, career, or location

## Synthetic Data Requirements

All examples and test fixtures must use:
* Domains: example.com, example.org, example.invalid
* Names: Alice Example, Bob Example, etc.
* Addresses, phone numbers, etc. must be clearly fictional
* No real values merely anonymized

## Enforcement

* Pre-commit hooks check for common PII patterns
* CI pipeline includes secret scanning
* Manual review before merging
* .gitignore enforces exclusion of private directories

## Policy Updates

This policy may be updated as needed. Contributors must comply with the latest version.