"""Startup behaviour, tested directly rather than through every TestClient."""

import json
import logging
from contextlib import contextmanager

import pytest

from app.core.config import settings
from app.main import JsonLogFormatter, _fail_orphaned_jobs, docs_urls
from app.models import (
    GeometryVersion,
    JobStatus,
    Media,
    MediaKind,
    Project,
    SimulationJob,
    User,
)
from tests.typing import AuthenticatedTestClient


@pytest.fixture
def scope(db_session):
    @contextmanager
    def _scope():
        yield db_session

    return _scope


@pytest.fixture
def job(db_session) -> SimulationJob:
    user = User(email="startup@kryova.dev", hashed_password="not-a-real-hash")
    project = Project(name="Orphan", owner=user)
    media = Media(
        owner=user,
        kind=MediaKind.CAD,
        filename="part.stl",
        size_bytes=1,
        sha256="0" * 64,
    )
    version = GeometryVersion(
        project=project, media=media, version_number=1, filename="part.stl", file_format="stl"
    )
    simulation = SimulationJob(
        project=project,
        geometry_version=version,
        status=JobStatus.RUNNING,
        solver="linear-static-tet4",
        load_case={},
    )
    db_session.add_all([user, project, media, version, simulation])
    db_session.flush()
    return simulation


def test_jobs_orphaned_by_a_restart_are_failed(db_session, scope, job) -> None:
    # The in-process queue does not survive a restart, so a job still marked
    # RUNNING at startup has no worker and would be polled forever.
    _fail_orphaned_jobs(session_factory=scope)

    db_session.refresh(job)
    assert job.status is JobStatus.FAILED
    assert "restart" in job.error


def test_a_queued_job_is_also_failed(db_session, scope, job) -> None:
    job.status = JobStatus.QUEUED
    db_session.flush()

    _fail_orphaned_jobs(session_factory=scope)

    db_session.refresh(job)
    assert job.status is JobStatus.FAILED


def test_finished_jobs_are_left_alone(db_session, scope, job) -> None:
    job.status = JobStatus.SUCCEEDED
    db_session.flush()

    _fail_orphaned_jobs(session_factory=scope)

    db_session.refresh(job)
    assert job.status is JobStatus.SUCCEEDED


def _record(message: str, *args: object, **extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="kryova.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=args,
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


class TestJsonLogging:
    """The production formatter used a `%(message)r` template, which emits
    Python's repr: single-quoted, Python-escaped, and never valid JSON. Every
    line it produced was unparseable by the shipper it existed for."""

    def test_a_line_parses_as_json(self) -> None:
        line = JsonLogFormatter().format(_record("meshing finished"))
        assert json.loads(line)["msg"] == "meshing finished"

    def test_a_message_with_quotes_and_braces_still_parses(self) -> None:
        # The exact shape repr mangles: apostrophes, double quotes, braces.
        message = """it's a {"json": "payload"} with 'quotes'"""
        parsed = json.loads(JsonLogFormatter().format(_record(message)))
        assert parsed["msg"] == message

    def test_interpolation_arguments_are_applied(self) -> None:
        parsed = json.loads(JsonLogFormatter().format(_record("failed %d job(s)", 3)))
        assert parsed["msg"] == "failed 3 job(s)"

    def test_level_and_logger_are_carried(self) -> None:
        parsed = json.loads(JsonLogFormatter().format(_record("hello")))
        assert parsed["level"] == "INFO"
        assert parsed["logger"] == "kryova.test"
        assert parsed["ts"]

    def test_a_request_id_rides_along_when_set(self) -> None:
        parsed = json.loads(JsonLogFormatter().format(_record("hello", request_id="abc123")))
        assert parsed["request_id"] == "abc123"

    def test_a_traceback_is_one_json_string_not_many_lines(self) -> None:
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            record = _record("crashed")
            record.exc_info = sys.exc_info()
        parsed = json.loads(JsonLogFormatter().format(record))
        assert "ValueError: boom" in parsed["exception"]


class TestDocsExposure:
    def test_development_serves_the_docs(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "environment", "development")
        assert docs_urls() == {
            "openapi_url": f"{settings.api_v1_prefix}/openapi.json",
            "docs_url": "/docs",
            "redoc_url": "/redoc",
        }

    def test_production_serves_none_of_it(self, monkeypatch) -> None:
        # Including the OpenAPI document: hiding only the two HTML pages in
        # front of it would hide nothing.
        monkeypatch.setattr(settings, "environment", "production")
        assert set(docs_urls().values()) == {None}


class TestHealthCheck:
    def test_reports_ok_when_both_dependencies_answer(
        self, client: AuthenticatedTestClient, monkeypatch
    ) -> None:
        import app.core.database as database

        monkeypatch.setattr(database, "check_database", lambda: None)
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {
            "status": "ok",
            "checks": {"database": "ok", "media_store": "ok"},
        }

    def test_a_dead_database_is_a_503_not_a_200(
        self, client: AuthenticatedTestClient, monkeypatch
    ) -> None:
        # A probe that answers 200 while the database is gone keeps a broken
        # instance in the load balancer, which is the whole failure mode.
        import app.core.database as database

        def explode() -> None:
            raise OSError("connection refused")

        monkeypatch.setattr(database, "check_database", explode)
        response = client.get("/health")
        assert response.status_code == 503
        assert response.json()["status"] == "degraded"
        assert "connection refused" in response.json()["checks"]["database"]
        assert response.json()["checks"]["media_store"] == "ok"

    def test_an_unwritable_media_store_is_also_degraded(
        self, client: AuthenticatedTestClient, monkeypatch
    ) -> None:
        import app.core.database as database
        import app.main as main

        monkeypatch.setattr(database, "check_database", lambda: None)

        def explode() -> None:
            raise PermissionError("read-only file system")

        monkeypatch.setattr(main, "_check_media_store", explode)
        response = client.get("/health")
        assert response.status_code == 503
        assert "read-only file system" in response.json()["checks"]["media_store"]


class TestRequestId:
    def test_every_response_carries_one(self, client: AuthenticatedTestClient) -> None:
        response = client.get("/api/v1/materials")
        assert response.headers["x-request-id"]

    def test_a_caller_supplied_id_is_kept(self, client: AuthenticatedTestClient) -> None:
        # So a trace survives the hop from the frontend.
        response = client.get("/api/v1/materials", headers={"X-Request-ID": "trace-42"})
        assert response.headers["x-request-id"] == "trace-42"

    def test_two_requests_get_different_ids(self, client: AuthenticatedTestClient) -> None:
        first = client.get("/api/v1/materials").headers["x-request-id"]
        second = client.get("/api/v1/materials").headers["x-request-id"]
        assert first != second
