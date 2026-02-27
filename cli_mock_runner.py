"""
cli_mock_runner.py

Phase 1 deliverable: A CLI simulation of the full agent negotiation loop.

Run this to prove the agent can successfully negotiate an interview time
without any real calendar credentials, email accounts, or a running database.

Usage:
    python cli_mock_runner.py

    # Or with a custom candidate timezone:
    CANDIDATE_TZ=America/Chicago python cli_mock_runner.py

The CLI puts YOU in the role of the candidate — type natural language replies
and watch the agent reason, check availability, and book a slot.

Scenarios to try:
    "I can't do Monday, how about Wednesday afternoon?"
    "Thursday at 2pm EST works for me"
    "I'm busy all week, can we do next week?"
    "I'd like to speak with a human please"
    (Just keep saying no to trigger escalation)
"""

import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Allow running from repo root without installing the package
sys.path.insert(0, str(Path(__file__).parent))

import structlog

# ── Bootstrap minimal config so imports work without a real .env ─────────────
os.environ.setdefault("POSTGRES_PASSWORD", "mock")
os.environ.setdefault("REDIS_PASSWORD", "mock")
os.environ.setdefault("SECRET_KEY", "mock_secret_key_for_cli_testing_only_32c")
os.environ.setdefault("OPENAI_API_KEY", os.environ.get("OPENAI_API_KEY", ""))
os.environ.setdefault("ANTHROPIC_API_KEY", os.environ.get("ANTHROPIC_API_KEY", ""))

from config import get_settings
from logging_config import configure_logging
from orchestrator import AgentOrchestrator, SchedulingContext
from state_machine import StateContext, SchedulingStatus, StateMachine

configure_logging()
logger = structlog.get_logger("cli_runner")

# ─────────────────────────────────────────────────────────────────────────────
# Mock LLM that responds without real API keys (for demo / CI environments)
# ─────────────────────────────────────────────────────────────────────────────

class MockLLMOrchestrator(AgentOrchestrator):
    """
    Overrides the LLM call with a deterministic scripted agent for CI / demo.
    Uses real API if OPENAI_API_KEY or ANTHROPIC_API_KEY is set.
    """

    def __init__(self):
        super().__init__(use_mock=True)
        self._turn = 0
        self._has_real_api = bool(
            os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
        )

    async def _scripted_decision(self, candidate_message: str, proposed_slots: list[str]) -> str:
        """
        Simple scripted responses that simulate what the LLM would do.
        Used when no API key is available.
        """
        self._turn += 1
        lower = candidate_message.lower()

        # Candidate wants a human
        if any(w in lower for w in ["human", "person", "someone else", "real"]):
            return json.dumps({
                "thought": "Candidate requested a human agent. Must escalate immediately.",
                "action": "escalate",
                "action_input": {},
                "email_body": None,
                "escalate_reason": "Candidate explicitly requested human contact.",
            })

        # Candidate mentions a specific day — try to check calendar
        if self._turn == 1 or ("thursday" in lower or "wednesday" in lower or "works" in lower):
            now = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0)
            start = (now + timedelta(days=1)).isoformat()
            end   = (now + timedelta(days=8)).isoformat()
            return json.dumps({
                "thought": "Candidate provided time preference. Checking calendar for available slots.",
                "action": "check_calendar",
                "action_input": {
                    "interviewer_ids": ["interviewer-001"],
                    "date_range_start": start,
                    "date_range_end": end,
                    "duration_minutes": 45,
                },
                "email_body": None,
                "escalate_reason": None,
            })

        # After checking calendar, propose slots
        now = datetime.now(timezone.utc).replace(hour=14, minute=0, second=0, microsecond=0)
        slot = (now + timedelta(days=3)).isoformat()
        return json.dumps({
            "thought": "Calendar returned slots. Proposing the best 3 options to candidate.",
            "action": "send_email",
            "action_input": {},
            "email_subject": "Interview Scheduling — Available Times",
            "email_body": (
                f"Thanks for getting back to me! Based on your preference, "
                f"I have the following slots available:\n\n"
                f"1. Thursday at 2:00 PM EST\n"
                f"2. Thursday at 3:30 PM EST\n"
                f"3. Friday at 10:00 AM EST\n\n"
                f"Which works best for you? Just reply with your preference."
            ),
            "escalate_reason": None,
        })


# ─────────────────────────────────────────────────────────────────────────────
# Colourised terminal output helpers
# ─────────────────────────────────────────────────────────────────────────────

CYAN   = "\033[96m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def print_agent(text: str):
    print(f"\n{CYAN}{BOLD}🤖 Agent:{RESET}")
    for line in text.split("\n"):
        print(f"   {CYAN}{line}{RESET}")

def print_system(text: str):
    print(f"\n{YELLOW}⚙  System: {text}{RESET}")

def print_success(text: str):
    print(f"\n{GREEN}{BOLD}✅ {text}{RESET}")

def print_error(text: str):
    print(f"\n{RED}{BOLD}❌ {text}{RESET}")

def print_separator():
    print(f"\n{YELLOW}{'─' * 60}{RESET}")


# ─────────────────────────────────────────────────────────────────────────────
# Main CLI Loop
# ─────────────────────────────────────────────────────────────────────────────

async def run_cli_simulation():
    print(f"\n{BOLD}{'=' * 60}{RESET}")
    print(f"{BOLD}  Autonomous Interview Scheduler — CLI Mock Runner{RESET}")
    print(f"{BOLD}  Phase 1: Agent Brain Test{RESET}")
    print(f"{BOLD}{'=' * 60}{RESET}")
    print("\nYou are playing the role of the candidate.")
    print("The agent will negotiate an interview time with you.")
    print("Type 'quit' to exit.\n")

    # ── Set up a mock scheduling request ─────────────────────────────────────
    request_id    = str(uuid.uuid4())
    candidate_tz  = os.environ.get("CANDIDATE_TZ", "America/New_York")
    now_utc       = datetime.now(timezone.utc)
    window_start  = now_utc.isoformat()
    window_end    = (now_utc + timedelta(days=7)).isoformat()

    state_context = StateContext(request_id=request_id)
    orchestrator  = MockLLMOrchestrator()

    sched_ctx = SchedulingContext(
        request_id         = request_id,
        candidate_email    = "candidate@example.com",
        candidate_timezone = candidate_tz,
        interviewer_ids    = ["interviewer-001"],
        duration_minutes   = 45,
        search_window_start= window_start,
        search_window_end  = window_end,
        state_context      = state_context,
        use_mock           = True,
    )

    print_system(f"Request ID: {request_id}")
    print_system(f"Candidate timezone: {candidate_tz}")
    print_system(f"Interview duration: 45 minutes")
    print_system(f"Search window: Next 7 days")

    # ── Initial outreach (no candidate input yet) ────────────────────────────
    print_separator()
    print_system("Agent sending initial outreach email...")

    sm = StateMachine(sched_ctx.state_context)

    result = await orchestrator.run_turn(sched_ctx)
    sched_ctx.state_context = result.updated_context   # persist updated state

    if result.escalated:
        print_error(f"Agent escalated immediately: {result.escalation_reason}")
        return

    if result.email_body:
        print_agent(result.email_body)

    # ── Negotiation loop ─────────────────────────────────────────────────────
    while not StateMachine(sched_ctx.state_context).is_terminal:
        print_separator()
        try:
            candidate_reply = input(f"\n{BOLD}You (candidate):{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break

        if candidate_reply.lower() in ("quit", "exit", "q"):
            print("\nSession ended by user.")
            break

        if not candidate_reply:
            print("(Please type a response)")
            continue

        # Record candidate reply in state context
        sm_local = StateMachine(sched_ctx.state_context)
        sm_local.record_candidate_reply(
            email_body=candidate_reply,
            subject="Re: Interview Scheduling",
        )
        sched_ctx.state_context = sm_local.context

        print_system(f"Processing reply (loop {sched_ctx.state_context.negotiation_loop_count})...")

        # Run another agent turn
        result = await orchestrator.run_turn(sched_ctx)
        sched_ctx.state_context = result.updated_context

        if result.escalated:
            print_separator()
            print_error(f"ESCALATED — {result.escalation_reason}")
            print_agent(result.email_body or "Connecting you with our team...")
            print_system("A human recruiter has been alerted (Slack notification would fire here).")
            break

        if result.email_body:
            print_agent(result.email_body)

        # Check terminal states
        status = sched_ctx.state_context.status
        if status == SchedulingStatus.SCHEDULED:
            print_separator()
            print_success("INTERVIEW BOOKED SUCCESSFULLY!")
            print_success(f"Slot (UTC): {sched_ctx.state_context.booked_slot_utc}")
            print_success(f"Event IDs: {sched_ctx.state_context.calendar_event_ids}")
            print_success(f"Video Link: {sched_ctx.state_context.video_link}")
            break

    # ── Final state summary ──────────────────────────────────────────────────
    print_separator()
    print_system("FINAL STATE:")
    ctx_dict = sched_ctx.state_context.to_dict()
    print(json.dumps(ctx_dict, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(run_cli_simulation())
