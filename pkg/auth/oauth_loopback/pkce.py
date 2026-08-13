"""PKCE (RFC 7636) helpers for OAuth public clients."""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass


def _b64url_nopad(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


@dataclass(frozen=True, slots=True)
class PkcePair:
    """code_verifier plus S256 code_challenge."""

    verifier: str
    challenge: str
    method: str = "S256"


def generate_code_verifier(num_bytes: int = 32) -> str:
    """Return a high-entropy code_verifier (43-128 chars after encoding)."""
    if num_bytes < 32 or num_bytes > 96:
        raise ValueError("num_bytes must be between 32 and 96 inclusive")
    return _b64url_nopad(secrets.token_bytes(num_bytes))


def code_challenge_s256(verifier: str) -> str:
    """BASE64URL(SHA256(ASCII(code_verifier))) without padding."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return _b64url_nopad(digest)


def generate_pkce() -> PkcePair:
    """Generate a fresh PKCE verifier/challenge pair (S256)."""
    verifier = generate_code_verifier()
    return PkcePair(verifier=verifier, challenge=code_challenge_s256(verifier))


def generate_state(num_bytes: int = 32) -> str:
    """Cryptographically random OAuth ``state`` (CSRF binding)."""
    return _b64url_nopad(secrets.token_bytes(num_bytes))
