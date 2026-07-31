from __future__ import annotations

import time

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from pkg.auth.oidc import (
    AWS_AUDIENCE,
    DEFAULT_ISSUER,
    GCP_AUDIENCE,
    OidcKeyManager,
    OidcTokenEngine,
    TokenRequest,
    reset_key_manager,
)


def test_key_manager_rsa_generation_and_jwks() -> None:
    reset_key_manager()
    km = OidcKeyManager(primary_kid="test-key-1")
    keypair = km.get_keypair()
    assert keypair.kid == "test-key-1"
    assert isinstance(keypair.private_key, rsa.RSAPrivateKey)

    jwks = km.get_jwks()
    assert "keys" in jwks
    assert len(jwks["keys"]) == 1

    key0 = jwks["keys"][0]
    assert key0["kty"] == "RSA"
    assert key0["alg"] == "RS256"
    assert key0["use"] == "sig"
    assert key0["kid"] == "test-key-1"
    assert "n" in key0
    assert "e" in key0


def test_key_manager_rotation_support() -> None:
    reset_key_manager()
    km = OidcKeyManager(primary_kid="v1")
    kp1 = km.get_keypair()

    # Generate a second key and add it
    new_kp = OidcKeyManager._generate_keypair("v2")
    km.add_keypair("v2", new_kp.private_bytes_pem(), set_as_primary=True)

    assert km.primary_kid == "v2"
    assert km.get_keypair().kid == "v2"
    assert km.get_keypair("v1").kid == "v1"

    jwks = km.get_jwks()
    assert len(jwks["keys"]) == 2
    kids = {k["kid"] for k in jwks["keys"]}
    assert kids == {"v1", "v2"}


def test_token_engine_jwt_claims_gcp() -> None:
    reset_key_manager()
    km = OidcKeyManager(primary_kid="gcp-key")
    engine = OidcTokenEngine(key_manager=km, issuer_url="https://api.launchpad.yourdomain.com")

    req = TokenRequest(
        org_id="org-123",
        workspace_id="ws-456",
        env_type="production",
        provider="gcp",
        ttl_seconds=600,
    )
    token = engine.generate_token(req)

    # Decode JOSE header without verification to check kid
    unverified_header = jwt.get_unverified_header(token)
    assert unverified_header["kid"] == "gcp-key"
    assert unverified_header["alg"] == "RS256"

    # Verify signature using public key from KeyManager
    pub_key_pem = km.get_keypair().public_bytes_pem()
    payload = jwt.decode(
        token,
        pub_key_pem,
        algorithms=["RS256"],
        audience=GCP_AUDIENCE,
    )

    assert payload["iss"] == "https://api.launchpad.yourdomain.com"
    assert payload["sub"] == "organization:org-123:workspace:ws-456:environment:production"
    assert payload["aud"] == GCP_AUDIENCE
    assert payload["workspace_id"] == "ws-456"
    assert payload["org_id"] == "org-123"
    assert payload["environment"] == "production"
    assert payload["exp"] - payload["iat"] == 600
    assert "jti" in payload


def test_token_engine_jwt_claims_aws() -> None:
    reset_key_manager()
    km = OidcKeyManager(primary_kid="aws-key")
    engine = OidcTokenEngine(key_manager=km)

    req = TokenRequest(
        org_id="org-abc",
        workspace_id="ws-xyz",
        env_type="staging",
        provider="aws",
        ttl_seconds=900,
    )
    token = engine.generate_token(req)

    pub_key_pem = km.get_keypair().public_bytes_pem()
    payload = jwt.decode(
        token,
        pub_key_pem,
        algorithms=["RS256"],
        audience=AWS_AUDIENCE,
    )

    assert payload["iss"] == DEFAULT_ISSUER
    assert payload["sub"] == "organization:org-abc:workspace:ws-xyz:environment:staging"
    assert payload["aud"] == AWS_AUDIENCE
    assert payload["workspace_id"] == "ws-xyz"


def test_token_engine_ttl_max_cap() -> None:
    reset_key_manager()
    km = OidcKeyManager(primary_kid="cap-key")
    engine = OidcTokenEngine(key_manager=km)

    # Request 3600 seconds, engine must cap at 900 seconds max (15 minutes)
    req = TokenRequest(
        org_id="org-1",
        workspace_id="ws-1",
        provider="gcp",
        ttl_seconds=3600,
    )
    token = engine.generate_token(req)

    pub_key_pem = km.get_keypair().public_bytes_pem()
    payload = jwt.decode(
        token,
        pub_key_pem,
        algorithms=["RS256"],
        audience=GCP_AUDIENCE,
    )

    assert payload["exp"] - payload["iat"] == 900
