# SmartHire
SmartHire – Autonomous Interview Scheduler
📌 Problem Statement
In today’s recruitment process, interview scheduling is mostly handled manually through multiple email exchanges between recruiters and candidates. This traditional approach creates several challenges:
Delays caused by repeated back-and-forth communication
Miscommunication between recruiter and candidate
Time zone confusion
Scheduling conflicts and double bookings
Poor candidate experience due to slow responses
Recruiters spend significant time coordinating interview availability instead of focusing on evaluation and hiring decisions. As hiring scales, manual scheduling becomes inefficient and error-prone. Therefore, an intelligent and automated solution is required to streamline interview scheduling without continuous human involvement.

💡 Proposed Solution
SmartHire is an AI-powered Autonomous Interview Scheduler that automates the entire interview coordination process from email receipt to calendar booking.

🔄 Workflow Overview
The candidate sends availability via email in natural language.
The system reads and interprets the message using a Large Language Model (LLM).
Available dates and time slots are extracted from unstructured text.
The recruiter’s calendar is checked for free slots.
A matching available slot is identified.
The system books the meeting automatically.
A Google Meet link is generated.
Confirmation emails are sent to both recruiter and candidate.
Both calendars are updated instantly.
After initial setup, the entire process runs automatically with zero manual intervention.

🎯 Objectives
Automate interview scheduling end-to-end
Reduce recruiter administrative workload
Eliminate scheduling conflicts and double bookings
Improve candidate experience with instant responses
Integrate AI with real-world enterprise tools
Build a scalable and production-ready system

🏗️ System Architecture & Approach
SmartHire follows a modular and scalable architecture composed of the following layers:
1️⃣ Natural Language Processing Layer
The system uses LLMs to understand availability written in natural language (e.g., “I’m free next Monday between 2 PM and 5 PM”).
It extracts structured date-time information from unstructured text while handling multiple formats and phrasing variations.
2️⃣ Calendar Intelligence Layer
Using OAuth 2.0 authentication, the system connects securely to Google Calendar.
It checks real-time availability and ensures no overlapping bookings occur.
3️⃣ Scheduling Engine

A custom state machine manages the scheduling lifecycle:

Email received

Availability extracted

Slot matched

Booking confirmed

Notification sent

Internal functions include:

check_calendar()

book_slot()

4️⃣ Notification & Communication Layer

The system automatically:

Generates Google Meet links

Sends confirmation emails

Updates recruiter and candidate calendars

Sends status notifications

5️⃣ Background Processing

Asynchronous processing ensures scalability and responsiveness:

Task queues handle scheduling jobs

Background workers process booking requests

Caching improves performance

🛠️ Technology Stack
Frontend
React
Next.js (TypeScript)
Tailwind CSS

Backend & APIs
Python
FastAPI
SQLAlchemy
Alembic

Database & Caching
PostgreSQL
Redis

Background Processing
Celery
AI & Language Models

OpenAI GPT-4o
Anthropic Claude 3.5 Sonnet
LiteLLM
Presidio

Infrastructure & Integrations
AWS SES
SendGrid
OAuth 2.0
Microsoft Graph API
Google Calendar API
Docker
Docker Compose
AI Framework Concepts
ReAct Framework
Pydantic Agents

Custom State Machine

🔗 Third-Party Resources Used

SmartHire integrates with the following external services:

Google Calendar API for availability checking and booking

Gmail API / SMTP for email communication

AWS SES / SendGrid for email delivery

OAuth 2.0 for secure authentication

Microsoft Graph API for optional Outlook integration

LLM providers (OpenAI, Anthropic)

⚙️ Setup & Run Instructions
🔹 Prerequisites

Node.js (v18 or above)

Python (v3.10 or above)

PostgreSQL

Redis

Docker & Docker Compose

Google Cloud Project with Calendar API enabled

OAuth credentials

🔹 1. Clone Repository
git clone https://github.com/your-repo/smarthire.git
cd smarthire
🔹 2. Backend Setup

Create virtual environment:

python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

Install dependencies:

pip install -r requirements.txt

Create .env file:

DATABASE_URL=postgresql://user:password@localhost/smarthire
REDIS_URL=redis://localhost:6379
OPENAI_API_KEY=your_key
ANTHROPIC_API_KEY=your_key
GOOGLE_CLIENT_ID=your_id
GOOGLE_CLIENT_SECRET=your_secret
SMTP_EMAIL=your_email
SMTP_PASSWORD=your_password

Run migrations:

alembic upgrade head

Start backend server:

uvicorn main:app --reload
🔹 3. Start Celery Worker
celery -A app.worker worker --loglevel=info
🔹 4. Frontend Setup
cd frontend
npm install
npm run dev

Application runs at:

http://localhost:3000
🔹 5. Docker Setup (Optional)
docker-compose up --build
📊 Expected Outcomes

80–90% reduction in scheduling coordination time

Instant automated responses to candidates

Zero double bookings due to real-time checks

Improved candidate experience

Recruiters focus on interviews and evaluation

Scalable to support multiple recruiters and departments

🔮 Future Enhancements

Multi-recruiter intelligent slot allocation

Automatic time zone detection

WhatsApp and Slack integration

SMS interview reminders

Analytics dashboard for HR insights

Support for panel interviews

👥 Team Members & Roles

Team Name: Coding Crew
Institute: Institute of Aeronautical Engineering

Name	Role
Bojja Lavanya	Backend Development & API Integration
Bathula Mahitha	 Frontend Development & UI Design API Integration
T. Naveen Kumar	AI Integration & LLM Processing
M. Sai Srinith	Database & Deployment
