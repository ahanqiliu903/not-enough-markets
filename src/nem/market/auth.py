"""Kalshi API request signing.

Each authenticated request carries three headers. The signed message is
`timestamp_ms + METHOD + path`, where path includes `/trade-api/v2` and excludes the query
string. Ed25519 keys sign the message directly; RSA keys use PSS with SHA-256, MGF1-SHA256
and a 32-byte salt. Verify against current Kalshi docs; these details drift.
"""

import base64
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

SigningKey = Ed25519PrivateKey | RSAPrivateKey  # gitleaks:allow (type alias, not a secret)


class Signer:
    def __init__(self, key_id: str, signing_key: SigningKey) -> None:
        self.key_id = key_id
        self._key = signing_key

    @classmethod
    def from_pem_file(cls, key_id: str, path: str | Path) -> "Signer":
        key = serialization.load_pem_private_key(Path(path).read_bytes(), password=None)
        if not isinstance(key, SigningKey):
            raise ValueError(f"{path}: expected an Ed25519 or RSA private key")
        return cls(key_id, key)

    def sign(self, message: bytes) -> bytes:
        if isinstance(self._key, Ed25519PrivateKey):
            return self._key.sign(message)
        return self._key.sign(
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )

    def headers(self, method: str, path: str, timestamp_ms: int) -> dict[str, str]:
        path = path.split("?", 1)[0]
        message = f"{timestamp_ms}{method.upper()}{path}".encode()
        return {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-TIMESTAMP": str(timestamp_ms),
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(self.sign(message)).decode(),
        }

    def __repr__(self) -> str:
        return "Signer(key_id=***)"
