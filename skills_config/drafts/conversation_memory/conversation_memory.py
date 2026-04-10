"""
conversation_memory - Access and search past conversation history for session continuity.
"""
from app.skills.base import BaseSkill, SkillResult
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
import json
import aiofiles


class ConversationMemorySkill(BaseSkill):
    name = "conversation_memory"
    description = "Access, search, and reference past conversation history from previous sessions."
    
    CONVERSATIONS_DIR = Path("data/conversations")
    
    async def run(self, **kwargs) -> SkillResult:
        action = kwargs.get("action", "list")
        
        try:
            if action == "list":
                return await self._list_sessions(kwargs.get("limit", 20))
            elif action == "search":
                return await self._search_conversations(
                    query=kwargs.get("query", ""),
                    limit=kwargs.get("limit", 10)
                )
            elif action == "read":
                return await self._read_session(kwargs.get("session_id"))
            elif action == "save":
                return await self._save_session(
                    session_data=kwargs.get("session_data", {}),
                    session_id=kwargs.get("session_id")
                )
            else:
                return SkillResult(
                    success=False,
                    error=f"Unknown action: {action}. Valid actions: list, search, read, save"
                )
        except Exception as e:
            return SkillResult(success=False, error=str(e))
    
    async def _ensure_dir(self) -> None:
        """Ensure conversations directory exists."""
        self.CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)
    
    async def _list_sessions(self, limit: int = 20) -> SkillResult:
        """List available conversation sessions."""
        await self._ensure_dir()
        
        sessions = []
        for file_path in sorted(
            self.CONVERSATIONS_DIR.glob("*.json"), 
            reverse=True
        )[:limit]:
            try:
                async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
                    content = await f.read()
                    data = json.loads(content)
                    sessions.append({
                        "session_id": file_path.stem,
                        "date": data.get("date", file_path.stem[:10]),
                        "summary": data.get("summary", "No summary available"),
                        "message_count": len(data.get("messages", [])),
                        "participants": data.get("participants", [])
                    })
            except (json.JSONDecodeError, KeyError):
                sessions.append({
                    "session_id": file_path.stem,
                    "date": file_path.stem[:10],
                    "summary": "Unable to read session file",
                    "message_count": 0,
                    "participants": []
                })
        
        return SkillResult(
            success=True,
            output={
                "sessions": sessions,
                "total_returned": len(sessions)
            }
        )
    
    async def _search_conversations(
        self, 
        query: str, 
        limit: int = 10
    ) -> SkillResult:
        """Search across all conversations for matching text."""
        if not query or not query.strip():
            return SkillResult(
                success=False, 
                error="Search query cannot be empty"
            )
        
        await self._ensure_dir()
        query_lower = query.lower().strip()
        matches = []
        
        for file_path in sorted(
            self.CONVERSATIONS_DIR.glob("*.json"),
            reverse=True
        ):
            try:
                async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
                    content = await f.read()
                    data = json.loads(content)
                
                session_matches = []
                for idx, message in enumerate(data.get("messages", [])):
                    text = message.get("content", "")
                    if query_lower in text.lower():
                        # Extract context around the match
                        context = self._extract_context(
                            data.get("messages", []),
                            idx,
                            query
                        )
                        session_matches.append({
                            "message_index": idx,
                            "speaker": message.get("speaker", "unknown"),
                            "timestamp": message.get("timestamp"),
                            "excerpt": context,
                            "matched_text": query
                        })
                
                if session_matches:
                    matches.append({
                        "session_id": file_path.stem,
                        "date": data.get("date", file_path.stem[:10]),
                        "matches": session_matches[:5]  # Limit matches per session
                    })
                
                if len(matches) >= limit:
                    break
                    
            except (json.JSONDecodeError, KeyError):
                continue
        
        return SkillResult(
            success=True,
            output={
                "query": query,
                "matches": matches[:limit],
                "total_sessions_matched": len(matches)
            }
        )
    
    async def _read_session(self, session_id: Optional[str]) -> SkillResult:
        """Read full content of a specific session."""
        if not session_id:
            return SkillResult(
                success=False, 
                error="session_id is required for read action"
            )
        
        await self._ensure_dir()
        file_path = self.CONVERSATIONS_DIR / f"{session_id}.json"
        
        if not file_path.exists():
            # Try to find by partial match
            potential = list(self.CONVERSATIONS_DIR.glob(f"*{session_id}*.json"))
            if potential:
                file_path = potential[0]
            else:
                return SkillResult(
                    success=False,
                    error=f"Session not found: {session_id}"
                )
        
        try:
            async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
                content = await f.read()
                data = json.loads(content)
            
            return SkillResult(
                success=True,
                output={
                    "session_id": file_path.stem,
                    "date": data.get("date"),
                    "summary": data.get("summary"),
                    "participants": data.get("participants", []),
                    "messages": data.get("messages", []),
                    "metadata": data.get("metadata", {})
                }
            )
        except json.JSONDecodeError as e:
            return SkillResult(
                success=False,
                error=f"Failed to parse session file: {e}"
            )
    
    async def _save_session(
        self, 
        session_data: Dict[str, Any],
        session_id: Optional[str] = None
    ) -> SkillResult:
        """Save current session to conversation history."""
        await self._ensure_dir()
        
        timestamp = datetime.now()
        if not session_id:
            session_id = timestamp.strftime("%Y-%m-%d_%H%M%S")
        
        file_path = self.CONVERSATIONS_DIR / f"{session_id}.json"
        
        # Structure the session data
        record = {
            "session_id": session_id,
            "date": timestamp.strftime("%Y-%m-%d"),
            "timestamp": timestamp.isoformat(),
            "summary": session_data.get("summary", ""),
            "participants": session_data.get("participants", []),
            "messages": session_data.get("messages", []),
            "metadata": {
                "saved_at": timestamp.isoformat(),
                "message_count": len(session_data.get("messages", [])),
                **session_data.get("metadata", {})
            }
        }
        
        async with aiofiles.open(file_path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(record, indent=2, ensure_ascii=False))
        
        return SkillResult(
            success=True,
            output={
                "session_id": session_id,
                "file_path": str(file_path),
                "message_count": len(record["messages"])
            }
        )
    
    def _extract_context(
        self, 
        messages: List[Dict], 
        match_idx: int, 
        query: str,
        context_chars: int = 200
    ) -> str:
        """Extract text context around a match for readable excerpts."""
        if not messages or match_idx >= len(messages):
            return ""
        
        msg = messages[match_idx]
        text = msg.get("content", "")
        
        # Find match position
        pos = text.lower().find(query.lower())
        if pos == -1:
            return text[:context_chars] + ("..." if len(text) > context_chars else "")
        
        # Extract around match
        start = max(0, pos - context_chars // 2)
        end = min(len(text), pos + len(query) + context_chars // 2)
        
        excerpt = text[start:end]
        if start > 0:
            excerpt = "..." + excerpt
        if end < len(text):
            excerpt = excerpt + "..."
        
        return excerpt