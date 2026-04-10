"""
WebSocket connection handler — chat messaging, thread routing, agent streaming.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import WebSocket, WebSocketDisconnect

from app.config import settings
from app.interfaces.web_auth import (
    active_ws,
    get_user_from_cookie,
    MAX_MESSAGE_LENGTH,
)

log = logging.getLogger(__name__)


async def broadcast_to_thread(thread_id: str, payload: dict, exclude_user: str = "") -> None:
    """Send a WS frame to all online participants of a thread (except exclude_user)."""
    from app.storage.threads import get_participants
    participants = get_participants(thread_id)
    frame = json.dumps(payload)
    for uname in participants:
        if uname == exclude_user:
            continue
        ws = active_ws.get(f"web:{uname}")
        if ws:
            try:
                await ws.send_text(frame)
            except Exception:
                pass


async def websocket_endpoint(websocket: WebSocket):
    """Main WebSocket endpoint — handles chat messages for General and thread-scoped conversations."""
    user = get_user_from_cookie(websocket)
    if not user:
        await websocket.close(code=4001)
        return

    await websocket.accept()
    session_key = f"web:{user}"
    active_ws[session_key] = websocket
    log.info("WebSocket connected: user=%s", user)

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_text(
                    json.dumps({"type": "error", "detail": "Invalid JSON"})
                )
                continue

            message = str(data.get("message", "")).strip()
            if not message:
                continue

            # Message length limit
            if len(message) > MAX_MESSAGE_LENGTH:
                await websocket.send_text(
                    json.dumps({"type": "error", "detail": "Message too long (max 10,000 characters)"})
                )
                continue

            thread_id = data.get("thread_id") or None

            # Rest-mode guard
            try:
                from app.state_manager import get_state_manager
                if not get_state_manager().is_accepting_messages():
                    rest = (
                        f"CDCN Agent is currently in rest mode and will be back online "
                        f"at {settings.wake_start_time}."
                    )
                    payload = {"type": "token", "content": rest}
                    done_payload = {"type": "done"}
                    if thread_id:
                        payload["thread_id"] = thread_id
                        done_payload["thread_id"] = thread_id
                    await websocket.send_text(json.dumps(payload))
                    await websocket.send_text(json.dumps(done_payload))
                    continue
            except RuntimeError:
                pass  # Manager not yet initialised — proceed

            # ── Thread-scoped message ──────────────────────────────────────
            if thread_id:
                from app.storage.threads import is_participant, save_message

                if not is_participant(thread_id, user):
                    await websocket.send_text(
                        json.dumps({"type": "error", "detail": "Not a participant", "thread_id": thread_id})
                    )
                    continue

                # Save user message
                save_message(thread_id, sender=user, role="user", content=message)

                # Broadcast user message to other participants
                ts_now = datetime.now(timezone.utc).isoformat()
                await broadcast_to_thread(thread_id, {
                    "type": "thread_msg",
                    "thread_id": thread_id,
                    "sender": user,
                    "content": message,
                    "ts": ts_now,
                }, exclude_user=user)

                # Signal thinking to all participants
                thinking_payload = {"type": "thinking", "thread_id": thread_id}
                await websocket.send_text(json.dumps(thinking_payload))
                await broadcast_to_thread(thread_id, thinking_payload, exclude_user=user)

                # Stream agent response — separate session per thread
                from app.gateway.router import _get_agent_router
                router = _get_agent_router()
                full_response = []
                async for token in router.handle_message(
                    user_id=f"web:{user}",
                    role="admin",
                    message=message,
                    channel_id=f"thread:{thread_id}",
                    display_name=user,
                ):
                    token_payload = {"type": "token", "content": token, "thread_id": thread_id}
                    await websocket.send_text(json.dumps(token_payload))
                    await broadcast_to_thread(thread_id, token_payload, exclude_user=user)
                    full_response.append(token)

                done_payload = {"type": "done", "thread_id": thread_id}
                await websocket.send_text(json.dumps(done_payload))
                await broadcast_to_thread(thread_id, done_payload, exclude_user=user)

                # Save agent response
                if full_response:
                    save_message(thread_id, sender="agent", role="assistant", content="".join(full_response))

                continue

            # ── General chat (no thread_id) — existing behaviour ───────────
            await websocket.send_text(json.dumps({"type": "thinking"}))

            from app.gateway.router import _get_agent_router
            router = _get_agent_router()
            async for token in router.handle_message(
                user_id=f"web:{user}",
                role="admin",
                message=message,
                channel_id=session_key,
                display_name=user,
            ):
                await websocket.send_text(
                    json.dumps({"type": "token", "content": token})
                )
            await websocket.send_text(json.dumps({"type": "done"}))

    except WebSocketDisconnect:
        log.info("WebSocket disconnected: user=%s", user)
    except Exception as exc:
        log.error("WebSocket error for user=%s: %s", user, exc)
        try:
            await websocket.send_text(
                json.dumps({"type": "error", "detail": "An internal error occurred."})
            )
        except Exception:
            pass
    finally:
        active_ws.pop(session_key, None)
