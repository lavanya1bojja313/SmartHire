from enum import Enum
from typing import Optional
from pydantic import BaseModel, ConfigDict


class AgentAction(str, Enum):
    CHECK_CALENDAR = "check_calendar"
    BOOK_SLOT = "book_slot"
    SEND_EMAIL = "send_email"
    ESCALATE = "escalate"


class AgentDecision(BaseModel):
    model_config = ConfigDict(extra="allow")

    thought: str
    action: AgentAction
    action_input: dict = {}
    email_body: Optional[str] = None
    email_subject: Optional[str] = None
    escalate_reason: Optional[str] = None


class AvailableSlot(BaseModel):
    slot_utc: str
    slot_local: str


class CalendarCheckResult(BaseModel):
    available_slots: list[AvailableSlot]
    total_found: int
    search_window_start: str
    search_window_end: str


class CheckCalendarInput(BaseModel):
    interviewer_ids: list[str]
    date_range_start: str
    date_range_end: str
    duration_minutes: int


class BookSlotInput(BaseModel):
    request_id: str
    slot_utc: str
    interviewer_ids: list[str]
    candidate_email: str
    duration_minutes: int


class BookSlotResult(BaseModel):
    success: bool
    calendar_event_ids: list[str] = []
    video_link: Optional[str] = None
    reason: Optional[str] = None
