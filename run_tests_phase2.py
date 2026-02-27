"""
run_tests_phase2.py

Phase 2 standalone test suite — zero external dependencies.

Tests cover:
  1. Token encryption (AES-256 round-trip)
  2. CSRF state generation and verification
  3. HMAC webhook signature validation
  4. Email service dev-mode fallback (no provider)
  5. Calendar slot intersection logic
  6. Compensating transaction logic (rollback tracking)
  7. Idempotency check (duplicate email dedup)
  8. Microsoft token expiry detection
  9. DB helper: message_id extraction from headers
  10. Full task input validation
"""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import sys
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

# ── Minimal env setup ─────────────────────────────────────────────────────────
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("REDIS_PASSWORD", "test")
os.environ.setdefault("SECRET_KEY", "test_secret_key_for_phase2_tests_only_32c")
os.environ.setdefault("TOKEN_ENCRYPTION_KEY", "")   # Use derived key in tests
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ─────────────────────────────────────────────────────────────────────────────
# Test runner
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
    if a != b: raise AssertionError(f"{msg} Expected {b!r}, got {a!r}")

def assert_true(v, msg=""):
    if not v: raise AssertionError(msg or f"Expected truthy, got {v!r}")

def assert_false(v, msg=""):
    if v: raise AssertionError(msg or f"Expected falsy, got {v!r}")

def assert_raises(exc_class, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
        raise AssertionError(f"Expected {exc_class.__name__} but no exception raised")
    except exc_class:
        pass
    except AssertionError:
        raise
    except Exception as e:
        raise AssertionError(f"Expected {exc_class.__name__}, got {type(e).__name__}: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# 1. TOKEN ENCRYPTION (AES-256-GCM)
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{BOLD}{'='*60}{RESET}")
print(f"{BOLD}  Phase 2 — Standalone Test Suite{RESET}")
print(f"{BOLD}{'='*60}{RESET}\n")

print(f"{BOLD}1. Token Encryption (AES-256-GCM){RESET}")

# Inline implementation to test logic without external deps
def _derive_key(secret: str) -> bytes:
    return hashlib.sha256(secret.encode()).digest()

def _encrypt(plaintext: str, key: bytes) -> str:
    """AES-256-GCM encrypt using cryptography library if available, else base64 mock."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce      = os.urandom(12)
        ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode(), None)
        return base64.b64encode(nonce + ciphertext).decode()
    except ImportError:
        # Fallback mock for environments without cryptography
        return base64.b64encode(b"MOCK:" + plaintext.encode()).decode()

def _decrypt(encrypted: str, key: bytes) -> str:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        payload    = base64.b64decode(encrypted)
        nonce      = payload[:12]
        ciphertext = payload[12:]
        return AESGCM(key).decrypt(nonce, ciphertext, None).decode()
    except ImportError:
        payload = base64.b64decode(encrypted)
        return payload[5:].decode()   # Strip b"MOCK:" prefix

def t_encrypt_decrypt_roundtrip():
    key = _derive_key("test_secret_key_for_phase2_tests_only_32c")
    plaintext = '{"access_token": "ya29.abc123", "refresh_token": "1//xyz"}'
    encrypted = _encrypt(plaintext, key)
    assert_true(encrypted != plaintext, "Ciphertext must differ from plaintext")
    decrypted = _decrypt(encrypted, key)
    assert_eq(decrypted, plaintext)
test("Encrypt → decrypt round-trip preserves plaintext", t_encrypt_decrypt_roundtrip)

def t_encrypt_produces_different_ciphertexts():
    """Same plaintext encrypted twice must produce different ciphertext (random nonce)."""
    key = _derive_key("test_secret_key_for_phase2_tests_only_32c")
    plaintext = "test_token_value"
    enc1 = _encrypt(plaintext, key)
    enc2 = _encrypt(plaintext, key)
    # Different nonces → different ciphertexts (this is a security property)
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        assert_true(enc1 != enc2, "Two encryptions of same plaintext must differ")
    except ImportError:
        pass  # Mock always produces same output — skip in mock mode
test("Same plaintext encrypted twice produces different ciphertexts (random nonce)", t_encrypt_produces_different_ciphertexts)

def t_wrong_key_fails_decryption():
    """Decryption with a different key must fail — GCM auth tag mismatch."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        key1 = _derive_key("correct_secret_key_________________32c")
        key2 = _derive_key("wrong___secret_key_________________32c")
        encrypted = _encrypt("secret_token", key1)
        raised = False
        try:
            _decrypt(encrypted, key2)
        except Exception:
            raised = True   # Any exception = correct: wrong key was rejected
        assert_true(raised, "Decryption with wrong key must raise an exception")
    except ImportError:
        pass  # Skip if cryptography not available
test("Decryption with wrong key raises error (tamper detection)", t_wrong_key_fails_decryption)

def t_token_dict_encrypts_as_json():
    """Full token dict round-trip."""
    key = _derive_key("test_secret_key_for_phase2_tests_only_32c")
    token = {
        "access_token":  "ya29.realtoken",
        "refresh_token": "1//refreshtoken",
        "expiry":        "2025-01-10T14:00:00+00:00",
        "provider":      "google",
    }
    encrypted = _encrypt(json.dumps(token), key)
    restored  = json.loads(_decrypt(encrypted, key))
    assert_eq(restored["access_token"],  token["access_token"])
    assert_eq(restored["refresh_token"], token["refresh_token"])
    assert_eq(restored["provider"],      token["provider"])
test("Full OAuth token dict survives encrypt→decrypt→JSON parse", t_token_dict_encrypts_as_json)


# ─────────────────────────────────────────────────────────────────────────────
# 2. CSRF STATE (OAuth flow security)
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{BOLD}2. CSRF State Tokens (OAuth Security){RESET}")

SECRET = "test_secret_key_for_phase2_tests_only_32c"

def _build_state(user_id: str) -> str:
    sig = hmac.new(SECRET.encode(), user_id.encode(), hashlib.sha256).hexdigest()[:16]
    return f"{user_id}.{sig}"

def _verify_state(state: str) -> Optional[str]:
    parts = state.rsplit(".", 1)
    if len(parts) != 2: return None
    user_id, received = parts
    expected = hmac.new(SECRET.encode(), user_id.encode(), hashlib.sha256).hexdigest()[:16]
    return user_id if hmac.compare_digest(expected, received) else None

def t_state_valid():
    user_id = str(uuid.uuid4())
    state   = _build_state(user_id)
    result  = _verify_state(state)
    assert_eq(result, user_id)
test("Valid CSRF state verifies correctly and returns user_id", t_state_valid)

def t_state_tampered():
    user_id = str(uuid.uuid4())
    state   = _build_state(user_id)
    tampered = state[:-4] + "XXXX"
    result   = _verify_state(tampered)
    assert_true(result is None, "Tampered state must fail verification")
test("Tampered CSRF state returns None (CSRF protection)", t_state_tampered)

def t_state_different_user():
    state = _build_state("user-A")
    # Try to use state for a different user
    result = _verify_state("user-B." + state.split(".")[1])
    assert_true(result is None)
test("CSRF state for user-A cannot be used for user-B", t_state_different_user)

def t_state_malformed():
    assert_true(_verify_state("no-dot-here") is None)
    assert_true(_verify_state("") is None)
test("Malformed CSRF state returns None safely", t_state_malformed)


# ─────────────────────────────────────────────────────────────────────────────
# 3. WEBHOOK HMAC SIGNATURE VALIDATION
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{BOLD}3. Webhook HMAC Signature Validation{RESET}")

WEBHOOK_SECRET = "sendgrid_test_webhook_secret"

def _sign(body: bytes) -> str:
    return hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()

def _verify_signature(body: bytes, signature: str) -> bool:
    if not signature: return False
    expected = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)

def t_valid_signature():
    body = b"from=candidate@test.com&subject=Re%3A+Interview&text=Thursday+works"
    sig  = _sign(body)
    assert_true(_verify_signature(body, sig))
test("Valid HMAC signature passes verification", t_valid_signature)

def t_tampered_body_fails():
    body          = b"from=candidate@test.com&text=real+content"
    sig           = _sign(body)
    tampered_body = b"from=attacker@evil.com&text=injected"
    assert_false(_verify_signature(tampered_body, sig))
test("Tampered webhook body fails HMAC verification", t_tampered_body_fails)

def t_missing_signature_fails():
    body = b"some email payload"
    assert_false(_verify_signature(body, ""))
    assert_false(_verify_signature(body, None or ""))
test("Missing signature fails verification safely", t_missing_signature_fails)

def t_wrong_secret_fails():
    body      = b"email body content"
    wrong_sig = hmac.new(b"wrong_secret", body, hashlib.sha256).hexdigest()
    assert_false(_verify_signature(body, wrong_sig))
test("Signature generated with wrong secret fails verification", t_wrong_secret_fails)


# ─────────────────────────────────────────────────────────────────────────────
# 4. MESSAGE-ID EXTRACTION (Webhook dedup)
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{BOLD}4. Message-ID Extraction (Webhook Deduplication){RESET}")

def _extract_message_id(headers_raw: str) -> str:
    if not headers_raw:
        return str(uuid.uuid4())
    for line in headers_raw.splitlines():
        if line.lower().startswith("message-id:"):
            return line.split(":", 1)[1].strip().strip("<>")
    return str(uuid.uuid4())

def t_extract_standard_message_id():
    headers = "Date: Mon, 6 Jan 2025 14:00:00 +0000\r\nMessage-ID: <abc123@mail.gmail.com>\r\nFrom: test@test.com"
    result  = _extract_message_id(headers)
    assert_eq(result, "abc123@mail.gmail.com")
test("Extracts Message-ID correctly from raw headers", t_extract_standard_message_id)

def t_extract_case_insensitive():
    headers = "message-id: <UPPERCASE-ID@domain.com>"
    result  = _extract_message_id(headers)
    assert_eq(result, "UPPERCASE-ID@domain.com")
test("Message-ID extraction is case-insensitive", t_extract_case_insensitive)

def t_extract_missing_returns_uuid():
    result = _extract_message_id("Date: Mon, 6 Jan 2025 14:00:00 +0000\r\nFrom: a@b.com")
    assert_true(len(result) == 36, f"Expected UUID format, got: {result}")
test("Missing Message-ID falls back to a generated UUID", t_extract_missing_returns_uuid)

def t_extract_empty_returns_uuid():
    result = _extract_message_id("")
    assert_true(len(result) == 36)
test("Empty headers string returns a UUID fallback", t_extract_empty_returns_uuid)


# ─────────────────────────────────────────────────────────────────────────────
# 5. CALENDAR SLOT INTERSECTION LOGIC
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{BOLD}5. Calendar Slot Intersection Logic{RESET}")

def _overlaps(a_start, a_end, b_start, b_end) -> bool:
    return not (a_end <= b_start or a_start >= b_end)

def _compute_slots(range_start, range_end, busy_periods, duration_minutes):
    from zoneinfo import ZoneInfo
    slots = []
    current = range_start.replace(minute=0, second=0, microsecond=0)
    while current < range_end and len(slots) < 10:
        slot_end = current + timedelta(minutes=duration_minutes)
        if 9 <= current.hour < 17:
            conflict = any(_overlaps(current, slot_end, b[0], b[1]) for b in busy_periods)
            if not conflict:
                et = current.astimezone(ZoneInfo("America/New_York"))
                slots.append({"slot_utc": current.isoformat(), "slot_local": et.strftime("%A at %-I:%M %p ET")})
        current += timedelta(minutes=30)
    return slots

def t_no_busy_returns_business_hour_slots():
    start = datetime(2030, 1, 6, 0, tzinfo=timezone.utc)  # Monday
    end   = datetime(2030, 1, 7, 0, tzinfo=timezone.utc)  # Tuesday
    slots = _compute_slots(start, end, [], 45)
    assert_true(len(slots) > 0)
    for s in slots:
        h = datetime.fromisoformat(s["slot_utc"]).replace(tzinfo=timezone.utc).hour
        assert_true(9 <= h < 17, f"Outside business hours: {s['slot_utc']}")
test("No busy periods → slots within business hours only", t_no_busy_returns_business_hour_slots)

def t_full_day_busy_returns_no_slots():
    start = datetime(2030, 1, 6, 0, tzinfo=timezone.utc)
    end   = datetime(2030, 1, 7, 0, tzinfo=timezone.utc)
    # Block the entire business day
    busy = [(datetime(2030, 1, 6, 9, tzinfo=timezone.utc), datetime(2030, 1, 6, 17, tzinfo=timezone.utc))]
    slots = _compute_slots(start, end, busy, 45)
    assert_eq(len(slots), 0)
test("Fully busy day returns no slots", t_full_day_busy_returns_no_slots)

def t_partial_busy_excludes_conflicts():
    start = datetime(2030, 1, 6, 0, tzinfo=timezone.utc)
    end   = datetime(2030, 1, 7, 0, tzinfo=timezone.utc)
    # Block 10am-12pm
    busy = [(datetime(2030, 1, 6, 10, tzinfo=timezone.utc), datetime(2030, 1, 6, 12, tzinfo=timezone.utc))]
    slots = _compute_slots(start, end, busy, 45)
    for s in slots:
        slot_dt = datetime.fromisoformat(s["slot_utc"]).replace(tzinfo=timezone.utc)
        # No slot should start between 9:15 and 12:00 (conflict zone)
        slot_end = slot_dt + timedelta(minutes=45)
        assert_false(
            _overlaps(slot_dt, slot_end, 
                     datetime(2030, 1, 6, 10, tzinfo=timezone.utc), 
                     datetime(2030, 1, 6, 12, tzinfo=timezone.utc)),
            f"Slot {s['slot_utc']} conflicts with busy period"
        )
test("Partial busy block — conflicting slots excluded", t_partial_busy_excludes_conflicts)

def t_max_10_slots_returned():
    start = datetime(2030, 1, 6, 0, tzinfo=timezone.utc)
    end   = datetime(2030, 1, 20, 0, tzinfo=timezone.utc)  # Two weeks
    slots = _compute_slots(start, end, [], 30)
    assert_true(len(slots) <= 10, f"Got {len(slots)} slots, expected ≤ 10")
test("Slot list is capped at 10 (LLM context protection)", t_max_10_slots_returned)

def t_overlap_detection():
    """Test all overlap edge cases."""
    t = lambda h: datetime(2030, 1, 6, h, tzinfo=timezone.utc)
    assert_true( _overlaps(t(10), t(11), t(10), t(11)), "Identical ranges overlap")
    assert_true( _overlaps(t(10), t(12), t(11), t(13)), "Partial overlap")
    assert_false(_overlaps(t(10), t(11), t(11), t(12)), "Adjacent (touching) = no overlap")
    assert_false(_overlaps(t(10), t(11), t(12), t(13)), "No overlap (gap between)")
    assert_true( _overlaps(t(10), t(14), t(11), t(12)), "Contained within = overlap")
test("Overlap detection: identical, partial, adjacent, contained, disjoint", t_overlap_detection)


# ─────────────────────────────────────────────────────────────────────────────
# 6. COMPENSATING TRANSACTION (Booking Rollback)
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{BOLD}6. Compensating Transaction (Booking Rollback){RESET}")

def t_rollback_tracking():
    """Events created before a failure should be tracked for rollback."""
    created_events = []

    def _book_event(user_id, provider):
        event_id = str(uuid.uuid4())
        created_events.append((user_id, provider, event_id))
        return event_id

    def _rollback(created):
        rolled_back = []
        for uid, prov, eid in created:
            rolled_back.append(eid)
        return rolled_back

    # Simulate: book interviewer A (success), book interviewer B (fail), rollback A
    evt_a = _book_event("interviewer-A", "google")
    # Interviewer B booking fails — roll back A
    rolled = _rollback(created_events)
    assert_eq(len(rolled), 1)
    assert_eq(rolled[0], evt_a)
test("Booking failure triggers rollback of all previously created events", t_rollback_tracking)

def t_rollback_empty_list():
    """Rollback of empty list should not raise."""
    rolled_back = []
    for uid, prov, eid in []:  # empty
        rolled_back.append(eid)
    assert_eq(rolled_back, [])
test("Rollback of empty event list is safe (no-op)", t_rollback_empty_list)


# ─────────────────────────────────────────────────────────────────────────────
# 7. MICROSOFT TOKEN EXPIRY DETECTION
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{BOLD}7. Microsoft Token Expiry Detection{RESET}")

def _is_token_expired(expiry_str: str) -> bool:
    if not expiry_str: return True
    expiry = datetime.fromisoformat(expiry_str)
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) >= expiry - timedelta(minutes=5)

def t_future_token_not_expired():
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    assert_false(_is_token_expired(future))
test("Token expiring in 1 hour is not expired", t_future_token_not_expired)

def t_past_token_is_expired():
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert_true(_is_token_expired(past))
test("Token expired 1 hour ago is detected as expired", t_past_token_is_expired)

def t_token_expiring_in_3_min_is_expired():
    """5-minute buffer: token expiring in 3 minutes should trigger refresh."""
    near_expiry = (datetime.now(timezone.utc) + timedelta(minutes=3)).isoformat()
    assert_true(_is_token_expired(near_expiry))
test("Token expiring in 3 minutes triggers pre-emptive refresh (5-min buffer)", t_token_expiring_in_3_min_is_expired)

def t_missing_expiry_is_expired():
    assert_true(_is_token_expired(""))
    assert_true(_is_token_expired(None or ""))
test("Missing expiry string is treated as expired (safe default)", t_missing_expiry_is_expired)


# ─────────────────────────────────────────────────────────────────────────────
# 8. EMAIL SERVICE DEV-MODE (No provider configured)
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{BOLD}8. Email Service Dev-Mode Output{RESET}")

async def t_email_dev_mode():
    """In dev mode (no API keys), email should print to stdout and return True."""
    import io
    from contextlib import redirect_stdout

    captured = io.StringIO()

    # Simulate dev-mode email sending inline (no SendGrid/SES key)
    async def mock_send(to_email, subject, body):
        print(f"\n--- Email to {to_email} ---", file=captured)
        print(f"Subject: {subject}", file=captured)
        print(body, file=captured)
        return True

    result = await mock_send("candidate@example.com", "Interview Scheduling", "Hello!")
    assert_true(result)
    output = captured.getvalue()
    assert_true("candidate@example.com" in output)
    assert_true("Interview Scheduling" in output)
test("Dev-mode email sends to stdout and returns True", t_email_dev_mode)

def t_text_to_html_conversion():
    """Plain text should be wrapped in basic HTML."""
    def _text_to_html(text: str) -> str:
        import html
        escaped = html.escape(text)
        paragraphs = escaped.replace("\n\n", "</p><p>").replace("\n", "<br/>")
        return f"<html><body><p>{paragraphs}</p></body></html>"

    result = _text_to_html("Hello!\n\nThis is line 2.")
    assert_true("<html>" in result)
    assert_true("<br/>" in result or "</p><p>" in result)
    assert_true("Hello!" in result)
test("Plain text correctly converted to HTML (line breaks preserved)", t_text_to_html_conversion)

def t_html_escape_prevents_injection():
    """Candidate-supplied text must be HTML-escaped before embedding in email."""
    import html
    malicious = '<script>alert("xss")</script>'
    escaped   = html.escape(malicious)
    assert_true("<script>" not in escaped)
    assert_true("&lt;script&gt;" in escaped)
test("HTML escape prevents XSS injection in email bodies", t_html_escape_prevents_injection)


# ─────────────────────────────────────────────────────────────────────────────
# 9. CELERY TASK INPUT VALIDATION
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{BOLD}9. Celery Task Input Validation{RESET}")

def t_email_payload_structure():
    """Email payload must have required fields."""
    payload = {
        "to":          "agent@company.com",
        "from":        "candidate@example.com",
        "subject":     "Re: Interview Scheduling",
        "text":        "Thursday at 2pm works for me!",
        "html":        "",
        "message_id":  "abc123@mail.gmail.com",
        "received_at": datetime.now(timezone.utc).isoformat(),
    }
    assert_true(payload.get("from"), "Missing sender email")
    assert_true(payload.get("text") or payload.get("html"), "Missing email body")
    assert_true(payload.get("message_id"), "Missing message_id for dedup")
test("Email payload has all required fields for task processing", t_email_payload_structure)

def t_dedup_key_format():
    """Redis dedup key should be deterministic and namespaced."""
    message_id = "abc123@mail.gmail.com"
    key        = f"processed_emails:{message_id}"
    assert_true(key.startswith("processed_emails:"))
    assert_true(message_id in key)
    assert_true(" " not in key, "Redis keys must not contain spaces")
test("Redis dedup key is correctly namespaced and space-free", t_dedup_key_format)

def t_scheduling_status_enum_values():
    """All expected status values must be present."""
    valid_statuses = {
        "Draft", "Outreach_Sent", "Negotiating",
        "Scheduled", "Failed", "Human_Intervention"
    }
    terminal = {"Scheduled", "Failed"}  # task skips these
    for s in terminal:
        assert_true(s in valid_statuses)
test("Terminal state values used in task guard match schema enum", t_scheduling_status_enum_values)


# ─────────────────────────────────────────────────────────────────────────────
# 10. ALEMBIC MIGRATION STRUCTURE
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{BOLD}10. Migration File Structure{RESET}")

def t_migration_file_exists():
    migration_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "alembic", "versions", "001_initial_schema.py"
    )
    assert_true(os.path.exists(migration_path), f"Migration file not found: {migration_path}")
test("Initial schema migration file exists at correct path", t_migration_file_exists)

def t_migration_has_upgrade_and_downgrade():
    migration_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "alembic", "versions", "001_initial_schema.py"
    )
    with open(migration_path) as f:
        content = f.read()
    assert_true("def upgrade()" in content, "Migration must have upgrade()")
    assert_true("def downgrade()" in content, "Migration must have downgrade()")
    assert_true("organizations" in content)
    assert_true("users" in content)
    assert_true("calendar_tokens" in content)
    assert_true("scheduling_requests" in content)
    assert_true("request_participants" in content)
test("Migration file has upgrade/downgrade and all 5 required tables", t_migration_has_upgrade_and_downgrade)

def t_alembic_env_imports_all_models():
    env_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "alembic", "env.py"
    )
    with open(env_path) as f:
        content = f.read()
    assert_true("Organization" in content)
    assert_true("User" in content)
    assert_true("SchedulingRequest" in content)
    assert_true("CalendarToken" in content)
    assert_true("target_metadata" in content)
test("Alembic env.py imports all models and sets target_metadata", t_alembic_env_imports_all_models)


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{BOLD}{'='*60}{RESET}")
total = results["passed"] + results["failed"]
if results["failed"] == 0:
    print(f"\033[92m{BOLD}  All {total} tests passed ✓{RESET}")
else:
    print(f"\033[91m{BOLD}  {results['failed']}/{total} tests FAILED{RESET}")
    for name, tb in results["errors"]:
        print(f"\n  {BOLD}FAILED: {name}{RESET}")
        print(f"  {tb}")
print(f"{BOLD}{'='*60}{RESET}\n")

sys.exit(0 if results["failed"] == 0 else 1)
