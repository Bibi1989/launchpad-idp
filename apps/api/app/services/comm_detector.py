"""Detect how a service communicates: Kafka, RabbitMQ, gRPC, Redis, HTTP.

Scans a repository's dependency manifests, config/env hints, and ``.proto`` files
to infer each service's communication *capabilities* (what buses/protocols it
participates in). This feeds the service-connection graph so a multi-repo,
microservice workspace can be wired up automatically - while leaving the operator
free to override or add connections.

Heuristic and best-effort: dependency presence implies participation, not
direction. Where role can be inferred (a ``.proto`` file => gRPC server) it is;
otherwise the role is PEER and the user confirms specifics in the graph editor.
"""

from __future__ import annotations

import re
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field

from app.core.logging import get_logger

logger = get_logger(__name__)


class CommKind(str, Enum):
    KAFKA = "kafka"
    RABBITMQ = "rabbitmq"
    GRPC = "grpc"
    REDIS = "redis"
    HTTP = "http"
    # Datastore protocols. The detector never emits these (it only detects the
    # comm kinds above); they are valid edge/connection protocols so a service can
    # be wired to a database node in the graph.
    POSTGRES = "postgres"
    MYSQL = "mysql"
    MARIADB = "mariadb"
    MONGODB = "mongodb"


class CommRole(str, Enum):
    SERVER = "server"      # exposes (gRPC server, HTTP API)
    CLIENT = "client"      # calls out (gRPC client)
    PEER = "peer"          # participates in a bus (Kafka/RabbitMQ/Redis)


class ServiceCapability(BaseModel):
    kind: CommKind
    role: CommRole
    evidence: str = Field(description="what signal detected this (dep/file/env)")


class ServiceComms(BaseModel):
    service: str
    capabilities: list[ServiceCapability] = Field(default_factory=list)

    def kinds(self) -> set[CommKind]:
        return {c.kind for c in self.capabilities}


# Dependency-name substrings per protocol (covers npm / pip / go / java / rust / .net).
_KAFKA_DEPS = (
    "kafkajs", "kafka-python", "confluent-kafka", "aiokafka", "node-rdkafka",
    "segmentio/kafka-go", "confluentinc/confluent-kafka-go", "shopify/sarama",
    "twmb/franz-go", "org.apache.kafka", "spring-kafka", "rdkafka", "confluent.kafka",
)
_RABBIT_DEPS = (
    "amqplib", "aio-pika", "pika", "amqp", "kombu", "celery",
    "streadway/amqp", "rabbitmq/amqp091-go", "spring-rabbit", "spring-amqp",
    "rabbitmq.client", "lapin",
)
_GRPC_DEPS = (
    "@grpc/grpc-js", "grpc", "grpcio", "grpcio-tools", "google.golang.org/grpc",
    "io.grpc", "grpc-netty", "tonic", "grpc.core", "grpc.net",
)
_REDIS_DEPS = (
    "ioredis", "redis", "aioredis", "go-redis", "redigo",
    "spring-data-redis", "stackexchange.redis",
)

_ENV_HINTS = {
    CommKind.KAFKA: re.compile(r"KAFKA_(BROKERS|BOOTSTRAP|SERVERS)", re.IGNORECASE),
    CommKind.RABBITMQ: re.compile(r"(RABBITMQ|AMQP)_?(URL|URI|HOST)", re.IGNORECASE),
    CommKind.REDIS: re.compile(r"REDIS_(URL|URI|HOST)", re.IGNORECASE),
    CommKind.GRPC: re.compile(r"GRPC_(PORT|ADDR|TARGET|SERVER)", re.IGNORECASE),
}


def _dependency_blob(root: Path) -> str:
    """Concatenate dependency manifests into one lowercased searchable blob."""
    parts: list[str] = []
    manifests = (
        "package.json", "requirements.txt", "pyproject.toml", "Pipfile",
        "go.mod", "pom.xml", "build.gradle", "build.gradle.kts",
        "Cargo.toml", "composer.json",
    )
    for name in manifests:
        # Root + one level of app subdirs (monorepo).
        for path in [root / name, *root.glob(f"*/{name}"), *root.glob(f"apps/*/{name}")]:
            if path.is_file():
                try:
                    parts.append(path.read_text(encoding="utf-8", errors="ignore"))
                except OSError:
                    continue
    return "\n".join(parts).lower()


def _env_blob(root: Path) -> str:
    parts: list[str] = []
    for name in (".env.example", ".env.sample", ".env.template", "docker-compose.yml", "compose.yml"):
        for path in [root / name, *root.glob(f"*/{name}")]:
            if path.is_file():
                try:
                    parts.append(path.read_text(encoding="utf-8", errors="ignore"))
                except OSError:
                    continue
    return "\n".join(parts)


def _has_any(blob: str, needles: tuple[str, ...]) -> str | None:
    for needle in needles:
        if needle.lower() in blob:
            return needle
    return None


def detect_service_comms(root: str | Path, *, service_name: str = "app") -> ServiceComms:
    """Infer a single service's communication capabilities from its repo tree."""
    root_path = Path(root)
    deps = _dependency_blob(root_path)
    env = _env_blob(root_path)
    caps: list[ServiceCapability] = []
    seen: set[tuple[CommKind, CommRole]] = set()

    def add(kind: CommKind, role: CommRole, evidence: str) -> None:
        key = (kind, role)
        if key not in seen:
            seen.add(key)
            caps.append(ServiceCapability(kind=kind, role=role, evidence=evidence))

    # gRPC: a .proto file means this service exposes a gRPC server; a grpc client
    # lib without a proto means it calls one.
    proto = next(iter(root_path.rglob("*.proto")), None)
    grpc_dep = _has_any(deps, _GRPC_DEPS)
    if proto is not None:
        add(CommKind.GRPC, CommRole.SERVER, f"proto:{proto.name}")
    if grpc_dep:
        # A gRPC client library present => this service also calls gRPC.
        add(CommKind.GRPC, CommRole.CLIENT, f"dep:{grpc_dep}")

    for kind, needles in (
        (CommKind.KAFKA, _KAFKA_DEPS),
        (CommKind.RABBITMQ, _RABBIT_DEPS),
        (CommKind.REDIS, _REDIS_DEPS),
    ):
        dep = _has_any(deps, needles)
        if dep:
            add(kind, CommRole.PEER, f"dep:{dep}")

    # Env/config hints reinforce or add bus participation.
    for kind, pattern in _ENV_HINTS.items():
        if pattern.search(env):
            role = CommRole.CLIENT if kind == CommKind.GRPC else CommRole.PEER
            add(kind, role, "env-hint")

    logger.info(
        "comm_detected",
        service=service_name,
        kinds=[c.kind.value for c in caps],
    )
    return ServiceComms(service=service_name, capabilities=caps)


def detect_from_package_json_names(dependency_names: list[str]) -> set[CommKind]:
    """Helper for callers that already parsed a package manifest's dep names."""
    blob = " ".join(dependency_names).lower()
    kinds: set[CommKind] = set()
    if _has_any(blob, _KAFKA_DEPS):
        kinds.add(CommKind.KAFKA)
    if _has_any(blob, _RABBIT_DEPS):
        kinds.add(CommKind.RABBITMQ)
    if _has_any(blob, _GRPC_DEPS):
        kinds.add(CommKind.GRPC)
    if _has_any(blob, _REDIS_DEPS):
        kinds.add(CommKind.REDIS)
    return kinds
