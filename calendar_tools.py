"""
app/agent/tools/calendar_tools.py

Implements the two core tools the agent can invoke:
  - check_calendar: queries free/busy for a set of interviewers
  - book_slot: atomically holds a time on all calendars

Phase 1: Both tools operate in MOCK mode — they consume a static JSON
fixture so we can test agent reasoning without live calendar credentials.

Phase 2: The `IntegrationEngine` (services/calendar.py) replaces the mock
with real Google/Microsoft Calendar API calls. The interface stays identical
so the agent code never changes.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import structlog

from schemas import (
    AvailableSlot,
    BookSlotResult,
    CalendarCheckResult,
)
from config import get_settings

logger = structlog.get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# MOCK DATA — replaced in Phase 2 with live API calls
# Simulates a 5-day window with some slots already blocked.
# ─────────────────────────────────────────────────────────────────────────────

def _generate_mock_slots(
    start: datetime,
    end: datetime,
    duration_minutes: int,
    blocked_utc: list[str],
) -> list[AvailableSlot]:
    """
    Walk the date range in 30-minute increments during business hours (9–17 UTC).
    Skip slots that overlap with any blocked period.
    Returns up to 10 slots to avoid overwhelming the agent context.
    """
    slots: list[AvailableSlot] = []
    current = start.replace(minute=0, second=0, microsecond=0)

    # Normalise blocked times to datetime objects
    blocked_dts = [datetime.fromisoformat(b).replace(tzinfo=timezone.utc) for b in blocked_utc]

    while current < end and len(slots) < 10:
        # Only propose slots during working hours (9am–5pm UTC)
        if 9 <= current.hour < 17:
            slot_end = current + timedelta(minutes=duration_minutes)
            # Check for overlap with any blocked slot (assume each block is 60 min)
            conflict = any(
                not (slot_end <= b or current >= b + timedelta(minutes=60))
                for b in blocked_dts
            )
            if not conflict:
                # Format a human-readable ET version for email copy
                et_time = current.astimezone(ZoneInfo("America/New_York"))
                slots.append(AvailableSlot(
                    slot_utc=current.isoformat(),
                    slot_local=et_time.strftime("%A, %B %-d at %-I:%M %p ET"),
                ))
        current += timedelta(minutes=30)

    return slots


# Simulated pre-blocked slots (meetings already on interviewer calendars)
MOCK_BLOCKED_SLOTS_UTC = [
    "2025-01-07T10:00:00+00:00",  # Tuesday 10am UTC
    "2025-01-07T14:00:00+00:00",  # Tuesday 2pm UTC
    "2025-01-08T09:00:00+00:00",  # Wednesday 9am UTC
    "2025-01-09T11:00:00+00:00",  # Thursday 11am UTC
]

# In-memory "calendar" for mock booking — persists within a CLI session
_mock_booked_slots: dict[str, str] = {}  # slot_utc → request_id


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC TOOL FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

async def check_calendar(
    interviewer_ids: list[str],
    date_range_start: str,
    date_range_end: str,
    duration_minutes: int,
    *,
    use_mock: bool = True,
) -> CalendarCheckResult:
    """
    Returns available slots across ALL required interviewers.

    In mock mode: generates slots from a static fixture.
    In live mode (Phase 2): calls Google/Microsoft free/busy API,
    takes the intersection across all interviewers, and applies
    the 15-minute Redis cache to avoid rate limits.

    Args:
        interviewer_ids:   Internal user UUIDs for required interviewers.
        date_range_start:  ISO-8601 UTC start of search window.
        date_range_end:    ISO-8601 UTC end of search window.
        duration_minutes:  Required interview length.
        use_mock:          True during Phase 1 testing.

    Returns:
        CalendarCheckResult with available slots and metadata.
    """
    logger.info(
        "check_calendar.called",
        interviewer_ids=interviewer_ids,
        start=date_range_start,
        end=date_range_end,
        duration_minutes=duration_minutes,
        mock=use_mock,
    )

    start_dt = datetime.fromisoformat(date_range_start).replace(tzinfo=timezone.utc)
    end_dt   = datetime.fromisoformat(date_range_end).replace(tzinfo=timezone.utc)

    if use_mock:
        # Simulate slight latency of a real API call
        slots = _generate_mock_slots(
            start=start_dt,
            end=end_dt,
            duration_minutes=duration_minutes,
            blocked_utc=MOCK_BLOCKED_SLOTS_UTC + list(_mock_booked_slots.keys()),
        )
    else:
        # Phase 2: delegate to the real IntegrationEngine
        from calendar_service import CalendarIntegrationService
        slots = await CalendarIntegrationService().get_shared_availability(
            interviewer_ids=interviewer_ids,
            start=start_dt,
            end=end_dt,
            duration_minutes=duration_minutes,
        )

    logger.info("check_calendar.result", slots_found=len(slots))
    return CalendarCheckResult(
        available_slots=slots,
        total_found=len(slots),
        search_window_start=date_range_start,
        search_window_end=date_range_end,
    )


async def book_slot(
    request_id: str,
    slot_utc: str,
    interviewer_ids: list[str],
    candidate_email: str,
    duration_minutes: int,
    *,
    use_mock: bool = True,
) -> BookSlotResult:
    """
    Atomically holds a slot on all interviewer calendars.

    CRITICAL: This is the "last-mile" live check. Even if check_calendar
    returned this slot 30 seconds ago, we verify it's still free here
    before committing — calendar race conditions are real.

    In mock mode: checks the in-memory _mock_booked_slots dict.
    In live mode (Phase 2): creates calendar events via API, rolling back
    all created events if any single one fails (compensating transaction).

    Returns:
        BookSlotResult with success flag and calendar event IDs.
    """
    logger.info(
        "book_slot.called",
        request_id=request_id,
        slot_utc=slot_utc,
        interviewer_ids=interviewer_ids,
        candidate_email=candidate_email,
        use_mock=use_mock,
    )

    if use_mock:
        # Simulate a race condition ~10% of the time for realism
        import random
        if slot_utc in _mock_booked_slots:
            logger.warning("book_slot.race_condition", slot_utc=slot_utc)
            return BookSlotResult(
                success=False,
                reason="Slot was just taken by another booking. Please choose an alternative.",
            )

        if random.random() < 0.1:
            logger.warning("book_slot.simulated_race_condition")
            return BookSlotResult(
                success=False,
                reason="Simulated calendar conflict — slot became unavailable.",
            )

        # Lock the slot
        _mock_booked_slots[slot_utc] = request_id
        fake_event_ids = [str(uuid.uuid4()) for _ in interviewer_ids]

        logger.info("book_slot.success", event_ids=fake_event_ids)
        return BookSlotResult(
            success=True,
            calendar_event_ids=fake_event_ids,
            video_link="https://meet.google.com/mock-meeting-link",
        )
    else:
        # Phase 2: delegate to IntegrationEngine
        from calendar_service import CalendarIntegrationService
        return await CalendarIntegrationService().book_interview_slot(
            request_id=request_id,
            slot_utc=slot_utc,
            interviewer_ids=interviewer_ids,
            candidate_email=candidate_email,
            duration_minutes=duration_minutes,
        )
