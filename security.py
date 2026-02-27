"""
Security helpers: JWT validation, org-scoped RBAC, and permission checks.

Two tiers of access:
  - RECRUITER  : can view and manage their own org's requests
  - ADMIN      : full access including org settings and all requests

Every protected route depends on get_current_user(), which verifies the
Bearer token and loads the caller's org context.  Routes that need admin
access additionally depend on require_admin().
"""

import os
import logging
from datetime import datetime, timezone
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)

SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-in-production")
ALGORITHM = "HS256"

bearer_scheme = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Schemas (lightweight dataclasses — no ORM import needed here)
# ---------------------------------------------------------------------------

from dataclasses import dataclass, field


@dataclass
class TokenPayload:
    """Parsed, validated JWT payload."""
    user_id: str
    org_id: str
    role: str          # "recruiter" | "admin"
    email: str
    exp: int


@dataclass
class CurrentUser:
    """Dependency injection object attached to every protected request."""
    user_id: str
    org_id: str
    role: str
    email: str


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def decode_token(token: str) -> TokenPayload:
    """
    Decode and validate a JWT. Raises HTTPException on any failure so
    callers don't need to handle jwt exceptions directly.
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError as exc:
        logger.warning("Invalid JWT: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Validate required claims are present
    required = {"user_id", "org_id", "role", "email", "exp"}
    missing = required - set(payload.keys())
    if missing:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token missing required claims: {missing}",
        )

    return TokenPayload(**{k: payload[k] for k in required})


def create_access_token(
    user_id: str,
    org_id: str,
    role: str,
    email: str,
    expires_in_seconds: int = 3600,
) -> str:
    """
    Mint a signed JWT. Used in tests and the auth endpoint.
    In production this would be called by the auth service after
    verifying SSO credentials.
    """
    now = int(datetime.now(timezone.utc).timestamp())
    payload = {
        "user_id": user_id,
        "org_id": org_id,
        "role": role,
        "email": email,
        "exp": now + expires_in_seconds,
        "iat": now,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------

async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> CurrentUser:
    """
    FastAPI dependency — verifies Bearer token and returns the caller's identity.

    Raises 401 if no token is supplied or the token is invalid/expired.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_token(credentials.credentials)
    return CurrentUser(
        user_id=payload.user_id,
        org_id=payload.org_id,
        role=payload.role,
        email=payload.email,
    )


async def require_admin(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """
    FastAPI dependency — additionally requires the ADMIN role.
    Use on routes that modify org settings or access cross-org data.
    """
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user


def assert_same_org(current_user: CurrentUser, resource_org_id: str) -> None:
    """
    Inline guard — raise 403 if a recruiter tries to access another org's data.
    Admins bypass this check.
    """
    if current_user.role == "admin":
        return
    if current_user.org_id != resource_org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: resource belongs to a different organization",
        )
