Technical Blueprint: Autonomous Interview Scheduler
1. Product & Requirements Clarification
Before defining the architecture, we establish the critical operating assumptions:

Asynchronous Flow: Scheduling negotiation happens via email over hours or days. The system must maintain state across long periods.
Timezone Complexity: The system must flawlessly handle timezone conversions between recruiters, multiple interviewers, and the candidate.
Calendar Mutability: Interviewer calendars change dynamically. The system must verify time slots at the exact moment of booking, not just when the slot was proposed.
Human Fallback: If the AI agent enters a confusing loop or cannot find a time after N tries, it must gracefully pause and alert a human recruiter.
2. User Flows
Flow A: Initiation (The Recruiter)
Recruiter logs into the Dashboard (or via ATS integration like Greenhouse).
Recruiter creates a scheduling request: selects the Candidate, the required Interviewers, interview duration, and deadline.
System fetches immediate availability of interviewers and triggers the AI Agent.
Flow B: Autonomous Negotiation (The Agent & Candidate)
Initial Outreach: Agent drafts and sends a personalized email to the candidate proposing 3 optimal time blocks.
Candidate Replies: Candidate responds in natural language (e.g., "I can't do Tuesday, but Thursday afternoon EST works.").
Intent Parsing & Reasoning: Agent parses the email, extracts the temporal constraints, cross-references Thursday afternoon EST with the interviewers' live calendars, and identifies a 2:00 PM slot.
Resolution:
If available: Agent locks a temporary hold, replies to confirm, and generates calendar invites + video links upon confirmation.
If unavailable: Agent replies proposing alternative times close to Thursday afternoon.
Completion: Invites are generated, ATS is updated to "Scheduled", and Recruiter is notified.
3. System Architecture
To handle async state and external API rate limits, we use an Event-Driven Microservices Architecture.

Frontend Application (Web UI): SPA for recruiters to track agent status, intervene if needed, and manage org settings.
Core API Backend: Handles CRUD for users, scheduling requests, and orchestrates tasks.
Webhooks/Ingestion Service: A highly available gateway solely responsible for catching inbound emails (via SendGrid/SES webhook) and dropping them into a Message Queue.
Agentic AI Worker (The Brain): Async workers that consume from the queue. They fetch context, call the LLM for reasoning/entity extraction, query the Calendar Integration, and decide the next action.
Integration Engine: Abstraction layer over Google/Microsoft Calendars, Zoom/Meet, and ATS APIs.
4. Tech Stack (with reasoning)
Component  Technology  Reasoning
Frontend  React + Next.js (TypeScript), Tailwind CSS  Fast SSR loading, superb ecosystem, strict typing for dashboard state management.
Backend API  Python + FastAPI  Python is the undisputed king of AI/LLM tooling (LangChain, LlamaIndex, Pydantic). FastAPI offers superb async performance.
Database  PostgreSQL  Strict relational integrity is required for users, orgs, and transactional state mapping.
Caching/Queues  Redis + Celery  Redis for rapid caching of calendar availability (to avoid API rate limits). Celery for robust background task processing of emails.
LLM Engine  OpenAI GPT-4o / Anthropic Claude 3.5 Sonnet  Top-tier reasoning required for complex temporal logic and polite communication. Structured Outputs via Pydantic.
Email Gateway  AWS SES or SendGrid  Reliable inbound email parsing (webhook ingestion) and high-deliverability outbound sending.
5. Database Schema (Core Entities)
sql
Table Organization {
  id UUID PK
  name VARCHAR
  settings JSONB -- e.g., default_working_hours, timezone
}
Table User {
  id UUID PK
  org_id UUID FK
  role ENUM(Admin, Recruiter, Interviewer)
  email VARCHAR
  calendar_token_id UUID FK -- OAuth tokens stored securely
}
Table Candidate {
  id UUID PK
  email VARCHAR UNIQUE
  timezone VARCHAR
  ats_candidate_id VARCHAR
}
Table SchedulingRequest {
  id UUID PK
candidate_id UUID FK
  status ENUM(Draft, Outreach_Sent, Negotiating, Scheduled, Failed, Human_Intervention)
  duration_minutes INT
  state_machine_context JSONB -- Stores conversation history & pending slots
  created_at TIMESTAMP
}
Table RequestParticipant {
  request_id UUID FK
  user_id UUID FK (Interviewer)
  is_required BOOLEAN
}
6. API Contracts (REST / Async)
Internal Core APIs:

POST /api/v1/requests → Creates a scheduling job and queues the initial outreach.
GET /api/v1/requests/{id}/timeline → Returns the log of Agent-Candidate interactions.
POST /api/v1/requests/{id}/override → Allows a human recruiter to take over and manually schedule.
External Webhook (The Ingestion Point):

POST /webhooks/inbound-email
Payload: { "to": "agent@company.com", "from": "candidate@gmail.com", "subject": "Re: Interview", "text": "...", "html": "..." }
Action: Validates HMAC signature, publishes to email_processing_queue, returns 202 Accepted immediately.
7. Auth & Security Model
Authentication: OAuth 2.0. Users log in via Google/Microsoft, which seamlessly captures their Calendar access tokens in one go.
Authorization: strict RBAC. Recruiters can only see scheduling requests for their org_id.
Data Privacy & AI Security:
PII Scrubbing: Before sending candidate email text to the LLM, a local lightweight NLP model (e.g., Presidio) scrubs phone numbers and non-essential PII.
Calendar Scopes: Request calendar.freebusy.read and calendar.events.write scopes to adhere to the Principle of Least Privilege.
Encryption: AES-256 for encrypting OAuth tokens in the DB using an AWS KMS or Hashicorp Vault key.
8. Scalability & Resiliency Plan
Rate Limiting Protection: Google/Outlook APIs severely throttle calendar queries. We will cache an interviewer's free/busy matrix in Redis for 15 minutes to answer agent heuristics without hitting the external API, only performing a live check right before booking the final invite.
Idempotent Workers: Email webhook retries can cause duplicate processing. Every inbound email message ID is checked against a Redis processed_emails set to ensure the agent doesn't reply to the same email twice.
LLM Fallback: If OpenAI goes down, the orchestrator automatically routes prompt inference to Anthropic Claude via LiteLLM to ensure no dropped candidates.
9. Folder Structure (Backend Monorepo Component)
text
/backend
├── app/
│   ├── api/                 # FastAPI routers (webhooks, dashboard APIs)
│   ├── core/                # Config, DB connections, Security middlewares
│   ├── models/              # SQLAlchemy definitions
│   ├── services/            # Business logic (ATS sync, Calendar API wrappers)
│   ├── agent/               # The AI Brain
│   │   ├── prompts/         # Version-controlled prompt templates
│   │   ├── tools/           # Functions the LLM can call (check_calendar, book_slot)
│   │   └── state_machine.py # Manages transitioning scheduling states
│   └── worker/              # Celery task definitions (process_inbound_email)
├── tests/                   # Pytest suite
└── docker-compose.yml       # Local dev with Postgres and Redis
10. Execution Roadmap & Build Phases
Phase 1: The Core Brain (Weeks 1-3)
Focus: Can an LLM actually negotiate time?

Define the Agent prompt utilizing ReAct (Reasoning + Acting) framework.
Build pure Python mock tests using dummy free_busy JSON arrays.
Output: A CLI script acting as a candidate chatting with the agent to achieve a booked slot.
Phase 2: Integrations Foundation (Weeks 4-6)
Focus: Hooking the brain to reality.

Implement Google Calendar and Microsoft Graph OAuth flows.
Implement the SendGrid/SES webhook ingestion and outgoing email service.
Connect the Agent script to real external APIs via Celery workers.
Phase 3: The Dashboard & State Management (Weeks 7-9)
Focus: Making it a product.
Build the FastAPI CRUD layer and DB migrations.
Build the Next.js Recruiter Dashboard.
Implement strictly defined State Machine transitions so Recruiters can track exactly where a candidate is in the pipeline.
Phase 4: Edge Cases & Human-in-the-Loop (Weeks 10-12)
Focus: Production readiness.

Implement handling for 3+ interviewer overlap (Group Interviews).
Implement Rescheduling logic (Candidate asks to change an already booked time).
Build the "Human Intervention" alerts (e.g., if the Agent detects anger, or loops 3 times without a resolution).
Phase 5: Beta & Security Audit (Week 13-14)
Pen-testing OAuth token storage and tenant row-level security.
Onboard 2 internal recruiters to shadow the agent (Agent drafts emails, human hits "Send") before flipping the switch to Fully Autonomous mode.