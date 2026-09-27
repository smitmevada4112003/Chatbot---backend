# Alias / re-export canonical authentication functions from auth.py
from auth import (
    hash_password,
    verify_password,
    create_access_token,
    decode_access_token,
    get_current_user,
    get_optional_current_user,
    require_admin,
    JWT_SECRET_KEY,
    JWT_ALGORITHM,
    JWT_EXPIRE_MINUTES
)

__all__ = [
    "hash_password",
    "verify_password",
    "create_access_token",
    "decode_access_token",
    "get_current_user",
    "get_optional_current_user",
    "require_admin",
    "JWT_SECRET_KEY",
    "JWT_ALGORITHM",
    "JWT_EXPIRE_MINUTES",
]
