"""RepoImportService.start_import: multi-repo clone + merge + connection graph."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pkg.detector.models import DetectedService, DetectionResult, ProjectLayout

from app.schemas.repo_import import RepoImportCreateRequest, RepoRef
from app.services.comm_detector import CommKind
from app.services.repo_import import RepoImportService


def _make_service(tmp_path: Path) -> tuple[RepoImportService, dict]:
    svc = RepoImportService(session=MagicMock())
    state = {"n": 0, "clones": []}

    def fake_clone(*, repo_url: str, branch: str = "main", token=None, import_id=None):
        idx = state["n"]
        state["n"] += 1
        root = tmp_path / f"clone{idx}"
        root.mkdir(parents=True, exist_ok=True)
        # Both repos use Kafka -> should share a broker node in the graph.
        (root / "package.json").write_text('{"dependencies":{"kafkajs":"^2"}}', encoding="utf-8")
        cloned = SimpleNamespace(
            import_id=f"imp{idx}",
            root_dir=root,
            repo_url=repo_url,
            branch=branch,
            commit_sha="abc123def456",
        )
        state["clones"].append(cloned)
        return cloned

    def fake_detect(root):
        name = f"svc-{Path(root).name}"
        return DetectionResult(
            layout=ProjectLayout.SINGLE,
            services=[DetectedService(id=name, name=name, path=".")],
            datastores=[],
        )

    svc._importer.clone = fake_clone  # type: ignore[method-assign]
    svc._importer.read_meta = lambda import_id: {  # type: ignore[method-assign]
        "repo_url": "https://github.com/acme/orders.git",
        "branch": "main",
        "commit_sha": "abc123def456",
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    svc._importer.cleanup = lambda import_id: None  # type: ignore[method-assign]
    svc._detector.detect = fake_detect  # type: ignore[method-assign]
    svc._resolve_token = lambda *a, **k: None  # type: ignore[method-assign]
    return svc, state


@pytest.mark.asyncio
async def test_start_import_two_repos_merges_and_graphs(tmp_path: Path) -> None:
    svc, state = _make_service(tmp_path)
    request = RepoImportCreateRequest(
        git_repo_url="https://github.com/acme/orders.git",
        repos=[RepoRef(git_repo_url="https://github.com/acme/billing.git", name="billing")],
    )
    owner = SimpleNamespace(id="user-1")

    session = await svc.start_import(request, owner=owner)

    # Both repos imported; both services present in one workspace session.
    assert len(session.repos) == 2
    assert len(session.services) == 2
    # Every repo (primary + secondary) lives under apps/<name>/; the container root
    # holds only metadata, never a repo's source tree.
    container = state["clones"][0].root_dir
    assert (container / "apps" / "orders" / "package.json").is_file()
    assert (container / "apps" / "billing" / "package.json").is_file()
    assert not (container / "package.json").exists()  # primary no longer at root
    assert not (container / "repos").exists()  # old layout gone
    # Connection graph built and rendered; both Kafka participants share the bus.
    assert session.service_graph is not None
    assert session.mermaid and "flowchart LR" in session.mermaid
    kafka_edges = [e for e in session.service_graph["edges"] if e["protocol"] == CommKind.KAFKA.value]
    assert len(kafka_edges) == 2
    # Services are named after their repos (not launch-web / svc-*).
    assert {s.name for s in session.services} == {"orders", "billing"}
    # Merged detection cached to disk (at the container root) for save_as_workspace.
    detection_json = (container / ".launchpad" / "detection.json").read_text(encoding="utf-8")
    assert '"orders"' in detection_json


def test_apply_repo_naming_single_and_monorepo() -> None:
    # Single service -> named exactly after the repo (no launch- prefix).
    single = RepoImportService._apply_repo_naming(
        "orders", [DetectedService(id="launch-web", name="launch-web", path=".")]
    )
    assert single[0].name == "orders"
    assert single[0].id == "orders"

    # Monorepo -> repo-prefixed, launch- stripped, unique.
    multi = RepoImportService._apply_repo_naming(
        "shop",
        [
            DetectedService(id="launch-web", name="launch-web", path="apps/web"),
            DetectedService(id="launch-server", name="launch-server", path="apps/api"),
        ],
    )
    assert [s.name for s in multi] == ["shop-web", "shop-server"]
    # Paths (build context) are preserved.
    assert multi[0].path == "apps/web"


@pytest.mark.asyncio
async def test_start_import_names_service_after_repo(tmp_path: Path) -> None:
    svc, state = _make_service(tmp_path)
    request = RepoImportCreateRequest(git_repo_url="https://github.com/acme/payments-api.git")
    owner = SimpleNamespace(id="user-1")

    session = await svc.start_import(request, owner=owner)

    # Backend repo is named after the repo, not launch-web.
    assert len(session.services) == 1
    assert session.services[0].name == "payments-api"
    assert not session.services[0].name.startswith("launch-")


@pytest.mark.asyncio
async def test_start_import_single_repo_unchanged(tmp_path: Path) -> None:
    svc, state = _make_service(tmp_path)
    request = RepoImportCreateRequest(git_repo_url="https://github.com/acme/solo.git")
    owner = SimpleNamespace(id="user-1")

    session = await svc.start_import(request, owner=owner)

    # Single-repo: one repo stays at the root, no apps/ or repos/ subtree.
    assert len(session.repos) == 1
    assert len(session.services) == 1
    assert (state["clones"][0].root_dir / "package.json").is_file()
    assert not (state["clones"][0].root_dir / "apps").exists()
    assert not (state["clones"][0].root_dir / "repos").exists()


@pytest.mark.asyncio
async def test_save_as_workspace_duplicate_name_raises_409(tmp_path: Path) -> None:
    from uuid import uuid4
    from fastapi import HTTPException
    from app.schemas.repo_import import RepoImportSaveRequest

    mock_session = MagicMock()
    svc = RepoImportService(session=mock_session)

    import_root = tmp_path / "import_root"
    import_root.mkdir(parents=True, exist_ok=True)
    (import_root / ".launchpad").mkdir(parents=True, exist_ok=True)
    svc._importer.get_root = lambda import_id: import_root
    svc._importer.read_meta = lambda import_id: {"repo_url": "https://github.com/acme/test.git", "branch": "main"}
    svc._read_detection = lambda root: DetectionResult(
        layout=ProjectLayout.SINGLE,
        services=[DetectedService(id="test", name="test", path=".")],
        datastores=[],
    )

    mock_org = SimpleNamespace(id=uuid4(), plan="free")
    mock_session.get = AsyncMock(return_value=mock_org)

    # 1. Org membership query -> returns membership with organization
    res_membership = MagicMock()
    res_membership.scalar_one_or_none.return_value = SimpleNamespace(organization=mock_org)
    # 2. Plan quota check query -> count = 0
    res_count = MagicMock()
    res_count.scalar_one.return_value = 0
    # 3. Existing workspace check query -> returns duplicate workspace
    res_workspace = MagicMock()
    res_workspace.scalar_one_or_none.return_value = SimpleNamespace(id=uuid4())

    mock_session.execute = AsyncMock(side_effect=[res_membership, res_count, res_workspace])

    owner = SimpleNamespace(id=uuid4(), email="user@example.com")
    request = RepoImportSaveRequest(name="launch-test", runtime_mode="kubernetes")

    with pytest.raises(HTTPException) as exc_info:
        await svc.save_as_workspace("imp-1", request, owner=owner, org_id=mock_org.id)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "workspace_exists"


@pytest.mark.asyncio
async def test_save_as_workspace_integrity_error_raises_409(tmp_path: Path) -> None:
    from uuid import uuid4
    from fastapi import HTTPException
    from sqlalchemy.exc import IntegrityError
    from app.schemas.repo_import import RepoImportSaveRequest

    mock_session = MagicMock()
    svc = RepoImportService(session=mock_session)

    import_root = tmp_path / "import_root"
    import_root.mkdir(parents=True, exist_ok=True)
    svc._importer.get_root = lambda import_id: import_root
    svc._importer.read_meta = lambda import_id: {"repo_url": "https://github.com/acme/test.git", "branch": "main"}
    svc._allocate_durable_dir = lambda name: tmp_path / "durable"
    svc._read_detection = lambda root: DetectionResult(
        layout=ProjectLayout.SINGLE,
        services=[DetectedService(id="test", name="test", path=".")],
        datastores=[],
    )

    mock_org = SimpleNamespace(id=uuid4(), plan="free")
    mock_session.get = AsyncMock(return_value=mock_org)

    # 1. Org membership query -> returns membership with organization
    res_membership = MagicMock()
    res_membership.scalar_one_or_none.return_value = SimpleNamespace(organization=mock_org)
    # 2. Plan quota check query -> count = 0
    res_count = MagicMock()
    res_count.scalar_one.return_value = 0
    # 3. Existing workspace check query -> returns None (no duplicate pre-commit)
    res_workspace = MagicMock()
    res_workspace.scalar_one_or_none.return_value = None
    # 4. Org resolve context query -> returns membership with organization
    res_membership2 = MagicMock()
    res_membership2.scalar_one_or_none.return_value = SimpleNamespace(organization=mock_org)
    # 5. Project resolve query -> returns a project
    res_project = MagicMock()
    res_project.scalar_one_or_none.return_value = SimpleNamespace(id=uuid4())

    mock_session.execute = AsyncMock(side_effect=[res_membership, res_count, res_workspace, res_membership2, res_project])
    mock_session.commit = AsyncMock(side_effect=IntegrityError("stmt", "params", Exception("duplicate key")))
    mock_session.rollback = AsyncMock()

    owner = SimpleNamespace(id=uuid4(), email="user@example.com")
    request = RepoImportSaveRequest(name="launch-test", runtime_mode="kubernetes")

    with pytest.raises(HTTPException) as exc_info:
        await svc.save_as_workspace("imp-1", request, owner=owner, org_id=mock_org.id)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "workspace_exists"
    mock_session.rollback.assert_called_once()


@pytest.mark.asyncio
async def test_save_as_workspace_link_mode_succeeds(tmp_path: Path) -> None:
    from uuid import uuid4
    from app.schemas.repo_import import RepoImportSaveRequest
    from app.models.domain import ProvisioningWorkspace

    mock_session = MagicMock()
    svc = RepoImportService(session=mock_session)

    import_root = tmp_path / "import_root"
    import_root.mkdir(parents=True, exist_ok=True)
    (import_root / ".launchpad").mkdir(parents=True, exist_ok=True)
    svc._importer.get_root = lambda import_id: import_root
    svc._importer.read_meta = lambda import_id: {"repo_url": "https://github.com/acme/test.git", "branch": "main"}
    svc._read_detection = lambda root: DetectionResult(
        layout=ProjectLayout.SINGLE,
        services=[DetectedService(id="test", name="test", path=".")],
        datastores=[],
    )

    mock_org = SimpleNamespace(id=uuid4(), plan="free")
    mock_session.get = AsyncMock(return_value=mock_org)

    res_membership = MagicMock()
    res_membership.scalar_one_or_none.return_value = SimpleNamespace(organization=mock_org)
    res_count = MagicMock()
    res_count.scalar_one.return_value = 0
    res_workspace = MagicMock()
    res_workspace.scalar_one_or_none.return_value = None
    res_membership2 = MagicMock()
    res_membership2.scalar_one_or_none.return_value = SimpleNamespace(organization=mock_org)
    res_project = MagicMock()
    res_project.scalar_one_or_none.return_value = SimpleNamespace(id=uuid4())

    mock_session.execute = AsyncMock(side_effect=[res_membership, res_count, res_workspace, res_membership2, res_project])
    mock_session.commit = AsyncMock()

    owner = SimpleNamespace(id=uuid4(), email="user@example.com")
    request = RepoImportSaveRequest(name="launch-link-test", runtime_mode="kubernetes")

    import_id = str(uuid4())
    result = await svc.save_as_workspace(import_id, request, owner=owner, org_id=mock_org.id, link_mode=True)
    assert result.workspace_id is not None
    mock_session.add.assert_called_once()
    added_ws = mock_session.add.call_args[0][0]
    assert isinstance(added_ws, ProvisioningWorkspace)
    assert added_ws.name == "launch-link-test"



