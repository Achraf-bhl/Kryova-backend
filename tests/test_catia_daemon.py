"""The daemon, driven directly: mock CATIA, re-validation, tiers, artefacts.

These import `scripts/catia_bridge` -- the code that actually ships to the
Windows workstation -- and drive `BridgeSession` with real frames. Only the
WebSocket is absent, and `bridge.py` (which is only the socket) is thin by
design precisely so that this is a meaningful test rather than a test of a
parallel implementation.

The point of the mock being faithful is proved here: the STEP it writes is read
by the same `geometry.inspect` a browser upload goes through, and the PNG is
decoded rather than merely counted.
"""

import base64
import json
import math
import struct
import sys
import zlib
from pathlib import Path

import pytest

from app.geometry.inspect import inspect

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from catia_bridge.backend import TOOL_METHODS, CatiaOperationError  # noqa: E402
from catia_bridge.mock_catia import MockCatia  # noqa: E402
from catia_bridge.session import BridgeSession  # noqa: E402
from catia_bridge.tool_table import TOOLS, ToolRefused, check_call  # noqa: E402


@pytest.fixture
def mock(tmp_path: Path) -> MockCatia:
    return MockCatia(tmp_path / "catia")


@pytest.fixture
def session(mock: MockCatia):
    sent: list[dict] = []
    bridge = BridgeSession(mock, bridge_version="1.0.0", hostname="WS-TEST", send=sent.append)
    bridge.sent = sent  # type: ignore[attr-defined]
    return bridge


def call(session, tool: str, arguments: dict | None = None, **frame_extra) -> dict:
    identifier = f"call-{len(session.sent)}"
    session.handle_frame(
        json.dumps(
            {
                "type": "call",
                "id": identifier,
                "tool": tool,
                "conversation_id": "conv-1",
                "arguments": arguments or {},
                **frame_extra,
            }
        )
    )
    return session.sent[-1]


def build_bracket(session) -> None:
    assert call(session, "catia_new_part", {"name": "Bracket"})["ok"]
    assert call(
        session, "catia_sketch_rectangle", {"plane": "XY", "width_mm": 60, "height_mm": 20}
    )["ok"]
    assert call(session, "catia_pad", {"sketch": "Sketch.1", "length_mm": 10})["ok"]


# -- the vocabulary agrees with the server's ---------------------------------


def test_daemon_table_covers_exactly_the_server_vocabulary():
    from app.catia.tool_specs import CATIA_TOOL_SPECS

    server = {spec.name for spec in CATIA_TOOL_SPECS}
    # `catia_status` is answered by the server and never reaches a device.
    assert set(TOOLS) | {"catia_status"} == server
    assert set(TOOLS) == set(TOOL_METHODS)


def test_daemon_tiers_match_the_server_tiers():
    from app.catia.tool_specs import CATIA_TOOL_SPECS

    for spec in CATIA_TOOL_SPECS:
        if spec.name in TOOLS:
            assert TOOLS[spec.name][0] == str(spec.tier), spec.name


def test_the_two_validator_copies_behave_identically():
    from catia_bridge import validation as daemon_validation

    from app.catia import validation as server_validation

    schema = {
        "type": "object",
        "properties": {"a": {"type": "number", "minimum": 1}},
        "required": ["a"],
        "additionalProperties": False,
    }
    for value in ({"a": 5}, {}, {"a": 0}, {"a": 1, "b": 2}, {"a": "x"}):
        server_error = _error(lambda: server_validation.validate(value, schema))
        daemon_error = _error(lambda: daemon_validation.validate(value, schema))
        assert server_error == daemon_error, value


def _error(fn) -> str | None:
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        return str(exc)
    return None


# -- re-validation at the daemon ---------------------------------------------


def test_daemon_refuses_a_tool_it_does_not_implement(session):
    result = call(session, "catia_run_vbscript", {"code": "..."})
    assert result["ok"] is False
    assert "not a tool this bridge implements" in result["error"]


def test_daemon_refuses_the_server_only_status_tool(session):
    assert call(session, "catia_status")["ok"] is False


def test_daemon_refuses_an_argument_the_schema_does_not_allow(session):
    build_bracket(session)
    result = call(session, "catia_pad", {"sketch": "Sketch.1", "length_mm": 5, "x": 1})
    assert result["ok"] is False
    assert "unknown field" in result["error"]


def test_daemon_refuses_a_destructive_call_with_no_approval_token(session):
    build_bracket(session)
    result = call(session, "catia_restore", {"checkpoint": {"checkpoint_id": "c1"}})
    assert result["ok"] is False
    assert "approval token" in result["error"]


def test_the_tier_is_never_read_off_the_wire(session):
    """A frame claiming a destructive tool is a read is still destructive."""
    build_bracket(session)
    result = call(
        session,
        "catia_restore",
        {"checkpoint": {"checkpoint_id": "c1"}},
        tier="read",
    )
    assert result["ok"] is False
    assert "approval token" in result["error"]


def test_check_call_rejects_non_object_arguments():
    with pytest.raises(ToolRefused, match="must be an object"):
        check_call("catia_measure", ["not", "a", "dict"], approval_token=None)


def test_server_added_fields_are_allowed_but_only_the_named_ones():
    # The server legitimately adds a transfer ceiling...
    check_call("catia_checkpoint", {"label": "x", "max_inline_bytes": 10}, approval_token=None)
    # ...and nothing else.
    with pytest.raises(ToolRefused, match="unknown field"):
        check_call("catia_checkpoint", {"label": "x", "rogue": 1}, approval_token=None)


# -- the mock is a real part model -------------------------------------------


def test_a_pad_produces_a_real_mass_and_bounding_box(session):
    build_bracket(session)
    data = call(session, "catia_measure")["data"]
    assert data["bounding_box_mm"]["size"] == [60, 20, 10]
    assert data["volume_mm3"] == pytest.approx(12000.0)
    # Kilograms already -- nothing above this converts.
    assert data["mass_kg"] == pytest.approx(12000 * 7850e-9, rel=1e-6)
    # And it never lets a simulated number pass for a measured one.
    assert data["approximate"] is True


def test_changing_a_parameter_changes_the_geometry(session):
    build_bracket(session)
    before = call(session, "catia_measure")["data"]["mass_kg"]
    assert call(session, "catia_set_parameter", {"name": "Height", "value": 20, "unit": "mm"})["ok"]
    after = call(session, "catia_measure")["data"]["mass_kg"]
    assert after == pytest.approx(before * 2, rel=1e-6)


def test_setting_a_parameter_in_the_wrong_unit_is_refused(session):
    build_bracket(session)
    result = call(session, "catia_set_parameter", {"name": "Height", "value": 20, "unit": "deg"})
    assert result["ok"] is False
    assert "typed" in result["error"]


def test_an_unknown_parameter_lists_the_real_names(session):
    build_bracket(session)
    result = call(session, "catia_set_parameter", {"name": "Wdith", "value": 1, "unit": "mm"})
    assert result["ok"] is False
    assert "Height" in result["error"]


def test_a_cut_that_removes_everything_is_refused(session):
    build_bracket(session)
    assert call(
        session, "catia_sketch_rectangle", {"plane": "XY", "width_mm": 60, "height_mm": 20}
    )["ok"]
    result = call(session, "catia_pocket", {"sketch": "Sketch.2", "through_all": True})
    assert result["ok"] is False
    assert "more material than the part contains" in result["error"]


def test_a_hole_larger_than_the_part_is_refused(session):
    build_bracket(session)
    result = call(session, "catia_hole", {"face": "top", "position": "center", "diameter_mm": 500})
    assert result["ok"] is False
    assert "does not fit" in result["error"]


def test_features_cannot_be_built_before_a_document_exists(session):
    result = call(session, "catia_sketch_circle", {"plane": "XY", "diameter_mm": 10})
    assert result["ok"] is False
    assert "No document is open" in result["error"]


def test_padding_without_a_solid_then_measuring_explains_itself(session):
    assert call(session, "catia_new_part", {"name": "Empty"})["ok"]
    result = call(session, "catia_measure")
    assert result["ok"] is False
    assert "no solid geometry" in result["error"]


def test_every_mutating_tool_returns_post_state_not_ok(session):
    """The agent is told to react to what it sees; it cannot react to a boolean."""
    build_bracket(session)
    for tool, arguments in (
        ("catia_hole", {"face": "top", "position": "center", "diameter_mm": 6}),
        ("catia_fillet", {"radius_mm": 2}),
        ("catia_chamfer", {"length_mm": 1}),
        ("catia_update", {}),
    ):
        data = call(session, tool, arguments)["data"]
        assert data["mass_kg"] > 0, tool
        assert data["bounding_box_mm"]["size"], tool
        assert data["features"], tool


# -- the six additional tools -------------------------------------------------


def test_a_hex_polygon_sketch_reports_its_area(session):
    assert call(session, "catia_new_part", {"name": "Hex"})["ok"]
    data = call(
        session, "catia_sketch_polygon", {"plane": "XY", "sides": 6, "diameter_mm": 20}
    )["data"]
    assert data["shape"] == "polygon-6"
    assert data["area_mm2"] == pytest.approx(0.5 * 6 * 10**2 * math.sin(math.pi / 3), rel=1e-6)


def test_a_shaft_revolves_a_circle_into_a_sphere_shaped_solid(session):
    assert call(session, "catia_new_part", {"name": "Ball"})["ok"]
    assert call(session, "catia_sketch_circle", {"plane": "XY", "diameter_mm": 10})["ok"]
    data = call(session, "catia_shaft", {"sketch": "Sketch.1"})["data"]
    assert data["mass_kg"] > 0
    assert data["bounding_box_mm"]["size"] == [10, 10, 10]


def test_a_groove_removes_material_from_an_existing_solid(session):
    build_bracket(session)
    before = call(session, "catia_measure")["data"]["mass_kg"]
    assert call(session, "catia_sketch_circle", {"plane": "XY", "diameter_mm": 4})["ok"]
    after = call(session, "catia_groove", {"sketch": "Sketch.2"})["data"]["mass_kg"]
    assert after < before


def test_a_mirror_doubles_the_mass(session):
    build_bracket(session)
    before = call(session, "catia_measure")["data"]["mass_kg"]
    after = call(session, "catia_mirror", {"plane": "ZX"})["data"]["mass_kg"]
    assert after == pytest.approx(before * 2, rel=1e-6)


def test_delete_feature_removes_a_subtractive_feature_and_restores_its_volume(session):
    build_bracket(session)
    before = call(session, "catia_measure")["data"]["mass_kg"]
    hole = call(session, "catia_hole", {"face": "top", "position": "center", "diameter_mm": 4})
    after_hole = call(session, "catia_measure")["data"]["mass_kg"]
    assert after_hole < before

    restored = call(session, "catia_delete_feature", {"feature": hole["data"]["feature"]})["data"]
    assert restored["mass_kg"] == pytest.approx(before, rel=1e-6)


def test_a_revolve_profile_sweeps_a_rod_of_the_stated_diameter(session):
    # The profile sits beside the axis, so revolving it gives pi r^2 L -- the
    # closed form the real CATIA path was verified against on V5-R33.
    assert call(session, "catia_new_part", {"name": "Rod"})["ok"]
    profile = call(
        session,
        "catia_sketch_revolve_profile",
        {"plane": "ZX", "outer_diameter_mm": 15, "length_mm": 60},
    )["data"]
    assert profile["shape"] == "revolve-profile"

    data = call(session, "catia_shaft", {"sketch": profile["sketch"]})["data"]
    expected = math.pi * 7.5**2 * 60
    assert data["volume_mm3"] == pytest.approx(expected, rel=1e-6)


def test_a_revolve_profile_with_a_bore_sweeps_a_tube(session):
    assert call(session, "catia_new_part", {"name": "Tube"})["ok"]
    profile = call(
        session,
        "catia_sketch_revolve_profile",
        {
            "plane": "ZX",
            "outer_diameter_mm": 15,
            "inner_diameter_mm": 11,
            "length_mm": 60,
        },
    )["data"]
    data = call(session, "catia_shaft", {"sketch": profile["sketch"]})["data"]
    expected = math.pi * (7.5**2 - 5.5**2) * 60
    assert data["volume_mm3"] == pytest.approx(expected, rel=1e-6)


def test_a_bore_wider_than_the_outside_is_refused(session):
    assert call(session, "catia_new_part", {"name": "Impossible"})["ok"]
    result = call(
        session,
        "catia_sketch_revolve_profile",
        {"plane": "ZX", "outer_diameter_mm": 10, "inner_diameter_mm": 12, "length_mm": 20},
    )
    assert result["ok"] is False
    assert "smaller than" in result["error"]


def test_a_groove_profile_removes_exactly_the_ring_it_describes(session):
    assert call(session, "catia_new_part", {"name": "Grooved shaft"})["ok"]
    profile = call(
        session,
        "catia_sketch_revolve_profile",
        {"plane": "ZX", "outer_diameter_mm": 15, "length_mm": 60},
    )["data"]
    assert call(session, "catia_shaft", {"sketch": profile["sketch"]})["ok"]
    before = call(session, "catia_measure")["data"]["volume_mm3"]

    ring = call(
        session,
        "catia_sketch_groove_profile",
        {
            "plane": "ZX",
            "shaft_diameter_mm": 15,
            "width_mm": 3,
            "depth_mm": 2,
            "distance_from_end_mm": 20,
        },
    )["data"]
    after = call(session, "catia_groove", {"sketch": ring["sketch"]})["data"]["volume_mm3"]

    expected = math.pi * (7.5**2 - 5.5**2) * 3
    assert before - after == pytest.approx(expected, rel=1e-6)


def test_a_groove_deeper_than_the_shaft_radius_is_refused(session):
    assert call(session, "catia_new_part", {"name": "Cut through"})["ok"]
    result = call(
        session,
        "catia_sketch_groove_profile",
        {
            "plane": "ZX",
            "shaft_diameter_mm": 10,
            "width_mm": 2,
            "depth_mm": 6,
            "distance_from_end_mm": 5,
        },
    )
    assert result["ok"] is False
    assert "through the centre" in result["error"]


def test_there_is_no_pattern_tool_while_its_direction_cannot_be_controlled(session):
    # Removed rather than shipped: on a live V5-R33 the only direction reference
    # AddNewRectPattern accepts steps the copies diagonally, so a `direction`
    # argument would quietly build the wrong part. See the note in catia_com.py.
    result = call(
        session, "catia_pattern_rectangular", {"direction": "x", "count": 5, "spacing_mm": 8}
    )
    assert result["ok"] is False


def test_shelling_hollows_the_part_and_keeps_its_outside_size(session):
    build_bracket(session)
    before = call(session, "catia_measure")["data"]
    data = call(session, "catia_shell", {"thickness_mm": 2})["data"]
    assert data["volume_mm3"] < before["volume_mm3"]
    assert data["bounding_box_mm"]["size"] == before["bounding_box_mm"]["size"]


def test_a_wall_thicker_than_the_part_is_refused(session):
    build_bracket(session)
    result = call(session, "catia_shell", {"thickness_mm": 40})
    assert result["ok"] is False


def test_delete_feature_refuses_an_unknown_name(session):
    build_bracket(session)
    result = call(session, "catia_delete_feature", {"feature": "Pad.99"})
    assert result["ok"] is False
    assert "No feature named" in result["error"]


def test_delete_feature_refuses_an_additive_feature_the_mock_cannot_recompute(session):
    build_bracket(session)
    result = call(session, "catia_delete_feature", {"feature": "Pad.1"})
    assert result["ok"] is False
    assert "cannot recompute the solid" in result["error"]


def test_list_features_reports_the_build_order(session):
    build_bracket(session)
    data = call(session, "catia_list_features")["data"]
    names = [f["name"] for f in data["features"]]
    assert names == ["Sketch.1", "Pad.1"]


# -- real artefacts ----------------------------------------------------------


def test_the_exported_step_is_a_solid_the_geometry_reader_accepts(tmp_path, session):
    build_bracket(session)
    data = call(session, "catia_export_step", {"note": "v1", "max_inline_bytes": 10**8})["data"]
    path = tmp_path / data["filename"]
    path.write_bytes(base64.b64decode(data["content_b64"]))

    stats = inspect(path, "step")
    # Not merely "starts with ISO-10303": OpenCASCADE opened it and found one
    # closed solid of exactly the right volume. That is what makes the whole
    # mesh-and-solve chain runnable on Linux.
    assert stats["solid_count"] == 1
    assert stats["volume_mm3"] == pytest.approx(12000.0, rel=1e-6)
    assert stats["bounding_box"]["size"] == pytest.approx([60, 20, 10], abs=1e-3)


def test_the_captured_view_is_a_decodable_png(session):
    build_bracket(session)
    data = call(session, "catia_capture_view", {"view": "iso", "max_inline_bytes": 10**8})["data"]
    png = base64.b64decode(data["content_b64"])

    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    width, height, depth, colour = struct.unpack(">2I2B", png[16:26])
    assert (width, height) == (data["width_px"], data["height_px"])
    assert (depth, colour) == (8, 2)  # 8-bit truecolour
    # Decompress the pixels to prove the stream is real, not just framed.
    idat = png[png.index(b"IDAT") + 4 : png.index(b"IEND") - 4]
    assert len(zlib.decompress(idat)) == height * (width * 3 + 1)


def test_every_viewpoint_renders(session):
    build_bracket(session)
    for view in ("iso", "front", "back", "top", "bottom", "left", "right"):
        result = call(session, "catia_capture_view", {"view": view})
        assert result["ok"], view


# -- checkpoints and restore -------------------------------------------------


def test_restore_rolls_the_part_back(session):
    build_bracket(session)
    snapshot = call(session, "catia_checkpoint", {"label": "before", "max_inline_bytes": 10**8})
    assert snapshot["ok"]
    before = call(session, "catia_measure")["data"]["mass_kg"]

    assert call(session, "catia_hole", {"face": "top", "position": "center", "diameter_mm": 8})[
        "ok"
    ]
    assert call(session, "catia_measure")["data"]["mass_kg"] < before

    restored = call(
        session,
        "catia_restore",
        {"checkpoint": {"checkpoint_id": "c1", "remote_ref": snapshot["data"]["remote_ref"]}},
        approval_token="signed-by-the-server",
    )
    assert restored["ok"]
    assert call(session, "catia_measure")["data"]["mass_kg"] == pytest.approx(before)


def test_a_checkpoint_over_the_inline_ceiling_still_records_a_local_snapshot(session):
    build_bracket(session)
    data = call(session, "catia_checkpoint", {"label": "big", "max_inline_bytes": 1})["data"]
    # No cloud copy, but the workstation keeps one -- so the mutation is not
    # refused and the checkpoint is not a lie.
    assert data["inline"] is False
    assert data["content_b64"] is None
    assert Path(data["remote_ref"]).is_file()


def test_a_document_lost_from_the_workstation_is_restored_from_the_cloud_copy(
    session, mock, tmp_path
):
    """The mechanic that makes 'come back tomorrow' survive a reimaged laptop."""
    build_bracket(session)
    snapshot = call(session, "catia_checkpoint", {"label": "eod", "max_inline_bytes": 10**8})
    document = Path(mock.doc_path)
    document.unlink()

    reopened = call(
        session,
        "catia_open_document",
        {
            "doc_name": "Bracket",
            "remote_path": str(document),
            "fallback_checkpoint": {"content_b64": snapshot["data"]["content_b64"]},
        },
    )
    assert reopened["ok"]
    assert reopened["data"]["restored_from_checkpoint"] is True
    assert call(session, "catia_measure")["data"]["bounding_box_mm"]["size"] == [60, 20, 10]


def test_reopening_with_no_file_and_no_checkpoint_says_so(session):
    build_bracket(session)
    result = call(
        session, "catia_open_document", {"doc_name": "Gone", "remote_path": "/nope/gone.CATPart"}
    )
    assert result["ok"] is False
    assert "no stored checkpoint" in result["error"]


# -- failure handling --------------------------------------------------------


def test_an_unexpected_backend_exception_becomes_a_result_not_a_crash(session, mock):
    def explode(**_kwargs):
        raise RuntimeError("COM error 0x80004005")

    mock.measure = explode  # type: ignore[method-assign]
    result = call(session, "catia_measure")
    assert result["ok"] is False
    # The exception type is included because COM messages alone are unhelpful.
    assert "RuntimeError" in result["error"]


def test_a_wedged_catia_is_reported_rather_than_hanging(session, mock):
    import time as time_module

    def wedged() -> None:
        time_module.sleep(30)  # a modal dialog: the probe never returns

    mock.health = wedged  # type: ignore[method-assign]
    result = call(session, "catia_measure")
    assert result["ok"] is False
    assert "modal dialog" in result["error"]


def test_a_health_failure_is_reported_verbatim(session, mock):
    def dead() -> None:
        raise CatiaOperationError("CATIA stopped responding to automation")

    mock.health = dead  # type: ignore[method-assign]
    result = call(session, "catia_measure")
    assert result["ok"] is False
    assert "stopped responding" in result["error"]


def test_a_server_ping_is_answered_with_a_pong(session):
    session.handle_frame(json.dumps({"type": "ping", "t": 99}))
    assert session.sent[-1] == {"type": "pong", "t": 99}


def test_junk_frames_are_ignored(session):
    session.handle_frame("}{")
    session.handle_frame('"a string"')
    session.handle_frame(json.dumps({"type": "unheard-of"}))
    assert session.sent == []


def test_the_hello_frame_reports_mock_mode_honestly(session):
    hello = session.hello_frame()
    assert hello["type"] == "hello"
    assert hello["mock"] is True
    assert "part" in hello["capabilities"]
