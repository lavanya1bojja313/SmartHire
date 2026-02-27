"""
FastAPI router: /api/v1/requests

CRUD + actions for SchedulingRequest objects.

All endpoints are org-scoped — a recruiter can only see requests that belong
to their own organization. Admins can see all.

Routes:
  GET    /                       list (with filters + pagination)
  POST   /                       create a new request
  GET    /{id}                   get single request
  PATCH  /{id}                   update mutable fields
  DELETE /{id}                   soft-cancel (sets state → cancelled)
  POST   /{id}/override          recruiter manually books a slot
  POST   /{id}/escalate          recruiter escalates to human
  GET    /{id}/audit              full audit trail for a request
"""

import logging
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from security import CurrentUser, assert_same_org, get_current_user
from models import AuditLog, SchedulingRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/requests", tags=["scheduling-requests"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class SchedulingRequestCreate(BaseModel):
    candidate_name: str = Field(..., min_length=1, max_length=255)
    candidate_email: EmailStr
    position_title: str = Field(..., min_length=1, max_length=255)
    # Optional: schedule immediately or start as draft
    auto_send: bool = False


class SchedulingRequestUpdate(BaseModel):
    candidate_name: Optional[str] = Field(None, min_length=1, max_length=255)
    position_title: Optional[str] = Field(None, min_length=1, max_length=255)


class ManualOverrideRequest(BaseModel):
    scheduled_at: datetime
    meeting_link: Optional[str] = None
    note: Optional[str] = None


class EscalateRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=1000)


class SchedulingRequestResponse(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    recruiter_id: Optional[uuid.UUID]
    candidate_name: str
    candidate_email: str
    position_title: str
    state: str
    loop_count: int
    scheduled_at: Optional[datetime]
    meeting_link: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AuditLogResponse(BaseModel):
    id: uuid.UUID
    actor: str
    event_type: str
    from_state: Optional[str]
    to_state: Optional[str]
    event_metadata: dict
    created_at: datetime

    model_config = {"from_attributes": True}


class PaginatedResponse(BaseModel):
    items: list[SchedulingRequestResponse]
    total: int
    page: int
    page_size: int


# ---------------------------------------------------------------------------
# Helper: build org-scoped base query
# ---------------------------------------------------------------------------

def _base_query(user: CurrentUser):
    """Return a Select against SchedulingRequest filtered to the user's org (or all for admin)."""
    q = select(SchedulingRequest)
    if user.role != "admin":
        q = q.where(SchedulingRequest.org_id == user.org_id)
    return q


async def _get_request_or_404(
    request_id: uuid.UUID,
    db: AsyncSession,
    user: CurrentUser,
) -> SchedulingRequest:
    """Fetch a SchedulingRequest, enforce org scope, or raise 404."""
    result = await db.execute(
        select(SchedulingRequest).where(SchedulingRequest.id == request_id)
    )
    req = result.scalar_one_or_none()
    if req is None:
        raise HTTPException(status_code=404, detail="Scheduling request not found")
    assert_same_org(user, str(req.org_id))
    return req


async def _append_audit(
    db: AsyncSession,
    req: SchedulingRequest,
    actor: str,
    event_type: str,
    from_state: Optional[str] = None,
    to_state: Optional[str] = None,
    event_metadata: Optional[dict] = None,
) -> None:
    """Write an audit log entry (fire-and-forget within the current transaction)."""
    entry = AuditLog(
        org_id=req.org_id,
        scheduling_request_id=req.id,
        actor=actor,
        event_type=event_type,
        from_state=from_state,
        to_state=to_state,
        event_metadata=event_metadata or {},
    )
    db.add(entry)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/", response_model=PaginatedResponse)
async def list_requests(
    state: Optional[str] = Query(None, description="Filter by state"),
    search: Optional[str] = Query(None, description="Search candidate name or email"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    List scheduling requests for the caller's org, with optional filters and pagination.
    Returns most recently updated first.
    """
    base = _base_query(current_user)

    # Optional state filter
    if state:
        base = base.where(SchedulingRequest.state == state)

    # Optional text search across candidate name and email
    if search:
        pattern = f"%{search}%"
        base = base.where(
            or_(
                SchedulingRequest.candidate_name.ilike(pattern),
                SchedulingRequest.candidate_email.ilike(pattern),
            )
        )

    # Total count (same filters, no pagination)
    count_result = await db.execute(
        select(func.count()).select_from(base.subquery())
    )
    total = count_result.scalar_one()

    # Paginated rows
    rows = await db.execute(
        base.order_by(SchedulingRequest.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = rows.scalars().all()

    return PaginatedResponse(
        items=[SchedulingRequestResponse.model_validate(r) for r in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/", response_model=SchedulingRequestResponse, status_code=status.HTTP_201_CREATED)
async def create_request(
    body: SchedulingRequestCreate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Create a new scheduling request.

    If auto_send=True the request starts in 'outreach_sent' state and
    the Celery task will be enqueued to send the first email.
    Otherwise it starts in 'draft' for the recruiter to review first.
    """
    initial_state = "outreach_sent" if body.auto_send else "draft"

    req = SchedulingRequest(
        org_id=current_user.org_id,
        recruiter_id=current_user.user_id,
        candidate_name=body.candidate_name,
        candidate_email=body.candidate_email,
        position_title=body.position_title,
        state=initial_state,
    )
    db.add(req)
    await db.flush()  # get the UUID before audit log insert

    await _append_audit(
        db, req,
        actor="recruiter",
        event_type="request_created",
        to_state=initial_state,
        event_metadata={"auto_send": body.auto_send, "created_by": current_user.email},
    )

    logger.info(
        "SchedulingRequest created: id=%s org=%s state=%s",
        req.id, req.org_id, initial_state,
    )

    from tasks import send_outreach_email
    if body.auto_send:
        send_outreach_email.delay(str(req.id))
    return SchedulingRequestResponse.model_validate(req)


@router.get("/{request_id}", response_model=SchedulingRequestResponse)
async def get_request(
    request_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Fetch a single scheduling request by ID."""
    req = await _get_request_or_404(request_id, db, current_user)
    return SchedulingRequestResponse.model_validate(req)


@router.patch("/{request_id}", response_model=SchedulingRequestResponse)
async def update_request(
    request_id: uuid.UUID,
    body: SchedulingRequestUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Update mutable fields (candidate name, position title).
    State transitions are NOT allowed here — use dedicated action endpoints.
    Requests in terminal states (scheduled, failed, cancelled) cannot be edited.
    """
    req = await _get_request_or_404(request_id, db, current_user)

    TERMINAL_STATES = {"scheduled", "failed", "cancelled"}
    if req.state in TERMINAL_STATES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot edit a request in state '{req.state}'",
        )

    changes = {}
    if body.candidate_name is not None:
        changes["candidate_name"] = (req.candidate_name, body.candidate_name)
        req.candidate_name = body.candidate_name
    if body.position_title is not None:
        changes["position_title"] = (req.position_title, body.position_title)
        req.position_title = body.position_title

    if changes:
        await _append_audit(
            db, req,
            actor="recruiter",
            event_type="request_updated",
            event_metadata={"changes": {k: {"from": v[0], "to": v[1]} for k, v in changes.items()}},
        )

    return SchedulingRequestResponse.model_validate(req)


@router.delete("/{request_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_request(
    request_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Cancel a scheduling request (sets state → cancelled).
    Already-scheduled requests cannot be cancelled via this endpoint —
    use the calendar service to remove the event first.
    """
    req = await _get_request_or_404(request_id, db, current_user)

    if req.state == "scheduled":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot cancel a scheduled request. Remove the calendar event first.",
        )
    if req.state == "cancelled":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Request is already cancelled",
        )

    prev_state = req.state
    req.state = "cancelled"

    await _append_audit(
        db, req,
        actor="recruiter",
        event_type="request_cancelled",
        from_state=prev_state,
        to_state="cancelled",
        event_metadata={"cancelled_by": current_user.email},
    )

    logger.info("Request %s cancelled by %s", req.id, current_user.email)


@router.post("/{request_id}/override", response_model=SchedulingRequestResponse)
async def manual_override(
    request_id: uuid.UUID,
    body: ManualOverrideRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Recruiter manually books a slot, bypassing the agent.
    This moves the request directly to 'scheduled' from any non-terminal state.
    """
    req = await _get_request_or_404(request_id, db, current_user)

    TERMINAL_STATES = {"scheduled", "cancelled", "failed"}
    if req.state in TERMINAL_STATES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot override a request in state '{req.state}'",
        )

    prev_state = req.state
    req.state = "scheduled"
    req.scheduled_at = body.scheduled_at
    req.meeting_link = body.meeting_link

    await _append_audit(
        db, req,
        actor="recruiter",
        event_type="manual_override",
        from_state=prev_state,
        to_state="scheduled",
        event_metadata={
            "scheduled_at": body.scheduled_at.isoformat(),
            "meeting_link": body.meeting_link,
            "note": body.note,
            "overridden_by": current_user.email,
        },
    )

    logger.info(
        "Request %s manually scheduled by %s for %s",
        req.id, current_user.email, body.scheduled_at,
    )
    return SchedulingRequestResponse.model_validate(req)


@router.post("/{request_id}/escalate", response_model=SchedulingRequestResponse)
async def escalate_request(
    request_id: uuid.UUID,
    body: EscalateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Manually escalate to human intervention.
    Can be triggered by a recruiter at any time, or called internally
    by the agent when it detects a stuck loop or negative sentiment.
    """
    req = await _get_request_or_404(request_id, db, current_user)

    TERMINAL_STATES = {"scheduled", "cancelled", "failed", "human_intervention"}
    if req.state in TERMINAL_STATES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot escalate a request in state '{req.state}'",
        )

    prev_state = req.state
    req.state = "human_intervention"

    await _append_audit(
        db, req,
        actor="recruiter",
        event_type="escalated",
        from_state=prev_state,
        to_state="human_intervention",
        event_metadata={"reason": body.reason, "escalated_by": current_user.email},
    )

    # TODO Phase 4: send Slack/email alert to recruiter
    logger.info("Request %s escalated: %s", req.id, body.reason)
    return SchedulingRequestResponse.model_validate(req)


@router.post("/{request_id}/resolve", response_model=SchedulingRequestResponse)
async def resolve_request(
    request_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Recruiter resolves an escalated request and returns it to the agent.
    Moves state from 'human_intervention' to 'negotiating'.
    """
    req = await _get_request_or_404(request_id, db, current_user)

    if req.state != "human_intervention":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Only requests in 'human_intervention' can be resolved",
        )

    req.state = "negotiating"

    await _append_audit(
        db, req,
        actor="recruiter",
        event_type="resolved",
        from_state="human_intervention",
        to_state="negotiating",
        event_metadata={"resolved_by": current_user.email},
    )

    logger.info("Request %s resolved back to negotiating by %s", req.id, current_user.email)
    return SchedulingRequestResponse.model_validate(req)


@router.get("/{request_id}/audit", response_model=list[AuditLogResponse])
async def get_audit_trail(
    request_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Return the full chronological audit trail for a scheduling request."""
    # Verify access via the parent request
    await _get_request_or_404(request_id, db, current_user)

    result = await db.execute(
        select(AuditLog)
        .where(AuditLog.scheduling_request_id == request_id)
        .order_by(AuditLog.created_at.asc())
    )
    entries = result.scalars().all()
    return [AuditLogResponse.model_validate(e) for e in entries]


# ─────────────────────────────────────────────────────────────────────────────
# Email Activity Log (dev mode in-memory, production uses SendGrid dashboard)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/emails/log", tags=["emails"])
async def list_email_log(
    request_id: Optional[str] = Query(None, description="Filter by scheduling request UUID"),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Return all emails sent by the agent (dev-mode in-memory log).
    In production, SendGrid/SES have their own activity dashboards.
    """
    from scheduling_email import EMAIL_LOG
    emails = EMAIL_LOG.copy()
    if request_id:
        emails = [e for e in emails if e.get("request_id") == request_id]
    # Return most recent first
    return list(reversed(emails))

