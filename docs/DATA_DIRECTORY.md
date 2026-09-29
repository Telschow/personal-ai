# Data Directory Policy

All personal runtime data lives outside source control.

## Directories
data/private/ - private user data, gitignored
data/local/ - machine-specific config, gitignored
data/runtime/ - generated caches, gitignored

## Never commit
.env
*.db
*.sqlite*
*.csv
*.mbox
*.json exports
embeddings/
vectorstores/
models/
logs/
outputs/

## Ingestion safety
Ingestion requires explicit source path.
Never scan home/, Downloads/, Documents/, .ssh, .env by default.
