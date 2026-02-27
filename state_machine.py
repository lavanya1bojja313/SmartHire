"""
app/agent/state_machine.py

Manages all valid state transitions for a SchedulingRequest.

Why a state machine?
  - Prevents illegal transitions (e.g. going from Scheduled back to Draft).
  - Gives recruiters a clear audit trail in the dashboard.
  - Makes the agent's behaviour predictable and testable.

States (mirrors the DB ENUM):
  Draft              → request created, agent not yet triggered
  Outreach_Sent      → first email sent to candidate
  Negotiating        → back-and-forth in progress
  Scheduled          → slot booked, invites sent
  Failed             → booking could not be completed after max retries
  Human_Intervention → escalated to a human recruiter

Transitions are logged to both structlog and the DB event timeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)


class SchedulingStatus(str, Enum):
    """Mirrors the DB ENUM — change both together."""
    DRAFT               = "Draft"
    OUTREACH_SENT       = "Outreach_Sent"
    NEGOTIATING         = "Negotiating"
    SCHEDULED           = "Scheduled"
    FAILED              = "Failed"
    HUMAN_INTERVENTION  = "Human_Intervention"


# ─────────────────────────────────────────────────────────────────────────────
# Valid transitions: {from_state: [allowed_to_states]}
# ─────────────────────────────────────────────────────────────────────────────
VALID_TRANSITIONS: dict[SchedulingStatus, list[SchedulingStatus]] = {
    SchedulingStatus.DRAFT: [
        SchedulingStatus.OUTREACH_SENT,
        SchedulingStatus.FAILED,          # immediate failure (no interviewers available)
    ],
    SchedulingStatus.OUTREACH_SENT: [
        SchedulingStatus.NEGOTIATING,
        SchedulingStatus.SCHEDULED,       # candidate accepts first proposal directly
        SchedulingStatus.HUMAN_INTERVENTION,
        SchedulingStatus.FAILED,
    ],
    SchedulingStatus.NEGOTIATING: [
        SchedulingStatus.NEGOTIATING,     # self-loop: more negotiation rounds
        SchedulingStatus.SCHEDULED,
        SchedulingStatus.HUMAN_INTERVENTION,
        SchedulingStatus.FAILED,
    ],
    # Terminal states — no transitions allowed out
    SchedulingStatus.SCHEDULED:          [],
    SchedulingStatus.FAILED:             [],
    SchedulingStatus.HUMAN_INTERVENTION: [
        SchedulingStatus.SCHEDULED,       # human manually books
        SchedulingStatus.FAILED,          # human gives up
    ],
}


@dataclass
class StateContext:
    """
    In-memory representation of a scheduling request's runtime state.

    This is serialised to the `state_machine_context` JSONB column on the
    SchedulingRequest table so the agent can resume after a process restart.
    """
    request_id: str
    status: SchedulingStatus = SchedulingStatus.DRAFT

    # Conversation history sent to LLM as context on every turn
    conversation_history: list[dict] = field(default_factory=list)

    # Slots the agent has already proposed (avoid re-proposing same times)
    proposed_slots_utc: list[str] = field(default_factory=list)

    # Number of back-and-forth loops with the candidate
    negotiation_loop_count: int = 0

    # Number of consecutive book_slot failures (race conditions)
    consecutive_booking_failures: int = 0

    # Booked slot info (populated on transition to SCHEDULED)
    booked_slot_utc: Optional[str] = None
    calendar_event_ids: list[str] = field(default_factory=list)
    video_link: Optional[str] = None

    # Escalation reason (populated on HUMAN_INTERVENTION)
    escalation_reason: Optional[str] = None

    # Audit log of every state transition with timestamps
    transition_log: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialise to dict for JSONB storage."""
        return {
            "request_id": self.request_id,
            "status": self.status.value,
            "conversation_history": self.conversation_history,
            "proposed_slots_utc": self.proposed_slots_utc,
            "negotiation_loop_count": self.negotiation_loop_count,
            "consecutive_booking_failures": self.consecutive_booking_failures,
            "booked_slot_utc": self.booked_slot_utc,
            "calendar_event_ids": self.calendar_event_ids,
            "video_link": self.video_link,
            "escalation_reason": self.escalation_reason,
            "transition_log": self.transition_log,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "StateContext":
        """Deserialise from JSONB storage."""
        ctx = cls(request_id=data["request_id"])
        ctx.status                      = SchedulingStatus(data["status"])
        ctx.conversation_history        = data.get("conversation_history", [])
        ctx.proposed_slots_utc          = data.get("proposed_slots_utc", [])
        ctx.negotiation_loop_count      = data.get("negotiation_loop_count", 0)
        ctx.consecutive_booking_failures= data.get("consecutive_booking_failures", 0)
        ctx.booked_slot_utc             = data.get("booked_slot_utc")
        ctx.calendar_event_ids          = data.get("calendar_event_ids", [])
        ctx.video_link                  = data.get("video_link")
        ctx.escalation_reason           = data.get("escalation_reason")
        ctx.transition_log              = data.get("transition_log", [])
        return ctx


class StateMachine:
    """
    Controls all state transitions for a single SchedulingRequest.

    Raises InvalidTransitionError for illegal moves, so bugs in the agent
    never silently corrupt state.
    """

    def __init__(self, context: StateContext) -> None:
        self.context = context

    # ── Public API ────────────────────────────────────────────────────────────

    def transition(
        self,
        new_status: SchedulingStatus,
        reason: str = "",
    ) -> None:
        """
        Attempt to move to `new_status`.

        Logs the transition and raises InvalidTransitionError if the move
        is not in the VALID_TRANSITIONS table.
        """
        current = self.context.status
        allowed = VALID_TRANSITIONS.get(current, [])

        if new_status not in allowed:
            raise InvalidTransitionError(
                f"Cannot transition from {current.value} → {new_status.value}. "
                f"Allowed: {[s.value for s in allowed]}"
            )

        logger.info(
            "state_machine.transition",
            request_id=self.context.request_id,
            from_state=current.value,
            to_state=new_status.value,
            reason=reason,
        )

        # Record in the audit log (stored in JSONB)
        self.context.transition_log.append({
            "from": current.value,
            "to": new_status.value,
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        self.context.status = new_status

    def record_email_sent(self, email_body: str, subject: str) -> None:
        """Append an outbound agent email to the conversation history."""
        self.context.conversation_history.append({
            "role": "agent",
            "subject": subject,
            "body": email_body,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        logger.debug("state_machine.email_sent", request_id=self.context.request_id)

    def record_candidate_reply(self, email_body: str, subject: str) -> None:
        """Append an inbound candidate email to the conversation history."""
        self.context.conversation_history.append({
            "role": "candidate",
            "subject": subject,
            "body": email_body,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        self.context.negotiation_loop_count += 1
        logger.debug(
            "state_machine.candidate_reply",
            request_id=self.context.request_id,
            loop=self.context.negotiation_loop_count,
        )

    def record_slot_proposed(self, slot_utc: str) -> None:
        """Track which slots have been proposed so we don't repeat them."""
        if slot_utc not in self.context.proposed_slots_utc:
            self.context.proposed_slots_utc.append(slot_utc)

    def record_booking_success(
        self,
        slot_utc: str,
        event_ids: list[str],
        video_link: str | None,
    ) -> None:
        """Capture booking details and transition to SCHEDULED."""
        self.context.booked_slot_utc    = slot_utc
        self.context.calendar_event_ids = event_ids
        self.context.video_link         = video_link
        self.context.consecutive_booking_failures = 0
        self.transition(SchedulingStatus.SCHEDULED, reason="Slot booked successfully")

    def record_booking_failure(self, reason: str) -> None:
        """Increment booking failure counter; may trigger escalation."""
        self.context.consecutive_booking_failures += 1
        logger.warning(
            "state_machine.booking_failure",
            request_id=self.context.request_id,
            count=self.context.consecutive_booking_failures,
            reason=reason,
        )

    def should_escalate(self, max_loops: int) -> tuple[bool, str]:
        """
        Returns (True, reason) if the agent should escalate to a human.
        Called after every agent turn before sending the next email.
        """
        if self.context.negotiation_loop_count >= max_loops:
            return True, f"Exceeded {max_loops} negotiation loops without resolution."
        if self.context.consecutive_booking_failures >= 2:
            return True, "Calendar booking failed 2+ consecutive times (possible race condition)."
        return False, ""

    def escalate(self, reason: str) -> None:
        """Transition to HUMAN_INTERVENTION and record the reason."""
        self.context.escalation_reason = reason
        self.transition(SchedulingStatus.HUMAN_INTERVENTION, reason=reason)

    @property
    def is_terminal(self) -> bool:
        """True if no further agent action is possible or needed."""
        return self.context.status in {
            SchedulingStatus.SCHEDULED,
            SchedulingStatus.FAILED,
            SchedulingStatus.HUMAN_INTERVENTION,
        }


class InvalidTransitionError(Exception):
    """Raised when a state transition is not permitted."""
    pass
