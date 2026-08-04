from __future__ import annotations

from celery import Celery
from celery.schedules import schedule

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "launchpad",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    beat_schedule={
        "ttl-reaper-every-5-minutes": {
            "task": "launchpad.reap_expired_environments",
            "schedule": schedule(run_every=settings.ttl_reaper_interval_seconds),
        },
        "preview-drift-scan": {
            "task": "launchpad.scan_preview_drift",
            "schedule": schedule(run_every=settings.drift_scan_interval_seconds),
        },
        "cost-metering-sample": {
            "task": "launchpad.sample_environment_costs",
            "schedule": schedule(run_every=settings.cost_sample_interval_seconds),
        },
    },
)
