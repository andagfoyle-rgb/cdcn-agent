# conversation_memory

**Name:** `conversation_memory`

**Description:** Enables CDCN Agent to access, search, and reference past conversation history from previous sessions. Provides continuity across sessions by maintaining a persistent log of discussions, proposals, decisions, and user interactions.

**USE WHEN:**
- User asks about topics discussed in previous sessions (e.g., "What did we decide about the venue hire?")
- User references past proposals, names, or actions (e.g., "Did Andrew activate the skill I proposed yesterday?")
- Context from earlier conversations would improve response quality
- Building upon or revising previous discussions or decisions
- User wants to see conversation history on a topic

**CALL FORMAT:**
```
conversation_memory(
    action="list" | "search" | "read" | "save",
    query="<search term or topic>" | None,
    session_id="<YYYY-MM-DD_session>" | None,
    limit=<int> | 10,
    include_content=<bool> | False
)
```

**ACTIONS:**
- `list` — Returns list of available conversation sessions (dates + summaries)
- `search` — Searches all conversations for the query term; returns matching excerpts
- `read` — Reads full content of a specific session by session_id
- `save` — Saves current session conversation to the history log

**LIMITATIONS:**
- Only accesses conversations stored in `data/conversations/` directory
- Search is case-insensitive text matching; no semantic/AI search
- Does not access real-time messaging systems or external databases
- Cannot modify or delete historical records, only append new ones