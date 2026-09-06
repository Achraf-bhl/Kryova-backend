"""The seat says which arguments it cannot take, and the model is never offered them.

`implemented_tools` answers "can this bridge run this tool at all", and the
server offers the intersection so a model is never handed a tool that would
fail on the workstation it is connected to. This is the same contract one level
down -- "can it take this *argument*" -- and it exists because the gap was
measured on a real V5-R33 seat, ladder prompt H4 run 9, 2026-09-06:

    CATIA: pad -- CATIA refused catia_pad: catia_pad accepts 'limit' in its
    schema, but this bridge does not implement that option yet.

The refusal is a good one: it names the option, says nothing was changed, and
the retry without `limit` built the pad. It is still one round of twenty spent
discovering something the daemon knew before the conversation started -- and on
a turn that ended by running out of rounds, that round was the margin.

Nineteen operations carry the gap (`test_backend_signatures.py::KNOWN_NARROWER`),
so it is a seam with no report across it rather than one tool's oversight. The
registry is one declaration read by four consumers and only the backend method
is hand-written, so the method is the only one that can fall behind -- and now
it says so at connect time.

Offline throughout: signatures and dictionaries, no CATIA and no socket.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from catia_bridge.backend import narrowed_options  # noqa: E402
from catia_bridge.generated_tools import TOOLS  # noqa: E402
from catia_bridge.mock_catia import MockCatia  # noqa: E402

from app.catia.connection import BridgeError, BridgeHello  # noqa: E402
from app.catia.dispatch import _without_unavailable_options  # noqa: E402
from app.catia.tool_specs import CATIA_TOOL_SPECS  # noqa: E402

SPECS = {spec.name: spec for spec in CATIA_TOOL_SPECS}


@pytest.fixture
def backend(tmp_path: Path) -> MockCatia:
    return MockCatia(tmp_path)


def _hello(**extra: object) -> dict[str, object]:
    return {"type": "hello", "catia_version": "V5-R33", **extra}


class TestWhatTheBackendReports:
    def test_pad_reports_the_option_that_cost_a_round(self, backend: MockCatia) -> None:
        assert "limit" in narrowed_options(backend)["catia_pad"]

    def test_it_agrees_with_what_the_call_would_refuse(self, backend: MockCatia) -> None:
        """The report and the refusal must come from one source, or the model
        is told one thing and gets another. Verified against the refusal path
        itself, for every tool and every option reported."""
        from catia_bridge.backend import unimplemented_options
        from catia_bridge.generated_tools import TOOL_METHODS

        for tool, options in narrowed_options(backend).items():
            for option in options:
                refusal = unimplemented_options(
                    tool, TOOL_METHODS[tool], backend, {option: "anything"}
                )
                assert refusal is not None, f"{tool}.{option} reported but not refused"

    def test_an_option_the_method_takes_is_not_reported(self, backend: MockCatia) -> None:
        assert "length_mm" not in narrowed_options(backend).get("catia_pad", ())
        assert "sketch" not in narrowed_options(backend).get("catia_pad", ())

    def test_a_tool_with_no_gap_is_absent_entirely(self, backend: MockCatia) -> None:
        """A dictionary listing every tool with an empty value would be sent on
        every connect and mean nothing."""
        reported = narrowed_options(backend)
        assert all(options for options in reported.values())
        assert len(reported) < len(TOOLS)

    def test_it_is_structural_not_a_hand_written_list(self) -> None:
        """It reads live signatures, so it cannot drift from what the method
        will do. Verified by adding a parameter and watching it disappear."""
        import tempfile

        class _Wider(MockCatia):
            def pad(self, *args, limit=None, **kwargs):  # type: ignore[override]
                return {}

        wider = _Wider(Path(tempfile.mkdtemp()))
        assert "limit" not in narrowed_options(wider).get("catia_pad", ())


class TestTheHelloFrameCarriesIt:
    def test_the_daemon_puts_it_in_hello(self, backend: MockCatia) -> None:
        """A report nothing sends is a report nobody reads."""
        from catia_bridge.session import BridgeSession

        session = BridgeSession(
            backend, bridge_version="1.0.0", hostname="test", send=lambda frame: None
        )
        frame = session.hello_frame()
        assert "limit" in frame["narrowed"]["catia_pad"]

    def test_the_server_parses_it(self) -> None:
        hello = BridgeHello.parse(_hello(narrowed={"catia_pad": ["limit", "up_to"]}))
        assert hello.unavailable_options("catia_pad") == ("limit", "up_to")

    def test_a_daemon_that_says_nothing_is_read_as_able(self) -> None:
        """An older daemon predates the field. Reading silence as "cannot take
        anything" would strip every optional argument from a bridge that
        supports them all."""
        hello = BridgeHello.parse(_hello())
        assert hello.unavailable_options("catia_pad") == ()

    def test_a_non_object_is_refused_at_the_door(self) -> None:
        with pytest.raises(BridgeError, match="narrowed"):
            BridgeHello.parse(_hello(narrowed=["catia_pad"]))

    def test_junk_entries_are_dropped_not_believed(self) -> None:
        hello = BridgeHello.parse(_hello(narrowed={"catia_pad": "limit", "catia_pocket": ["up_to"]}))
        assert hello.unavailable_options("catia_pad") == ()
        assert hello.unavailable_options("catia_pocket") == ("up_to",)

    def test_a_hostile_daemon_is_bounded(self) -> None:
        """Peer-supplied, so it is capped like everything else in the frame.
        The worst it can do is offer the model fewer arguments."""
        hello = BridgeHello.parse(
            _hello(narrowed={f"tool{i}": ["x" * 400] * 200 for i in range(2_000)})
        )
        assert len(hello.narrowed) <= 1_024
        assert all(len(options) <= 64 for options in hello.narrowed.values())
        assert all(len(option) <= 120 for options in hello.narrowed.values() for option in options)


class TestTheSchemaTheModelSees:
    def test_the_option_is_gone(self) -> None:
        stripped = _without_unavailable_options(SPECS["catia_pad"], ("limit", "up_to"))
        assert "limit" not in stripped.parameters["properties"]
        assert "up_to" not in stripped.parameters["properties"]

    def test_everything_else_survives(self) -> None:
        stripped = _without_unavailable_options(SPECS["catia_pad"], ("limit",))
        assert "length_mm" in stripped.parameters["properties"]
        assert stripped.parameters["required"] == SPECS["catia_pad"].parameters["required"]
        assert stripped.name == "catia_pad"
        assert stripped.description == SPECS["catia_pad"].description

    def test_a_required_argument_is_never_stripped(self) -> None:
        """A tool that cannot take a required argument is broken, not narrowed.
        Hiding the field turns a refusal that names the problem into a call
        that fails for a reason nothing states."""
        stripped = _without_unavailable_options(SPECS["catia_pad"], ("sketch",))
        assert "sketch" in stripped.parameters["properties"]

    def test_the_registry_is_not_mutated(self) -> None:
        """The specs are module-level and shared by every user on the server.
        Editing one in place would narrow the tool for everybody, permanently,
        because of one workstation."""
        _without_unavailable_options(SPECS["catia_pad"], ("limit",))
        assert "limit" in SPECS["catia_pad"].parameters["properties"]

    def test_nothing_to_strip_returns_the_spec_itself(self) -> None:
        assert _without_unavailable_options(SPECS["catia_pad"], ()) is SPECS["catia_pad"]

    def test_an_option_this_tool_does_not_have_is_ignored(self) -> None:
        assert _without_unavailable_options(SPECS["catia_pad"], ("nonsense",)) is SPECS["catia_pad"]

    def test_every_tool_the_mock_narrows_still_has_its_required_fields(
        self, backend: MockCatia
    ) -> None:
        """The end-to-end property: after stripping, every offered tool is
        still callable."""
        for tool, options in narrowed_options(backend).items():
            spec = SPECS.get(tool)
            if spec is None:
                continue
            stripped = _without_unavailable_options(spec, tuple(options))
            required = set(stripped.parameters.get("required") or ())
            assert required <= set(stripped.parameters["properties"]), tool
