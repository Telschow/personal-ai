# Personal AI and Job Agent Integration

## Overview

This document describes the integration between the Personal AI and Job Agent systems. It explains how the two systems interact, the data flows between them, and the security and privacy considerations.

## Integration Points

### 1. Data Flow

The integration between Personal AI and Job Agent is primarily one-way: Job Agent retrieves data from Personal AI but does not write back to it. The data flow is as follows:

1. **Job Agent retrieves structured career evidence** from Personal AI's memory and corpus stores.
2. **Job Agent uses this evidence** to generate career fit assessments and tailored proposals.
3. **Job Agent does not modify** Personal AI's data or state.

### 2. Technical Implementation

The integration is implemented through a read-only adapter in Job Agent:

```python
class PersonalAiCareerKnowledge:
    """Read-only adapter for Personal AI's career knowledge."""
    
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
    
    def health(self) -> bool:
        """Check if the adapter is healthy."""
        try:
            self.conn.execute("SELECT 1").fetchone()
            return True
        except Exception:
            return False
    
    def memory_search(self, query: str, limit: int = 5) -> list[CareerEvidence]:
        """Search Personal AI's memory for career-related evidence."""
        # Implementation details omitted
        
    def corpus_search(self, query: str, limit: int = 5) -> list[CareerEvidence]:
        """Search Personal AI's corpus for career-related evidence."""
        # Implementation details omitted
```

### 3. Security and Privacy

The integration follows these security principles:

1. **Read-only access**: Job Agent never modifies Personal AI's data.
2. **No writes**: Job Agent does not write back to Personal AI.
3. **No policy changes**: Job Agent does not alter Personal AI's policy or permissions.
4. **No secrets**: No credentials or sensitive data are exposed in the integration.
5. **No execution**: No code execution or shell commands are allowed through the integration.
6. **No network**: The integration is entirely local and does not involve network communication.

### 4. Usage in Job Agent

In Job Agent, the Personal AI adapter is used in the following contexts:

1. **Career fit assessment**: When analyzing a job's fit against the user's profile, Job Agent retrieves relevant evidence from Personal AI.
2. **Tailored proposal generation**: When generating a tailored proposal for a job, Job Agent uses evidence from Personal AI to inform the content.
3. **Evidence retrieval**: The adapter provides a consistent interface for retrieving evidence from Personal AI's memory and corpus stores.

### 5. Error Handling

The adapter includes error handling to ensure robustness:

1. **Health check**: The `health()` method verifies that the adapter is functioning correctly.
2. **Graceful degradation**: If Personal AI is unavailable or inaccessible, the adapter degrades gracefully to a null provider with an explicit risk note.
3. **No silent failures**: Any errors are logged and reported without exposing sensitive information.

## Conclusion

The integration between Personal AI and Job Agent is designed to be secure, privacy-preserving, and read-only. It allows Job Agent to leverage Personal AI's knowledge base for career-related tasks while maintaining the integrity and security of both systems.