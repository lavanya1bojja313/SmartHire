import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # App config
    env: str = os.getenv("ENV", "local")
    
    # DB
    database_url: str = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/interview_scheduler")
    
    # LLM — support both PRIMARY_LLM_MODEL (user .env) and LLM_PRIMARY_MODEL (legacy)
    llm_primary_model: str = (
        os.getenv("LLM_PRIMARY_MODEL")
        or os.getenv("PRIMARY_LLM_MODEL")
        or "gpt-4o"
    )
    llm_fallback_model: str = os.getenv("LLM_FALLBACK_MODEL", "claude-3-5-sonnet-20241022")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    
    # Agent
    agent_max_negotiation_loops: int = int(os.getenv("AGENT_MAX_NEGOTIATION_LOOPS", "3"))
    # Support both AGENT_EMAIL_ADDRESS (legacy) and SENDGRID_FROM_EMAIL (user .env)
    agent_email_address: str = (
        os.getenv("AGENT_EMAIL_ADDRESS")
        or os.getenv("SENDGRID_FROM_EMAIL")
        or "agent@test_scheduler.com"
    )
    sendgrid_from_name: str = os.getenv("SENDGRID_FROM_NAME", "ScheduleAI")
    
    # Email / Notifications
    sendgrid_api_key: str = os.getenv("SENDGRID_API_KEY", "")
    aws_access_key_id: str = os.getenv("AWS_ACCESS_KEY_ID", "")
    aws_secret_access_key: str = os.getenv("AWS_SECRET_ACCESS_KEY", "")
    slack_alert_webhook_url: str = os.getenv("SLACK_ALERT_WEBHOOK_URL", "")

    # Google OAuth2
    google_client_id: str = os.getenv("GOOGLE_CLIENT_ID", "")
    google_client_secret: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
    google_redirect_uri: str = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/api/v1/auth/google/callback")

    # Encryption key for token storage (AES-256; generate with: python -c "import secrets;print(secrets.token_hex(32))")
    token_encryption_key: str = os.getenv("TOKEN_ENCRYPTION_KEY", "")

def get_settings():
    return Settings()
