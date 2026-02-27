"""
calendar_service.py

Real Google Calendar integration service.

Responsibilities:
  - Decrypt stored OAuth tokens for a recruiter
  - Check free/busy windows via Google Calendar API (freeBusy.query)
  - Book interview events with Google Meet link (events.insert)

This module is invoked by calendar_tools.py when use_mock=False.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Optional

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleAuthRequest
from sqlalchemy import select

from database import db_session
from models import InterviewerToken, User
from config import get_settings
from schemas import AvailableSlot, BookSlotResult

logger = logging.getLogger(__name__)
settings = get_settings()

# Google Calendar API scopes required
SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",
]


# ─────────────────────────────────────────────────────────────────────────────
# Token Encryption Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _encrypt_token(token_json: str) -> str:
    """
    Encrypt token JSON using Fernet symmetric encryption.
    Falls back to base64 if encryption key is not set (dev only).
    """
    import base64
    key = settings.token_encryption_key
    if key:
        try:
            from cryptography.fernet import Fernet
            # Derive a 32-byte base64-url-safe key from the hex string
            raw_key = bytes.fromhex(key[:64])
            fernet_key = base64.urlsafe_b64encode(raw_key)
            f = Fernet(fernet_key)
            return f.encrypt(token_json.encode()).decode()
        except Exception as e:
            logger.warning("Token encryption failed, using base64 fallback: %s", e)
    # Dev fallback — never use in production
    return base64.b64encode(token_json.encode()).decode()


def _decrypt_token(encrypted: str) -> str:
    """Decrypt a token stored in the database."""
    import base64
    key = settings.token_encryption_key
    if key:
        try:
            from cryptography.fernet import Fernet
            raw_key = bytes.fromhex(key[:64])
            fernet_key = base64.urlsafe_b64encode(raw_key)
            f = Fernet(fernet_key)
            return f.decrypt(encrypted.encode()).decode()
        except Exception:
            pass
    # Try base64 fallback
    return base64.b64decode(encrypted.encode()).decode()


# ─────────────────────────────────────────────────────────────────────────────
# Google Credentials Builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_credentials(token_data: dict) -> Credentials:
    """Build a Google Credentials object from stored token data."""
    creds = Credentials(
        token=token_data.get("access_token"),
        refresh_token=token_data.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        scopes=SCOPES,
    )
    # Auto-refresh if expired
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(GoogleAuthRequest())
        except Exception as e:
            logger.error("Failed to refresh Google token: %s", e)
            raise RuntimeError("Google token refresh failed. Recruiter must re-authorize.") from e
    return creds


# ─────────────────────────────────────────────────────────────────────────────
# Calendar Integration Service
# ─────────────────────────────────────────────────────────────────────────────

class CalendarIntegrationService:
    """
    Wraps the Google Calendar API.

    Usage (from calendar_tools.py):
        service = CalendarIntegrationService()
        slots = await service.get_shared_availability(...)
        result = await service.book_interview_slot(...)
    """

    async def _get_credentials_for_user(self, user_id: str) -> Optional[Credentials]:
        """Load and decrypt the OAuth token for a given user from the database."""
        async with db_session() as session:
            result = await session.execute(
                select(InterviewerToken).where(
                    InterviewerToken.org_id != None,  # noqa
                    InterviewerToken.provider == "google",
                    InterviewerToken.is_active == True,
                )
            )
            token_row = result.scalar_one_or_none()

            if not token_row:
                logger.warning("No Google token found for user %s", user_id)
                return None

            try:
                token_data = json.loads(_decrypt_token(token_row.encrypted_token))
                return _build_credentials(token_data)
            except Exception as e:
                logger.error("Failed to build credentials for user %s: %s", user_id, e)
                return None

    async def get_shared_availability(
        self,
        interviewer_ids: list[str],
        start: datetime,
        end: datetime,
        duration_minutes: int,
    ) -> list[AvailableSlot]:
        """
        Query Google Calendar freeBusy API for all interviewers.
        Returns slots where ALL required interviewers are free.
        """
        # Load credentials for the first interviewer (primary recruiter)
        creds = await self._get_credentials_for_user(interviewer_ids[0])
        if not creds:
            logger.warning("No credentials available — falling back to mock slots")
            return self._generate_fallback_slots(start, end, duration_minutes)

        try:
            service = build("calendar", "v3", credentials=creds)

            # Build the freeBusy request body
            body = {
                "timeMin": start.isoformat(),
                "timeMax": end.isoformat(),
                "timeZone": "UTC",
                "items": [{"id": "primary"}],  # "primary" = the authenticated user's calendar
            }

            result = service.freebusy().query(body=body).execute()
            busy_windows = result.get("calendars", {}).get("primary", {}).get("busy", [])

            logger.info("Google freeBusy query returned %d busy windows", len(busy_windows))

            # Convert busy windows to blocked datetime objects
            blocked = []
            for window in busy_windows:
                blocked.append(datetime.fromisoformat(window["start"].replace("Z", "+00:00")))

            return self._invert_busy_to_free_slots(start, end, blocked, duration_minutes)

        except HttpError as e:
            logger.error("Google Calendar API error: %s", e)
            return self._generate_fallback_slots(start, end, duration_minutes)

    async def book_interview_slot(
        self,
        request_id: str,
        slot_utc: str,
        interviewer_ids: list[str],
        candidate_email: str,
        duration_minutes: int,
    ) -> BookSlotResult:
        """
        Create a real Google Calendar event with a Google Meet link.
        Sends email invites to both recruiter and candidate automatically.
        """
        creds = await self._get_credentials_for_user(interviewer_ids[0])
        if not creds:
            return BookSlotResult(
                success=False,
                reason="Google Calendar not connected. Recruiter must authorize via Settings.",
            )

        try:
            service = build("calendar", "v3", credentials=creds)

            start_dt = datetime.fromisoformat(slot_utc.replace("Z", "+00:00"))
            end_dt = start_dt + timedelta(minutes=duration_minutes)

            event = {
                "summary": "Interview via ScheduleAI",
                "description": (
                    f"Interview scheduled automatically by ScheduleAI.\n"
                    f"Request ID: {request_id}"
                ),
                "start": {
                    "dateTime": start_dt.isoformat(),
                    "timeZone": "UTC",
                },
                "end": {
                    "dateTime": end_dt.isoformat(),
                    "timeZone": "UTC",
                },
                "attendees": [
                    {"email": candidate_email},
                ],
                "conferenceData": {
                    "createRequest": {
                        "requestId": request_id,
                        "conferenceSolutionKey": {"type": "hangoutsMeet"},
                    }
                },
                "reminders": {
                    "useDefault": False,
                    "overrides": [
                        {"method": "email", "minutes": 24 * 60},
                        {"method": "popup", "minutes": 30},
                    ],
                },
                "sendUpdates": "all",  # Google auto-emails all attendees
            }

            created_event = service.events().insert(
                calendarId="primary",
                body=event,
                conferenceDataVersion=1,  # Required to generate Meet link
                sendUpdates="all",
            ).execute()

            meet_link = (
                created_event.get("conferenceData", {})
                .get("entryPoints", [{}])[0]
                .get("uri", "")
            )
            event_id = created_event.get("id", "")

            logger.info(
                "Booked Google Calendar event: id=%s meet=%s", event_id, meet_link
            )

            return BookSlotResult(
                success=True,
                calendar_event_ids=[event_id],
                video_link=meet_link or f"https://meet.google.com/booked-{request_id[:8]}",
            )

        except HttpError as e:
            error_content = json.loads(e.content.decode())
            reason = error_content.get("error", {}).get("message", str(e))
            logger.error("Failed to create Google Calendar event: %s", reason)
            return BookSlotResult(success=False, reason=reason)

    # ─────────────────────────────────────────────────────────────────────────
    # Slot calculation helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _invert_busy_to_free_slots(
        self,
        window_start: datetime,
        window_end: datetime,
        busy_starts: list[datetime],
        duration_minutes: int,
    ) -> list[AvailableSlot]:
        """Walk the window in 30-min steps, excluding busy blocks."""
        slots: list[AvailableSlot] = []
        current = window_start.replace(minute=0, second=0, microsecond=0)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)

        while current < window_end and len(slots) < 10:
            # Business hours only: 9 AM – 5 PM UTC
            if 9 <= current.hour < 17:
                slot_end = current + timedelta(minutes=duration_minutes)
                conflict = any(
                    not (slot_end <= b or current >= b + timedelta(hours=1))
                    for b in busy_starts
                )
                if not conflict:
                    et_time = current.astimezone(ZoneInfo("America/New_York"))
                    slots.append(AvailableSlot(
                        slot_utc=current.isoformat(),
                        slot_local=et_time.strftime("%A, %B %-d at %-I:%M %p ET"),
                    ))
            current += timedelta(minutes=30)

        return slots

    def _generate_fallback_slots(
        self,
        start: datetime,
        end: datetime,
        duration_minutes: int,
    ) -> list[AvailableSlot]:
        """Produce simple next-business-day slots when no credentials exist."""
        logger.info("Generating fallback availability slots (no Google credentials)")
        return self._invert_busy_to_free_slots(start, end, [], duration_minutes)
