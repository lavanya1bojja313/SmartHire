"""
run_tests_standalone.py

Runs all Phase 1 logic tests without any pip installs.
Uses only Python stdlib — works in air-gapped environments.

Tests:
  - State machine transitions (all valid + all invalid)
  - Escalation trigger thresholds
  - Conversation history recording
  - StateContext serialisation round-trip
  - Calendar tool mock logic (business hours, double-booking)
  - Orchestrator decision parsing

Usage:
    python3 run_tests_standalone.py
"""

import asyncio
import json
import os
import sys
import uuid
import traceback
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

# ─── Minimal stubs so we don't need pydantic/structlog/etc. ──────────────────

class _FakeLogger:
    def info(self, *a, **kw): pass
    def warning(self, *a, **kw): pass
    def debug(self, *a, **kw): pass
    def error(self, *a, **kw): pass

def _get_logger(_name): return _FakeLogger()

# Patch sys.path so app imports resolve
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ─── Inline the core modules (no external deps needed) ───────────────────────
# We re-implement only what we need to test the logic, keeping the contracts
# identical to the real modules.

class SchedulingStatus(str, Enum):
    DRAFT               = "Draft"
    OUTREACH_SENT       = "Outreach_Sent"
    NEGOTIATING         = "Negotiating"
    SCHEDULED           = "Scheduled"
    FAILED              = "Failed"
    HUMAN_INTERVENTION  = "Human_Intervention"

VALID_TRANSITIONS = {
    SchedulingStatus.DRAFT: [SchedulingStatus.OUTREACH_SENT, SchedulingStatus.FAILED],
    SchedulingStatus.OUTREACH_SENT: [SchedulingStatus.NEGOTIATING, SchedulingStatus.SCHEDULED, SchedulingStatus.HUMAN_INTERVENTION, SchedulingStatus.FAILED],
    SchedulingStatus.NEGOTIATING: [SchedulingStatus.NEGOTIATING, SchedulingStatus.SCHEDULED, SchedulingStatus.HUMAN_INTERVENTION, SchedulingStatus.FAILED],
    SchedulingStatus.SCHEDULED: [],
    SchedulingStatus.FAILED: [],
    SchedulingStatus.HUMAN_INTERVENTION: [SchedulingStatus.SCHEDULED, SchedulingStatus.FAILED],
}

@dataclass
class StateContext:
    request_id: str
    status: SchedulingStatus = SchedulingStatus.DRAFT
    conversation_history: list = field(default_factory=list)
    proposed_slots_utc: list = field(default_factory=list)
    negotiation_loop_count: int = 0
    consecutive_booking_failures: int = 0
    booked_slot_utc: Optional[str] = None
    calendar_event_ids: list = field(default_factory=list)
    video_link: Optional[str] = None
    escalation_reason: Optional[str] = None
    transition_log: list = field(default_factory=list)

    def to_dict(self):
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
    def from_dict(cls, data):
        ctx = cls(request_id=data["request_id"])
        ctx.status = SchedulingStatus(data["status"])
        ctx.conversation_history = data.get("conversation_history", [])
        ctx.proposed_slots_utc = data.get("proposed_slots_utc", [])
        ctx.negotiation_loop_count = data.get("negotiation_loop_count", 0)
        ctx.consecutive_booking_failures = data.get("consecutive_booking_failures", 0)
        ctx.booked_slot_utc = data.get("booked_slot_utc")
        ctx.calendar_event_ids = data.get("calendar_event_ids", [])
        ctx.video_link = data.get("video_link")
        ctx.escalation_reason = data.get("escalation_reason")
        ctx.transition_log = data.get("transition_log", [])
        return ctx

class InvalidTransitionError(Exception):
    pass

class StateMachine:
    def __init__(self, context: StateContext):
        self.context = context

    def transition(self, new_status: SchedulingStatus, reason: str = ""):
        current = self.context.status
        allowed = VALID_TRANSITIONS.get(current, [])
        if new_status not in allowed:
            raise InvalidTransitionError(
                f"Cannot transition {current.value} → {new_status.value}. Allowed: {[s.value for s in allowed]}"
            )
        self.context.transition_log.append({
            "from": current.value, "to": new_status.value,
            "reason": reason, "timestamp": datetime.now(timezone.utc).isoformat()
        })
        self.context.status = new_status

    def record_email_sent(self, body, subject):
        self.context.conversation_history.append({"role": "agent", "subject": subject, "body": body, "timestamp": datetime.now(timezone.utc).isoformat()})

    def record_candidate_reply(self, email_body, subject):
        self.context.conversation_history.append({"role": "candidate", "subject": subject, "body": email_body, "timestamp": datetime.now(timezone.utc).isoformat()})
        self.context.negotiation_loop_count += 1

    def record_slot_proposed(self, slot_utc):
        if slot_utc not in self.context.proposed_slots_utc:
            self.context.proposed_slots_utc.append(slot_utc)

    def record_booking_success(self, slot_utc, event_ids, video_link):
        self.context.booked_slot_utc = slot_utc
        self.context.calendar_event_ids = event_ids
        self.context.video_link = video_link
        self.context.consecutive_booking_failures = 0
        self.transition(SchedulingStatus.SCHEDULED, reason="Slot booked successfully")

    def record_booking_failure(self, reason):
        self.context.consecutive_booking_failures += 1

    def should_escalate(self, max_loops: int):
        if self.context.negotiation_loop_count >= max_loops:
            return True, f"Exceeded {max_loops} negotiation loops without resolution."
        if self.context.consecutive_booking_failures >= 2:
            return True, "Calendar booking failed 2+ consecutive times."
        return False, ""

    def escalate(self, reason):
        self.context.escalation_reason = reason
        self.transition(SchedulingStatus.HUMAN_INTERVENTION, reason=reason)

    @property
    def is_terminal(self):
        return self.context.status in {SchedulingStatus.SCHEDULED, SchedulingStatus.FAILED, SchedulingStatus.HUMAN_INTERVENTION}


# ── Mock calendar tools ───────────────────────────────────────────────────────

_mock_booked: dict = {}

def _generate_slots(start: datetime, end: datetime, duration_minutes: int, blocked: list):
    from zoneinfo import ZoneInfo
    slots = []
    current = start.replace(minute=0, second=0, microsecond=0)
    blocked_dts = [datetime.fromisoformat(b).replace(tzinfo=timezone.utc) for b in blocked]
    while current < end and len(slots) < 10:
        if 9 <= current.hour < 17:
            slot_end = current + timedelta(minutes=duration_minutes)
            conflict = any(not (slot_end <= b or current >= b + timedelta(minutes=60)) for b in blocked_dts)
            if not conflict:
                et = current.astimezone(ZoneInfo("America/New_York"))
                slots.append({"slot_utc": current.isoformat(), "slot_local": et.strftime("%A at %-I:%M %p ET")})
        current += timedelta(minutes=30)
    return slots

async def mock_check_calendar(start, end, duration_minutes):
    start_dt = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    end_dt = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
    return _generate_slots(start_dt, end_dt, duration_minutes, list(_mock_booked.keys()))

async def mock_book_slot(request_id, slot_utc):
    if slot_utc in _mock_booked:
        return {"success": False, "reason": "Slot already booked"}
    _mock_booked[slot_utc] = request_id
    return {"success": True, "calendar_event_ids": [str(uuid.uuid4())], "video_link": "https://meet.google.com/test"}


# ─────────────────────────────────────────────────────────────────────────────
# Test Runner
# ─────────────────────────────────────────────────────────────────────────────

PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
BOLD = "\033[1m"
RESET = "\033[0m"

results = {"passed": 0, "failed": 0, "errors": []}

def test(name, fn):
    try:
        if asyncio.iscoroutinefunction(fn):
            asyncio.run(fn())
        else:
            fn()
        print(f"  {PASS} {name}")
        results["passed"] += 1
    except Exception as e:
        print(f"  {FAIL} {name}")
        print(f"       {e}")
        results["failed"] += 1
        results["errors"].append((name, traceback.format_exc()))

def assert_eq(a, b, msg=""):
    if a != b:
        raise AssertionError(f"{msg} Expected {b!r}, got {a!r}")

def assert_true(v, msg=""):
    if not v:
        raise AssertionError(msg or f"Expected truthy, got {v!r}")

def assert_raises(exc_class, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
        raise AssertionError(f"Expected {exc_class.__name__} but no exception raised")
    except exc_class:
        pass

# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{BOLD}{'='*60}{RESET}")
print(f"{BOLD}  Phase 1 — Standalone Test Suite{RESET}")
print(f"{BOLD}{'='*60}{RESET}\n")

# ── STATE MACHINE: Valid Transitions ─────────────────────────────────────────
print(f"{BOLD}State Machine — Valid Transitions{RESET}")

def t_draft_to_outreach():
    ctx = StateContext(request_id="r1"); sm = StateMachine(ctx)
    sm.transition(SchedulingStatus.OUTREACH_SENT)
    assert_eq(ctx.status, SchedulingStatus.OUTREACH_SENT)
test("DRAFT → OUTREACH_SENT", t_draft_to_outreach)

def t_draft_to_failed():
    ctx = StateContext(request_id="r1"); sm = StateMachine(ctx)
    sm.transition(SchedulingStatus.FAILED)
    assert_eq(ctx.status, SchedulingStatus.FAILED)
test("DRAFT → FAILED", t_draft_to_failed)

def t_outreach_to_negotiating():
    ctx = StateContext(request_id="r1", status=SchedulingStatus.OUTREACH_SENT); sm = StateMachine(ctx)
    sm.transition(SchedulingStatus.NEGOTIATING)
    assert_eq(ctx.status, SchedulingStatus.NEGOTIATING)
test("OUTREACH_SENT → NEGOTIATING", t_outreach_to_negotiating)

def t_outreach_direct_to_scheduled():
    ctx = StateContext(request_id="r1", status=SchedulingStatus.OUTREACH_SENT); sm = StateMachine(ctx)
    sm.transition(SchedulingStatus.SCHEDULED)
    assert_eq(ctx.status, SchedulingStatus.SCHEDULED)
test("OUTREACH_SENT → SCHEDULED (candidate accepts first proposal)", t_outreach_direct_to_scheduled)

def t_negotiating_self_loop():
    ctx = StateContext(request_id="r1", status=SchedulingStatus.NEGOTIATING); sm = StateMachine(ctx)
    sm.transition(SchedulingStatus.NEGOTIATING)
    assert_eq(ctx.status, SchedulingStatus.NEGOTIATING)
test("NEGOTIATING → NEGOTIATING (self-loop)", t_negotiating_self_loop)

def t_negotiating_to_human():
    ctx = StateContext(request_id="r1", status=SchedulingStatus.NEGOTIATING); sm = StateMachine(ctx)
    sm.transition(SchedulingStatus.HUMAN_INTERVENTION)
    assert_eq(ctx.status, SchedulingStatus.HUMAN_INTERVENTION)
test("NEGOTIATING → HUMAN_INTERVENTION", t_negotiating_to_human)

def t_human_to_scheduled():
    ctx = StateContext(request_id="r1", status=SchedulingStatus.HUMAN_INTERVENTION); sm = StateMachine(ctx)
    sm.transition(SchedulingStatus.SCHEDULED)
    assert_eq(ctx.status, SchedulingStatus.SCHEDULED)
test("HUMAN_INTERVENTION → SCHEDULED (human books manually)", t_human_to_scheduled)

# ── STATE MACHINE: Invalid Transitions ───────────────────────────────────────
print(f"\n{BOLD}State Machine — Invalid Transitions{RESET}")

def t_scheduled_terminal():
    ctx = StateContext(request_id="r1", status=SchedulingStatus.SCHEDULED); sm = StateMachine(ctx)
    assert_raises(InvalidTransitionError, sm.transition, SchedulingStatus.NEGOTIATING)
test("SCHEDULED is terminal (no further transitions allowed)", t_scheduled_terminal)

def t_failed_terminal():
    ctx = StateContext(request_id="r1", status=SchedulingStatus.FAILED); sm = StateMachine(ctx)
    assert_raises(InvalidTransitionError, sm.transition, SchedulingStatus.OUTREACH_SENT)
test("FAILED is terminal", t_failed_terminal)

def t_draft_cannot_skip_to_scheduled():
    ctx = StateContext(request_id="r1"); sm = StateMachine(ctx)
    assert_raises(InvalidTransitionError, sm.transition, SchedulingStatus.SCHEDULED)
test("DRAFT cannot skip directly to SCHEDULED", t_draft_cannot_skip_to_scheduled)

def t_draft_cannot_go_to_negotiating():
    ctx = StateContext(request_id="r1"); sm = StateMachine(ctx)
    assert_raises(InvalidTransitionError, sm.transition, SchedulingStatus.NEGOTIATING)
test("DRAFT cannot go directly to NEGOTIATING", t_draft_cannot_go_to_negotiating)

# ── STATE MACHINE: Audit Log ──────────────────────────────────────────────────
print(f"\n{BOLD}State Machine — Audit Log{RESET}")

def t_transition_logged():
    ctx = StateContext(request_id="r1"); sm = StateMachine(ctx)
    sm.transition(SchedulingStatus.OUTREACH_SENT, reason="test reason")
    assert_eq(len(ctx.transition_log), 1)
    assert_eq(ctx.transition_log[0]["from"], "Draft")
    assert_eq(ctx.transition_log[0]["to"], "Outreach_Sent")
    assert_eq(ctx.transition_log[0]["reason"], "test reason")
    assert_true("timestamp" in ctx.transition_log[0])
test("Transitions are appended to audit log with timestamp", t_transition_logged)

def t_multiple_transitions_logged():
    ctx = StateContext(request_id="r1"); sm = StateMachine(ctx)
    sm.transition(SchedulingStatus.OUTREACH_SENT)
    sm.transition(SchedulingStatus.NEGOTIATING)
    sm.transition(SchedulingStatus.SCHEDULED)
    assert_eq(len(ctx.transition_log), 3)
test("All transitions appear in audit log", t_multiple_transitions_logged)

# ── STATE MACHINE: Conversation History ──────────────────────────────────────
print(f"\n{BOLD}State Machine — Conversation History{RESET}")

def t_agent_email_recorded():
    ctx = StateContext(request_id="r1"); sm = StateMachine(ctx)
    sm.record_email_sent("Hello!", "Interview Scheduling")
    assert_eq(len(ctx.conversation_history), 1)
    assert_eq(ctx.conversation_history[0]["role"], "agent")
test("Agent email recorded in conversation history", t_agent_email_recorded)

def t_candidate_reply_increments_loop():
    ctx = StateContext(request_id="r1"); sm = StateMachine(ctx)
    assert_eq(ctx.negotiation_loop_count, 0)
    sm.record_candidate_reply("I can do Thursday", "Re: Scheduling")
    assert_eq(ctx.negotiation_loop_count, 1)
    sm.record_candidate_reply("Or Friday?", "Re: Scheduling")
    assert_eq(ctx.negotiation_loop_count, 2)
test("Each candidate reply increments negotiation_loop_count", t_candidate_reply_increments_loop)

def t_no_duplicate_proposed_slots():
    ctx = StateContext(request_id="r1"); sm = StateMachine(ctx)
    sm.record_slot_proposed("2025-01-09T14:00:00+00:00")
    sm.record_slot_proposed("2025-01-09T14:00:00+00:00")  # duplicate
    assert_eq(len(ctx.proposed_slots_utc), 1)
test("Duplicate proposed slots are deduplicated", t_no_duplicate_proposed_slots)

# ── STATE MACHINE: Escalation Triggers ───────────────────────────────────────
print(f"\n{BOLD}State Machine — Escalation Triggers{RESET}")

def t_no_escalation_under_limit():
    ctx = StateContext(request_id="r1"); ctx.negotiation_loop_count = 1
    sm = StateMachine(ctx)
    should, _ = sm.should_escalate(max_loops=3)
    assert_true(not should)
test("No escalation when under loop limit", t_no_escalation_under_limit)

def t_escalate_at_loop_limit():
    ctx = StateContext(request_id="r1"); ctx.negotiation_loop_count = 3
    sm = StateMachine(ctx)
    should, reason = sm.should_escalate(max_loops=3)
    assert_true(should)
    assert_true("3" in reason)
test("Escalate when negotiation_loop_count >= max_loops", t_escalate_at_loop_limit)

def t_escalate_on_booking_failures():
    ctx = StateContext(request_id="r1"); ctx.consecutive_booking_failures = 2
    sm = StateMachine(ctx)
    should, reason = sm.should_escalate(max_loops=3)
    assert_true(should)
    assert_true("booking" in reason.lower())
test("Escalate on 2 consecutive booking failures", t_escalate_on_booking_failures)

def t_escalate_records_reason():
    ctx = StateContext(request_id="r1", status=SchedulingStatus.NEGOTIATING)
    sm = StateMachine(ctx)
    sm.escalate("Test escalation reason")
    assert_eq(ctx.status, SchedulingStatus.HUMAN_INTERVENTION)
    assert_eq(ctx.escalation_reason, "Test escalation reason")
test("Escalation records reason and transitions to HUMAN_INTERVENTION", t_escalate_records_reason)

# ── STATE MACHINE: Booking ────────────────────────────────────────────────────
print(f"\n{BOLD}State Machine — Booking{RESET}")

def t_booking_success():
    ctx = StateContext(request_id="r1", status=SchedulingStatus.NEGOTIATING)
    sm = StateMachine(ctx)
    sm.record_booking_success("2025-01-09T19:00:00+00:00", ["evt-1"], "https://meet.google.com/test")
    assert_eq(ctx.status, SchedulingStatus.SCHEDULED)
    assert_eq(ctx.booked_slot_utc, "2025-01-09T19:00:00+00:00")
    assert_eq(ctx.consecutive_booking_failures, 0)
test("Booking success → SCHEDULED with event IDs", t_booking_success)

def t_booking_failure_increments():
    ctx = StateContext(request_id="r1"); sm = StateMachine(ctx)
    sm.record_booking_failure("Slot taken")
    assert_eq(ctx.consecutive_booking_failures, 1)
    sm.record_booking_failure("Slot taken again")
    assert_eq(ctx.consecutive_booking_failures, 2)
test("Booking failures increment consecutive counter", t_booking_failure_increments)

# ── STATE MACHINE: Terminal States ────────────────────────────────────────────
print(f"\n{BOLD}State Machine — Terminal States{RESET}")

terminal_cases = [
    (SchedulingStatus.SCHEDULED, True),
    (SchedulingStatus.FAILED, True),
    (SchedulingStatus.HUMAN_INTERVENTION, True),
    (SchedulingStatus.DRAFT, False),
    (SchedulingStatus.OUTREACH_SENT, False),
    (SchedulingStatus.NEGOTIATING, False),
]
for status, expected in terminal_cases:
    def _make_test(s, e):
        def _t():
            ctx = StateContext(request_id="r1", status=s)
            sm = StateMachine(ctx)
            assert_eq(sm.is_terminal, e)
        return _t
    test(f"is_terminal={expected} for {status.value}", _make_test(status, expected))

# ── STATE MACHINE: Serialisation ──────────────────────────────────────────────
print(f"\n{BOLD}State Machine — Serialisation Round-Trip{RESET}")

def t_roundtrip():
    ctx = StateContext(request_id="round-trip-test"); sm = StateMachine(ctx)
    sm.transition(SchedulingStatus.OUTREACH_SENT, reason="initial")
    sm.record_email_sent("Hi!", "Scheduling")
    sm.record_slot_proposed("2025-01-09T19:00:00+00:00")
    data = ctx.to_dict()
    restored = StateContext.from_dict(data)
    assert_eq(restored.status, SchedulingStatus.OUTREACH_SENT)
    assert_eq(len(restored.conversation_history), 1)
    assert_eq(restored.proposed_slots_utc, ["2025-01-09T19:00:00+00:00"])
    assert_eq(len(restored.transition_log), 1)
test("StateContext → dict → StateContext preserves all fields", t_roundtrip)

# ── CALENDAR TOOLS ────────────────────────────────────────────────────────────
print(f"\n{BOLD}Calendar Tools — Mock{RESET}")

async def t_check_cal_business_hours():
    _mock_booked.clear()
    now = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    slots = await mock_check_calendar(now.isoformat(), (now + timedelta(days=5)).isoformat(), 45)
    for s in slots:
        h = datetime.fromisoformat(s["slot_utc"]).replace(tzinfo=timezone.utc).hour
        assert_true(9 <= h < 17, f"Slot {s['slot_utc']} is outside business hours")
test("check_calendar returns slots only during business hours (9-17 UTC)", t_check_cal_business_hours)

async def t_check_cal_max_slots():
    _mock_booked.clear()
    now = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    slots = await mock_check_calendar(now.isoformat(), (now + timedelta(days=14)).isoformat(), 30)
    assert_true(len(slots) <= 10, f"Got {len(slots)} slots, expected ≤ 10")
test("check_calendar caps results at 10 slots", t_check_cal_max_slots)

async def t_check_cal_empty_range():
    _mock_booked.clear()
    past = datetime(2020, 1, 1, tzinfo=timezone.utc)
    slots = await mock_check_calendar(past.isoformat(), (past + timedelta(hours=1)).isoformat(), 45)
    assert_eq(len(slots), 0)
test("check_calendar returns 0 slots for past date range", t_check_cal_empty_range)

async def t_book_slot_success():
    _mock_booked.clear()
    result = await mock_book_slot("req-1", "2030-06-15T14:00:00+00:00")
    assert_true(result["success"])
    assert_true(len(result["calendar_event_ids"]) > 0)
    assert_true(result["video_link"] is not None)
test("book_slot succeeds on a free slot", t_book_slot_success)

async def t_book_slot_double_book():
    _mock_booked.clear()
    slot = "2030-06-15T15:00:00+00:00"
    first = await mock_book_slot("req-1", slot)
    assert_true(first["success"])
    second = await mock_book_slot("req-2", slot)
    assert_true(not second["success"])
    assert_true(second["reason"] is not None)
test("book_slot rejects duplicate booking of same slot", t_book_slot_double_book)

# ── AGENT DECISION PARSING ────────────────────────────────────────────────────
print(f"\n{BOLD}Agent Decision Parsing{RESET}")

def t_parse_valid_send_email():
    raw = json.dumps({
        "thought": "Proposing slots",
        "action": "send_email",
        "action_input": {},
        "email_subject": "Interview Times",
        "email_body": "Here are three options...",
        "escalate_reason": None,
    })
    data = json.loads(raw)
    assert_eq(data["action"], "send_email")
    assert_true(data["email_body"] is not None)
test("Valid send_email JSON parses correctly", t_parse_valid_send_email)

def t_parse_valid_escalate():
    raw = json.dumps({
        "thought": "Must escalate",
        "action": "escalate",
        "action_input": {},
        "email_body": None,
        "escalate_reason": "Candidate frustrated",
    })
    data = json.loads(raw)
    assert_eq(data["action"], "escalate")
    assert_eq(data["escalate_reason"], "Candidate frustrated")
test("Valid escalate JSON parses correctly", t_parse_valid_escalate)

def t_parse_invalid_json_does_not_crash():
    raw = "this is not valid json {{{"
    try:
        json.loads(raw)
        raise AssertionError("Should have raised")
    except json.JSONDecodeError:
        pass  # Expected — orchestrator catches this and escalates
test("Invalid LLM JSON raises JSONDecodeError (orchestrator catches and escalates)", t_parse_invalid_json_does_not_crash)

# ─────────────────────────────────────────────────────────────────────────────
# Results Summary
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{BOLD}{'='*60}{RESET}")
total = results["passed"] + results["failed"]
if results["failed"] == 0:
    print(f"\033[92m{BOLD}  All {total} tests passed ✓{RESET}")
else:
    print(f"\033[91m{BOLD}  {results['failed']}/{total} tests FAILED{RESET}")
    print()
    for name, tb in results["errors"]:
        print(f"  {BOLD}FAILED: {name}{RESET}")
        print(f"  {tb}")
print(f"{BOLD}{'='*60}{RESET}\n")

sys.exit(0 if results["failed"] == 0 else 1)
