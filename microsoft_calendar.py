"""
app/services/microsoft_calendar.py

Microsoft Graph API integration: OAuth 2.0 (MSAL) + Calendar API.

Scopes requested (Principle of Least Privilege):
  - Calendars.Read        → read free/busy info
  - Calendars.ReadWrite   → create interview events
  - User.Read             → get user profile on login
  - offline_access        → get refresh_token for long-lived sessions

Key difference from Google:
  - Microsoft uses MSAL (Microsoft Authentication Library) instead of google-auth.
  - The free/busy API is called via the /calendarView endpoint or the
    /calendar/getSchedule endpoint (batched — more efficient for multi-interviewer).
  - Token refresh is handled by MSAL's ConfidentialClientApplication automatically.

Rate limiting:
  - Same 15-minute Redis cache strategy as Google.
  - Microsoft throttles at 10,000 requests/10 minutes per app.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logger = structlog.get_logger(__name__)

MICROSOFT_SCOPES = [
    "Calendars.Read",
    "Calendars.ReadWrite",
    "User.Read",
    "offline_access",
]

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
FREEBUSY_CACHE_TTL_SECONDS = 900  # 15 minutes


class MicrosoftCalendarService:
    """
    Handles all Microsoft Graph Calendar API interactions.
    Mirrors the same interface as GoogleCalendarService for easy substitution.
    """

    def __init__(self, redis_client=None):
        from config import get_settings
        self.settings     = get_settings()
        self.redis_client = redis_client

    # ── OAuth Flow ────────────────────────────────────────────────────────────

    def get_authorization_url(self, state: str) -> str:
        """
        Generate the Microsoft OAuth consent URL.

        Uses MSAL's authorization code flow with PKCE for security.
        """
        import msal
        app = self._build_msal_app()
        result = app.initiate_auth_code_flow(
            scopes=MICROSOFT_SCOPES,
            redirect_uri=self.settings.microsoft_redirect_uri,
            state=state,
            prompt="consent",       # Always show consent to get refresh_token
            response_mode="query",
        )
        logger.info("microsoft_oauth.auth_url_generated", state=state)
        # Store the flow dict in cache so callback can use it (PKCE verification)
        # In production this would be stored in a short-lived session or Redis
        return result["auth_uri"]

    def exchange_code_for_tokens(self, code: str, state: str) -> dict:
        """
        Exchange an auth code for access + refresh tokens via MSAL.

        Returns:
            Dict with: access_token, refresh_token, expiry, scope.
        """
        import msal
        app = self._build_msal_app()

        # MSAL acquires the token and manages the cache internally
        result = app.acquire_token_by_authorization_code(
            code=code,
            scopes=MICROSOFT_SCOPES,
            redirect_uri=self.settings.microsoft_redirect_uri,
        )

        if "error" in result:
            raise OAuthError(
                f"Microsoft token exchange failed: {result.get('error_description', result['error'])}"
            )

        logger.info("microsoft_oauth.tokens_exchanged", scopes=result.get("scope"))

        # Calculate expiry from expires_in (seconds from now)
        expiry = datetime.now(timezone.utc) + timedelta(seconds=result.get("expires_in", 3600))

        return {
            "access_token":  result["access_token"],
            "refresh_token": result.get("refresh_token", ""),
            "expiry":        expiry.isoformat(),
            "scope":         result.get("scope", ""),
            "token_type":    result.get("token_type", "Bearer"),
        }

    def refresh_access_token(self, stored_token_dict: dict) -> dict:
        """
        Refresh an expired access token using MSAL.

        MSAL handles the refresh flow automatically when you call
        acquire_token_by_refresh_token.
        """
        import msal
        app = self._build_msal_app()

        refresh_token = stored_token_dict.get("refresh_token")
        if not refresh_token:
            raise OAuthError("No refresh_token available — user must re-authenticate.")

        result = app.acquire_token_by_refresh_token(
            refresh_token=refresh_token,
            scopes=MICROSOFT_SCOPES,
        )

        if "error" in result:
            raise OAuthError(
                f"Microsoft token refresh failed: {result.get('error_description', result['error'])}"
            )

        logger.info("microsoft_oauth.token_refreshed")
        expiry = datetime.now(timezone.utc) + timedelta(seconds=result.get("expires_in", 3600))

        return {
            **stored_token_dict,
            "access_token": result["access_token"],
            "refresh_token": result.get("refresh_token", stored_token_dict.get("refresh_token")),
            "expiry": expiry.isoformat(),
        }

    def is_token_expired(self, token_dict: dict) -> bool:
        """Check if the access token has expired (with 5-minute buffer)."""
        expiry_str = token_dict.get("expiry")
        if not expiry_str:
            return True
        expiry = datetime.fromisoformat(expiry_str)
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= expiry - timedelta(minutes=5)

    # ── Calendar API: Get Schedule (Free/Busy) ────────────────────────────────

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    async def get_freebusy(
        self,
        token_dict: dict,
        time_min: datetime,
        time_max: datetime,
        user_email: str,
        *,
        bypass_cache: bool = False,
    ) -> list[dict]:
        """
        Query the free/busy schedule for a user via Microsoft Graph.

        Uses the /calendar/getSchedule endpoint which supports batch queries
        (multiple users in one API call) — more efficient for group interviews.

        Returns:
            List of busy periods: [{"start": ISO, "end": ISO}, ...]
        """
        cache_key = f"freebusy:microsoft:{user_email}:{time_min.date()}:{time_max.date()}"

        if not bypass_cache and self.redis_client:
            cached = await self._get_from_cache(cache_key)
            if cached is not None:
                logger.info("microsoft_calendar.freebusy.cache_hit", email=user_email)
                return cached

        logger.info(
            "microsoft_calendar.freebusy.api_call",
            email=user_email,
            bypass_cache=bypass_cache,
        )

        token_dict = self._ensure_fresh_token(token_dict)
        headers    = self._auth_headers(token_dict)

        payload = {
            "schedules":            [user_email],
            "startTime": {
                "dateTime": time_min.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
                "timeZone": "UTC",
            },
            "endTime": {
                "dateTime": time_max.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
                "timeZone": "UTC",
            },
            "availabilityViewInterval": 30,  # 30-minute granularity
        }

        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{GRAPH_BASE_URL}/me/calendar/getSchedule",
                headers=headers,
                json=payload,
            )
            self._raise_for_status(response, "getSchedule")

        data   = response.json()
        schedules = data.get("value", [{}])
        busy_slots = []

        for schedule in schedules:
            for item in schedule.get("scheduleItems", []):
                if item.get("status") in ("busy", "tentative", "oof"):
                    busy_slots.append({
                        "start": item["start"]["dateTime"],
                        "end":   item["end"]["dateTime"],
                    })

        if self.redis_client:
            await self._set_in_cache(cache_key, busy_slots, FREEBUSY_CACHE_TTL_SECONDS)

        logger.info(
            "microsoft_calendar.freebusy.result",
            email=user_email,
            busy_periods=len(busy_slots),
        )
        return busy_slots

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
        online_meeting: bool = True,
    ) -> dict:
        """
        Create a calendar event with a Teams meeting link.

        Args:
            online_meeting: If True, adds a Microsoft Teams online meeting.

        Returns:
            Dict with event_id and teams_join_url.
        """
        token_dict = self._ensure_fresh_token(token_dict)
        headers    = self._auth_headers(token_dict)

        event_body = {
            "subject": summary,
            "body": {
                "contentType": "HTML",
                "content": description or summary,
            },
            "start": {
                "dateTime": start_utc.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
                "timeZone": "UTC",
            },
            "end": {
                "dateTime": end_utc.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
                "timeZone": "UTC",
            },
            "attendees": [
                {
                    "emailAddress": {"address": email},
                    "type": "required",
                }
                for email in attendees
            ],
            "isOnlineMeeting": online_meeting,
            "onlineMeetingProvider": "teamsForBusiness" if online_meeting else "unknown",
            "reminderMinutesBeforeStart": 15,
            "isReminderOn": True,
        }

        logger.info(
            "microsoft_calendar.create_event",
            summary=summary,
            start=start_utc.isoformat(),
            attendees_count=len(attendees),
        )

        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{GRAPH_BASE_URL}/me/events",
                headers=headers,
                json=event_body,
            )
            self._raise_for_status(response, "create_event")

        data      = response.json()
        event_id  = data.get("id")
        teams_url = data.get("onlineMeeting", {}).get("joinUrl")

        logger.info("microsoft_calendar.event_created", event_id=event_id)
        return {"event_id": event_id, "meet_link": teams_url}

    async def delete_event(self, token_dict: dict, event_id: str):
        """Delete an event (used during compensating transaction on booking failure)."""
        token_dict = self._ensure_fresh_token(token_dict)
        headers    = self._auth_headers(token_dict)

        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.delete(
                f"{GRAPH_BASE_URL}/me/events/{event_id}",
                headers=headers,
            )
            if response.status_code not in (200, 204):
                logger.error(
                    "microsoft_calendar.delete_event_failed",
                    event_id=event_id,
                    status=response.status_code,
                )
        logger.info("microsoft_calendar.event_deleted", event_id=event_id)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _build_msal_app(self):
        import msal
        return msal.ConfidentialClientApplication(
            client_id=self.settings.microsoft_client_id,
            client_credential=self.settings.microsoft_client_secret,
            authority=f"https://login.microsoftonline.com/{self.settings.microsoft_tenant_id}",
        )

    def _auth_headers(self, token_dict: dict) -> dict:
        return {
            "Authorization": f"Bearer {token_dict['access_token']}",
            "Content-Type":  "application/json",
        }

    def _ensure_fresh_token(self, token_dict: dict) -> dict:
        """Auto-refresh token if expired before making an API call."""
        if self.is_token_expired(token_dict):
            logger.info("microsoft_calendar.auto_refreshing_token")
            return self.refresh_access_token(token_dict)
        return token_dict

    def _raise_for_status(self, response, operation: str):
        """Raise a descriptive error for non-2xx responses."""
        if response.status_code >= 400:
            error_body = ""
            try:
                error_body = response.json().get("error", {}).get("message", "")
            except Exception:
                error_body = response.text[:200]
            raise GraphAPIError(
                f"Microsoft Graph {operation} failed ({response.status_code}): {error_body}"
            )

    async def _get_from_cache(self, key: str) -> Optional[list]:
        try:
            raw = await self.redis_client.get(key)
            return json.loads(raw) if raw else None
        except Exception as e:
            logger.warning("microsoft_calendar.cache_get_failed", key=key, error=str(e))
            return None

    async def _set_in_cache(self, key: str, value: list, ttl: int):
        try:
            await self.redis_client.setex(key, ttl, json.dumps(value))
        except Exception as e:
            logger.warning("microsoft_calendar.cache_set_failed", key=key, error=str(e))


class OAuthError(Exception):
    """Raised when OAuth token exchange or refresh fails."""
    pass

class GraphAPIError(Exception):
    """Raised when a Microsoft Graph API call returns an error."""
    pass
