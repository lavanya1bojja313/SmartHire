"""
Celery application factory — flat layout version.
Reads config directly from environment variables.
"""

import os
from celery import Celery


def _get_redis_url() -> str:
    return os.getenv("REDIS_URL", "redis://localhost:6379/0")


def _get_database_url() -> str:
    return os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/interview_scheduler",
    )


def create_celery_app() -> Celery:
    redis_url = _get_redis_url()

    app = Celery(
        "interview_scheduler",
        broker=redis_url,
        backend=redis_url,
    )

    app.conf.update(
        # Serialization
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],

        # Timezone
        timezone="UTC",
        enable_utc=True,

        # Retry / reliability
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        task_max_retries=3,

        # Result expiry — keep results for 1 hour
        result_expires=3600,

        # Auto-discover tasks in tasks.py
        imports=["tasks"],
    )

    return app


celery_app = create_celery_app()
