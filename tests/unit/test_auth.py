import base64
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa

from nem.market.auth import Signer

PATH = "/trade-api/v2/portfolio/orders"


def decode(headers: dict[str, str]) -> bytes:
    return base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"])


def test_ed25519_signature_verifies() -> None:
    key = ed25519.Ed25519PrivateKey.generate()
    h = Signer("kid", key).headers("get", PATH + "?limit=5", 1700000000000)
    assert h["KALSHI-ACCESS-KEY"] == "kid"
    assert h["KALSHI-ACCESS-TIMESTAMP"] == "1700000000000"
    # method uppercased, query string excluded
    key.public_key().verify(decode(h), f"1700000000000GET{PATH}".encode())


def test_rsa_pss_signature_verifies() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    h = Signer("kid", key).headers("POST", PATH, 1700000000000)
    key.public_key().verify(
        decode(h),
        f"1700000000000POST{PATH}".encode(),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
        hashes.SHA256(),
    )


def test_from_pem_file(tmp_path: Path) -> None:
    key = ed25519.Ed25519PrivateKey.generate()
    pem = tmp_path / "k.pem"
    pem.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    h = Signer.from_pem_file("kid", pem).headers("GET", PATH, 1)
    key.public_key().verify(decode(h), f"1GET{PATH}".encode())


def test_from_pem_file_rejects_unsupported_key(tmp_path: Path) -> None:
    from cryptography.hazmat.primitives.asymmetric import ec

    pem = tmp_path / "k.pem"
    pem.write_bytes(
        ec.generate_private_key(ec.SECP256R1()).private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    with pytest.raises(ValueError, match="Ed25519 or RSA"):
        Signer.from_pem_file("kid", pem)


def test_repr_hides_key_id() -> None:
    assert "secret-id" not in repr(Signer("secret-id", ed25519.Ed25519PrivateKey.generate()))
