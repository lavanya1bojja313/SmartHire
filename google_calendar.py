"""
app/services/google_calendar.py

Google Calendar integration: OAuth 2.0 flow + Calendar API calls.

Scopes requested (Principle of Least Privilege per PLAN.md):
  - calendar.freebusy    → read free/busy info (not event details)
  - calendar.events      → create/delete interview events

OAuth flow:
  1. Recruiter/Interviewer clicks "Connect Google Calendar" in dashboard.
  2. We redirect to Google's consent screen with state=user_id.
  3. Google redirects back to /auth/google/callback with code.
  4. We exchange code for access_token + refresh_token.
  5. We encrypt and store both tokens linked to the user record.
  6. On every API call, we auto-refresh if the access token is expired.

Rate limiting (per PLAN.md):
  - Free/busy results are cached in Redis for 15 minutes.
  - The live check before booking always bypasses the cache.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logger = structlog.get_logger(__name__)

# Google OAuth scopes — minimal permissions
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/calendar.freebusy",
    "https://www.googleapis.com/auth/calendar.events",
    "openid",
    "email",
    "profile",
]

# Cache TTL: 15 minutes per PLAN.md resiliency plan
FREEBUSY_CACHE_TTL_SECONDS = 900


class GoogleCalendarService:
    """
    Handles all Google Calendar API interactions.

    Designed to be instantiated per-request (lightweight — no heavy state).
    Token storage/retrieval is delegated to the caller (Celery worker or API route).
    """

    def __init__(self, redis_client=None):
        from config import get_settings
        self.settings     = get_settings()
        self.redis_client = redis_client  # Optional — skip caching if None

    # ── OAuth Flow ────────────────────────────────────────────────────────────

    def get_authorization_url(self, state: str) -> str:
        """
        Generate the Google OAuth consent URL to redirect the user to.

        Args:
            state: An opaque value (e.g. user_id) returned unchanged in callback.
                   Used to prevent CSRF and match the callback to the user.
        Returns:
            URL string to redirect the user's browser to.
        """
        from google_auth_oauthlib.flow import Flow
        flow = self._build_flow()
        auth_url, _ = flow.authorization_url(
            access_type="offline",      # Request refresh_token
            include_granted_scopes="true",
            prompt="consent",           # Force consent screen to always get refresh_token
            state=state,
        )
        logger.info("google_oauth.auth_url_generated", state=state)
        return auth_url

    def exchange_code_for_tokens(self, code: str, redirect_uri: str) -> dict:
        """
        Exchange an authorization code for access + refresh tokens.

        Args:
            code:         The code from Google's callback query param.
            redirect_uri: Must exactly match the one used in get_authorization_url.
        Returns:
            Dict with keys: access_token, refresh_token, expiry (ISO-8601 UTC).
        """
        from google_auth_oauthlib.flow import Flow
        flow = self._build_flow()
        flow.redirect_uri = redirect_uri
        flow.fetch_token(code=code)
        creds = flow.credentials

        expiry_iso = creds.expiry.isoformat() if creds.expiry else None
        logger.info("google_oauth.tokens_exchanged", has_refresh=bool(creds.refresh_token))

        return {
            "access_token":  creds.token,
            "refresh_token": creds.refresh_token,
            "expiry":        expiry_iso,
            "token_uri":     creds.token_uri,
            "client_id":     creds.client_id,
            "client_secret": creds.client_secret,
            "scopes":        list(creds.scopes or []),
        }

    def refresh_access_token(self, stored_token_dict: dict) -> dict:
        """
        Use the stored refresh_token to get a new access_token.

        Called automatically before any API call if the access token is
        expired. Returns an updated token dict to be re-encrypted and stored.
        """
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request

        creds = self._dict_to_credentials(stored_token_dict)
        if creds.expired and creds.refresh_token:
            logger.info("google_oauth.refreshing_token")
            creds.refresh(Request())
            return {
                **stored_token_dict,
                "access_token": creds.token,
                "expiry": creds.expiry.isoformat() if creds.expiry else None,
            }
        return stored_token_dict

    # ── Calendar API: Free/Busy ───────────────────────────────────────────────

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
    async def get_freebusy(
        self,
        token_dict: dict,
        time_min: datetime,
        time_max: datetime,
        calendar_id: str = "primary",
        *,
        bypass_cache: bool = False,
    ) -> list[dict]:
        """
        Query the free/busy information for a calendar.

        Results are cached in Redis for 15 minutes to protect against
        Google's API rate limits. The bypass_cache flag is set to True
        during the final booking check.

        Args:
            token_dict:   Decrypted OAuth token dict for the interviewer.
            time_min:     Start of the query window (UTC).
            time_max:     End of the query window (UTC).
            calendar_id:  Google calendar ID (default: "primary").
            bypass_cache: If True, always hits the live API.

        Returns:
            List of busy period dicts: [{"start": ISO, "end": ISO}, ...]
        """
        cache_key = f"freebusy:google:{calendar_id}:{time_min.date()}:{time_max.date()}"

        # ── Check cache first (unless bypassed for live booking check) ────────
        if not bypass_cache and self.redis_client:
            cached = await self._get_from_cache(cache_key)
            if cached is not None:
                logger.info("google_calendar.freebusy.cache_hit", calendar_id=calendar_id)
                return cached

        # ── Live API call ─────────────────────────────────────────────────────
        logger.info(
            "google_calendar.freebusy.api_call",
            calendar_id=calendar_id,
            bypass_cache=bypass_cache,
        )
        creds   = self._dict_to_credentials(token_dict)
        service = self._build_service(creds)

        body = {
            "timeMin": time_min.replace(tzinfo=timezone.utc).isoformat(),
            "timeMax": time_max.replace(tzinfo=timezone.utc).isoformat(),
            "items":   [{"id": calendar_id}],
        }
        result   = service.freebusy().query(body=body).execute()
        busy_periods = result.get("calendars", {}).get(calendar_id, {}).get("busy", [])

        # ── Cache the result ──────────────────────────────────────────────────
        if self.redis_client:
            await self._set_in_cache(cache_key, busy_periods, FREEBUSY_CACHE_TTL_SECONDS)

        logger.info(
            "google_calendar.freebusy.result",
            calendar_id=calendar_id,
            busy_periods=len(busy_periods),
        )
        return busy_periods

    # ── Calendar API: Create Event ────────────────────────────────────────────

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    async def create_event(
        self,
        token_dict: dict,
        summary: str,
        start_utc: datetime,
        end_utc: datetime,
        attendees: list[str],
        description: str = "",
        calendar_id: str = "primary",
        conference_solution: str = "hangoutsMeet",
    ) -> dict:
        """
        Create a calendar event and attach a Google Meet link.

        Args:
            token_dict:          Decrypted OAuth token dict.
            summary:             Event title (e.g. "Interview: Jane Doe").
            start_utc / end_utc: Event times in UTC.
            attendees:           List of email addresses to invite.
            conference_solution: "hangoutsMeet" or "addOn".

        Returns:
            Dict with event_id and hangout_link.
        """
        import uuid
        creds   = self._dict_to_credentials(token_dict)
        service = self._build_service(creds)

        event_body = {
            "summary":     summary,
            "description": description,
            "start": {
                "dateTime": start_utc.replace(tzinfo=timezone.utc).isoformat(),
                "timeZone": "UTC",
            },
            "end": {
                "dateTime": end_utc.replace(tzinfo=timezone.utc).isoformat(),
                "timeZone": "UTC",
            },
            "attendees": [{"email": email} for email in attendees],
            "conferenceData": {
                "createRequest": {
                    "requestId": str(uuid.uuid4()),
                    "conferenceSolutionKey": {"type": conference_solution},
                }
            },
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "email",  "minutes": 1440},   # 24h
                    {"method": "popup",  "minutes": 15},
                ],
            },
            "guestsCanModifyEvent": False,
            "guestsCanSeeOtherGuests": True,
        }

        logger.info(
            "google_calendar.create_event",
            summary=summary,
            start=start_utc.isoformat(),
            attendees_count=len(attendees),
        )

        created = service.events().insert(
            calendarId=calendar_id,
            body=event_body,
            conferenceDataVersion=1,  # Required to get Meet link
            sendUpdates="all",        # Send invites to all attendees
        ).execute()

        event_id   = created.get("id")
        meet_link  = created.get("hangoutLink") or created.get("conferenceData", {}).get("entryPoints", [{}])[0].get("uri")

        logger.info("google_calendar.event_created", event_id=event_id, meet_link=meet_link)
        return {"event_id": event_id, "meet_link": meet_link}

    async def delete_event(self, token_dict: dict, event_id: str, calendar_id: str = "primary"):
        """Delete a calendar event (used for rollback on partial booking failure)."""
        creds   = self._dict_to_credentials(token_dict)
        service = self._build_service(creds)
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
        logger.info("google_calendar.event_deleted", event_id=event_id)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _build_flow(self):
        from google_auth_oauthlib.flow import Flow
        return Flow.from_client_config(
            client_config={
                "web": {
                    "client_id":     self.settings.google_client_id,
                    "client_secret": self.settings.google_client_secret,
                    "redirect_uris": [self.settings.google_redirect_uri],
                    "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
                    "token_uri":     "https://oauth2.googleapis.com/token",
                }
            },
            scopes=GOOGLE_SCOPES,
        )

    def _dict_to_credentials(self, token_dict: dict):
        from google.oauth2.credentials import Credentials
        return Credentials(
            token=token_dict["access_token"],
            refresh_token=token_dict.get("refresh_token"),
            token_uri=token_dict.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=token_dict.get("client_id", self.settings.google_client_id),
            client_secret=token_dict.get("client_secret", self.settings.google_client_secret),
            scopes=token_dict.get("scopes", GOOGLE_SCOPES),
        )

    def _build_service(self, credentials):
        from googleapiclient.discovery import build
        return build("calendar", "v3", credentials=credentials, cache_discovery=False)

    async def _get_from_cache(self, key: str) -> Optional[list]:
        try:
            raw = await self.redis_client.get(key)
            return json.loads(raw) if raw else None
        except Exception as e:
            logger.warning("google_calendar.cache_get_failed", key=key, error=str(e))
            return None

    async def _set_in_cache(self, key: str, value: list, ttl: int):
        try:
            await self.redis_client.setex(key, ttl, json.dumps(value))
        except Exception as e:
            logger.warning("google_calendar.cache_set_failed", key=key, error=str(e))
