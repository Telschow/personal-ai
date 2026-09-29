# Configuration Audit

## Executive Summary

This audit examines the configuration systems of the `personal-ai` repository. The system uses two complementary configuration approaches: environment-driven configuration for the Personal AI core (`personal_ai/config.py`) and YAML-based configuration for the Job Agent (`job_agent/config.yaml`). Both systems support environment variable overrides for infrastructure settings while deliberately excluding secrets from configuration files.

**Overall Assessment:** The configuration approach is thoughtful and security-conscious. Secrets are deliberately kept out of configuration files and expected from environment variables or secure stores. The configuration is typed and validated (using Pydantic for Job Agent, dataclasses with validation for Personal AI). Key risks involve the dual configuration paradigms which could lead to inconsistency, environment variable naming collisions, and the potential for misconfiguration when deploying across different environments (local vs Docker vs production).

---

## Configuration Systems

### 1. Personal AI Configuration (`personal_ai/config.py`)
- **Approach:** Environment-driven, dataclass-based with explicit validation
- **Variables:** 
  - `PERSONAL_AI_EMBEDDING_MODEL` (optional, for vector search)
  - `PERSONAL_AI_RETRIEVAL_MODE` (keyword/semantic/hybrid)
  - `PERSONAL_AI_VISION_MODEL` (optional, for vision extraction)
  - `PERSONAL_AI_VISION_PROMPT_VERSION` (default: `v2`)
  - `PERSONAL_AI_CHAT_MODEL` (default: `qwen3.5:9b`)
  - `PERSONAL_AI_API_HOST` (default: `127.0.0.1`)
  - `PERSONAL_AI_API_PORT` (default: `8000`)
  - `OLLAMA_BASE_URL` (default: `http://localhost:11434`)
- **Validation:** 
  - Dataclasses with `__post_init__` validation
  - Explicit value checks (port ranges, enum validation)
  - Clear error messages for invalid values
- **Secrets:** None; all secrets expected from environment or secure stores
- **Fallbacks:** Sensible defaults for all configuration values
- **Override Priority:** CLI args > environment variables > defaults

### 2. Job Agent Configuration (`job_agent/config.yaml`)
- **Approach:** YAML-based, Pydantic validation (`BaseModel`)
- **Sections:** 
  - `llm` (provider, model, base_url, temperature, timeout)
  - `search` (global_enabled, max_jobs_per_source, etc.)
  - `jobs` (salary thresholds, freshness_days, etc.)
  - `ranking` (weights that must sum to 1.0)
  - `digest` (report type and directory)
  - `projects` (sandbox root, auto_publish)
  - `application` (auto_submit, never_submit)
  - `career` (knowledge, weights, llm, tracks, location, discovery, pacing, budgets, settings)
  - `sources` (ATS provider configurations, RSS, sitemap, direct domains)
- **Validation:** 
  - Pydantic `BaseModel` with `model_validator` and `Field` constraints
  - Cross-field validation (weight sums, positive values, enum validation)
  - Environment variable overriding via `with_env_overrides()` method
- **Secrets:** 
  - Explicit comment: "All secrets are expected from environment variables or a local, git-ignored `.env` overlay; this module never stores secrets."
  - ATS tokens (Greenhouse, Lever, etc.) are treated as board identifiers, not secrets
  - Actual secrets (API keys, etc.) expected from environment
- **Override Priority:** Environment variables (with specific prefixes like `JOB_AGENT_*`) > YAML file > defaults

### 3. Configuration Override Mechanisms
- **Personal AI:** 
  - `load_api_settings()`, `load_embedding_settings()`, etc. read directly from `os.environ`
  - CLI arguments parsed first, then override environment-derived settings
- **Job Agent:** 
  - `load_config()` reads YAML file, then applies `with_env_overrides()`
  - Specific environment variables: `JOB_AGENT_DATABASE_PATH`, `JOB_AGENT_REPORT_DIR`, etc.
  - LLM settings: `JOB_AGENT_LLM_MODEL`, `JOB_AGENT_LLM_BASE_URL`
  - Source catalog: `JOB_AGENT_SOURCE_CATALOG`
- **Shared Pattern:** Both systems allow environment overrides but use different naming conventions

---

## Configuration Risks & Concerns

| # | Category | Risk | Severity | Evidence |
|---|---|---|---|---|
| C1 | Dual Configuration Paradigms | Two different configuration systems (environment/dataclass vs YAML/Pydantic) could lead to inconsistency, confusion, and maintenance overhead. Developers must understand both systems. | MEDIUM | `personal_ai/config.py` uses dataclasses + os.environ; `job_agent/config.yaml` uses Pydantic YAML + specific env var overrides |
| C2 | Environment Variable Naming Collisions | Potential for naming collisions between Personal AI and Job Agent environment variables (e.g., both might use `DATABASE_PATH` or `MODEL` with different meanings). | MEDIUM | Personal AI uses `PERSONAL_AI_DATABASE_ENV`; Job Agent uses `JOB_AGENT_DATABASE_PATH` - different but similar concepts |
| C3 | Misconfiguration Risk | The flexibility of environment overrides increases the risk of misconfiguration in production environments. Invalid values could cause silent failures or unexpected behavior. | MEDIUM | Both systems have validation, but invalid env vars could still cause issues if not caught at startup |
| C4 | Secret Leakage Through Logs/Error Messages | If configuration values containing secrets were accidentally logged or included in error messages, they could be exposed. While the systems avoid storing secrets in config, misconfiguration could lead to leakage. | LOW | Both systems avoid secrets in config; validation errors would typically not include the invalid value |
| C5 | Default Value Drift | Default values in the two systems could drift over time, leading to unexpected behavior when relying on defaults. Example: different default LLM models or ports. | LOW | Personal AI default chat model: `qwen3.5:9b`; Job Agent default LLM model: `qwen3.5:9b` (currently aligned) |
| C6 | Configuration File Exposure | If the `config.yaml` file were accidentally committed to a public repository, it could expose infrastructure configuration (though not secrets). The `.gitignore` should protect against this. | LOW | `job_agent/config.yaml` is not in `.gitignore` but contains no secrets; `job_agent/config.example.yaml` is the template |
| C7 | Environment Variable Override Complexity | The override precedence (CLI > env > defaults) is documented but could be confusing in complex deployment scenarios. Users might not understand which value is taking effect in a given situation. | LOW-Medium | Documented in code but could be clearer in user-facing documentation |
| C8 | Lack of Configuration Versioning | No mechanism to track configuration versions or detect when configuration files are out of sync with code expectations. | LOW | Configuration is expected to evolve with the code; no versioning mechanism exists |
| C9 | Secret Detection Difficulty | While the systems avoid storing secrets in configuration, it relies on developer discipline and code review to ensure no secrets accidentally get added. Automated secret scanning might flag false positives in example files or documentation. | LOW | `job_agent/config.example.yaml` contains `example-company` tokens that are deliberately not secrets but could trigger secret scanners |
| C10 | Immutable Infrastructure Assumptions | Some configuration values assume stable infrastructure (e.g., Ollama running on localhost:11434). Changes to infrastructure require configuration updates. | LOW | Expected behavior; infrastructure configuration should change when infrastructure changes |

---

## Configuration Strengths

| # | Strength | Description |
|---|---|---|
| S1 | Secret Exclusion by Design | Both systems explicitly avoid storing secrets in configuration files. Secrets are expected from environment variables or secure stores. |
| S2 | Typed and Validated Configuration | Both systems use strong typing (dataclasses/Pydantic) with validation to catch misconfiguration early at startup. |
| S3 | Sensible Defaults | Both systems provide sensible defaults for all configuration values, allowing the system to run with minimal configuration in development environments. |
| S4 | Environment Variable Overrides | Both systems support environment variable overrides for flexibility in different deployment scenarios (local, Docker, CI/CD, production). |
| S5 | Clear Documentation | Both systems have clear comments documenting what each configuration value does and what values are expected. |
| S6 | Configuration File Separation | Separation of concerns: Personal AI configuration focuses on runtime behavior; Job Agent configuration focuses on career-search behavior. |
| S7 | Inheritance and Composition | Job Agent configuration uses Pydantic's compositional model (nested models) for logical grouping of related settings. |
| S8 | Validation Error Messages | Both systems provide clear, actionable error messages when configuration values are invalid. |
| S9 | Example Configurations | Both systems provide example configuration files (`config.example.yaml`) to help users get started. |
| S10 | No Hardcoded Secrets | Grep checks confirm no hardcoded API keys, passwords, or tokens in configuration files or code (except in test files and examples). |

---

## Recommendations (Non-Modification)

**R1 — Configuration Paradigm Unification:** Consider unifying the two configuration systems into a single approach (e.g., extend the Job Agent's Pydantic-based YAML system to handle Personal AI configuration, or create a shared configuration layer). This would reduce cognitive overhead and ensure consistency.

**R2 — Environment Variable Naming Convention:** Establish and document a clear environment variable naming convention that prevents collisions between the two systems. Example: `PERSONAL_AI_*` for Personal AI, `JOB_AGENT_*` for Job Agent, with no overlap in semantic meaning.

**R3 — Configuration Validation Testing:** Add tests that verify configuration validation works correctly for edge cases (invalid types, out-of-range values, missing required fields, etc.) in both systems.

**R4 — Secret Detection Rule Tuning:** Tune any automated secret detection rules to ignore known false positives like `example-company` tokens in example configuration files.

**R5 — Configuration Change Documentation:** Document which configuration values require a restart to take effect versus which can be changed at runtime (though few are likely runtime-changeable given the system's architecture).

**R6 — Configuration File Template Improvement:** Improve the `config.example.yaml` file to include inline comments explaining what each section does and what values are typical, while making it clear that secrets should not be stored here.

**R7 — Environment Variable Precedence Clarification:** Clearly document the configuration precedence order (CLI arguments > environment variables > configuration file > defaults) in user-facing documentation for both systems.

**R8 — Configuration Validation Observability:** Consider adding startup logging that shows the effective configuration values after all overrides have been applied, to help users debug configuration issues.

**R9 — Infrastructure Assumption Documentation:** Clearly document infrastructure assumptions in the configuration files (e.g., "Assumes Ollama is running at the specified base_url", "Assumes the specified database file is writable").

**R10 — Configuration Versioning Consideration:** Consider adding a configuration version field or hash to help detect when configuration files are significantly out of date with code expectations (though this adds complexity).

**R11 — Configuration Example Improvements:** Improve example configuration files to better illustrate realistic values while avoiding any appearance of storing secrets.

**R12 — Cross-System Configuration Consistency Check:** Consider adding a startup check or CI job that verifies that related configuration values between the two systems are semantically consistent (e.g., both referring to the same Ollama instance when intended).

---

## Confidence Estimate

| Finding | Confidence |
|---|---|
| C1-C3 | Medium (architectural analysis, requires understanding of both systems) |
| C4-C10 | Low-Medium (speculative risks, some require specific deployment scenarios) |
| S1-S10 | High (code inspection, documentation review) |
| R1-R12 | Medium (requires implementation to verify effectiveness) |

---

## Next Steps (Post-Audit)

1. Consider unifying configuration paradigms or at least documenting the relationship clearly
2. Establish clear environment variable naming conventions to prevent collisions
3. Add configuration validation tests for edge cases
4. Tune secret detection rules to ignore known false positives
5. Document which configuration values require restart vs runtime change
6. Improve example configuration files with better comments and realistic values
7. Clarify configuration precedence order in user documentation
8. Add startup logging of effective configuration values
9. Document infrastructure assumptions in configuration files
10. Consider adding configuration versioning or consistency checks
11. Improve example configuration files to illustrate realistic usage
12. Add startup check for related configuration consistency between systems