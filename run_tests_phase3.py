#!/usr/bin/env python3
"""
Phase 3 standalone tests.
No database or external services required — all logic is tested in isolation.

Run: python3 run_tests_phase3.py
"""

import sys
import traceback
import uuid
import time
import copy
from datetime import datetime, timezone, timedelta

# ── Simple test harness ────────────────────────────────────────────────────

passed = 0
failed = 0


def test(name):
    """Decorator to register and run a test."""
    def decorator(fn):
        global passed, failed
        try:
            fn()
            print(f"  ✓  {name}")
            passed += 1
        except AssertionError as e:
            print(f"  ✗  {name}")
            print(f"       AssertionError: {e}")
            failed += 1
        except Exception as e:
            print(f"  ✗  {name}")
            traceback.print_exc()
            failed += 1
        return fn
    return decorator


def section(title):
    print(f"\n{'─'*55}")
    print(f"  {title}")
    print(f"{'─'*55}")


# ── Inline JWT implementation (mirrors security.py without fastapi dep) ───

import hmac
import hashlib
import base64
import json


def _b64_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64_decode(s: str) -> bytes:
    pad = 4 - len(s) % 4
    if pad < 4:
        s += "=" * pad
    return base64.urlsafe_b64decode(s)


def make_token(payload: dict, secret: str = "test-secret", expires_in: int = 3600) -> str:
    now = int(time.time())
    payload = {**payload, "exp": now + expires_in, "iat": now}
    header = _b64_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = _b64_encode(json.dumps(payload).encode())
    sig = hmac.new(
        secret.encode(), f"{header}.{body}".encode(), hashlib.sha256
    ).digest()
    return f"{header}.{body}.{_b64_encode(sig)}"


def decode_token_unsafe(token: str, secret: str = "test-secret") -> dict:
    """Decode without verifying exp — used for tests that need expired tokens."""
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Malformed token")
    header_b64, body_b64, sig_b64 = parts
    expected_sig = hmac.new(
        secret.encode(), f"{header_b64}.{body_b64}".encode(), hashlib.sha256
    ).digest()
    provided_sig = _b64_decode(sig_b64)
    if not hmac.compare_digest(expected_sig, provided_sig):
        raise ValueError("Invalid signature")
    return json.loads(_b64_decode(body_b64))


def decode_token(token: str, secret: str = "test-secret") -> dict:
    payload = decode_token_unsafe(token, secret)
    if payload.get("exp", 0) < time.time():
        raise ValueError("Token expired")
    return payload


# ── Inline state machine (mirrors Phase 1) ────────────────────────────────

VALID_TRANSITIONS = {
    "draft":              {"outreach_sent", "cancelled"},
    "outreach_sent":      {"negotiating", "human_intervention", "failed", "cancelled"},
    "negotiating":        {"scheduled", "human_intervention", "failed", "cancelled", "outreach_sent"},
    "scheduled":          set(),  # terminal
    "failed":             set(),  # terminal
    "human_intervention": {"negotiating", "cancelled"},
    "cancelled":          set(),  # terminal
}


def transition(current: str, target: str) -> str:
    if target not in VALID_TRANSITIONS.get(current, set()):
        raise ValueError(f"Illegal transition: {current} → {target}")
    return target


# ── Inline RBAC helpers ───────────────────────────────────────────────────

class ForbiddenError(Exception): pass
class UnauthorizedError(Exception): pass


def assert_same_org(user_org_id: str, resource_org_id: str, role: str):
    if role == "admin":
        return  # admins bypass org check
    if user_org_id != resource_org_id:
        raise ForbiddenError("Access denied: resource belongs to a different organization")


# ── Pydantic-style schema validation ─────────────────────────────────────

import re

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validate_create_request(data: dict) -> dict:
    errors = []
    name = data.get("candidate_name", "").strip()
    if not name:
        errors.append("candidate_name is required")
    elif len(name) > 255:
        errors.append("candidate_name too long")

    email = data.get("candidate_email", "").strip()
    if not EMAIL_RE.match(email):
        errors.append("candidate_email is invalid")

    title = data.get("position_title", "").strip()
    if not title:
        errors.append("position_title is required")
    elif len(title) > 255:
        errors.append("position_title too long")

    if errors:
        raise ValueError("; ".join(errors))

    return {
        "candidate_name": name,
        "candidate_email": email.lower(),
        "position_title": title,
        "auto_send": bool(data.get("auto_send", False)),
    }


# ═══════════════════════════════════════════════════════════════════════════
# TESTS
# ═══════════════════════════════════════════════════════════════════════════

section("1. JWT — Token creation and validation")


@test("Create token with correct claims")
def _():
    token = make_token({"user_id": "u1", "org_id": "o1", "role": "recruiter", "email": "a@b.com"})
    payload = decode_token(token)
    assert payload["user_id"] == "u1"
    assert payload["org_id"] == "o1"
    assert payload["role"] == "recruiter"
    assert "exp" in payload
    assert "iat" in payload


@test("Expired token is rejected")
def _():
    token = make_token({"user_id": "u1", "org_id": "o1", "role": "recruiter", "email": "x@y.com"}, expires_in=-1)
    try:
        decode_token(token)
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "expired" in str(e).lower()


@test("Tampered signature is rejected")
def _():
    token = make_token({"user_id": "u1", "org_id": "o1", "role": "admin", "email": "a@b.com"})
    tampered = token[:-5] + "XXXXX"
    try:
        decode_token_unsafe(tampered)
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "signature" in str(e).lower() or "invalid" in str(e).lower()


@test("Wrong secret is rejected")
def _():
    token = make_token({"user_id": "u1", "org_id": "o1", "role": "recruiter", "email": "a@b.com"})
    try:
        decode_token(token, secret="wrong-secret")
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


@test("Admin token has correct role claim")
def _():
    token = make_token({"user_id": "admin1", "org_id": "o1", "role": "admin", "email": "admin@corp.com"})
    payload = decode_token(token)
    assert payload["role"] == "admin"


section("2. RBAC — Org-scoped authorization")


@test("Recruiter can access own org's resources")
def _():
    assert_same_org("org-a", "org-a", "recruiter")  # no exception


@test("Recruiter cannot access another org's resources")
def _():
    try:
        assert_same_org("org-a", "org-b", "recruiter")
        assert False, "Should have raised ForbiddenError"
    except ForbiddenError:
        pass


@test("Admin bypasses org check")
def _():
    assert_same_org("org-a", "org-b", "admin")  # no exception — admin sees all


@test("Admin can access all orgs")
def _():
    for org in ["org-a", "org-b", "org-c"]:
        assert_same_org("org-admin", org, "admin")


section("3. State Machine — Transitions")


@test("Draft → outreach_sent is valid")
def _():
    assert transition("draft", "outreach_sent") == "outreach_sent"


@test("Draft → negotiating is invalid (must go via outreach_sent)")
def _():
    try:
        transition("draft", "negotiating")
        assert False
    except ValueError:
        pass


@test("Negotiating → scheduled is valid")
def _():
    assert transition("negotiating", "scheduled") == "scheduled"


@test("Scheduled is terminal — no transitions out")
def _():
    for target in ["draft", "outreach_sent", "negotiating", "failed", "cancelled"]:
        try:
            transition("scheduled", target)
            assert False, f"Should not allow scheduled → {target}"
        except ValueError:
            pass


@test("Failed is terminal")
def _():
    try:
        transition("failed", "negotiating")
        assert False
    except ValueError:
        pass


@test("Cancelled is terminal")
def _():
    try:
        transition("cancelled", "draft")
        assert False
    except ValueError:
        pass


@test("Human intervention can return to negotiating")
def _():
    assert transition("human_intervention", "negotiating") == "negotiating"


@test("Human intervention can be cancelled")
def _():
    assert transition("human_intervention", "cancelled") == "cancelled"


@test("Full happy path: draft → outreach_sent → negotiating → scheduled")
def _():
    state = "draft"
    for next_state in ["outreach_sent", "negotiating", "scheduled"]:
        state = transition(state, next_state)
    assert state == "scheduled"


@test("Escalation path: negotiating → human_intervention → negotiating → scheduled")
def _():
    state = "negotiating"
    state = transition(state, "human_intervention")
    state = transition(state, "negotiating")
    state = transition(state, "scheduled")
    assert state == "scheduled"


section("4. Request Validation — Input sanitization")


@test("Valid create request passes")
def _():
    data = validate_create_request({
        "candidate_name": "Jane Smith",
        "candidate_email": "jane@example.com",
        "position_title": "Senior Engineer",
        "auto_send": False,
    })
    assert data["candidate_email"] == "jane@example.com"


@test("Email is lowercased on validation")
def _():
    data = validate_create_request({
        "candidate_name": "Test",
        "candidate_email": "TEST@EXAMPLE.COM",
        "position_title": "Engineer",
    })
    assert data["candidate_email"] == "test@example.com"


@test("Missing candidate_name raises validation error")
def _():
    try:
        validate_create_request({"candidate_email": "a@b.com", "position_title": "Dev"})
        assert False
    except ValueError as e:
        assert "candidate_name" in str(e)


@test("Invalid email raises validation error")
def _():
    try:
        validate_create_request({
            "candidate_name": "Jane", "candidate_email": "not-an-email", "position_title": "Dev"
        })
        assert False
    except ValueError as e:
        assert "candidate_email" in str(e)


@test("Empty position_title raises validation error")
def _():
    try:
        validate_create_request({"candidate_name": "Jane", "candidate_email": "j@e.com", "position_title": ""})
        assert False
    except ValueError as e:
        assert "position_title" in str(e)


@test("Name longer than 255 chars is rejected")
def _():
    try:
        validate_create_request({
            "candidate_name": "A" * 256,
            "candidate_email": "a@b.com",
            "position_title": "Dev",
        })
        assert False
    except ValueError:
        pass


@test("auto_send defaults to False")
def _():
    data = validate_create_request({
        "candidate_name": "Jane", "candidate_email": "j@e.com", "position_title": "Dev"
    })
    assert data["auto_send"] == False


section("5. Action Guards — Terminal state protection")


def guard_cancel(state: str) -> str:
    if state == "scheduled":
        raise ValueError("Cannot cancel a scheduled request. Remove the calendar event first.")
    if state == "cancelled":
        raise ValueError("Request is already cancelled")
    return "cancelled"


def guard_override(state: str) -> str:
    if state in {"scheduled", "cancelled", "failed"}:
        raise ValueError(f"Cannot override a request in state '{state}'")
    return "scheduled"


def guard_escalate(state: str) -> str:
    if state in {"scheduled", "cancelled", "failed", "human_intervention"}:
        raise ValueError(f"Cannot escalate from '{state}'")
    return "human_intervention"


@test("Cancelling a scheduled request raises error")
def _():
    try:
        guard_cancel("scheduled")
        assert False
    except ValueError as e:
        assert "Remove the calendar event" in str(e)


@test("Cancelling an already-cancelled request raises error")
def _():
    try:
        guard_cancel("cancelled")
        assert False
    except ValueError as e:
        assert "already cancelled" in str(e)


@test("Cancelling negotiating request is allowed")
def _():
    assert guard_cancel("negotiating") == "cancelled"


@test("Manual override on scheduled request raises error")
def _():
    try:
        guard_override("scheduled")
        assert False
    except ValueError:
        pass


@test("Manual override on draft is allowed")
def _():
    assert guard_override("draft") == "scheduled"


@test("Cannot escalate a scheduled request")
def _():
    try:
        guard_escalate("scheduled")
        assert False
    except ValueError:
        pass


@test("Cannot double-escalate")
def _():
    try:
        guard_escalate("human_intervention")
        assert False
    except ValueError:
        pass


@test("Escalating from outreach_sent is allowed")
def _():
    assert guard_escalate("outreach_sent") == "human_intervention"


section("6. Audit Log — Immutability contract")


class FakeAuditLog:
    def __init__(self):
        self._entries = []

    def append(self, entry: dict):
        # Immutable: set created_at once, never allow updates
        entry["id"] = str(uuid.uuid4())
        entry["created_at"] = datetime.now(timezone.utc).isoformat()
        self._entries.append(entry)

    def all(self):
        return copy.deepcopy(self._entries)  # deep copy — mutations cannot affect stored data


@test("Audit entries are appended in order")
def _():
    log = FakeAuditLog()
    log.append({"actor": "system", "event_type": "created"})
    log.append({"actor": "agent", "event_type": "email_sent"})
    entries = log.all()
    assert entries[0]["event_type"] == "created"
    assert entries[1]["event_type"] == "email_sent"


@test("Each audit entry gets a unique ID")
def _():
    log = FakeAuditLog()
    for _ in range(5):
        log.append({"actor": "agent", "event_type": "test"})
    ids = [e["id"] for e in log.all()]
    assert len(set(ids)) == 5, "All IDs should be unique"


@test("Audit log returns a copy — mutations do not affect stored data")
def _():
    log = FakeAuditLog()
    log.append({"actor": "agent", "event_type": "original"})
    entries = log.all()
    entries[0]["event_type"] = "mutated"  # mutate the returned copy
    assert log.all()[0]["event_type"] == "original", "Original must not be affected"


@test("Audit entries have created_at timestamps")
def _():
    log = FakeAuditLog()
    log.append({"actor": "recruiter", "event_type": "override"})
    entry = log.all()[0]
    assert "created_at" in entry
    # Should be a parseable ISO timestamp
    datetime.fromisoformat(entry["created_at"])


# ═══════════════════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════════════════

print(f"\n{'═'*55}")
print(f"  Results: {passed} passed, {failed} failed")
print(f"{'═'*55}\n")

if failed > 0:
    sys.exit(1)
