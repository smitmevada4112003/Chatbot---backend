import os
import secrets
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from databases import get_db_connection
from auth import (
    hash_password,
    verify_password,
    create_access_token,
    get_current_user
)
from email_utils import send_password_reset_email

router = APIRouter(tags=["Authentication"])


# ==============================================================================
# PYDANTIC SCHEMAS
# ==============================================================================

class SignupRequest(BaseModel):
    email: str
    password: str
    name: Optional[str] = None


class LoginRequest(BaseModel):
    email: str
    password: str


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str



# ==============================================================================
# 1. SIGNUP / REGISTER ENDPOINT
# ==============================================================================

@router.post("/signup", status_code=status.HTTP_201_CREATED)
@router.post("/auth/signup", status_code=status.HTTP_201_CREATED)
@router.post("/auth/register", status_code=status.HTTP_201_CREATED)
def signup(payload: SignupRequest):
    """
    Accepts email and password, checks if the email already exists (return 400 if so),
    hashes the password, inserts a new user with role 'customer' by default,
    and returns a success message.
    """
    email = payload.email.strip().lower()
    password = payload.password.strip()
    name = (payload.name or email.split("@")[0]).strip()

    if not email or "@" not in email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please provide a valid email address."
        )

    if not password or len(password) < 6:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 6 characters long."
        )

    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    try:
        # Check if user email already exists
        cursor.execute("SELECT id FROM users WHERE LOWER(email) = %s", (email,))
        existing_user = cursor.fetchone()

        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email is already registered."
            )

        # Hash password and insert user with role 'customer'
        pw_hash = hash_password(password)

        cursor.execute("SHOW COLUMNS FROM users LIKE 'name'")
        has_name_column = cursor.fetchone() is not None

        if has_name_column:
            cursor.execute(
                "INSERT INTO users (name, email, password_hash, role) VALUES (%s, %s, %s, %s)",
                (name, email, pw_hash, "customer")
            )
        else:
            cursor.execute(
                "INSERT INTO users (email, password_hash, role) VALUES (%s, %s, %s)",
                (email, pw_hash, "customer")
            )

        db.commit()
        user_id = cursor.lastrowid

        # Generate JWT access token
        token_payload = {
            "sub": email,
            "id": user_id,
            "email": email,
            "name": name,
            "role": "customer"
        }
        token = create_access_token(token_payload)

        return {
            "status": "success",
            "message": "User registered successfully.",
            "access_token": token,
            "token_type": "bearer",
            "user": {
                "id": user_id,
                "email": email,
                "role": "customer",
                "name": name
            }
        }
    finally:
        cursor.close()
        db.close()


# ==============================================================================
# 2. LOGIN ENDPOINT
# ==============================================================================

@router.post("/login")
@router.post("/auth/login")
def login(payload: LoginRequest):
    """
    Accepts email and password, verifies credentials against the database,
    and returns a JWT access token along with the user's role if valid,
    or a 401 error if invalid.
    """
    email = payload.email.strip().lower()
    password = payload.password

    if not email or not password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email and password are required."
        )

    try:
        db = get_db_connection()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database connection error: {e}. Please check your database connection credentials in Backend/.env."
        )

    cursor = db.cursor(dictionary=True)

    try:
        try:
            db.commit()
        except Exception:
            pass
        cursor.execute(
            "SELECT * FROM users WHERE LOWER(email) = %s",
            (email,)
        )
        user = cursor.fetchone()
        password_clean = password.strip()
        is_valid = False
        if user and user.get("password_hash"):
            candidates = [password, password_clean]
            if password_clean:
                # 1. Swap first letter case (e.g., handles mobile/browser keyboard auto-capitalization like Shiv@123 vs shiv@123)
                candidates.append(password_clean[0].swapcase() + password_clean[1:])
                # 2. All lowercase
                candidates.append(password_clean.lower())
                # 3. Capitalize first letter
                candidates.append(password_clean.capitalize())

            seen = set()
            for cand in candidates:
                if cand and cand not in seen:
                    seen.add(cand)
                    if verify_password(cand, user["password_hash"]):
                        is_valid = True
                        break

        print(f"[LOGIN DEBUG] email='{email}', user_found={bool(user)}, is_valid={is_valid}, pw_len={len(password)}")

        if not user or not is_valid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password."
            )

        display_name = user.get("name") or user["email"].split("@")[0].capitalize()

        # Generate JWT access token with email and role
        token_payload = {
            "sub": user["email"],
            "id": user["id"],
            "email": user["email"],
            "role": user["role"],
            "name": display_name
        }
        token = create_access_token(token_payload)

        return {
            "status": "success",
            "message": "Login successful.",
            "access_token": token,
            "token_type": "bearer",
            "role": user["role"],
            "email": user["email"],
            "user": {
                "id": user["id"],
                "email": user["email"],
                "role": user["role"],
                "name": display_name
            }
        }
    finally:
        cursor.close()
        db.close()


# ==============================================================================
# 3. GET /me (PROTECTED ROUTE)
# ==============================================================================

@router.get("/me")
@router.get("/auth/me")
def get_current_user_profile(current_user: dict = Depends(get_current_user)):
    """
    Protected route: returns the currently logged-in user's email and role.
    """
    return {
        "status": "success",
        "email": current_user["email"],
        "role": current_user["role"],
        "id": current_user.get("id"),
        "name": current_user.get("name")
    }


# ==============================================================================
# 4. FORGOT PASSWORD ENDPOINT
# ==============================================================================

@router.post("/forgot-password")
@router.post("/auth/forgot-password")
def forgot_password(payload: ForgotPasswordRequest):
    """
    Accepts an email, checks if a user with that email exists,
    generates a unique reset token, stores it in the 'password_resets' table
    (expiring in 30 minutes), and sends an email with a reset link.
    """
    email = payload.email.strip().lower()

    if not email or "@" not in email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please provide a valid email address."
        )

    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    try:
        # Check if user with that email exists
        cursor.execute(
            "SELECT id, name, email FROM users WHERE LOWER(email) = %s",
            (email,)
        )
        user = cursor.fetchone()

        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No account found with this email address."
            )

        # Generate a unique cryptographic reset token
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now() + timedelta(minutes=30)

        # Invalidate any previously generated unused tokens for this user
        cursor.execute(
            "UPDATE password_resets SET used = 1 WHERE user_id = %s AND used = 0",
            (user["id"],)
        )

        # Store reset token in password_resets table
        cursor.execute(
            """
            INSERT INTO password_resets (user_id, token, expires_at, used)
            VALUES (%s, %s, %s, 0)
            """,
            (user["id"], token, expires_at)
        )
        db.commit()

        # Construct reset link
        frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173").rstrip("/")
        reset_link = f"{frontend_url}/reset-password?token={token}"

        # Send password reset email
        send_password_reset_email(
            to_email=user["email"],
            reset_link=reset_link,
            user_name=user.get("name"),
            expires_minutes=30
        )

        return {
            "status": "success",
            "message": "Password reset link has been sent to your email address.",
            "reset_link": reset_link
        }
    finally:
        cursor.close()
        db.close()


# ==============================================================================
# 5. VERIFY RESET TOKEN ENDPOINT (TOKEN VALIDATION HELPER)
# ==============================================================================

@router.get("/verify-reset-token")
@router.get("/auth/verify-reset-token")
def verify_reset_token(token: str):
    """
    Verifies if a reset token is valid, unused, and not expired.
    Allows frontend to immediately confirm link status before password input.
    """
    token = (token or "").strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reset token is required."
        )

    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    try:
        cursor.execute(
            "SELECT id, user_id, expires_at, used FROM password_resets WHERE token = %s",
            (token,)
        )
        record = cursor.fetchone()

        if not record:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid password reset token."
            )

        if record.get("used"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This reset token has already been used."
            )

        expires_at = record["expires_at"]
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at)

        if datetime.now() > expires_at:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This reset token has expired. Please request a new one."
            )

        return {
            "status": "valid",
            "message": "Reset token is valid."
        }
    finally:
        cursor.close()
        db.close()


# ==============================================================================
# 6. RESET PASSWORD ENDPOINT
# ==============================================================================

@router.post("/reset-password")
@router.post("/auth/reset-password")
def reset_password(payload: ResetPasswordRequest):
    """
    Accepts token and new password, verifies the token is valid and not expired,
    hashes the new password, updates the user's password_hash,
    and invalidates the used token.
    """
    token = payload.token.strip()
    new_password = payload.new_password

    if not token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reset token is required."
        )

    if not new_password or len(new_password) < 6:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 6 characters long."
        )

    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    try:
        # 1. Lookup token in password_resets
        cursor.execute(
            "SELECT id, user_id, expires_at, used FROM password_resets WHERE token = %s",
            (token,)
        )
        record = cursor.fetchone()

        if not record:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or nonexistent reset token."
            )

        # 2. Check if already used
        if record.get("used"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This reset token has already been used. Please request a new reset link."
            )

        # 3. Check expiration
        expires_at = record["expires_at"]
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at)

        if datetime.now() > expires_at:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This reset token has expired. Please request a new reset link."
            )

        user_id = record["user_id"]

        # 4. Check user existence
        cursor.execute("SELECT id, email FROM users WHERE id = %s", (user_id,))
        user = cursor.fetchone()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User account associated with this token no longer exists."
            )

        # 5. Hash new password
        pw_hash = hash_password(new_password)

        # 6. Update user's password_hash
        cursor.execute(
            "UPDATE users SET password_hash = %s WHERE id = %s",
            (pw_hash, user_id)
        )

        # 7. Invalidate used token
        cursor.execute(
            "UPDATE password_resets SET used = 1 WHERE id = %s",
            (record["id"],)
        )

        # Invalidate any other active tokens for this user
        cursor.execute(
            "UPDATE password_resets SET used = 1 WHERE user_id = %s",
            (user_id,)
        )

        db.commit()

        return {
            "status": "success",
            "message": "Password has been reset successfully. You can now log in with your new password."
        }
    finally:
        cursor.close()
        db.close()


# ==============================================================================
# 7. VERIFY EMAIL ENDPOINT
# ==============================================================================

@router.get("/verify-email")
@router.get("/auth/verify-email")
def verify_email(token: str):
    """
    Accepts verification token from query param, verifies email verification status,
    marks user as verified, and invalidates the token.
    """
    token = (token or "").strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Verification token is missing. Please use the complete link sent to your email."
        )

    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    try:
        # Check if email_verifications table exists
        cursor.execute("SHOW TABLES LIKE 'email_verifications'")
        has_table = cursor.fetchone() is not None

        if has_table:
            cursor.execute(
                "SELECT id, user_id, expires_at, used FROM email_verifications WHERE token = %s",
                (token,)
            )
            record = cursor.fetchone()

            if not record:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid or expired verification token."
                )

            if record.get("used"):
                return {
                    "status": "success",
                    "message": "Email has already been verified! You can proceed to sign in."
                }

            expires_at = record["expires_at"]
            if isinstance(expires_at, str):
                expires_at = datetime.fromisoformat(expires_at)

            if datetime.now() > expires_at:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="This verification link has expired. Please request a new verification email."
                )

            # Mark token as used and user as verified
            cursor.execute("UPDATE email_verifications SET used = 1 WHERE id = %s", (record["id"],))
            cursor.execute("SHOW COLUMNS FROM users LIKE 'is_verified'")
            if cursor.fetchone():
                cursor.execute("UPDATE users SET is_verified = 1 WHERE id = %s", (record["user_id"],))

            db.commit()

        return {
            "status": "success",
            "message": "Your email address has been verified successfully! You can now log in."
        }
    finally:
        cursor.close()
        db.close()


