"""Regression: namespace-ensure survives a slow/stale local API server.

Reproduces the provisioning failure where the local kind/k3d apiserver read
times out (urllib3 MaxRetryError / ReadTimeoutError) and verifies the one-time
kubeconfig-refresh-and-retry self-heal, plus the read-timeout tuple.
"""

from __future__ import annotations

import pytest
from kubernetes.client.rest import ApiException
from urllib3.exceptions import MaxRetryError, ReadTimeoutError

from app.services.kubernetes import KubernetesProvisioner


class _FakeCore:
    def __init__(self, read_script: list[object]) -> None:
        self._read_script = read_script
        self._read_i = 0
        self.calls: list[tuple[str, str, object]] = []

    def read_namespace(self, name: str, _request_timeout=None):
        self.calls.append(("read", name, _request_timeout))
        action = self._read_script[self._read_i]
        self._read_i += 1
        if isinstance(action, Exception):
            raise action
        return action

    def create_namespace(self, body, _request_timeout=None):
        self.calls.append(("create", body.metadata.name, _request_timeout))
        return body


class _Resources:
    def __init__(self) -> None:
        self.created_namespace = False


def _provisioner() -> KubernetesProvisioner:
    # Bypass __init__ (no real kubeconfig) - we only exercise namespace ensure.
    return KubernetesProvisioner.__new__(KubernetesProvisioner)


def _timeout_error() -> MaxRetryError:
    return MaxRetryError(
        pool=None,
        url="/api/v1/namespaces/launchpad-env-x",
        reason=ReadTimeoutError(None, "/", "read timed out"),
    )


def test_existing_namespace_uses_connect_read_tuple() -> None:
    p = _provisioner()
    p._core = _FakeCore(["exists"])
    p._ensure_namespace_exists("ns-a", {}, _Resources())
    # A (connect, read) tuple, not a scalar total that shrinks the read deadline.
    assert p._core.calls[0][2] == (5, 30)


def test_missing_namespace_is_created() -> None:
    p = _provisioner()
    p._core = _FakeCore([ApiException(status=404)])
    resources = _Resources()
    p._ensure_namespace_exists("ns-b", {"env": "preview"}, resources)
    assert resources.created_namespace is True
    assert [c[0] for c in p._core.calls] == ["read", "create"]


def test_transient_timeout_recovers_and_retries() -> None:
    p = _provisioner()
    bad = _FakeCore([_timeout_error()])
    healthy = _FakeCore(["exists"])
    p._core = bad

    recovered = {"count": 0}

    def fake_recover() -> None:
        recovered["count"] += 1
        p._core = healthy  # simulate kubeconfig refresh + client reload

    p._recover_cluster_connection = fake_recover  # type: ignore[method-assign]
    p._ensure_namespace_exists("ns-c", {}, _Resources())

    assert recovered["count"] == 1
    assert len(bad.calls) == 1
    assert len(healthy.calls) == 1


def test_persistent_timeout_gives_up_after_one_retry() -> None:
    p = _provisioner()
    p._core = _FakeCore([_timeout_error(), _timeout_error()])
    p._recover_cluster_connection = lambda: None  # type: ignore[method-assign]
    with pytest.raises(MaxRetryError):
        p._ensure_namespace_exists("ns-d", {}, _Resources())


def test_non_404_api_error_propagates_without_recovery() -> None:
    p = _provisioner()
    p._core = _FakeCore([ApiException(status=403)])
    called = {"recover": False}

    def fake_recover() -> None:
        called["recover"] = True

    p._recover_cluster_connection = fake_recover  # type: ignore[method-assign]
    with pytest.raises(ApiException):
        p._ensure_namespace_exists("ns-e", {}, _Resources())
    assert called["recover"] is False
