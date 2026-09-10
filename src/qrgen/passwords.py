"""Password hashing for the admin account (stdlib PBKDF2)."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets

ITERATIONS = 200_000
ALGORITHM = "pbkdf2_sha256"


def hash_password(password: str, iterations: int = ITERATIONS) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), iterations)
    return f"{ALGORITHM}${iterations}${salt}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, digest = encoded.split("$")
        if algorithm != ALGORITHM:
            return False
        expected = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt), int(iterations)
        ).hex()
        return hmac.compare_digest(expected, digest)
    except (ValueError, TypeError):
        return False


def admin_configured() -> bool:
    return bool(os.environ.get("ADMIN_USERNAME") and os.environ.get("ADMIN_PASSWORD_HASH"))
