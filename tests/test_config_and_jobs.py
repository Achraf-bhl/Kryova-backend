"""Settings validation and the job-queue seam.

These run offline: nothing here opens a database connection.
"""

import importlib
from typing import Any, cast

import pytest

from app.core.config import Settings
from app.jobs import InlineJobQueue, JobQueue, ThreadPoolJobQueue, get_job_queue

VALID = {
    "database_url": "postgresql://user:pw@example.neon.tech/db",
    "secret_key": "x" * 48,
}


def build(**overrides) -> Settings:
    """A Settings instance built from arguments only, ignoring any local .env."""
    # `_env_file` is pydantic-settings' documented per-instance override; it is
    # absent from the generated __init__ signature, hence the cast.
    factory = cast(Any, Settings)
    return factory(_env_file=None, **{**VALID, **overrides})


class TestDatabaseUrl:
    def test_a_bare_postgres_url_is_rewritten_onto_psycopg(self) -> None:
        assert build().database_url.startswith("postgresql+psycopg://")

    def test_a_non_postgres_url_is_refused(self) -> None:
        with pytest.raises(ValueError, match="must be a PostgreSQL URL"):
            build(database_url="sqlite:///./kryova.db")


class TestJobQueueBackend:
    """`celery` used to be an accepted value with no Celery behind it: the
    queue it selected ran the job inline, silently moving minutes of FEA onto
    the request thread. An unknown backend now has to fail at startup."""

    def test_celery_is_refused_with_an_actionable_message(self) -> None:
        with pytest.raises(ValueError) as caught:
            build(job_queue_backend="celery")
        message = str(caught.value)
        assert "threadpool" in message and "inline" in message

    def test_the_supported_backends_are_accepted(self) -> None:
        assert build(job_queue_backend="threadpool").job_queue_backend == "threadpool"
        assert build(job_queue_backend="inline").job_queue_backend == "inline"

    def test_there_is_no_celery_module_left_to_import(self) -> None:
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("app.jobs.celery_app")

    def test_only_two_queue_implementations_exist(self) -> None:
        assert set(JobQueue.__subclasses__()) == {InlineJobQueue, ThreadPoolJobQueue}


class TestJobQueueSelection:
    def test_inline_jobs_wins_over_the_backend_setting(self, monkeypatch) -> None:
        # What `--reload` dev servers and the test suite rely on.
        from app.core.config import settings

        get_job_queue.cache_clear()
        monkeypatch.setattr(settings, "inline_jobs", True)
        monkeypatch.setattr(settings, "job_queue_backend", "threadpool")
        try:
            assert isinstance(get_job_queue(), InlineJobQueue)
        finally:
            get_job_queue.cache_clear()

    def test_the_default_is_a_thread_pool(self, monkeypatch) -> None:
        from app.core.config import settings

        get_job_queue.cache_clear()
        monkeypatch.setattr(settings, "inline_jobs", False)
        monkeypatch.setattr(settings, "job_queue_backend", "threadpool")
        try:
            queue = get_job_queue()
            assert isinstance(queue, ThreadPoolJobQueue)
            queue.shutdown()
        finally:
            get_job_queue.cache_clear()


class TestProductionHardening:
    def test_a_default_secret_key_refuses_to_boot_production(self) -> None:
        with pytest.raises(ValueError, match="SECRET_KEY"):
            build(
                environment="production",
                secret_key="changeme",
                cookie_secure=True,
                cors_origins=["https://kryova.app"],
            )

    def test_a_plaintext_cors_origin_refuses_to_boot_production(self) -> None:
        with pytest.raises(ValueError, match="http://"):
            build(environment="production", cookie_secure=True, cors_origins=["http://kryova.app"])


class TestProxySettings:
    def test_forwarded_headers_are_distrusted_by_default(self) -> None:
        # Trusting them without a proxy in front means no rate limit at all.
        assert build().trust_proxy_headers is False


class TestCatiaSettings:
    """The CATIA layer is built in parallel and reads these; a rename here
    breaks a repo it cannot see."""

    def test_the_documented_keys_exist_with_their_defaults(self) -> None:
        settings = build()
        assert settings.catia_enabled is True
        assert settings.catia_call_timeout_s == 30.0
        assert settings.catia_export_timeout_s == 180.0
        assert settings.catia_device_token_ttl_days == 365
        assert settings.catia_pairing_code_ttl_minutes == 10
        assert settings.catia_ops_per_minute == 60

    def test_an_export_gets_a_longer_budget_than_an_ordinary_call(self) -> None:
        settings = build()
        assert settings.catia_export_timeout_s > settings.catia_call_timeout_s


class TestAiContextSettings:
    def test_summarisation_starts_before_anything_is_dropped(self) -> None:
        settings = build()
        assert settings.ai_summarise_after_messages < settings.ai_max_context_messages
