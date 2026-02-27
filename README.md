# ScheduleAI — Autonomous Interview Scheduler

> An AI-powered agent that reads candidate emails, checks recruiter availability, and books interviews automatically — with zero human intervention for routine scheduling.

---

## Problem Statement

Recruiting teams waste enormous time on back-and-forth email chains just to find a mutually available interview slot. A single scheduling thread can span 5–10 emails over several days, consume recruiter attention, and delay the hiring pipeline. For high-volume hiring, this is a significant operational bottleneck.

**The core problem:** Interview scheduling is repetitive, rule-based, and time-consuming — but it currently requires a human to do it.

---

## Proposed Solution & Approach

ScheduleAI is a fully autonomous scheduling agent that:

1. **Reads inbound candidate emails** via a webhook (SendGrid Inbound Parse or Gmail)
2. **Understands availability requests** using GPT-4o (natural language understanding)
3. **Checks the recruiter's real calendar** via Google Calendar API (`freeBusy` query)
4. **Decides the next action** — propose slots, confirm, or escalate to a human
5. **Sends a reply email** to the candidate from the recruiter's address
6. **Books a Google Meet interview** when both parties agree on a time
7. **Updates the recruiter dashboard** in real-time showing all active requests

The agent operates as a **finite state machine**:

```
Draft → Outreach Sent → Negotiating → Scheduled
                                  ↘ Human Intervention (if stuck)
```

Each state transition is audited and visible to the recruiter. The system escalates to human review after 3 failed negotiation rounds.

---

## Technology Stack

### Backend
| Component | Technology |
|---|---|
| API Framework | **FastAPI** (Python 3.12, async) |
| Task Queue | **Celery** + Redis broker |
| Database | **PostgreSQL 16** via SQLAlchemy (async) |
| Migrations | **Alembic** |
| AI Orchestrator | **OpenAI GPT-4o** (`openai` Python SDK) |
| Email Sending | **Gmail SMTP** (SSL port 465) via smtplib |
| Calendar Integration | **Google Calendar API v3** (OAuth2) |
| Token Security | JWT (`python-jose`) + Fernet encryption |

### Frontend
| Component | Technology |
|---|---|
| Dashboard | **React** (Next.js, JSX) |
| Styling | Vanilla CSS with glassmorphism design |
| State management | React hooks (`useState`, `useEffect`) |

### Infrastructure
| Component | Technology |
|---|---|
| Containerisation | **Docker** + Docker Compose |
| Message Broker | **Redis 7** |
| Reverse Proxy (prod) | Nginx (optional) |

### Third-Party APIs & Services
| Service | Purpose |
|---|---|
| **OpenAI API** | GPT-4o for email understanding and reply generation |
| **Google Calendar API** | `freeBusy` queries + event creation with Meet links |
| **Gmail SMTP** | Sending candidate emails via App Password |
| **SendGrid Inbound Parse** *(optional)* | Receiving candidate reply emails as webhooks |

---

## Setup & Run Instructions

### Prerequisites
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed and running
- An OpenAI API key → [platform.openai.com/api-keys](https://platform.openai.com/api-keys)
- A Gmail account + App Password (for sending emails)

---

### Step 1 — Clone and Configure

```bash
# Copy the environment template
copy .env.example .env
```

Open `.env` and fill in these **minimum required** values:

```env
SECRET_KEY=your-random-32-char-hex-string
OPENAI_API_KEY=sk-proj-...
GMAIL_USER=your.email@gmail.com
GMAIL_APP_PASSWORD=yourapppassword   # No spaces — 16 chars
```

**Generate a SECRET_KEY:**
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

**Get a Gmail App Password:**
1. Enable 2-Step Verification at [myaccount.google.com/security](https://myaccount.google.com/security)
2. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
3. Create one for "ScheduleAI" — copy the 16-character code (remove spaces)

---

### Step 2 — Start the System

```bash
docker-compose up --build
```

First run takes ~2 minutes to download images and install packages. All 4 services start automatically:

| Service | URL |
|---|---|
| **Dashboard** | http://localhost:3000 |
| **API** (FastAPI) | http://localhost:8000 |
| **API Docs** (Swagger) | http://localhost:8000/docs |

---

### Step 3 — Connect Google Calendar *(optional, for real slot booking)*

1. Create a project at [console.cloud.google.com](https://console.cloud.google.com) → Enable **Google Calendar API**
2. Create OAuth 2.0 credentials (Web Application type), set redirect URI:
   `http://localhost:8000/auth/google/callback`
3. Add to `.env`:
```env
GOOGLE_CLIENT_ID=xxxxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxxxx
TOKEN_ENCRYPTION_KEY=your-32-char-hex
```
4. Restart: `docker-compose restart api worker`
5. Go to **Dashboard → Settings → Connect Google**

---

### Step 4 — Test the Full Flow

1. Open **http://localhost:3000** → Dashboard
2. Click **New Request** → fill in candidate details
3. ✅ Tick **"Auto-send outreach email"**
4. Click **Create** — the agent immediately sends a scheduling email to the candidate
5. Click **Emails** tab to see the email that was sent
6. Click any request row to see the full **Audit Trail**

**To simulate a candidate reply:**
```bash
docker-compose exec api python -c "
from tasks import process_candidate_reply
process_candidate_reply.delay(
    'PASTE-REQUEST-UUID',
    'Hi, I am available Thursday at 3 PM. Does that work?'
)
print('Queued!')
"
```

**Watch the agent work in real time:**
```bash
docker-compose logs -f worker
```

---

### Stop / Reset

```bash
# Stop all containers
docker-compose down

# Full reset (wipes database)
docker-compose down -v && docker-compose up --build
```

---

## Project Structure

```
HACK/
├── main.py               # FastAPI app entry point
├── models.py             # SQLAlchemy DB models
├── orchestrator.py       # AI agent core logic
├── state_machine.py      # Request state transitions
├── tasks.py              # Celery background tasks
├── scheduling_email.py   # Email sending service (Gmail/SendGrid)
├── calendar_service.py   # Google Calendar API integration
├── auth.py               # JWT auth + Google OAuth2 routes
├── scheduling_requests.py# REST API for scheduling requests
├── config.py             # Environment configuration
├── dashboard.jsx         # React frontend dashboard
├── docker-compose.yml    # Full stack orchestration
└── .env.example          # Environment variable template
```

---

## Team Members

| Name | Role |
|---|---|
| Tallapaneni Naveen Kumar Chowdary | Full-Stack Developer & AI Integration |
| *(add team member)* | *(role)* |
| *(add team member)* | *(role)* |

---

## Common Issues

| Problem | Fix |
|---|---|
| Request stays in "Draft" | ✅ Tick the **Auto-send** checkbox when creating the request |
| Emails not sending | Check `docker-compose logs worker` for errors. Verify `GMAIL_APP_PASSWORD` has no spaces |
| Dashboard not loading | Confirm Docker is running: `docker-compose ps` |
| `connection refused` on port 5432 | Database not ready — wait 30s and retry |
| Google Calendar not connecting | Ensure redirect URI matches exactly: `http://localhost:8000/auth/google/callback` |
