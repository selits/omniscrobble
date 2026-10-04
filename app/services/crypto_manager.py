"""Cryptographic utilities for at-rest token and configuration encryption in Omniscrobble.

Provides AES-256-GCM authenticated symmetric encryption with PBKDF2-HMAC-SHA256
key derivation. Transparently encrypts tokens and state files when CONFIG_ENCRYPTION_KEY
is configured, while maintaining backward-compatible plain JSON read/write fallbacks.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

from app.config import Config
from app.services.atomic_writer import atomic_write_json

logger = logging.getLogger("omniscrobble.crypto")

# Default PBKDF2 iteration count
PBKDF2_ITERATIONS = 100_000


def derive_key(passphrase: str, salt: bytes, iterations: int = PBKDF2_ITERATIONS) -> bytes:
    """Derive a 256-bit (32-byte) symmetric key from a passphrase and salt using PBKDF2-HMAC-SHA256."""
    if not passphrase:
        raise ValueError("Encryption passphrase cannot be empty")
    return hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, iterations, dklen=32)


def encrypt_bytes(raw_bytes: bytes, passphrase: str) -> dict[str, Any]:
    """Encrypt arbitrary bytes with AES-256-GCM and return a structured dictionary."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    salt = os.urandom(16)
    key = derive_key(passphrase, salt)
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, raw_bytes, None)

    return {
        "encrypted": True,
        "algo": "aes-256-gcm",
        "kdf": "pbkdf2_sha256",
        "iterations": PBKDF2_ITERATIONS,
        "salt": salt.hex(),
        "nonce": nonce.hex(),
        "ciphertext": ciphertext.hex(),
    }


def decrypt_bytes(data: dict[str, Any], passphrase: str) -> bytes:
    """Decrypt an AES-256-GCM structured dictionary back to raw bytes."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not is_encrypted_payload(data):
        raise ValueError("Data is not a valid encrypted payload dictionary")

    salt = bytes.fromhex(data["salt"])
    nonce = bytes.fromhex(data["nonce"])
    ciphertext = bytes.fromhex(data["ciphertext"])
    iterations = int(data.get("iterations", PBKDF2_ITERATIONS))

    key = derive_key(passphrase, salt, iterations=iterations)
    aesgcm = AESGCM(key)
    try:
        return aesgcm.decrypt(nonce, ciphertext, None)
    except Exception as e:
        logger.error("Failed to decrypt payload: invalid key or corrupted ciphertext: %s", e)
        raise ValueError("Failed to decrypt payload: invalid passphrase or corrupted data") from e


def encrypt_payload(data: Any, passphrase: str) -> dict[str, Any]:
    """Serialize data to JSON and encrypt with AES-256-GCM."""
    raw_str = json.dumps(data)
    return encrypt_bytes(raw_str.encode("utf-8"), passphrase)


def decrypt_payload(data: dict[str, Any], passphrase: str) -> Any:
    """Decrypt an AES-256-GCM dictionary and parse as JSON."""
    decrypted_bytes = decrypt_bytes(data, passphrase)
    return json.loads(decrypted_bytes.decode("utf-8"))


def is_encrypted_payload(data: Any) -> bool:
    """Check if a data structure represents an AES-256-GCM encrypted envelope."""
    return (
        isinstance(data, dict)
        and data.get("encrypted") is True
        and data.get("algo") == "aes-256-gcm"
        and "salt" in data
        and "nonce" in data
        and "ciphertext" in data
    )


class CryptoManager:
    """Manager for transparent at-rest encryption and decryption of configuration files."""

    def __init__(self, default_key: Optional[str] = None):
        self._default_key = default_key

    @property
    def effective_key(self) -> str:
        """Resolve effective encryption key from instance or global config."""
        return self._default_key or getattr(Config, "CONFIG_ENCRYPTION_KEY", "") or ""

    def is_encryption_enabled(self) -> bool:
        """Check whether at-rest encryption is globally active."""
        return bool(self.effective_key)

    def read_secure_json(self, file_path: Path, passphrase: Optional[str] = None) -> Any:
        """Read and parse a JSON file, automatically decrypting if encrypted."""
        target = Path(file_path)
        if not target.exists():
            return None

        with open(target, "r", encoding="utf-8") as f:
            raw_content = json.load(f)

        if is_encrypted_payload(raw_content):
            key = passphrase or self.effective_key
            if not key:
                logger.error(
                    "Encrypted file detected at %s but CONFIG_ENCRYPTION_KEY is not configured", target
                )
                raise ValueError(
                    f"File '{target.name}' is encrypted at rest. Set CONFIG_ENCRYPTION_KEY to decrypt."
                )
            return decrypt_payload(raw_content, key)

        return raw_content

    def write_secure_json(
        self,
        file_path: Path,
        data: Any,
        passphrase: Optional[str] = None,
        indent: int = 2,
    ) -> None:
        """Atomically persist data to disk, encrypting if a key is configured."""
        key = passphrase or self.effective_key
        if key:
            encrypted_envelope = encrypt_payload(data, key)
            atomic_write_json(file_path, encrypted_envelope, indent=indent)
        else:
            atomic_write_json(file_path, data, indent=indent)


# Global singleton instance
crypto_mgr = CryptoManager()
