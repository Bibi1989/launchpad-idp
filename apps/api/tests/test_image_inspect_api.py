from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.deps.auth import get_current_user
from app.routers import provisioning as provisioning_router


class _FakeUser:
    id = "00000000-0000-0000-0000-000000000001"
    email = "tester@example.com"


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    app = FastAPI()
    app.include_router(provisioning_router.router)
    app.dependency_overrides[get_current_user] = lambda: _FakeUser()

    monkeypatch.setattr(
        provisioning_router,
        "inspect_image_exposed_ports",
        lambda image: [5000] if "afroshop" in image else [],
    )

    return TestClient(app)


def test_inspect_image_prefers_http_expose(client: TestClient) -> None:
    response = client.post(
        "/provisioning/images/inspect",
        json={"image": "bibi1989/afroshopclient:1.0"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["image"] == "bibi1989/afroshopclient:1.0"
    assert body["exposed_ports"] == [5000]
    assert body["listen_port"] == 5000


def test_inspect_image_defaults_when_no_expose(client: TestClient) -> None:
    response = client.post(
        "/provisioning/images/inspect",
        json={"image": "library/unknown:latest"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["exposed_ports"] == []
    assert body["listen_port"] == 80
