"""NodePort allocation is per-user (per owner) so one user's previews cluster in
their own region of the small range instead of hashing globally by env id."""

from __future__ import annotations

import pytest

from app.services.kubernetes import allocate_node_port


def test_allocate_prefers_owner_seeded_start() -> None:
    # Same owner -> same preferred start across different environments.
    a1 = allocate_node_port("env-1", port_min=30080, port_max=30089, owner_key="alice@x.io")
    a2 = allocate_node_port("env-2", port_min=30080, port_max=30089, owner_key="alice@x.io")
    assert a1 == a2  # both start at alice's home port when nothing is used yet

    # A different owner gets a (very likely) different home port.
    b1 = allocate_node_port("env-3", port_min=30080, port_max=30089, owner_key="bob@x.io")
    assert 30080 <= b1 <= 30089
    # Not asserting a1 != b1 strictly (hash collision possible), but the seed differs.


def test_allocate_skips_used_ports_from_owner_home() -> None:
    # alice's home is taken -> next free port from her start is returned.
    home = allocate_node_port("env-1", port_min=30080, port_max=30089, owner_key="alice@x.io")
    nxt = allocate_node_port(
        "env-2", port_min=30080, port_max=30089, owner_key="alice@x.io", used_ports={home}
    )
    assert nxt != home
    assert 30080 <= nxt <= 30089


def test_allocate_raises_when_range_full() -> None:
    full = set(range(30080, 30085))
    with pytest.raises(RuntimeError, match="No free NodePort"):
        allocate_node_port(
            "env-x", port_min=30080, port_max=30084, owner_key="alice@x.io", used_ports=full
        )


def test_allocate_falls_back_to_env_id_without_owner() -> None:
    # No owner_key -> stable per-env hash (backward compatible).
    p1 = allocate_node_port("env-1", port_min=30080, port_max=30089)
    p2 = allocate_node_port("env-1", port_min=30080, port_max=30089)
    assert p1 == p2
