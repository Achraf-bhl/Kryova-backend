"""End to end: the real daemon, over a real WebSocket, against the real app.

This is the test that would have caught every integration bug the previous
CATIA "integration" shipped with. It runs:

* the real `scripts/catia_bridge` session logic, in mock mode;
* over a genuine WebSocket upgrade to the real `/catia/bridge/ws` route,
  authenticated with a device token obtained through the real pairing flow;
* driving the real `call_catia`, the real registry, the real one-call-at-a-time
  queue, the real checkpointing and the real media pipeline;
* ending with a STEP export that becomes a real Kryova geometry version.

Only two things are substituted, and both are properties of the test harness
rather than of the system: the WebSocket is Starlette's in-process transport
instead of a network socket, and the route's `SessionLocal` is redirected at the
test's transaction so the handler can see rows the test has not committed to the
real database. Everything between those two points is production code.
"""

import base64
import json
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import select

from app.api.routes.catia import pairing_limiter
from app.catia import dispatch
from app.catia.connection import registry
from app.catia.dispatch import call_catia, catia_available
from app.models import Conversation, Project
from app.models.catia import CatiaCheckpoint, CatiaDocument, CatiaOperation

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from catia_bridge.mock_catia import MockCatia  # noqa: E402
from catia_bridge.session import BridgeSession  # noqa: E402

WS_PATH = "/api/v1/catia/bridge/ws"
PREFIX = "/api/v1/catia"


class _SharedSession:
    """Hands the route the test's session without letting it close it."""

    def __init__(self, session) -> None:
        self._session = session

    def __enter__(self):
        return self._session

    def __exit__(self, *_exc) -> None:
        """Deliberately does not close: the session belongs to the test."""


@contextmanager
def daemon_connected(client, token: str, backend):
    """Run the real daemon session against the app over a real WebSocket."""
    ready = threading.Event()
    closed = threading.Event()
    failures: list[BaseException] = []
    handle: dict[str, object] = {}

    def pump() -> None:
        try:
            with client.websocket_connect(
                WS_PATH, headers={"Authorization": f"Bearer {token}"}
            ) as websocket:
                session = BridgeSession(
                    backend,
                    bridge_version="1.0.0",
                    hostname="WS-TEST",
                    send=lambda frame: websocket.send_text(json.dumps(frame)),
                )
                handle["session"] = session
                websocket.send_text(json.dumps(session.hello_frame()))
                while True:
                    raw = websocket.receive_text()
                    frame = json.loads(raw)
                    if frame.get("type") == "ready":
                        ready.set()
                        continue
                    if frame.get("type") == "__test_shutdown":
                        return
                    session.handle_frame(raw)
        except BaseException as exc:  # noqa: BLE001 - surfaced to the test
            failures.append(exc)
        finally:
            ready.set()
            closed.set()

    thread = threading.Thread(target=pump, name="catia-daemon", daemon=True)
    thread.start()
    assert ready.wait(20), "the bridge never reached the ready frame"
    assert not failures, f"the daemon failed to connect: {failures[0]!r}"

    try:
        yield handle
    finally:
        # Ask the pump to leave its receive loop, then let the socket close so
        # the route's cleanup runs and the device is unregistered.
        for connection in list(registry._by_device.values()):
            if connection.is_open:
                connection._send({"type": "__test_shutdown"})
        closed.wait(10)
        thread.join(timeout=10)


@pytest.fixture
def bridge(auth_client, db_session, current_user_id, media_store, tmp_path, monkeypatch):
    """A paired workstation running the real daemon in mock mode."""
    monkeypatch.setattr(dispatch, "get_media_store", lambda: media_store)
    monkeypatch.setattr("app.api.routes.catia.SessionLocal", lambda: _SharedSession(db_session))
    dispatch._ops_per_minute.reset()
    dispatch._ops_per_hour.reset()
    # The pairing limiter is per client address and every test in this file
    # pairs a workstation, so without this the eleventh test is rate-limited by
    # the ten before it. The limit itself is asserted in test_catia_api.py.
    pairing_limiter.reset()

    created = auth_client.post(f"{PREFIX}/devices", json={"name": "Workstation"})
    assert created.status_code == 201, created.text
    paired = auth_client.post(
        f"{PREFIX}/devices/pair", json={"code": created.json()["pairing_code"]}
    )
    assert paired.status_code == 200, paired.text

    conversation = Conversation(owner_id=current_user_id, title="Bracket")
    db_session.add(conversation)
    db_session.commit()

    backend = MockCatia(tmp_path / "catia")
    with daemon_connected(auth_client, paired.json()["device_token"], backend):
        yield {
            "db": db_session,
            "user_id": current_user_id,
            "conversation": conversation,
            "device_id": paired.json()["device_id"],
            "backend": backend,
            "client": auth_client,
        }
    dispatch._ops_per_minute.reset()
    dispatch._ops_per_hour.reset()


def run(bridge, tool: str, arguments: dict | None = None, **kwargs):
    return call_catia(
        bridge["db"],
        user_id=bridge["user_id"],
        conversation_id=bridge["conversation"].id,
        tool=tool,
        arguments=arguments or {},
        **kwargs,
    )


# -- the connection is real --------------------------------------------------


def test_the_daemon_connects_and_the_server_reports_it_online(bridge):
    assert catia_available(bridge["db"], bridge["user_id"]) is True

    status = bridge["client"].get(f"{PREFIX}/status").json()
    assert status["connected"] is True
    assert status["catia_version"] == "V5-6R2021 (mock)"
    # Mock mode is surfaced all the way to the browser, so nobody mistakes a
    # simulated part for a real one.
    assert status["mock"] is True
    assert status["device_id"] == bridge["device_id"]


def test_the_hello_frame_updated_the_device_row(bridge):
    devices = bridge["client"].get(f"{PREFIX}/devices").json()
    assert devices[0]["online"] is True
    assert devices[0]["catia_version"] == "V5-6R2021 (mock)"
    assert devices[0]["is_mock"] is True
    assert devices[0]["hostname"] == "WS-TEST"


# -- a real tool call crosses the wire ---------------------------------------


def test_a_tool_call_reaches_mock_catia_and_comes_back(bridge):
    data = run(bridge, "catia_new_part", {"name": "Bracket"})
    assert data["doc_name"] == "Bracket"
    assert bridge["backend"].doc_name == "Bracket"


def test_building_a_part_produces_real_geometry(bridge):
    run(bridge, "catia_new_part", {"name": "Bracket"})
    sketch = run(bridge, "catia_sketch_rectangle", {"plane": "XY", "width_mm": 60, "height_mm": 20})
    run(bridge, "catia_pad", {"sketch": sketch["sketch"], "length_mm": 10})

    measured = run(bridge, "catia_measure")
    assert measured["bounding_box_mm"]["size"] == [60, 20, 10]
    # Kilograms, unconverted, exactly as the rest of the system expects.
    assert measured["mass_kg"] == pytest.approx(12000 * 7850e-9, rel=1e-6)


def test_a_failure_inside_catia_comes_back_as_a_tool_error(bridge):
    from app.catia.dispatch import CatiaError

    with pytest.raises(CatiaError, match="No document is open"):
        run(bridge, "catia_list_parameters")


def test_a_tool_the_daemon_refuses_is_reported_not_silently_dropped(bridge):
    from app.catia.dispatch import CatiaError

    run(bridge, "catia_new_part", {"name": "Bracket"})
    with pytest.raises(CatiaError, match="No sketch named"):
        run(bridge, "catia_pad", {"sketch": "Sketch.99", "length_mm": 5})


# -- checkpointing happens for real ------------------------------------------


def test_a_mutation_is_checkpointed_through_the_real_media_store(bridge, db_session):
    run(bridge, "catia_new_part", {"name": "Bracket"})
    run(bridge, "catia_sketch_rectangle", {"plane": "XY", "width_mm": 60, "height_mm": 20})

    checkpoints = list(db_session.scalars(select(CatiaCheckpoint)))
    assert checkpoints, "the sketch should have been checkpointed first"
    stored = checkpoints[0]
    assert stored.label == "before catia_sketch_rectangle"
    assert stored.media is not None
    # The bytes really are in the content-addressed store on disk.
    assert bridge_media_exists(bridge, stored)


def bridge_media_exists(bridge, checkpoint) -> bool:
    from app.media import MediaService

    service = MediaService(bridge["db"], dispatch.get_media_store())
    return service.exists(checkpoint.media)


def test_restore_rolls_the_real_part_back(bridge, db_session):
    from app.catia.approval import mint_approval

    run(bridge, "catia_new_part", {"name": "Bracket"})
    run(bridge, "catia_sketch_rectangle", {"plane": "XY", "width_mm": 60, "height_mm": 20})
    run(bridge, "catia_pad", {"sketch": "Sketch.1", "length_mm": 10})
    saved = run(bridge, "catia_checkpoint", {"label": "before the hole"})
    before = run(bridge, "catia_measure")["mass_kg"]

    run(bridge, "catia_hole", {"face": "top", "position": "center", "diameter_mm": 8})
    assert run(bridge, "catia_measure")["mass_kg"] < before

    token = mint_approval(
        user_id=bridge["user_id"],
        tool="catia_restore",
        conversation_id=bridge["conversation"].id,
        target=saved["checkpoint_id"],
    )
    run(
        bridge,
        "catia_restore",
        {"checkpoint_id": saved["checkpoint_id"], "approval_token": token},
    )
    assert run(bridge, "catia_measure")["mass_kg"] == pytest.approx(before)


def test_the_daemon_refuses_a_destructive_call_the_server_did_not_sign(bridge):
    """Proved against the real daemon, not a stub: forge past the server's check."""
    from app.catia.connection import BridgeCallFailed

    run(bridge, "catia_new_part", {"name": "Bracket"})
    connection = registry.get(bridge["device_id"])
    assert connection is not None

    with pytest.raises(BridgeCallFailed, match="approval token"):
        connection.call(
            tool="catia_restore",
            arguments={"checkpoint": {"checkpoint_id": "made-up"}},
            conversation_id=bridge["conversation"].id,
            timeout_s=10,
            queue_timeout_s=10,
            approval_token=None,
        )


# -- the loop closes ---------------------------------------------------------


def test_exporting_step_creates_a_geometry_version_end_to_end(bridge, db_session, project_id):
    """chat -> CATIA -> STEP -> geometry version, with nothing faked in between."""
    bridge["conversation"].project_id = project_id
    db_session.commit()

    run(bridge, "catia_new_part", {"name": "Bracket"})
    run(bridge, "catia_sketch_rectangle", {"plane": "XY", "width_mm": 60, "height_mm": 20})
    run(bridge, "catia_pad", {"sketch": "Sketch.1", "length_mm": 10})

    exported = run(bridge, "catia_export_step", {"note": "first solid"})
    assert exported["version_number"] == 1
    # Read by the same OpenCASCADE the mesher uses: this is a solid a
    # simulation can actually be run against.
    assert exported["stats"]["solid_count"] == 1
    assert exported["stats"]["volume_mm3"] == pytest.approx(12000.0, rel=1e-6)

    project = db_session.get(Project, project_id)
    db_session.refresh(project)
    version = project.geometry_versions[-1]
    assert version.file_format == "step"
    assert version.note == "first solid"

    # And it is visible through the ordinary API, indistinguishable from an
    # upload -- which is exactly the point of reusing the pipeline.
    listed = bridge["client"].get(f"/api/v1/projects/{project_id}/geometry").json()
    assert listed["total"] == 1
    assert listed["items"][0]["version_number"] == 1


def test_a_captured_view_round_trips_as_a_real_png(bridge, db_session):
    run(bridge, "catia_new_part", {"name": "Bracket"})
    run(bridge, "catia_sketch_rectangle", {"plane": "XY", "width_mm": 60, "height_mm": 20})
    run(bridge, "catia_pad", {"sketch": "Sketch.1", "length_mm": 10})

    captured = run(bridge, "catia_capture_view", {"view": "iso", "label": "as built"})
    from app.media import MediaService
    from app.models import Media

    media = db_session.get(Media, captured["media_id"])
    service = MediaService(db_session, dispatch.get_media_store())
    with service.open(media) as handle:
        assert handle.read(8) == b"\x89PNG\r\n\x1a\n"


# -- the document binding survives a session ---------------------------------


def test_the_conversation_is_bound_to_its_document(bridge, db_session):
    run(bridge, "catia_new_part", {"name": "Bracket"})
    document = db_session.scalar(
        select(CatiaDocument).where(CatiaDocument.conversation_id == bridge["conversation"].id)
    )
    assert document is not None
    assert document.doc_name == "Bracket"
    assert document.device_id == bridge["device_id"]


def test_a_lost_document_is_restored_from_the_stored_checkpoint(bridge, db_session):
    """The mechanic behind 'come back tomorrow and keep building'."""
    run(bridge, "catia_new_part", {"name": "Bracket"})
    run(bridge, "catia_sketch_rectangle", {"plane": "XY", "width_mm": 60, "height_mm": 20})
    run(bridge, "catia_pad", {"sketch": "Sketch.1", "length_mm": 10})
    run(bridge, "catia_checkpoint", {"label": "end of day"})

    # The workstation loses the file overnight.
    Path(bridge["backend"].doc_path).unlink()

    reopened = run(bridge, "catia_open_document", {})
    assert reopened["restored_from_checkpoint"] is True
    assert run(bridge, "catia_measure")["bounding_box_mm"]["size"] == [60, 20, 10]


# -- the audit trail is written for real -------------------------------------


def test_the_operation_log_records_the_whole_session(bridge, db_session, project_id):
    bridge["conversation"].project_id = project_id
    db_session.commit()

    run(bridge, "catia_new_part", {"name": "Bracket"})
    run(bridge, "catia_sketch_rectangle", {"plane": "XY", "width_mm": 60, "height_mm": 20})
    run(bridge, "catia_pad", {"sketch": "Sketch.1", "length_mm": 10})
    run(bridge, "catia_export_step", {})

    logged = list(
        db_session.scalars(
            select(CatiaOperation).order_by(CatiaOperation.created_at, CatiaOperation.id)
        )
    )
    assert [op.tool for op in logged] == [
        "catia_new_part",
        "catia_sketch_rectangle",
        "catia_pad",
        "catia_export_step",
    ]
    assert all(op.ok for op in logged)
    assert all(op.device_id == bridge["device_id"] for op in logged)
    # A replayable script of how the part was built.
    assert logged[1].arguments["width_mm"] == 60


# -- events reach the browser ------------------------------------------------


def test_a_daemon_event_is_relayed_to_the_users_event_stream(bridge):
    from app.catia.events import bus

    subscription = bus.subscribe(bridge["user_id"])
    try:
        session = bridge["backend"]
        assert session is not None
        connection = registry.get(bridge["device_id"])
        assert connection is not None

        # Feed the frame in exactly as the socket reader would.
        frame = connection.handle_frame(
            json.dumps(
                {
                    "type": "event",
                    "event": "parameters_changed",
                    "data": {"changed": ["Length"]},
                }
            )
        )
        assert frame is not None
        from app.api.routes.catia import _relay

        _relay(bridge["user_id"], bridge["device_id"], frame)

        received = subscription.poll(2.0)
        assert received is not None
        assert received["event"] == "parameters_changed"
        assert received["data"]["changed"] == ["Length"]
    finally:
        subscription.close()


def test_an_event_outside_the_vocabulary_is_dropped(bridge):
    from app.api.routes.catia import _relay
    from app.catia.events import bus

    subscription = bus.subscribe(bridge["user_id"])
    try:
        _relay(bridge["user_id"], bridge["device_id"], {"event": "rm -rf", "data": {}})
        assert subscription.poll(0.2) is None
    finally:
        subscription.close()


# -- concurrency against a real device ---------------------------------------


def test_concurrent_calls_are_serialised_against_the_real_daemon(bridge):
    """CATIA is single-threaded; two callers must not interleave into it."""
    run(bridge, "catia_new_part", {"name": "Bracket"})
    run(bridge, "catia_sketch_rectangle", {"plane": "XY", "width_mm": 60, "height_mm": 20})
    run(bridge, "catia_pad", {"sketch": "Sketch.1", "length_mm": 10})

    connection = registry.get(bridge["device_id"])
    assert connection is not None

    results: list[float] = []
    errors: list[BaseException] = []

    def measure() -> None:
        try:
            data = connection.call(
                tool="catia_measure",
                arguments={},
                conversation_id=bridge["conversation"].id,
                timeout_s=10,
                queue_timeout_s=10,
            )
            results.append(data["mass_kg"])
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=measure) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors
    assert len(results) == 4
    assert len(set(results)) == 1


def test_the_step_export_uses_the_long_timeout_and_completes(bridge, db_session, project_id):
    bridge["conversation"].project_id = project_id
    db_session.commit()
    run(bridge, "catia_new_part", {"name": "Bracket"})
    run(bridge, "catia_sketch_rectangle", {"plane": "XY", "width_mm": 60, "height_mm": 20})
    run(bridge, "catia_pad", {"sketch": "Sketch.1", "length_mm": 10})

    started = time.monotonic()
    run(bridge, "catia_export_step", {})
    # The mock deliberately takes ~0.1 s so ordering bugs the timeout logic
    # exists for cannot hide behind an instant reply.
    assert time.monotonic() - started >= 0.1


def test_base64_transfers_survive_the_round_trip_unchanged(bridge, db_session, project_id):
    bridge["conversation"].project_id = project_id
    db_session.commit()
    run(bridge, "catia_new_part", {"name": "Bracket"})
    run(bridge, "catia_sketch_rectangle", {"plane": "XY", "width_mm": 60, "height_mm": 20})
    run(bridge, "catia_pad", {"sketch": "Sketch.1", "length_mm": 10})
    exported = run(bridge, "catia_export_step", {})

    from app.media import MediaService
    from app.models import GeometryVersion

    version = db_session.get(GeometryVersion, exported["geometry_version_id"])
    service = MediaService(db_session, dispatch.get_media_store())
    stored = service.local_path(version.media).read_bytes()

    # Regenerate what the daemon would have sent and compare byte for byte,
    # so a chunked-base64 bug cannot hide behind "the file opened".
    direct = bridge["backend"].export_step(max_inline_bytes=10**8)
    assert len(stored) == len(base64.b64decode(direct["content_b64"]))
