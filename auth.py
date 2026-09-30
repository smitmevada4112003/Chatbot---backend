import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from passlib.context import CryptContext

# Load environment configuration
load_dotenv()

# ==============================================================================
# CONFIGURATION
# ==============================================================================
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY") or os.getenv("JWT_SECRET", "super_secret_jwt_key_orderbot_2026_enterprise_secure")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES") or os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))

# Password hashing context using passlib with bcrypt
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# HTTP Bearer token extractor
security = HTTPBearer(auto_error=False)


# ==============================================================================
# PASSWORD HASHING & VERIFICATION
# ==============================================================================

def hash_password(password: str) -> str:
    """Hash a plaintext password using passlib (bcrypt)."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against its hashed bcrypt version."""
    if not plain_password or not hashed_password:
        return False
    try:
        if pwd_context.verify(plain_password, hashed_password):
            return True
    except Exception:
        pass

    try:
        import bcrypt
        pw_bytes = plain_password.encode("utf-8")
        h_bytes = hashed_password.encode("utf-8") if isinstance(hashed_password, str) else hashed_password
        return bcrypt.checkpw(pw_bytes, h_bytes)
    except Exception:
        return False


# ==============================================================================
# JWT TOKEN GENERATION & DECODING
# ==============================================================================

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Create a signed JWT token with a configurable expiration time.
    """
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta if expires_delta else timedelta(minutes=JWT_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    """
    Decode and validate a signed JWT token.
    Raises 401 HTTPException if the token is invalid or expired.
    """
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired authentication token."
        )


# ==============================================================================
# FASTAPI DEPENDENCIES: ROLE-BASED ACCESS CONTROL
# ==============================================================================

def get_current_user(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)) -> dict:
    """
    FastAPI dependency: extracts and verifies the JWT from the Authorization header (Bearer token).
    Returns the user's email, role, and details.
    Raises a 401 error if the token is missing, invalid, or expired.
    """
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials were not provided."
        )

    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        email: Optional[str] = payload.get("email") or payload.get("sub")
        role: Optional[str] = payload.get("role", "customer")

        if not email:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token payload: missing email/subject."
            )

        user_id = payload.get("id")
        name = payload.get("name")

        # Fallback: if user_id is missing from token payload, fetch from users table
        if not user_id and email:
            try:
                from databases import get_db_connection
                _db = get_db_connection()
                _c = _db.cursor()
                _c.execute("SELECT id, name FROM users WHERE LOWER(email) = %s", (email.lower(),))
                _u = _c.fetchone()
                if _u:
                    user_id = _u[0]
                    if not name and _u[1]:
                        name = _u[1]
                _c.close()
                _db.close()
            except Exception:
                pass

        return {
            "id": user_id,
            "email": email,
            "role": role,
            "name": name
        }
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials or token has expired."
        )


def get_optional_current_user(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)) -> Optional[dict]:
    """
    FastAPI dependency: extracts and verifies the JWT from the Authorization header if provided.
    Returns user dict if valid token, or None if no token or invalid token. Does not raise 401.
    """
    if not credentials or not credentials.credentials:
        return None
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        email: Optional[str] = payload.get("email") or payload.get("sub")
        role: Optional[str] = payload.get("role", "customer")
        if not email:
            return None

        user_id = payload.get("id")
        name = payload.get("name")
        if not user_id and email:
            try:
                from databases import get_db_connection
                _db = get_db_connection()
                _c = _db.cursor()
                _c.execute("SELECT id, name FROM users WHERE LOWER(email) = %s", (email.lower(),))
                _u = _c.fetchone()
                if _u:
                    user_id = _u[0]
                    if not name and _u[1]:
                        name = _u[1]
                _c.close()
                _db.close()
            except Exception:
                pass

        return {
            "id": user_id,
            "email": email,
            "role": role,
            "name": name
        }
    except Exception:
        return None


def require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    """
    FastAPI dependency: uses get_current_user and additionally checks
    that the user's role is 'admin', raising a 403 error otherwise.
    """
    user_role = (current_user.get("role") or "").strip().lower()
    if user_role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: Administrator privileges required."
        )
    return current_user
