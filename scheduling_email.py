"""
app/services/email.py

Outbound email service using SendGrid as primary, AWS SES as fallback.

Responsibilities:
  - Send agent → candidate emails
  - Send confirmation emails with calendar details
  - Send human-intervention alerts to recruiters (Slack + email)

Design decisions:
  - SendGrid is primary: better deliverability analytics, easier bounce tracking.
  - SES fallback: kicks in automatically if SendGrid returns 5xx or raises.
  - All emails are sent from the AGENT_EMAIL_ADDRESS configured in settings.
  - Thread tracking: we set the Reply-To header so candidate replies come
    back to the inbound webhook address (the same AGENT_EMAIL_ADDRESS).
  - Retry: up to 3 attempts with exponential backoff before giving up.
"""

import structlog
from datetime import datetime, timezone
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logger = structlog.get_logger(__name__)

# ── In-memory email log (dev mode only) ─────────────────────────────────────
# Stores the last 200 emails sent. Reset on worker restart.
# In production, real email providers have their own delivery dashboards.
EMAIL_LOG: list[dict] = []
MAX_LOG_SIZE = 200


class EmailService:
    """
    Sends transactional emails on behalf of the scheduling agent.
    Abstracts the difference between SendGrid and SES.
    """

    def __init__(self):
        from config import get_settings
        self.settings = get_settings()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def send(
        self,
        to_email: str,
        subject: str,
        body: str,
        reply_to: str | None = None,
        request_id: str | None = None,
    ) -> bool:
        """
        Send an email. Tries SendGrid first, falls back to SES on failure.

        Args:
            to_email:   Recipient email address.
            subject:    Email subject line.
            body:       Plain-text email body (HTML is auto-generated from this).
            reply_to:   Reply-To header. Defaults to AGENT_EMAIL_ADDRESS.
            request_id: Used as a custom header for tracking in email logs.

        Returns:
            True on success, raises EmailDeliveryError on all retries exhausted.
        """
        from_email  = self.settings.agent_email_address
        reply_to    = reply_to or from_email
        html_body   = self._text_to_html(body)

        logger.info(
            "email.send",
            to=to_email,
            subject=subject[:50],
            request_id=request_id,
        )

        # 1. Try Gmail SMTP first (easiest — no domain required)
        gmail_password = os.getenv("GMAIL_APP_PASSWORD", "")
        gmail_user = os.getenv("GMAIL_USER", from_email)
        if gmail_password and gmail_user:
            try:
                await self._send_via_gmail(
                    to_email=to_email,
                    subject=subject,
                    body=body,
                    from_email=gmail_user,
                    reply_to=reply_to,
                    request_id=request_id,
                )
                logger.info("email.sent_via_gmail", to=to_email)
                self._log_email(to_email, subject, body, request_id, provider="gmail")
                return True
            except Exception as e:
                logger.warning("email.gmail_failed", error=str(e), to=to_email)

        # 2. Try SendGrid
        if self.settings.sendgrid_api_key:
            try:
                await self._send_via_sendgrid(
                    to_email=to_email,
                    subject=subject,
                    text_body=body,
                    html_body=html_body,
                    from_email=from_email,
                    reply_to=reply_to,
                    request_id=request_id,
                )
                logger.info("email.sent_via_sendgrid", to=to_email)
                self._log_email(to_email, subject, body, request_id, provider="sendgrid")
                return True
            except Exception as e:
                logger.warning(
                    "email.sendgrid_failed",
                    error=str(e),
                    to=to_email,
                )

        # 3. Fall back to SES
        if self.settings.aws_access_key_id if hasattr(self.settings, 'aws_access_key_id') else False:
            try:
                await self._send_via_ses(
                    to_email=to_email,
                    subject=subject,
                    text_body=body,
                    html_body=html_body,
                    from_email=from_email,
                    reply_to=reply_to,
                )
                logger.info("email.sent_via_ses", to=to_email)
                self._log_email(to_email, subject, body, request_id, provider="ses")
                return True
            except Exception as e:
                logger.error("email.ses_also_failed", error=str(e), to=to_email)
                raise EmailDeliveryError(f"All email providers failed: {e}") from e

        # 4. No provider configured — dev mode stdout + log
        if self.settings.env == "production":
            raise EmailDeliveryError(
                "No email provider configured. Set GMAIL_APP_PASSWORD, SENDGRID_API_KEY, or AWS credentials."
            )
        else:
            self._log_email(to_email, subject, body, request_id, provider="dev_stdout")
            print(f"\n{'─'*60}")
            print(f"📧 [DEV] Email to {to_email}")
            print(f"Subject: {subject}")
            print(f"─{'-'*59}")
            print(body)
            print(f"{'─'*60}\n")
            return True

    def _log_email(self, to: str, subject: str, body: str, request_id, provider: str):
        """Append to in-memory log for the Emails tab in the dashboard."""
        entry = {
            "id": len(EMAIL_LOG) + 1,
            "to": to,
            "subject": subject,
            "body": body,
            "request_id": request_id,
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "provider": provider,
        }
        EMAIL_LOG.append(entry)
        if len(EMAIL_LOG) > MAX_LOG_SIZE:
            EMAIL_LOG.pop(0)

    async def _send_via_gmail(self, to_email, subject, body, from_email, reply_to, request_id):
        """
        Send via Gmail SMTP using an App Password.
        Uses port 465 (SSL) — more reliable inside Docker than port 587 (TLS).
        """
        import smtplib
        import ssl
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        import asyncio
        import os

        gmail_user = os.getenv("GMAIL_USER", from_email)
        gmail_password = os.getenv("GMAIL_APP_PASSWORD", "")

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{getattr(self.settings, 'sendgrid_from_name', 'ScheduleAI')} <{gmail_user}>"
        msg["To"] = to_email
        msg["Reply-To"] = reply_to or gmail_user
        if request_id:
            msg["X-Request-ID"] = str(request_id)

        msg.attach(MIMEText(body, "plain"))
        msg.attach(MIMEText(self._text_to_html(body), "html"))

        def _smtp_send():
            context = ssl.create_default_context()
            # Port 465 (SSL) — works in Docker, no STARTTLS handshake needed
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context, timeout=10) as server:
                server.login(gmail_user, gmail_password)
                server.sendmail(gmail_user, to_email, msg.as_string())

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _smtp_send)


    async def send_human_alert(
        self,
        recruiter_email: str,
        request_id: str,
        candidate_email: str,
        escalation_reason: str,
    ) -> None:
        """
        Alert a human recruiter when the agent escalates.
        Sends both an email and a Slack webhook (if configured).
        """
        subject = f"⚠️  Interview Scheduling Needs Your Attention — {candidate_email}"
        body = (
            f"The scheduling agent was unable to complete booking for:\n\n"
            f"  Candidate: {candidate_email}\n"
            f"  Request ID: {request_id}\n"
            f"  Reason: {escalation_reason}\n\n"
            f"Please log in to the scheduler dashboard to manually complete this booking:\n"
            f"  https://your-dashboard.com/requests/{request_id}/override\n\n"
            f"The candidate has been informed that a team member will be in touch."
        )
        await self.send(
            to_email=recruiter_email,
            subject=subject,
            body=body,
            request_id=request_id,
        )
        await self._send_slack_alert(
            request_id=request_id,
            candidate_email=candidate_email,
            reason=escalation_reason,
        )

    # ── Private: Provider Implementations ────────────────────────────────────

    async def _send_via_sendgrid(
        self,
        to_email: str,
        subject: str,
        text_body: str,
        html_body: str,
        from_email: str,
        reply_to: str,
        request_id: str | None,
    ) -> None:
        """Send via SendGrid Python SDK."""
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail, ReplyTo, CustomArg

        message = Mail(
            from_email=from_email,
            to_emails=to_email,
            subject=subject,
            plain_text_content=text_body,
            html_content=html_body,
        )
        message.reply_to = ReplyTo(reply_to)

        # Custom header for webhook-to-request correlation
        if request_id:
            message.add_custom_arg(CustomArg("request_id", request_id))

        client = SendGridAPIClient(self.settings.sendgrid_api_key)
        # SendGrid SDK is sync — run in executor to avoid blocking event loop
        import asyncio
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None, lambda: client.send(message)
        )

        if response.status_code not in (200, 202):
            raise EmailDeliveryError(
                f"SendGrid returned status {response.status_code}: {response.body}"
            )

    async def _send_via_ses(
        self,
        to_email: str,
        subject: str,
        text_body: str,
        html_body: str,
        from_email: str,
        reply_to: str,
    ) -> None:
        """Send via AWS SES using boto3."""
        import boto3
        import asyncio

        ses = boto3.client("ses", region_name="us-east-1")
        params = {
            "Source": from_email,
            "Destination": {"ToAddresses": [to_email]},
            "ReplyToAddresses": [reply_to],
            "Message": {
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {
                    "Text": {"Data": text_body, "Charset": "UTF-8"},
                    "Html":  {"Data": html_body,  "Charset": "UTF-8"},
                },
            },
        }
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: ses.send_email(**params))

    async def _send_slack_alert(
        self,
        request_id: str,
        candidate_email: str,
        reason: str,
    ) -> None:
        """Post a message to the recruiter Slack channel via webhook."""
        webhook_url = self.settings.slack_alert_webhook_url
        if not webhook_url:
            logger.debug("email.slack_alert_skipped", reason="No webhook configured")
            return

        import httpx
        payload = {
            "text": (
                f"⚠️ *Interview scheduling escalated*\n"
                f"• Candidate: `{candidate_email}`\n"
                f"• Request ID: `{request_id}`\n"
                f"• Reason: {reason}\n"
                f"• Action: <https://your-dashboard.com/requests/{request_id}/override|Open in Dashboard>"
            )
        }
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(webhook_url, json=payload)
                if response.status_code != 200:
                    logger.warning(
                        "email.slack_alert_failed",
                        status=response.status_code,
                    )
                else:
                    logger.info("email.slack_alert_sent", request_id=request_id)
        except Exception as e:
            # Slack is best-effort — never let it break the main flow
            logger.warning("email.slack_alert_exception", error=str(e))

    @staticmethod
    def _text_to_html(text: str) -> str:
        """
        Convert plain text to minimal HTML for email clients that prefer it.
        Preserves line breaks and wraps in a readable font.
        """
        import html
        escaped = html.escape(text)
        paragraphs = escaped.replace("\n\n", "</p><p>").replace("\n", "<br/>")
        return f"""
        <html><body style="font-family: Arial, sans-serif; font-size: 14px; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
        <p>{paragraphs}</p>
        </body></html>
        """.strip()


class EmailDeliveryError(Exception):
    """Raised when email delivery fails after all retries."""
    pass
