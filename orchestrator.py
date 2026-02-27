"""
app/agent/orchestrator.py

The Agent Orchestrator — the "Brain" that drives the ReAct loop.

Responsibilities:
  1. Build the LLM prompt from conversation history + system instructions.
  2. Call the LLM (primary: GPT-4o, fallback: Claude via LiteLLM).
  3. Parse the structured JSON response.
  4. Execute the requested tool (check_calendar / book_slot).
  5. Feed the tool's Observation back to the LLM for the next step.
  6. Decide when to send an email or escalate.
  7. Update the StateContext after every action.

The orchestrator is intentionally stateless — all state lives in StateContext,
which is persisted to the DB by the Celery worker that calls this class.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from schemas import (
    AgentAction,
    AgentDecision,
    BookSlotInput,
    BookSlotResult,
    CalendarCheckResult,
    CheckCalendarInput,
)
from system_prompt import PROMPT_VERSION, build_system_prompt
from state_machine import SchedulingStatus, StateMachine, StateContext
from calendar_tools import book_slot, check_calendar
from config import get_settings
from logging_config import get_logger

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Data Transfer Objects — passed in by the Celery worker
# ─────────────────────────────────────────────────────────────────────────────

class SchedulingContext:
    """
    Everything the agent needs to know about a scheduling request.
    Assembled by the Celery worker from the DB before calling the orchestrator.
    """
    def __init__(
        self,
        request_id: str,
        candidate_email: str,
        candidate_timezone: str,          # e.g. "America/New_York"
        interviewer_ids: list[str],
        duration_minutes: int,
        search_window_start: str,         # ISO-8601 UTC
        search_window_end: str,           # ISO-8601 UTC
        state_context: StateContext,
        use_mock: bool = True,            # False in Phase 2+
    ):
        self.request_id          = request_id
        self.candidate_email     = candidate_email
        self.candidate_timezone  = candidate_timezone
        self.interviewer_ids     = interviewer_ids
        self.duration_minutes    = duration_minutes
        self.search_window_start = search_window_start
        self.search_window_end   = search_window_end
        self.state_context       = state_context
        self.use_mock            = use_mock


class OrchestratorResult:
    """What the orchestrator returns to the Celery worker after each turn."""
    def __init__(
        self,
        action_taken: AgentAction,
        email_body: Optional[str],
        email_subject: Optional[str],
        escalated: bool,
        escalation_reason: Optional[str],
        updated_context: StateContext,
    ):
        self.action_taken       = action_taken
        self.email_body         = email_body
        self.email_subject      = email_subject
        self.escalated          = escalated
        self.escalation_reason  = escalation_reason
        self.updated_context    = updated_context


# ─────────────────────────────────────────────────────────────────────────────
# LLM Client Wrapper (with primary → fallback routing)
# ─────────────────────────────────────────────────────────────────────────────

class LLMClient:
    """
    Thin wrapper around LiteLLM that routes GPT-4o → Claude on failure.
    LiteLLM gives us a unified interface so swapping models is trivial.
    """

    def __init__(self):
        self.settings = get_settings()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def complete(self, messages: list[dict], response_format: dict | None = None) -> str:
        """
        Call the primary LLM, falling back to the secondary if it errors.

        Returns raw JSON string from the model.
        """
        try:
            return await self._call_litellm(
                model=self.settings.llm_primary_model,
                messages=messages,
                response_format=response_format,
            )
        except Exception as primary_err:
            logger.warning(
                "llm.primary_failed_falling_back",
                model=self.settings.llm_primary_model,
                error=str(primary_err),
            )
            return await self._call_litellm(
                model=self.settings.llm_fallback_model,
                messages=messages,
                response_format=response_format,
            )

    async def _call_litellm(
        self,
        model: str,
        messages: list[dict],
        response_format: dict | None,
    ) -> str:
        """Make the actual LiteLLM call and return the raw content string."""
        import litellm

        # Set API keys from config (LiteLLM reads env vars too, but explicit is safer)
        litellm.openai_key    = self.settings.openai_api_key
        litellm.anthropic_key = self.settings.anthropic_api_key

        kwargs: dict = {
            "model": model,
            "messages": messages,
            "temperature": 0.2,      # Low temperature = more consistent structured output
            "max_tokens": 1500,
        }
        if response_format:
            kwargs["response_format"] = response_format

        logger.info("llm.calling", model=model, messages_count=len(messages))
        response = await litellm.acompletion(**kwargs)

        content = response.choices[0].message.content
        logger.info("llm.response_received", model=model, content_length=len(content or ""))
        return content or ""


# ─────────────────────────────────────────────────────────────────────────────
# PII Scrubber (keeps candidate data out of LLM context)
# ─────────────────────────────────────────────────────────────────────────────

class PIIScrubber:
    """
    Uses Microsoft Presidio to anonymise PII before sending to the LLM.
    Phone numbers, SSNs, and other non-scheduling data are replaced with
    placeholders like <PHONE_NUMBER>.

    The candidate's email is kept because the agent needs it to address them.
    """

    def __init__(self):
        self._analyzer = None    # Lazy load — Presidio has a slow startup
        self._anonymizer = None

    def _ensure_loaded(self):
        if self._analyzer is None:
            from presidio_analyzer import AnalyzerEngine
            from presidio_anonymizer import AnonymizerEngine
            self._analyzer  = AnalyzerEngine()
            self._anonymizer = AnonymizerEngine()

    def scrub(self, text: str) -> str:
        """Return text with PII replaced by entity-type placeholders."""
        try:
            self._ensure_loaded()
            results = self._analyzer.analyze(
                text=text,
                language="en",
                # Keep email so the agent can greet the candidate by name
                entities=["PHONE_NUMBER", "US_SSN", "CREDIT_CARD", "IP_ADDRESS"],
            )
            if not results:
                return text
            anonymized = self._anonymizer.anonymize(text=text, analyzer_results=results)
            return anonymized.text
        except Exception as e:
            # PII scrubbing failure should never crash the agent.
            # Log it and return the original text — better than dropping the email.
            logger.error("pii_scrubber.failed", error=str(e))
            return text


# ─────────────────────────────────────────────────────────────────────────────
# Main Orchestrator
# ─────────────────────────────────────────────────────────────────────────────

class AgentOrchestrator:
    """
    Drives the ReAct loop for a single turn (one candidate email = one turn).

    A "turn" ends when the agent decides to either:
      a) Send an email to the candidate (possibly after tool calls)
      b) Escalate to a human recruiter

    The Celery worker persists the updated StateContext to the DB after
    each turn, so the next inbound email resumes from the correct state.
    """

    def __init__(self, use_mock: bool = True):
        self.settings    = get_settings()
        self.llm         = LLMClient()
        self.pii_scrubber = PIIScrubber()
        self.use_mock    = use_mock

    async def run_turn(self, sched_ctx: SchedulingContext) -> OrchestratorResult:
        """
        Execute one full agent turn for the given scheduling context.

        This is the entry point called by the Celery worker.
        """
        sm = StateMachine(sched_ctx.state_context)

        logger.info(
            "orchestrator.turn_start",
            request_id=sched_ctx.request_id,
            status=sm.context.status.value,
            loop=sm.context.negotiation_loop_count,
        )

        # ── 1. Pre-turn escalation check ─────────────────────────────────────
        should_esc, esc_reason = sm.should_escalate(self.settings.agent_max_negotiation_loops)
        if should_esc:
            return self._do_escalate(sm, esc_reason)

        # ── 2. Transition to correct state ────────────────────────────────────
        if sm.context.status == SchedulingStatus.DRAFT:
            sm.transition(SchedulingStatus.OUTREACH_SENT, reason="Initial outreach")
        elif sm.context.status == SchedulingStatus.OUTREACH_SENT:
            sm.transition(SchedulingStatus.NEGOTIATING, reason="Candidate replied")
        # NEGOTIATING → NEGOTIATING is a self-loop (already valid)

        # ── 3. Build messages for the LLM ─────────────────────────────────────
        messages = self._build_messages(sched_ctx, sm.context)

        # ── 4. ReAct loop — up to 5 inner tool-call steps per turn ───────────
        MAX_INNER_STEPS = 5
        for step in range(MAX_INNER_STEPS):
            raw_response = await self.llm.complete(
                messages=messages,
                response_format={"type": "json_object"},
            )

            decision = self._parse_decision(raw_response)

            logger.info(
                "orchestrator.agent_decision",
                step=step,
                action=decision.action,
                thought_preview=decision.thought[:100],
            )

            # ── 4a. Tool: check_calendar ──────────────────────────────────────
            if decision.action == AgentAction.CHECK_CALENDAR:
                tool_input   = CheckCalendarInput(**decision.action_input)
                tool_result  = await check_calendar(
                    interviewer_ids=tool_input.interviewer_ids,
                    date_range_start=tool_input.date_range_start,
                    date_range_end=tool_input.date_range_end,
                    duration_minutes=tool_input.duration_minutes,
                    use_mock=self.use_mock,
                )
                # Track proposed slots to avoid re-proposing same times
                for slot in tool_result.available_slots[:3]:
                    sm.record_slot_proposed(slot.slot_utc)

                # Append Observation to the message thread for next LLM step
                messages.append({
                    "role": "assistant",
                    "content": raw_response,
                })
                messages.append({
                    "role": "user",
                    "content": f"Observation (check_calendar result):\n{tool_result.model_dump_json(indent=2)}",
                })
                continue   # next inner step

            # ── 4b. Tool: book_slot ───────────────────────────────────────────
            elif decision.action == AgentAction.BOOK_SLOT:
                tool_input  = BookSlotInput(**decision.action_input)
                tool_result = await book_slot(
                    request_id=tool_input.request_id,
                    slot_utc=tool_input.slot_utc,
                    interviewer_ids=tool_input.interviewer_ids,
                    candidate_email=tool_input.candidate_email,
                    duration_minutes=tool_input.duration_minutes,
                    use_mock=self.use_mock,
                )
                if tool_result.success:
                    sm.record_booking_success(
                        slot_utc=tool_input.slot_utc,
                        event_ids=tool_result.calendar_event_ids,
                        video_link=tool_result.video_link,
                    )
                    # Booking succeeded — generate confirmation email and exit loop
                    confirm_email = self._build_confirmation_email(
                        slot_utc=tool_input.slot_utc,
                        candidate_tz=sched_ctx.candidate_timezone,
                        video_link=tool_result.video_link,
                        duration_minutes=sched_ctx.duration_minutes,
                    )
                    sm.record_email_sent(confirm_email["body"], confirm_email["subject"])
                    return OrchestratorResult(
                        action_taken=AgentAction.SEND_EMAIL,
                        email_body=confirm_email["body"],
                        email_subject=confirm_email["subject"],
                        escalated=False,
                        escalation_reason=None,
                        updated_context=sm.context,
                    )
                else:
                    sm.record_booking_failure(tool_result.reason or "Unknown failure")
                    # Check if we've hit the booking failure escalation threshold
                    should_esc, esc_reason = sm.should_escalate(
                        self.settings.agent_max_negotiation_loops
                    )
                    if should_esc:
                        return self._do_escalate(sm, esc_reason)

                    # Feed failure back as Observation and let agent try another slot
                    messages.append({"role": "assistant", "content": raw_response})
                    messages.append({
                        "role": "user",
                        "content": f"Observation (book_slot result):\n{tool_result.model_dump_json(indent=2)}\nPlease try an alternative slot.",
                    })
                    continue

            # ── 4c. Escalate ──────────────────────────────────────────────────
            elif decision.action == AgentAction.ESCALATE:
                reason = decision.escalate_reason or "Agent requested escalation"
                return self._do_escalate(sm, reason)

            # ── 4d. Send Email (final action for this turn) ───────────────────
            elif decision.action == AgentAction.SEND_EMAIL:
                subject = decision.email_subject or "Interview Scheduling"
                body    = decision.email_body or ""
                sm.record_email_sent(body, subject)
                return OrchestratorResult(
                    action_taken=AgentAction.SEND_EMAIL,
                    email_body=body,
                    email_subject=subject,
                    escalated=False,
                    escalation_reason=None,
                    updated_context=sm.context,
                )

        # If we exhaust inner steps without a terminal action, escalate.
        logger.error(
            "orchestrator.max_inner_steps_exceeded",
            request_id=sched_ctx.request_id,
        )
        return self._do_escalate(sm, "Agent exceeded maximum inner reasoning steps.")

    # ── Private helpers ───────────────────────────────────────────────────────

    def _build_messages(
        self,
        sched_ctx: SchedulingContext,
        state: StateContext,
    ) -> list[dict]:
        """
        Construct the full message array for the LLM.
        Includes: system prompt, prior conversation history, and a user
        turn describing the current scheduling context.
        """
        system = build_system_prompt(max_loops=self.settings.agent_max_negotiation_loops)

        # Build a context summary injected as the first user message
        context_summary = (
            f"Scheduling Request ID: {sched_ctx.request_id}\n"
            f"Candidate Email: {sched_ctx.candidate_email}\n"
            f"Candidate Timezone: {sched_ctx.candidate_timezone}\n"
            f"Interview Duration: {sched_ctx.duration_minutes} minutes\n"
            f"Search Window: {sched_ctx.search_window_start} to {sched_ctx.search_window_end} (UTC)\n"
            f"Interviewer IDs: {', '.join(sched_ctx.interviewer_ids)}\n"
            f"Negotiation Loop: {state.negotiation_loop_count + 1} "
            f"of {self.settings.agent_max_negotiation_loops}\n"
            f"Already Proposed Slots: {', '.join(state.proposed_slots_utc) or 'None'}"
        )

        messages: list[dict] = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"[SCHEDULING CONTEXT]\n{context_summary}"},
        ]

        # Replay conversation history so the LLM has full context
        for entry in state.conversation_history:
            role    = "assistant" if entry["role"] == "agent" else "user"
            content = f"[{entry['role'].upper()} EMAIL — {entry['timestamp']}]\n{entry['body']}"
            # Scrub PII from candidate messages before sending to LLM
            if entry["role"] == "candidate":
                content = self.pii_scrubber.scrub(content)
            messages.append({"role": role, "content": content})

        return messages

    def _parse_decision(self, raw: str) -> AgentDecision:
        """
        Parse the LLM's JSON response into an AgentDecision.
        Falls back to an escalation if the JSON is malformed.
        """
        try:
            data = json.loads(raw)
            return AgentDecision(**data)
        except Exception as e:
            logger.error("orchestrator.parse_failed", error=str(e), raw=raw[:200])
            # Return a safe escalation decision instead of crashing
            return AgentDecision(
                thought=f"Failed to parse LLM response: {e}",
                action=AgentAction.ESCALATE,
                escalate_reason="Agent produced invalid structured output.",
            )

    def _do_escalate(self, sm: StateMachine, reason: str) -> OrchestratorResult:
        """Transition to HUMAN_INTERVENTION and return escalation result."""
        sm.escalate(reason)
        escalation_body = (
            "Thank you for your patience. I'm connecting you with a member of our "
            "recruiting team who will reach out shortly to find a time that works."
        )
        sm.record_email_sent(escalation_body, subject="Your Interview Request")
        return OrchestratorResult(
            action_taken=AgentAction.ESCALATE,
            email_body=escalation_body,
            email_subject="Your Interview Request",
            escalated=True,
            escalation_reason=reason,
            updated_context=sm.context,
        )

    def _build_confirmation_email(
        self,
        slot_utc: str,
        candidate_tz: str,
        video_link: str | None,
        duration_minutes: int,
    ) -> dict[str, str]:
        """Generate a polished confirmation email body after successful booking."""
        from zoneinfo import ZoneInfo
        slot_dt  = datetime.fromisoformat(slot_utc).replace(tzinfo=timezone.utc)
        local_dt = slot_dt.astimezone(ZoneInfo(candidate_tz))
        tz_abbr  = local_dt.strftime("%Z")
        local_str = local_dt.strftime("%A, %B %-d at %-I:%M %p") + f" {tz_abbr}"
        utc_str   = slot_dt.strftime("%-I:%M %p UTC")

        video_section = (
            f"\n\nVideo link: {video_link}" if video_link
            else "\n\nA calendar invite with video conference details will follow shortly."
        )

        body = (
            f"Great news — your interview has been confirmed!\n\n"
            f"Date & Time: {local_str} ({utc_str})\n"
            f"Duration: {duration_minutes} minutes"
            f"{video_section}\n\n"
            f"You'll receive a calendar invite shortly. "
            f"If you need to reschedule, just reply to this email.\n\n"
            f"Looking forward to speaking with you!"
        )
        return {"subject": "Interview Confirmed ✓", "body": body}
