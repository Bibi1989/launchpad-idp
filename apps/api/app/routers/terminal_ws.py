from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, ValidationError

from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger
from app.core.secrets import mask_terminal_output
from app.deps.auth import get_user_from_websocket
from app.services.provisioning import ProvisioningService
from app.services.sandbox_runner import get_sandbox_runner

logger = get_logger(__name__)
router = APIRouter()


class TerminalResizeMessage(BaseModel):
    type: str = Field(pattern=r"^resize$")
    cols: int = Field(ge=20, le=500)
    rows: int = Field(ge=5, le=200)


class TerminalInputMessage(BaseModel):
    type: str = Field(pattern=r"^input$")
    data: str


class TerminalControlMessage(BaseModel):
    type: str = Field(pattern=r"^(kill|restart)$")


async def _require_ws_user(websocket: WebSocket):
    await websocket.accept()
    user = await get_user_from_websocket(websocket)
    if user is None:
        await websocket.send_json({"type": "error", "message": "Authentication required"})
        await websocket.close(code=4401)
        return None
    return user


@router.websocket("/ws/terminal/{session_id}")
async def terminal_websocket(websocket: WebSocket, session_id: UUID) -> None:
    user = await _require_ws_user(websocket)
    if user is None:
        return
    runner = get_sandbox_runner()
    session = runner.get_session(str(session_id))
    if session is None or not session.alive:
        await websocket.send_json(
            {"type": "error", "message": "Terminal session not found or inactive"}
        )
        await websocket.close(code=4004)
        return

    await websocket.send_json(
        {
            "type": "ready",
            "session_id": str(session_id),
            "mode": session.mode,
            "cols": session.cols,
            "rows": session.rows,
        }
    )

    output_queue: asyncio.Queue[bytes] = asyncio.Queue()

    def on_output(chunk: bytes) -> None:
        output_queue.put_nowait(chunk)

    reader_task = asyncio.create_task(runner.read_loop(str(session_id), on_output))
    sender_task = asyncio.create_task(_pump_output(websocket, output_queue))

    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            if "text" in message and message["text"] is not None:
                await _handle_text_message(str(session_id), message["text"], runner, websocket)
            elif "bytes" in message and message["bytes"] is not None:
                raw = message["bytes"]
                await runner.write(str(session_id), raw)
    except WebSocketDisconnect:
        logger.info("terminal_ws_disconnected", session_id=str(session_id))
    except Exception:
        logger.exception("terminal_ws_error", session_id=str(session_id))
        try:
            await websocket.send_json({"type": "error", "message": "Terminal stream failed"})
        except Exception:
            pass
    finally:
        reader_task.cancel()
        sender_task.cancel()
        await runner.kill(str(session_id))


async def _pump_output(websocket: WebSocket, queue: asyncio.Queue[bytes]) -> None:
    while True:
        chunk = await queue.get()
        masked = mask_terminal_output(chunk.decode("utf-8", errors="replace"))
        await websocket.send_json({"type": "output", "data": masked})


async def _handle_text_message(
    session_id: str,
    text: str,
    runner: Any,
    websocket: WebSocket,
) -> None:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        await websocket.send_json({"type": "error", "message": "Invalid JSON message"})
        return

    msg_type = payload.get("type")
    try:
        if msg_type == "input":
            parsed = TerminalInputMessage.model_validate(payload)
            await runner.write(session_id, parsed.data.encode("utf-8"))
        elif msg_type == "resize":
            parsed_resize = TerminalResizeMessage.model_validate(payload)
            await runner.resize(session_id, parsed_resize.cols, parsed_resize.rows)
        elif msg_type == "kill":
            await runner.kill(session_id)
            await websocket.send_json({"type": "status", "status": "killed"})
            await websocket.close()
        elif msg_type == "restart":
            await websocket.send_json(
                {
                    "type": "error",
                    "message": "Restart via REST /provisioning/workspaces/{id}/terminal",
                }
            )
        else:
            await websocket.send_json(
                {"type": "error", "message": f"Unknown message type: {msg_type}"}
            )
    except ValidationError as exc:
        await websocket.send_json(
            {"type": "error", "message": "Invalid terminal message", "details": exc.errors()}
        )
    except RuntimeError as exc:
        await websocket.send_json({"type": "error", "message": str(exc)})


@router.websocket("/ws/terminal/workspace/{workspace_id}")
async def terminal_websocket_for_workspace(websocket: WebSocket, workspace_id: UUID) -> None:
    user = await _require_ws_user(websocket)
    if user is None:
        return
    cols = int(websocket.query_params.get("cols", "120"))
    rows = int(websocket.query_params.get("rows", "40"))
    run_init = websocket.query_params.get("run_init", "true").lower() != "false"

    async with AsyncSessionLocal() as db:
        service = ProvisioningService(db)
        try:
            session = await service.open_terminal(
                workspace_id,
                owner=user,
                cols=cols,
                rows=rows,
                run_init=run_init,
            )
        except Exception as exc:
            logger.exception("terminal_workspace_open_failed", workspace_id=str(workspace_id))
            await websocket.send_json({"type": "error", "message": str(exc)})
            await websocket.close(code=1011)
            return

    runner = get_sandbox_runner()
    await websocket.send_json(
        {
            "type": "ready",
            "session_id": session.session_id,
            "workspace_id": str(workspace_id),
            "mode": session.mode,
            "cols": session.cols,
            "rows": session.rows,
        }
    )

    output_queue: asyncio.Queue[bytes] = asyncio.Queue()

    def on_output(chunk: bytes) -> None:
        output_queue.put_nowait(chunk)

    reader_task = asyncio.create_task(runner.read_loop(session.session_id, on_output))
    sender_task = asyncio.create_task(_pump_output(websocket, output_queue))
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            if "text" in message and message["text"] is not None:
                await _handle_text_message(session.session_id, message["text"], runner, websocket)
            elif "bytes" in message and message["bytes"] is not None:
                await runner.write(session.session_id, message["bytes"])
    except WebSocketDisconnect:
        logger.info("terminal_ws_disconnected", session_id=session.session_id)
    finally:
        reader_task.cancel()
        sender_task.cancel()
        await runner.kill(session.session_id)
