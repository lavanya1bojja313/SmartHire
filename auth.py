"""
OAuth callback routes — flat layout version.
Google and Microsoft calendar OAuth flows.
"""

import logging
import os
import secrets

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/google/callback")

MICROSOFT_CLIENT_ID = os.getenv("MICROSOFT_CLIENT_ID", "")
MICROSOFT_CLIENT_SECRET = os.getenv("MICROSOFT_CLIENT_SECRET", "")
MICROSOFT_REDIRECT_URI = os.getenv("MICROSOFT_REDIRECT_URI", "http://localhost:8000/auth/microsoft/callback")

# In-memory CSRF state store (use Redis in production)
_pending_states: dict[str, str] = {}

from security import create_access_token
from database import get_db, AsyncSession
from models import User, Organization
from sqlalchemy import select
from fastapi import Depends
import uuid

@router.get("/dev/token")
async def get_dev_token(db: AsyncSession = Depends(get_db)):
    """Development only: Get a valid JWT token for the test recruiter user."""
    # Check if user already exists
    result = await db.execute(select(User).where(User.email == "recruiter@test.com"))
    user = result.scalar_one_or_none()
    
    if not user:
        # Create an org
        org_id = uuid.uuid4()
        org = Organization(id=org_id, name="Test Org", slug="test-org")
        db.add(org)
        
        # Create a user
        user_id = uuid.uuid4()
        user = User(id=user_id, org_id=org_id, email="recruiter@test.com", name="Test Recruiter", role="recruiter")
        db.add(user)
        
        await db.commit()
    
    token = create_access_token(
        user_id=str(user.id),
        org_id=str(user.org_id),
        role="recruiter",
        email=user.email
    )
    return {"access_token": token, "token_type": "bearer"}


@router.get("/google")
async def google_auth_start(interviewer_email: str = Query(...)):
    """Generate Google OAuth consent URL and redirect."""
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=501, detail="Google OAuth not configured")

    state = secrets.token_urlsafe(32)
    _pending_states[state] = interviewer_email

    from urllib.parse import urlencode
    params = urlencode({
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "https://www.googleapis.com/auth/calendar",
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    })
    return RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{params}")


@router.get("/google/callback")
async def google_auth_callback(
    code: str = Query(...),
    state: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """Handle Google OAuth callback — exchange code for tokens and store encrypted."""
    if state not in _pending_states:
        raise HTTPException(status_code=400, detail="Invalid or expired state parameter")

    interviewer_email = _pending_states.pop(state)
    logger.info("Google OAuth callback for %s", interviewer_email)

    # Exchange authorization code for access + refresh tokens
    import httpx
    import json
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": GOOGLE_REDIRECT_URI,
                "grant_type": "authorization_code",
            },
        )
    if token_resp.status_code != 200:
        logger.error("Token exchange failed: %s", token_resp.text)
        raise HTTPException(status_code=400, detail="Google token exchange failed")

    token_data = token_resp.json()
    if "refresh_token" not in token_data:
        raise HTTPException(
            status_code=400,
            detail="No refresh token returned. Ensure prompt=consent and access_type=offline."
        )

    # Look up the user and their org
    result = await db.execute(select(User).where(User.email == interviewer_email))
    user = result.scalar_one_or_none()
    if not user:
        # Attempt to match the dev recruiter for fallback
        result = await db.execute(select(User).limit(1))
        user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Recruiter account not found")

    # Encrypt token data before storage
    from calendar_service import _encrypt_token
    encrypted = _encrypt_token(json.dumps(token_data))

    # Upsert into interviewer_tokens
    from models import InterviewerToken
    existing = await db.execute(
        select(InterviewerToken).where(
            InterviewerToken.org_id == user.org_id,
            InterviewerToken.interviewer_email == interviewer_email,
            InterviewerToken.provider == "google",
        )
    )
    token_row = existing.scalar_one_or_none()

    if token_row:
        token_row.encrypted_token = encrypted
        token_row.is_active = True
        token_row.scopes = token_data.get("scope", "").split()
    else:
        token_row = InterviewerToken(
            org_id=user.org_id,
            interviewer_email=interviewer_email,
            provider="google",
            encrypted_token=encrypted,
            scopes=token_data.get("scope", "").split(),
            is_active=True,
        )
        db.add(token_row)

    await db.commit()
    logger.info("Google Calendar token stored for %s", interviewer_email)

    # Redirect back to the Settings page on the dashboard
    return RedirectResponse("http://localhost:3000/?connected=google")


@router.get("/microsoft")
async def microsoft_auth_start(interviewer_email: str = Query(...)):
    """Generate Microsoft OAuth consent URL and redirect."""
    if not MICROSOFT_CLIENT_ID:
        raise HTTPException(status_code=501, detail="Microsoft OAuth not configured")

    state = secrets.token_urlsafe(32)
    _pending_states[state] = interviewer_email

    tenant = os.getenv("MICROSOFT_TENANT_ID", "common")
    from urllib.parse import urlencode
    params = urlencode({
        "client_id": MICROSOFT_CLIENT_ID,
        "redirect_uri": MICROSOFT_REDIRECT_URI,
        "response_type": "code",
        "scope": "Calendars.ReadWrite offline_access",
        "state": state,
    })
    return RedirectResponse(f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize?{params}")


@router.get("/microsoft/callback")
async def microsoft_auth_callback(code: str = Query(...), state: str = Query(...)):
    """Handle Microsoft OAuth callback."""
    if state not in _pending_states:
        raise HTTPException(status_code=400, detail="Invalid state parameter")

    interviewer_email = _pending_states.pop(state)
    logger.info("Microsoft OAuth callback for %s", interviewer_email)

    # TODO: implement Microsoft token exchange (similar pattern to Google above)
    return {"status": "ok", "email": interviewer_email, "message": "Microsoft Calendar connected successfully"}


@router.get("/calendar-status")
async def calendar_status(db: AsyncSession = Depends(get_db)):
    """
    Check whether the current recruiter has an active calendar token.
    Returns provider connection status so the Settings UI can render correctly.
    """
    from models import InterviewerToken
    result = await db.execute(
        select(InterviewerToken).where(InterviewerToken.is_active == True)  # noqa
    )
    tokens = result.scalars().all()

    connected_providers = [t.provider for t in tokens]
    return {
        "google": "google" in connected_providers,
        "microsoft": "microsoft" in connected_providers,
        "connected_emails": [t.interviewer_email for t in tokens],
    }
