"""Celery task queue initialization and background job definitions.

Enables distributed execution of meshing, FEA solving, and geometry processing
across multiple worker nodes using Redis or RabbitMQ as the message broker.
"""

import logging
from typing import Any

from celery import Celery  # type: ignore

from app.core.config import settings

logger = logging.getLogger(__name__)

broker_url = settings.celery_broker_url or settings.redis_url or "redis://localhost:6379/0"
result_backend = settings.redis_url or broker_url

celery_app = Celery(
    "kryova",
    broker=broker_url,
    backend=result_backend,
    include=["app.jobs.celery_app"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,  # Max 1 hour per simulation job
)


@celery_app.task(name="kryova.run_simulation_task", bind=True, max_retries=2)
def run_simulation_task(self: Any, simulation_id: str) -> None:
    """Execute a queued simulation job via Celery worker."""
    from app.core.database import SessionLocal
    from app.media.store import get_media_store
    from app.simulation.runner import run_simulation

    logger.info("Executing simulation task %s via Celery worker", simulation_id)
    store = get_media_store()
    try:
        run_simulation(simulation_id, SessionLocal, store)
    except Exception as exc:
        logger.exception("Error executing Celery simulation task %s", simulation_id)
        raise self.retry(exc=exc, countdown=10)
