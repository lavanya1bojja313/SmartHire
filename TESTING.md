# ScheduleAI — Real-World Testing Guide

This guide walks you through testing the AI automation end-to-end in a real environment (with actual emails and real Google Calendar).

---

## Step 1: Set Up Credentials

Copy `.env.example` to `.env` and fill in the values:

```bash
cp .env.example .env
```

### Minimum Required (Email in Dev Mode — no actual sending)
```env
SECRET_KEY=any-long-random-string
DATABASE_URL=postgresql+asyncpg://postgres:postgres@db:5432/interview_scheduler
REDIS_URL=redis://redis:6379/0
```
With just these, the system runs completely — emails are **printed to the Docker logs** so you can see exactly what would have been sent to a candidate.

### To Send REAL Emails (SendGrid — Free Tier works)
1. Sign up at [sendgrid.com](https://sendgrid.com) → **Free** (100 emails/day)
2. Go to **Settings → API Keys → Create API Key**
3. Add to `.env`:
```env
SENDGRID_API_KEY=SG.xxxxxxxxxxxxxxxx
AGENT_EMAIL_ADDRESS=you@yourdomain.com
```
> ⚠️ `AGENT_EMAIL_ADDRESS` must be a **verified sender** in SendGrid.

### To Use Real Google Calendar
1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. **New Project** → Enable **Google Calendar API**
3. **APIs & Services → Credentials → Create OAuth 2.0 Client ID**
   - Type: **Web Application**
   - Redirect URI: `http://localhost:8000/auth/google/callback`
4. Add to `.env`:
```env
GOOGLE_CLIENT_ID=xxxxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxxxx
TOKEN_ENCRYPTION_KEY=  # generate with: python -c "import secrets; print(secrets.token_hex(32))"
```

---

## Step 2: Start the System

```bash
docker-compose up --build
```

Wait for all 4 containers to be healthy:
- `db` (PostgreSQL)
- `redis` (Message Broker)
- `api` (FastAPI — http://localhost:8000)
- `frontend` (Dashboard — http://localhost:3000)

---

## Step 3: Connect Google Calendar (if configured)

1. Open the Dashboard → click **Settings** in the sidebar
2. Click **Connect Google** under Calendar Integrations
3. Sign in with your Google account and grant permission
4. You'll be redirected back and see **"Connected — real slots enabled ✓"**

---

## Step 4: Send a Test Scheduling Request

1. Go to **Dashboard** tab
2. Click **New Request**
3. Fill in a candidate name, any email address (e.g., your own email for testing), and a position
4. ✅ **Check "Auto-send outreach email"**
5. Click **Create Request**

You should see:
- A new row appear with status **Outreach Sent**
- Click **Emails** in the sidebar → the first outreach email appears
- (If SendGrid is configured) The candidate receives a real email

---

## Step 5: Simulate a Candidate Reply

To simulate the candidate replying, run this command in your terminal:

```bash
docker-compose exec api python -c "
import asyncio
from tasks import process_candidate_reply

# Replace REQUEST_ID with the UUID from the dashboard
process_candidate_reply.delay(
    'PASTE-REQUEST-UUID-HERE',
    'Hi, I am available this Thursday at 3 PM IST. Does that work?'
)
print('Task queued!')
"
```

Watch what happens:
- The Celery worker processes the reply
- The AI reads the message and checks the calendar
- A reply email is generated → visible in the **Emails** tab
- The request state changes to **Negotiating**

---

## Step 6: Check What the Agent Sent

- Click **Emails** in the sidebar → full list of every email sent
- Click any email row to **expand and read the full body**
- Click a request → **Audit** tab to see every state transition

---

## Step 7: Test the Full Loop

If the AI and the candidate agree on a time:
- State changes to **Scheduled** (green badge)
- Calendar tab shows the interview booked
- (If Google Calendar is connected) A real Google Meet event is created in your calendar
- Both the recruiter and candidate get email invites automatically from Google

---

## Checking Logs

To see what the worker is doing in real time:

```bash
# See the AI making decisions (LLM calls, state changes)
docker-compose logs -f worker

# See API requests coming in
docker-compose logs -f api
```

---

## Common Issues

| Problem | Solution |
|---|---|
| `500 Internal Server Error` on POST request | Check `docker-compose logs api` for the traceback |
| Emails not showing in Emails tab | Check the Celery worker is running: `docker-compose ps` |
| `No refresh_token` error from Google | In Google OAuth, ensure `prompt=consent&access_type=offline` in the consent URL |
| Calendar connection resets on refresh | Go to Settings and check the green "Connected" status — if lost, reconnect |
| State not updating in dashboard | Click another tab and come back — it auto-fetches on navigation |

---

## Architecture Quick Reference

```
Candidate Email → Webhook (POST /webhooks/inbound)
                        ↓
               Celery Task Queue (Redis)
                        ↓
               AI Orchestrator (LLM)
                        ↓
           ┌────────────┴──────────────┐
    Check Calendar              Send Email
  (Google freeBusy API)     (SendGrid / stdout)
           └────────────┬──────────────┘
                        ↓
               Update Database (PostgreSQL)
                        ↓
               Dashboard UI Auto-refreshes
```
