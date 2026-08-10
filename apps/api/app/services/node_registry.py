"""Registry + live reverse-tunnel hub for hybrid local/edge agent nodes.

Persistence (``AgentNode``) is separated from the in-process live connection
hub (``LiveAgentHub``). The hub tracks the set of currently-connected agents and
routes commands to them over their WebSocket, awaiting a correlated result.

Agent authentication is HMAC-based (never a user JWT): the agent signs
``{node_id}.{ts}.{nonce}`` with its per-node secret, mirroring the GitHub
webhook signature scheme already used in ``services/webhook.py``.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi import WebSocket
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.secrets import decrypt_secret, encrypt_secret
from app.models.domain import AgentNode, AgentNodeStatus, User
from app.schemas.nodes import (
    ContainerSummary,
    NodeCommandAction,
    NodeCommandResult,
    NodeRead,
    NodeStatus,
    NodeTelemetry,
)

logger = get_logger(__name__)

# Enrollment tokens are prefixed so they are recognizable in logs/UX (value redacted).
ENROLLMENT_TOKEN_PREFIX = "lp_"
# Reject signatures whose timestamp drifts beyond this window (replay protection).
_MAX_CLOCK_SKEW_SECONDS = 60


def generate_enrollment_token() -> str:
    return f"{ENROLLMENT_TOKEN_PREFIX}{secrets.token_urlsafe(24)}"


def generate_agent_secret() -> str:
    return secrets.token_urlsafe(32)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.strip().encode("utf-8")).hexdigest()


def sign_agent_payload(secret: str, node_id: str, ts: str, nonce: str) -> str:
    """HMAC-SHA256 hex signature the agent computes for the WS handshake."""
    message = f"{node_id}.{ts}.{nonce}".encode()
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


class LiveAgentHub:
    """Process-level registry of connected agents + command correlation.

    A singleton (see :func:`get_agent_hub`) so REST dispatch and the WebSocket
    handler share the same live connection map.
    """

    def __init__(self) -> None:
        self._connections: dict[str, WebSocket] = {}
        self._pending: dict[str, asyncio.Future[NodeCommandResult]] = {}
        self._lock = asyncio.Lock()

    async def register(self, node_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            # Replace any stale connection for the same node.
            existing = self._connections.get(node_id)
            if existing is not None and existing is not websocket:
                try:
                    await existing.close(code=4409)
                except Exception as exc:  # noqa: BLE001 - best-effort takeover
                    logger.debug("agent_hub_takeover_close_failed", node_id=node_id, error=str(exc))
            self._connections[node_id] = websocket
        logger.info("agent_hub_registered", node_id=node_id, online=len(self._connections))

    async def unregister(self, node_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            if self._connections.get(node_id) is websocket:
                self._connections.pop(node_id, None)
        self._fail_pending(node_id)
        logger.info("agent_hub_unregistered", node_id=node_id, online=len(self._connections))

    async def force_disconnect(self, node_id: str, *, code: int = 4403) -> None:
        """Close and drop any live socket for this node (e.g. revoke)."""
        async with self._lock:
            websocket = self._connections.pop(node_id, None)
        if websocket is None:
            return
        try:
            await websocket.close(code=code)
        except Exception as exc:  # noqa: BLE001 - best-effort close
            logger.debug("agent_hub_force_close_failed", node_id=node_id, error=str(exc))
        self._fail_pending(node_id)
        logger.info("agent_hub_force_disconnected", node_id=node_id, online=len(self._connections))

    def _fail_pending(self, node_id: str) -> None:
        for command_id, future in list(self._pending.items()):
            if command_id.startswith(f"{node_id}:") and not future.done():
                future.set_exception(RuntimeError("Agent disconnected"))

    def is_online(self, node_id: str) -> bool:
        return node_id in self._connections

    def online_ids(self) -> set[str]:
        return set(self._connections.keys())

    def resolve(self, command_id: str, result: NodeCommandResult) -> None:
        future = self._pending.get(command_id)
        if future is not None and not future.done():
            future.set_result(result)

    async def dispatch(
        self,
        node_id: str,
        action: NodeCommandAction,
        payload: dict,
        *,
        timeout: float,
    ) -> NodeCommandResult:
        websocket = self._connections.get(node_id)
        if websocket is None:
            raise RuntimeError("Agent is not connected")

        command_id = f"{node_id}:{uuid4().hex}"
        loop = asyncio.get_running_loop()
        future: asyncio.Future[NodeCommandResult] = loop.create_future()
        self._pending[command_id] = future
        try:
            await websocket.send_json(
                {
                    "type": "command",
                    "command_id": command_id,
                    "action": action.value,
                    "payload": payload,
                }
            )
            return await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError as exc:
            raise TimeoutError("Agent did not respond in time") from exc
        finally:
            self._pending.pop(command_id, None)


_hub: LiveAgentHub | None = None


def get_agent_hub() -> LiveAgentHub:
    global _hub
    if _hub is None:
        _hub = LiveAgentHub()
    return _hub


class NodeRegistryService:
    """Database-backed lifecycle for agent nodes."""

    def __init__(self, session: AsyncSession, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._hub = get_agent_hub()

    # -- Enrollment / registration ---------------------------------------- #

    async def create_enrollment(
        self,
        *,
        name: str,
        owner: User,
        org_id: UUID | None,
        labels: dict[str, str],
    ) -> tuple[AgentNode, str]:
        """Create a PENDING node and a single-use install token (returned raw once)."""
        raw_token = generate_enrollment_token()
        node = AgentNode(
            owner_id=owner.id,
            org_id=org_id,
            name=name,
            status=AgentNodeStatus.PENDING,
            enrollment_token_hash=hash_token(raw_token),
            enrollment_expires_at=datetime.now(UTC)
            + timedelta(seconds=self._settings.agent_enrollment_ttl_seconds),
            labels_json=json.dumps(labels) if labels else None,
        )
        self._session.add(node)
        await self._session.commit()
        await self._session.refresh(node)
        return node, raw_token

    async def register_agent(
        self,
        *,
        enrollment_token: str,
        hostname: str | None,
        platform: str | None,
        agent_version: str | None,
        cpu_cores: int | None,
        mem_total_mb: int | None,
    ) -> tuple[AgentNode, str]:
        """Validate the install token and issue a per-node HMAC secret (returned raw once)."""
        token_hash = hash_token(enrollment_token)
        result = await self._session.execute(
            select(AgentNode).where(AgentNode.enrollment_token_hash == token_hash)
        )
        node = result.scalar_one_or_none()
        if node is None:
            raise ValueError("Invalid enrollment token")
        if node.status == AgentNodeStatus.REVOKED:
            raise ValueError("This node has been revoked")
        expires = node.enrollment_expires_at
        if expires is not None and expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if expires is None or expires < datetime.now(UTC):
            raise ValueError("Enrollment token has expired")

        agent_secret = generate_agent_secret()
        node.encrypted_agent_secret = encrypt_secret(agent_secret)
        # Single-use: burn the enrollment token now that a secret is issued.
        node.enrollment_token_hash = None
        node.enrollment_expires_at = None
        node.hostname = hostname
        node.platform = platform
        node.agent_version = agent_version
        node.cpu_cores = cpu_cores
        node.mem_total_mb = mem_total_mb
        node.status = AgentNodeStatus.OFFLINE
        await self._session.commit()
        await self._session.refresh(node)
        return node, agent_secret

    # -- WS authentication ------------------------------------------------- #

    async def authenticate_ws(self, params: dict[str, str]) -> AgentNode | None:
        """Verify the HMAC handshake carried in the WS query string."""
        node_id = params.get("node_id")
        ts = params.get("ts")
        nonce = params.get("nonce")
        sig = params.get("sig")
        if not (node_id and ts and nonce and sig):
            return None
        try:
            node_uuid = UUID(node_id)
        except ValueError:
            return None
        try:
            drift = abs(datetime.now(UTC).timestamp() - float(ts))
        except (TypeError, ValueError):
            return None
        if drift > _MAX_CLOCK_SKEW_SECONDS:
            return None

        node = await self._session.get(AgentNode, node_uuid)
        if node is None or node.status == AgentNodeStatus.REVOKED:
            return None
        if not node.encrypted_agent_secret:
            return None
        try:
            secret = decrypt_secret(node.encrypted_agent_secret)
        except ValueError:
            return None
        expected = sign_agent_payload(secret, node_id, ts, nonce)
        if not hmac.compare_digest(expected, sig):
            return None
        return node

    # -- Telemetry --------------------------------------------------------- #

    async def apply_heartbeat(self, node_id: UUID, telemetry: NodeTelemetry) -> None:
        node = await self._session.get(AgentNode, node_id)
        if node is None or node.status == AgentNodeStatus.REVOKED:
            return
        node.status = AgentNodeStatus.ONLINE
        node.last_heartbeat_at = datetime.now(UTC)
        node.cpu_percent = Decimal(str(round(telemetry.cpu_percent, 2)))
        node.mem_percent = Decimal(str(round(telemetry.mem_percent, 2)))
        node.disk_percent = Decimal(str(round(telemetry.disk_percent, 2)))
        node.docker_status = telemetry.docker_status[:32]
        if telemetry.cpu_cores:
            node.cpu_cores = telemetry.cpu_cores
        if telemetry.mem_total_mb:
            node.mem_total_mb = telemetry.mem_total_mb
        node.containers_json = json.dumps(
            [c.model_dump() for c in telemetry.containers]
        )
        await self._session.commit()

    async def mark_offline(self, node_id: UUID) -> None:
        node = await self._session.get(AgentNode, node_id)
        if node is None or node.status == AgentNodeStatus.REVOKED:
            return
        node.status = AgentNodeStatus.OFFLINE
        await self._session.commit()

    # -- Queries / mutations ---------------------------------------------- #

    async def list_nodes(self, *, org_id: UUID | None) -> list[NodeRead]:
        stmt = (
            select(AgentNode)
            .where(
                AgentNode.org_id == org_id,
                AgentNode.status != AgentNodeStatus.REVOKED,
            )
            .order_by(AgentNode.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return [self._to_read(node) for node in result.scalars().all()]

    async def get_node(self, node_id: UUID, *, org_id: UUID | None) -> AgentNode | None:
        node = await self._session.get(AgentNode, node_id)
        if node is None or node.org_id != org_id:
            return None
        return node

    async def get_node_read(self, node_id: UUID, *, org_id: UUID | None) -> NodeRead | None:
        node = await self.get_node(node_id, org_id=org_id)
        if node is None:
            return None
        return self._to_read(node)

    async def revoke_node(self, node: AgentNode) -> None:
        """Disconnect the agent and permanently delete the node row."""
        node_id = str(node.id)
        await self._hub.force_disconnect(node_id)
        await self._session.delete(node)
        await self._session.commit()

    async def dispatch_command(
        self,
        node: AgentNode,
        *,
        action: NodeCommandAction,
        payload: dict,
    ) -> NodeCommandResult:
        return await self._hub.dispatch(
            str(node.id),
            action,
            payload,
            timeout=self._settings.agent_command_timeout_seconds,
        )

    # -- Serialization ----------------------------------------------------- #

    def _to_read(self, node: AgentNode) -> NodeRead:
        # Online == a fresh heartbeat (shared DB state), NOT live-socket ownership in
        # this process: with --reload / multiple workers the WS may live in another
        # worker, yet the node is genuinely online while heartbeats keep arriving.
        online = self._is_heartbeat_fresh(node)
        status = self._effective_status(node, online)
        containers: list[ContainerSummary] = []
        if node.containers_json:
            try:
                containers = [
                    ContainerSummary.model_validate(item)
                    for item in json.loads(node.containers_json)
                ]
            except (ValueError, TypeError):
                containers = []
        labels: dict[str, str] = {}
        if node.labels_json:
            try:
                labels = dict(json.loads(node.labels_json))
            except (ValueError, TypeError):
                labels = {}
        return NodeRead(
            id=node.id,
            name=node.name,
            status=NodeStatus(status.value),
            online=online,
            labels=labels,
            hostname=node.hostname,
            platform=node.platform,
            agent_version=node.agent_version,
            cpu_cores=node.cpu_cores,
            mem_total_mb=node.mem_total_mb,
            last_heartbeat_at=node.last_heartbeat_at,
            cpu_percent=node.cpu_percent,
            mem_percent=node.mem_percent,
            disk_percent=node.disk_percent,
            docker_status=node.docker_status,
            containers=containers,
            created_at=node.created_at,
        )

    def _is_heartbeat_fresh(self, node: AgentNode) -> bool:
        if node.last_heartbeat_at is None:
            return False
        last = node.last_heartbeat_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        age = (datetime.now(UTC) - last).total_seconds()
        return age <= self._settings.agent_offline_after_seconds

    def _effective_status(self, node: AgentNode, online: bool) -> AgentNodeStatus:
        if node.status == AgentNodeStatus.REVOKED:
            return AgentNodeStatus.REVOKED
        if node.status == AgentNodeStatus.PENDING:
            return AgentNodeStatus.PENDING
        return AgentNodeStatus.ONLINE if online else AgentNodeStatus.OFFLINE
