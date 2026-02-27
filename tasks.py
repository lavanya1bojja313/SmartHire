"""
Celery tasks — flat layout version.
All imports use direct module names, not app.worker.x package paths.
"""

import logging
import os
import asyncio
from datetime import datetime, timezone, timedelta
from celery import shared_task
from sqlalchemy import select

from celery_app import celery_app
from database import db_session
from models import SchedulingRequest, AuditLog
from state_machine import StateContext, SchedulingStatus
from orchestrator import AgentOrchestrator, SchedulingContext
from scheduling_email import EmailService

logger = logging.getLogger(__name__)


async def _run_agent_async(request_id: str, candidate_reply: str | None = None) -> None:
    """Async core logic: load DB, step orchestrator, save DB, send email."""
    async with db_session() as session:
        # 1. Load Request
        result = await session.execute(
            select(SchedulingRequest).where(SchedulingRequest.id == request_id)
        )
        req = result.scalar_one_or_none()
        if not req:
            logger.error("SchedulingRequest %s not found.", request_id)
            return

        # 2. Build Agent State Context
        if req.agent_state:
            state_ctx = StateContext.from_dict(req.agent_state)
        else:
            state_ctx = StateContext(request_id=str(req.id), status=SchedulingStatus(req.state))

        # Add candidate reply to context if provided
        if candidate_reply:
            state_ctx.conversation_history.append({
                "role": "candidate",
                "content": candidate_reply,
                "timestamp": datetime.now(timezone.utc).isoformat()
            })
            state_ctx.negotiation_loop_count += 1

        # 3. Detect if real Google Calendar credentials are available
        from sqlalchemy import select as sa_select
        from models import InterviewerToken
        token_check = await session.execute(
            sa_select(InterviewerToken).where(
                InterviewerToken.provider == "google",
                InterviewerToken.is_active == True,  # noqa
            )
        )
        has_real_calendar = token_check.scalar_one_or_none() is not None
        use_mock_calendar = not has_real_calendar

        if has_real_calendar:
            logger.info("Real Google Calendar token detected — using live calendar for request %s", request_id)
        else:
            logger.info("No Google token found — using mock calendar for request %s", request_id)

        # 4. Build Scheduling Context
        now_utc = datetime.now(timezone.utc)
        sched_ctx = SchedulingContext(
            request_id=str(req.id),
            candidate_email=req.candidate_email,
            candidate_timezone="America/New_York",  # Defaulting; will be extracted from profile later
            interviewer_ids=[str(req.recruiter_id)] if req.recruiter_id else [],
            duration_minutes=45,
            search_window_start=now_utc.isoformat(),
            search_window_end=(now_utc + timedelta(days=14)).isoformat(),
            state_context=state_ctx,
            use_mock=use_mock_calendar,
        )

        # 4. Run Orchestrator
        orchestrator = AgentOrchestrator(use_mock=False)
        turn_result = await orchestrator.run_turn(sched_ctx)

        # 5. Persist updated State
        updated_state = turn_result.updated_context
        req.agent_state = updated_state.to_dict()
        old_state = req.state
        req.state = updated_state.status.value
        req.loop_count = updated_state.negotiation_loop_count
        
        # Write audit logs for any transitions
        if old_state != req.state:
            log_entry = AuditLog(
                org_id=req.org_id,
                scheduling_request_id=req.id,
                actor="agent",
                event_type="state_transition",
                from_state=old_state,
                to_state=req.state,
                event_metadata={"reason": turn_result.escalation_reason or "orchestrator_decision"}
            )
            session.add(log_entry)

        # 6. Send Email if necessary
        if turn_result.email_body:
            email_service = EmailService()
            try:
                await email_service.send(
                    to_email=req.candidate_email,
                    subject=turn_result.email_subject or "Interview Scheduling",
                    body=turn_result.email_body,
                    request_id=str(req.id),
                )
                
                # Audit email sent
                email_log = AuditLog(
                    org_id=req.org_id,
                    scheduling_request_id=req.id,
                    actor="agent",
                    event_type="email_sent",
                    from_state=req.state,
                    to_state=req.state,
                    event_metadata={"subject": turn_result.email_subject}
                )
                session.add(email_log)
                
            except Exception as e:
                logger.error("Failed to send email for request %s: %s", request_id, e)

        # Apply DB transactions
        await session.commit()
        logger.info("Agent processed request %s -> %s", request_id, req.state)


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="tasks.process_candidate_reply",
)
def process_candidate_reply(self, scheduling_request_id: str, email_body: str):
    """
    Process an inbound candidate email reply.
    Runs the agent orchestrator and sends the next email.
    """
    try:
        logger.info("Processing reply for request %s", scheduling_request_id)
        asyncio.run(_run_agent_async(scheduling_request_id, candidate_reply=email_body))
        return {"status": "ok", "request_id": scheduling_request_id}

    except Exception as exc:
        logger.error("Task failed for request %s: %s", scheduling_request_id, exc)
        raise self.retry(exc=exc)


@celery_app.task(name="tasks.send_outreach_email")
def send_outreach_email(scheduling_request_id: str):
    """Send the first outreach email to a candidate."""
    logger.info("Sending outreach for request %s", scheduling_request_id)
    asyncio.run(_run_agent_async(scheduling_request_id, candidate_reply=None))
    return {"status": "sent", "request_id": scheduling_request_id}


@celery_app.task(name="tasks.health_check")
def health_check():
    """Simple task to verify the worker is alive."""
    return {"status": "healthy"}
