"""
Webhook endpoint for inbound candidate emails — flat layout version.
"""

import hashlib
import hmac
import logging
import os

from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

SENDGRID_WEBHOOK_SECRET = os.getenv("SENDGRID_WEBHOOK_SECRET", "")


def _verify_sendgrid_signature(payload: bytes, signature: str) -> bool:
    """Verify HMAC-SHA256 signature from SendGrid."""
    if not SENDGRID_WEBHOOK_SECRET:
        logger.warning("SENDGRID_WEBHOOK_SECRET not set — skipping signature check")
        return True
    expected = hmac.new(
        SENDGRID_WEBHOOK_SECRET.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature or "")


@router.post("/inbound-email", status_code=status.HTTP_202_ACCEPTED)
async def inbound_email(
    request: Request,
    x_twilio_email_event_webhook_signature: str = Header(default=""),
):
    """
    Receive inbound candidate emails from SendGrid.
    Validates HMAC signature, deduplicates, and enqueues for processing.
    Returns 202 immediately — processing happens async in Celery.
    """
    body = await request.body()

    if not _verify_sendgrid_signature(body, x_twilio_email_event_webhook_signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    # Parse the form data SendGrid sends
    form = await request.form()
    message_id = form.get("headers", "").split("Message-ID:")[-1].split("\n")[0].strip()
    sender = form.get("from", "unknown")
    subject = form.get("subject", "")
    text_body = form.get("text", "")

    logger.info("Inbound email from %s | subject: %s | msg_id: %s", sender, subject, message_id)

    # TODO Phase 4: enqueue Celery task
    # from tasks import process_candidate_reply
    # process_candidate_reply.delay(scheduling_request_id, text_body)

    return {"status": "accepted"}
