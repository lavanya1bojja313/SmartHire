"""
ORM models matching PLAN.md's database schema.

Five tables:
  organizations         — tenant / company accounts
  users                 — recruiters and admins
  scheduling_requests   — one row per candidate / interview negotiation
  interviewer_tokens    — encrypted OAuth tokens per interviewer
  audit_log             — immutable event log for every state transition

All primary keys are UUIDs. Soft-delete pattern (deleted_at) is used on
organizations and users instead of hard deletes.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# organizations
# ---------------------------------------------------------------------------

class Organization(Base):
    __tablename__ = "organizations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    slug = Column(String(100), unique=True, nullable=False, index=True)
    settings = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    users = relationship("User", back_populates="organization", lazy="raise")
    scheduling_requests = relationship(
        "SchedulingRequest", back_populates="organization", lazy="raise"
    )


# ---------------------------------------------------------------------------
# users
# ---------------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email = Column(String(320), nullable=False)
    name = Column(String(255), nullable=False)
    role = Column(
        Enum("recruiter", "admin", name="user_role_enum"),
        nullable=False,
        default="recruiter",
    )
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    organization = relationship("Organization", back_populates="users", lazy="raise")

    __table_args__ = (
        Index("ix_users_org_email", "org_id", "email", unique=True),
    )


# ---------------------------------------------------------------------------
# scheduling_requests
# ---------------------------------------------------------------------------

SCHEDULING_STATES = [
    "draft",
    "outreach_sent",
    "negotiating",
    "scheduled",
    "failed",
    "human_intervention",
    "cancelled",
]


class SchedulingRequest(Base):
    __tablename__ = "scheduling_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Recruiter who owns this request
    recruiter_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Candidate info (PII — never sent to LLM raw)
    candidate_name = Column(String(255), nullable=False)
    candidate_email = Column(String(320), nullable=False)
    position_title = Column(String(255), nullable=False)

    # State machine
    state = Column(
        Enum(*SCHEDULING_STATES, name="scheduling_state_enum"),
        nullable=False,
        default="draft",
        index=True,
    )

    # Full conversation + agent state stored as JSONB for flexibility
    conversation_history = Column(JSONB, nullable=False, default=list)
    agent_state = Column(JSONB, nullable=False, default=dict)

    # Booked slot details (null until state == "scheduled")
    scheduled_at = Column(DateTime(timezone=True), nullable=True)
    calendar_event_id = Column(String(255), nullable=True)
    meeting_link = Column(String(1024), nullable=True)

    # Metadata
    loop_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    organization = relationship(
        "Organization", back_populates="scheduling_requests", lazy="raise"
    )
    audit_entries = relationship(
        "AuditLog", back_populates="scheduling_request", lazy="raise"
    )

    __table_args__ = (
        Index("ix_sr_org_state", "org_id", "state"),
        Index("ix_sr_candidate_email", "candidate_email"),
    )


# ---------------------------------------------------------------------------
# interviewer_tokens
# ---------------------------------------------------------------------------

class InterviewerToken(Base):
    """
    Encrypted OAuth tokens for each interviewer's calendar account.
    The 'encrypted_token' column stores AES-256-GCM ciphertext from
    core/encryption.py — never store plaintext tokens.
    """
    __tablename__ = "interviewer_tokens"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    interviewer_email = Column(String(320), nullable=False)
    provider = Column(
        Enum("google", "microsoft", name="calendar_provider_enum"),
        nullable=False,
    )
    encrypted_token = Column(Text, nullable=False)   # AES-256-GCM, base64-encoded
    scopes = Column(JSONB, nullable=False, default=list)
    is_active = Column(Boolean, nullable=False, default=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        Index("ix_tokens_org_email", "org_id", "interviewer_email", unique=True),
    )


# ---------------------------------------------------------------------------
# audit_log
# ---------------------------------------------------------------------------

class AuditLog(Base):
    """
    Immutable record of every state transition and agent action.
    Rows are never updated or deleted — this is an append-only table.
    """
    __tablename__ = "audit_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    scheduling_request_id = Column(
        UUID(as_uuid=True),
        ForeignKey("scheduling_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    actor = Column(String(50), nullable=False)    # "agent" | "recruiter" | "system"
    event_type = Column(String(100), nullable=False)  # e.g. "state_transition", "email_sent"
    from_state = Column(String(50), nullable=True)
    to_state = Column(String(50), nullable=True)
    event_metadata = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    scheduling_request = relationship(
        "SchedulingRequest", back_populates="audit_entries", lazy="raise"
    )

    __table_args__ = (
        Index("ix_audit_org_created", "org_id", "created_at"),
    )
