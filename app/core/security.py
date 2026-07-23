import secrets

from cryptography.fernet import Fernet
from fastapi import Header, HTTPException

from app.core.config import settings

_fernet = Fernet(settings.token_encryption_key.encode())


def encrypt_token(plaintext: str) -> bytes:
    return _fernet.encrypt(plaintext.encode())


def decrypt_token(ciphertext: bytes) -> str:
    # Decrypt at point of use only - never hold plaintext in module-level state (DATA-1).
    return _fernet.decrypt(ciphertext).decode()


async def require_admin_token(x_admin_token: str = Header(default="")) -> None:
    if not secrets.compare_digest(x_admin_token, settings.admin_token):
        raise HTTPException(status_code=403, detail="Forbidden")
