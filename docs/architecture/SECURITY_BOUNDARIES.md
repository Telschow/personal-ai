# Security Boundaries

## Trust Zones
LOCAL TRUSTED: User machine, local SQLite, Ollama
EXTERNAL SERVICES: Job providers, search engines
LLM PROVIDERS: Local Ollama only
MCP SERVERS: Not active
PLUGINS: None active
CONNECTORS: Source adapters read-only
DATABASES: SQLite local
FILE SYSTEM: Workspace sandbox
BROWSER: Local GUI
NETWORK: Outbound only for Job Agent

## Boundaries
Personal AI <-> Job Agent: read-only SQLite bridge sqlite3.connect(mode=ro)
Tools: PolicyEngine gates execution ALLOWED/DENIED/APPROVAL_REQUIRED
Memories: explicit creation only, untrusted everywhere
Ingestion: explicit path, no code execution
Retrieval: read-only, no state mutation
Agent: max 8 rounds, result bounding 12k chars

## Data Classification
PUBLIC: Job titles, companies, locations
PRIVATE: Personal documents, emails, chats
SENSITIVE: Health, financial, relationships, identity
SECRET: API keys, tokens – not stored

## Controls
Explicit ingestion
Scope-aware retrieval
Identifiers-only event logs
Content-hash idempotency
Fail-closed LLM validation
Anti-fabrication guards
Read-only integration

## Risks
Chrome history contains API keys and auth tokens
Financial data contains transaction patterns
Memory contains psychological profiles
No filesystem encryption
Path traversal possible via filesystem adapter
Tool argument prompt injection possible
