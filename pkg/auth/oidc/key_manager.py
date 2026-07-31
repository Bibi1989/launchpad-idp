from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey

logger = logging.getLogger(__name__)


def _int_to_base64url(val: int) -> str:
    """Convert an integer to a big-endian base64url encoded string without padding."""
    length = (val.bit_length() + 7) // 8 or 1
    val_bytes = val.to_bytes(length, byteorder="big")
    return base64.urlsafe_b64encode(val_bytes).rstrip(b"=").decode("utf-8")


@dataclass
class KeyPair:
    kid: str
    private_key: RSAPrivateKey
    public_key: RSAPublicKey

    def to_jwk(self) -> dict[str, str]:
        """Export public key components as RFC 7517/7518 JWK dictionary."""
        numbers = self.public_key.public_numbers()
        return {
            "kty": "RSA",
            "use": "sig",
            "alg": "RS256",
            "kid": self.kid,
            "n": _int_to_base64url(numbers.n),
            "e": _int_to_base64url(numbers.e),
        }

    def private_bytes_pem(self) -> bytes:
        """Export private key as PKCS8 PEM bytes."""
        return self.private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

    def public_bytes_pem(self) -> bytes:
        """Export public key as SubjectPublicKeyInfo PEM bytes."""
        return self.public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )


class OidcKeyManager:
    """Manages RSA 256 signing keys and JWKS generation for Launchpad OIDC Issuer.

    Supports multiple active key pairs with Key IDs (kid) for key rotation.
    """

    def __init__(
        self,
        *,
        primary_kid: str = "launchpad-key-1",
        private_key_pem: str | None = None,
        private_key_path: str | Path | None = None,
        rotated_keys_pem: dict[str, str] | None = None,
    ) -> None:
        self.primary_kid = primary_kid
        self._keys: dict[str, KeyPair] = {}

        # 1. Load or generate primary key
        if private_key_pem:
            keypair = self._parse_pem(self.primary_kid, private_key_pem)
        elif private_key_path and Path(private_key_path).is_file():
            pem = Path(private_key_path).read_text(encoding="utf-8")
            keypair = self._parse_pem(self.primary_kid, pem)
        else:
            logger.info("Generating ephemeral 2048-bit RSA key for kid=%s", self.primary_kid)
            keypair = self._generate_keypair(self.primary_kid)

        self._keys[self.primary_kid] = keypair

        # 2. Load any rotated/historical active keys
        if rotated_keys_pem:
            for kid, pem in rotated_keys_pem.items():
                if kid != self.primary_kid:
                    try:
                        self._keys[kid] = self._parse_pem(kid, pem)
                    except Exception as exc:
                        logger.error("Failed to parse rotated RSA key kid=%s: %s", kid, exc)

    @staticmethod
    def _generate_keypair(kid: str, key_size: int = 2048) -> KeyPair:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=key_size)
        public_key = private_key.public_key()
        return KeyPair(kid=kid, private_key=private_key, public_key=public_key)

    @staticmethod
    def _parse_pem(kid: str, pem_data: str | bytes) -> KeyPair:
        if isinstance(pem_data, str):
            pem_bytes = pem_data.encode("utf-8")
        else:
            pem_bytes = pem_data
        private_key = serialization.load_pem_private_key(pem_bytes, password=None)
        if not isinstance(private_key, RSAPrivateKey):
            raise ValueError(f"Key kid={kid} is not an RSA private key")
        return KeyPair(kid=kid, private_key=private_key, public_key=private_key.public_key())

    def add_keypair(self, kid: str, pem_data: str | bytes, set_as_primary: bool = False) -> KeyPair:
        keypair = self._parse_pem(kid, pem_data)
        self._keys[kid] = keypair
        if set_as_primary:
            self.primary_kid = kid
        return keypair

    def get_keypair(self, kid: str | None = None) -> KeyPair:
        target_kid = kid or self.primary_kid
        if target_kid not in self._keys:
            raise KeyError(f"RSA key kid='{target_kid}' not found in OidcKeyManager")
        return self._keys[target_kid]

    def get_jwks(self) -> dict[str, list[dict[str, str]]]:
        """Return full JSON Web Key Set dictionary containing active public RSA keys."""
        return {"keys": [kp.to_jwk() for kp in self._keys.values()]}

    def get_jwks_json(self) -> str:
        """Return JWKS serialized as formatted JSON string."""
        return json.dumps(self.get_jwks(), indent=2)


_global_key_manager: OidcKeyManager | None = None


def get_key_manager(
    *,
    primary_kid: str = "launchpad-key-1",
    private_key_pem: str | None = None,
    private_key_path: str | Path | None = None,
) -> OidcKeyManager:
    global _global_key_manager
    if _global_key_manager is None:
        _global_key_manager = OidcKeyManager(
            primary_kid=primary_kid,
            private_key_pem=private_key_pem,
            private_key_path=private_key_path,
        )
    return _global_key_manager


def reset_key_manager() -> None:
    """Reset singleton instance (used in tests)."""
    global _global_key_manager
    _global_key_manager = None
