"""Landing a design in CATIA, and saying whether what landed is what was iterated on (5.6).

Decision 1 says the agent designs on the open kernel and the result lands in CATIA. The compiled
plan, both runners and the comparator all existed; nothing joined them, so "send it to CATIA"
was a sentence and not a route. These tests hold the joined thing to what it claims.

Three layers, each proving something the others cannot:

* **The comparison** is offline and pure: what counts, what does not, and the third outcome
  (*unmeasured*) that the shared comparator cannot express.
* **The flow** runs through `land_in_catia` with a real OCCT on the left and an injected seat on
  the right, because the claims about *how it stops* -- the kernel failing, the seat refusing, the
  seat vanishing halfway -- need a seat that misbehaves on cue.
* **The route** goes over HTTP into the real daemon in mock mode on a real WebSocket, because the
  claim that a part lands in a conversation of its own, bound to a document, is about the path.

What none of it can say: whether a real V5 seat builds the same part. The mock computes volume
from a bounding box less swept cuts, so an agreement here proves the plumbing and the
comparator, never the geometry. That is THE QUEUE G8.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.catia.dispatch import CatiaError, CatiaUnavailable
from app.catia.landing import (
    AGREES,
    DIFFERS,
    NOT_COMPARED,
    UNMEASURED,
    Finding,
    Landing,
    compare_measurements,
    land_in_catia,
)
from app.core import designs
from app.core.config import settings
from app.design import DesignSpec, FeatureSpec, compile_spec, ref
from app.geometry import backends
from app.models import Conversation
from app.models.catia import CatiaDocument
from tests.test_catia_api import second_user
from tests.test_catia_e2e import bridge, run  # noqa: F401 - fixture re-export

SEND = "/api/v1/kernel/conversations/{id}/send-to-catia"


def _plate(*, name: str = "Plate", extra: list[FeatureSpec] | None = None) -> DesignSpec:
    return DesignSpec.of(
        name,
        material="steel-1018",
        features=[
            FeatureSpec("p.profile", "catia_sketch_create", {"support": "XY"}),
            FeatureSpec(
                "p.outline",
                "catia_sketch_rectangle",
                {"sketch": ref("p.profile"), "width_mm": 60.0, "height_mm": 20.0},
            ),
            FeatureSpec(
                "p.body", "catia_pad", {"sketch": ref("p.profile"), "length_mm": 10.0}
            ),
            *(extra or []),
        ],
    )


def _payload(**overrides):
    base = {
        "volume_mm3": 12000.0,
        "surface_area_mm2": 4000.0,
        "centre_of_mass_mm": [30.0, 10.0, 5.0],
        "bounding_box_mm": {"size": [60.0, 20.0, 10.0]},
        "face_count": 6,
        "edge_count": 12,
        "mass_kg": 0.0942,
    }
    base.update(overrides)
    return base


def _by_name(findings) -> dict[str, Finding]:
    return {finding.quantity: finding for finding in findings}


def _landing(findings, *, landed: bool = True) -> Landing:
    return Landing(design="Plate", plan_digest="d", landed=landed, findings=tuple(findings))


# -- the comparison -------------------------------------------------------------------------


class TestTheComparison:
    def test_two_identical_builds_agree_on_everything_that_counts(self) -> None:
        findings = compare_measurements(_payload(), _payload())

        assert {f.verdict for f in findings} == {AGREES}
        assert _landing(findings).agrees

    def test_a_part_that_did_not_land_is_never_agreed_whatever_was_measured(self) -> None:
        # `Landing` is data a caller can build; "agrees" must not be derivable from findings alone.
        findings = compare_measurements(_payload(), _payload())

        assert _landing(findings, landed=True).agrees
        assert not _landing(findings, landed=False).agrees

    def test_the_bridges_spelling_of_the_centre_of_mass_is_the_same_quantity(self) -> None:
        seat = _payload()
        seat["center_of_gravity_mm"] = seat.pop("centre_of_mass_mm")

        findings = _by_name(compare_measurements(_payload(), seat))

        assert findings["centre_of_mass_mm"].verdict == AGREES

    def test_a_volume_that_differs_names_both_numbers_and_fails_the_landing(self) -> None:
        findings = compare_measurements(_payload(), _payload(volume_mm3=12010.0))

        volume = _by_name(findings)["volume_mm3"]
        assert volume.verdict == DIFFERS
        assert (volume.occt, volume.catia) == (12000.0, 12010.0)
        landing = _landing(findings)
        assert not landing.agrees
        assert [f.quantity for f in landing.differing] == ["volume_mm3"]
        assert "DISAGREE on volume_mm3" in landing.summary()

    def test_a_centre_of_mass_that_moved_is_caught_though_the_volume_agrees(self) -> None:
        # The most sensitive check there is: a feature in the wrong place leaves the volume alone.
        findings = _by_name(
            compare_measurements(_payload(), _payload(centre_of_mass_mm=[30.0, 10.0, 6.0]))
        )

        assert findings["volume_mm3"].verdict == AGREES
        assert findings["centre_of_mass_mm"].verdict == DIFFERS

    def test_a_bounding_box_that_differs_is_caught(self) -> None:
        findings = _by_name(
            compare_measurements(
                _payload(), _payload(bounding_box_mm={"size": [60.0, 20.0, 12.0]})
            )
        )

        assert findings["bounding_box_size_mm"].verdict == DIFFERS

    def test_the_seats_four_decimal_rounding_is_not_a_divergence(self) -> None:
        # Measured 2026-09-11: CATIA prints 22869.0266 for 22869.026644707676.
        findings = _by_name(
            compare_measurements(
                _payload(volume_mm3=22869.026644707676), _payload(volume_mm3=22869.0266)
            )
        )

        assert findings["volume_mm3"].verdict == AGREES

    def test_a_quantity_the_seat_did_not_report_is_unmeasured_and_not_a_difference(self) -> None:
        # The bridge reports no face count on a seat. "Said nothing" and "counted differently"
        # are different facts with different fixes.
        seat = _payload()
        del seat["face_count"]

        findings = _by_name(compare_measurements(_payload(), seat))

        assert findings["face_count"].verdict == UNMEASURED
        assert "CATIA did not report it" in (findings["face_count"].note or "")
        landing = _landing(compare_measurements(_payload(), seat))
        assert landing.agrees  # what was measured agrees
        assert "Not reported by CATIA: face_count" in landing.summary()

    def test_a_face_count_that_differs_when_both_sides_report_one_is_a_difference(self) -> None:
        findings = _by_name(compare_measurements(_payload(), _payload(face_count=7)))

        assert findings["face_count"].verdict == DIFFERS

    def test_nothing_measured_on_both_sides_is_not_a_match(self) -> None:
        findings = compare_measurements(_payload(), {})

        assert {f.verdict for f in findings} == {UNMEASURED}
        landing = _landing(findings)
        assert not landing.agrees
        assert "not known to match" in landing.summary()

    def test_edge_counts_are_never_compared(self) -> None:
        # OCCT carries a seam edge per closed cylindrical face; CATIA carries none.
        findings = compare_measurements(_payload(edge_count=15), _payload(edge_count=14))

        assert "edge_count" not in {f.quantity for f in findings}
        assert "edge_count" in NOT_COMPARED and "seam edge" in NOT_COMPARED["edge_count"]
        assert "solid_count" in NOT_COMPARED

    def test_a_mass_that_differs_with_the_volume_agreeing_is_the_material_not_the_geometry(
        self,
    ) -> None:
        # Measured 2026-09-12: the two sides hold 7860 and 7870 kg/m3 for "steel".
        findings = compare_measurements(_payload(), _payload(mass_kg=0.0943))

        mass = _by_name(findings)[ "mass_kg"]
        assert mass.verdict == DIFFERS and not mass.counts
        assert "different densities" in (mass.note or "")
        assert _landing(findings).agrees, "a mass difference must not fail the geometry"

    def test_a_mass_that_differs_along_with_the_volume_is_not_blamed_on_the_material(self) -> None:
        findings = _by_name(
            compare_measurements(_payload(), _payload(volume_mm3=13000.0, mass_kg=0.1))
        )

        assert findings["mass_kg"].verdict == DIFFERS
        assert "densities" not in (findings["mass_kg"].note or "")


# -- the flow ------------------------------------------------------------------------------


class _Recorder:
    """A seat stand-in that builds on a second open kernel and records how it was called."""

    def __init__(self, *, fail_on: int | None = None, error: Exception | None = None) -> None:
        from app.kernel import OcctRunner

        self._inner = OcctRunner()
        self.calls: list[str] = []
        self.backends_seen: list[str] = []
        self._fail_on = fail_on
        self._error = error

    def __call__(self, tool, arguments):
        self.calls.append(tool)
        self.backends_seen.append(backends.selected_backend())
        if self._fail_on is not None and len(self.calls) == self._fail_on:
            raise self._error or CatiaError("refused")
        return self._inner(tool, arguments)


class TestTheFlow:
    def test_a_design_both_sides_build_lands_and_agrees(self, db_session) -> None:
        seat = _Recorder()

        landing = land_in_catia(
            db_session,
            user_id="u",
            plan=compile_spec(_plate()),
            source_conversation=Conversation(owner_id="u", title="x"),
            seat_runner=seat,
        )

        assert landing.landed and landing.agrees
        assert landing.stopped_on is None
        assert landing.calls_on_seat == len(seat.calls) - 1  # the measurement is not a build call
        assert "landed in CATIA and matches" in landing.summary()
        by = _by_name(landing.findings)
        assert by["volume_mm3"].occt == pytest.approx(60 * 20 * 10)
        assert by["face_count"].verdict == AGREES

    def test_the_seat_is_built_on_whatever_the_deployment_setting_says(
        self, db_session, monkeypatch
    ) -> None:
        # `call_catia` chooses a kernel from the deployment's setting; landing is the one place
        # that must use the seat whichever it is. The override must also not leak.
        monkeypatch.setattr(settings, "geometry_backend", "occt")
        seat = _Recorder()

        land_in_catia(
            db_session,
            user_id="u",
            plan=compile_spec(_plate()),
            source_conversation=Conversation(owner_id="u", title="x"),
            seat_runner=seat,
        )

        assert set(seat.backends_seen) == {"catia"}
        assert backends.selected_backend() == "occt"

    def test_a_design_the_open_kernel_cannot_build_never_reaches_the_seat(
        self, db_session
    ) -> None:
        seat = _Recorder()

        def refusing(tool, arguments):
            raise ValueError("The fillet is larger than the face it rounds.")

        landing = land_in_catia(
            db_session,
            user_id="u",
            plan=compile_spec(_plate()),
            source_conversation=Conversation(owner_id="u", title="x"),
            occt_runner=refusing,
            seat_runner=seat,
        )

        assert not landing.landed and landing.stopped_on == "occt"
        assert "larger than the face" in (landing.reason or "")
        assert seat.calls == [], "nothing may be sent when the kernel could not build it"
        assert "nothing was sent" in landing.summary()

    def test_a_seat_that_refuses_partway_says_where_and_does_not_claim_a_match(
        self, db_session
    ) -> None:
        seat = _Recorder(fail_on=3, error=CatiaError("The pad was refused."))

        landing = land_in_catia(
            db_session,
            user_id="u",
            plan=compile_spec(_plate()),
            source_conversation=Conversation(owner_id="u", title="x"),
            seat_runner=seat,
        )

        assert not landing.landed and not landing.agrees
        assert landing.stopped_on == "catia"
        assert "The pad was refused." in (landing.reason or "")
        assert landing.findings == ()
        assert "incomplete" in landing.summary()

    def test_a_seat_that_vanishes_partway_is_unavailable_not_a_refusal(self, db_session) -> None:
        seat = _Recorder(fail_on=3, error=CatiaUnavailable("The workstation went offline."))

        landing = land_in_catia(
            db_session,
            user_id="u",
            plan=compile_spec(_plate()),
            source_conversation=Conversation(owner_id="u", title="x"),
            seat_runner=seat,
        )

        assert landing.stopped_on == "catia-unavailable"
        assert landing.calls_on_seat == 2
        assert "went offline" in (landing.reason or "")

    def test_a_seat_that_cannot_measure_makes_the_landing_unknown_not_agreed(
        self, db_session
    ) -> None:
        class CannotMeasure(_Recorder):
            def __call__(self, tool, arguments):
                if tool == "catia_measure":
                    raise CatiaError("measure is not available")
                return super().__call__(tool, arguments)

        landing = land_in_catia(
            db_session,
            user_id="u",
            plan=compile_spec(_plate()),
            source_conversation=Conversation(owner_id="u", title="x"),
            seat_runner=CannotMeasure(),
        )

        assert landing.landed and not landing.agrees
        assert {f.verdict for f in landing.findings} == {UNMEASURED}


# -- the route -----------------------------------------------------------------------------


def _record(bridge, spec: DesignSpec) -> None:
    designs.save(bridge["db"], bridge["conversation"], spec)
    bridge["db"].commit()


def _send(bridge, conversation_id: str | None = None):
    return bridge["client"].post(SEND.format(id=conversation_id or bridge["conversation"].id))


class TestTheRoute:
    def test_the_design_lands_in_a_conversation_of_its_own(self, bridge) -> None:
        _record(bridge, _plate())

        response = _send(bridge)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["landed"] is True, body["summary"]
        assert body["design"] == "Plate"
        assert bridge["backend"].doc_name == "Plate"
        landing_id = body["landing_conversation_id"]
        assert landing_id and landing_id != bridge["conversation"].id
        db = bridge["db"]
        bound = db.scalars(
            select(CatiaDocument).where(CatiaDocument.conversation_id == landing_id)
        ).all()
        assert [row.doc_name for row in bound] == ["Plate"]
        # The conversation that was iterated in is untouched: its binding is not overwritten.
        assert (
            db.scalars(
                select(CatiaDocument).where(
                    CatiaDocument.conversation_id == bridge["conversation"].id
                )
            ).first()
            is None
        )
        landed = db.get(Conversation, landing_id)
        assert landed is not None and landed.owner_id == bridge["user_id"]
        assert "landed in CATIA" in landed.title

    def test_it_lands_on_the_seat_even_when_the_deployment_builds_on_the_open_kernel(
        self, bridge, monkeypatch
    ) -> None:
        _record(bridge, _plate())
        monkeypatch.setattr(settings, "geometry_backend", "occt")

        response = _send(bridge)

        assert response.status_code == 200, response.text
        assert bridge["backend"].doc_name == "Plate", "it must reach the daemon, not the kernel"

    def test_the_report_says_which_quantities_were_compared_and_which_were_not(
        self, bridge
    ) -> None:
        _record(bridge, _plate())

        body = _send(bridge).json()

        quantities = {f["quantity"]: f for f in body["findings"]}
        assert quantities["volume_mm3"]["verdict"] == AGREES
        assert quantities["bounding_box_size_mm"]["verdict"] == AGREES
        # The mock seat reports no face count: unmeasured, and it does not fail the landing.
        assert quantities["face_count"]["verdict"] == UNMEASURED
        assert set(body["not_compared"]) == {"edge_count", "solid_count"}

    def test_a_placement_difference_is_caught_end_to_end(self, bridge) -> None:
        # Not contrived: the mock seat draws a rectangle with its corner on the origin, where the
        # open kernel and a real seat both centre it ("omit `at` and it is centred on the sketch
        # origin"). Volume, area and the box are the same part; only where it sits differs, and
        # the centre of mass is the one quantity that sees that.
        _record(bridge, _plate())

        body = _send(bridge).json()

        by = {f["quantity"]: f for f in body["findings"]}
        assert by["volume_mm3"]["verdict"] == AGREES
        assert by["surface_area_mm2"]["verdict"] == AGREES
        assert by["bounding_box_size_mm"]["verdict"] == AGREES
        assert by["centre_of_mass_mm"]["verdict"] == DIFFERS
        assert body["agrees"] is False
        assert "DISAGREE on centre_of_mass_mm" in body["summary"]

    def test_a_divergence_on_the_seat_is_reported_with_both_numbers(self, bridge, monkeypatch) -> None:
        _record(bridge, _plate())
        seat = bridge["backend"]
        real_measure = seat.measure
        monkeypatch.setattr(
            seat, "measure", lambda: {**real_measure(), "volume_mm3": 12345.0}
        )

        response = _send(bridge)

        assert response.status_code == 200
        body = response.json()
        assert body["landed"] is True and body["agrees"] is False
        volume = {f["quantity"]: f for f in body["findings"]}["volume_mm3"]
        assert volume["verdict"] == DIFFERS
        assert volume["occt"] == pytest.approx(12000.0) and volume["catia"] == 12345.0
        assert "DISAGREE on volume_mm3" in body["summary"]

    def test_a_design_the_kernel_cannot_build_is_422_and_the_seat_is_untouched(
        self, bridge
    ) -> None:
        too_big = FeatureSpec(
            "p.edges",
            "catia_fillet",
            {"feature": ref("p.body"), "radius_mm": 50.0, "edges": "vertical"},
        )
        _record(bridge, _plate(extra=[too_big]))

        response = _send(bridge)

        assert response.status_code == 422
        assert "nothing was sent" in response.json()["detail"]
        assert bridge["backend"].doc_name is None

    def test_no_recorded_design_is_409_and_says_why(self, bridge) -> None:
        response = _send(bridge)

        assert response.status_code == 409
        assert "no recorded design" in response.json()["detail"]
        assert bridge["backend"].doc_name is None

    def test_an_offline_workstation_is_503_and_leaves_nothing_behind(self, bridge) -> None:
        from app.catia.connection import registry

        _record(bridge, _plate())
        for connection in list(registry._by_device.values()):
            connection.close("test")
        before = len(bridge["db"].scalars(select(Conversation)).all())

        response = _send(bridge)

        assert response.status_code == 503
        assert "nothing was sent" in response.json()["detail"]
        assert len(bridge["db"].scalars(select(Conversation)).all()) == before

    def test_another_users_conversation_is_a_404_not_a_403(self, bridge) -> None:
        _record(bridge, _plate())
        conversation_id = bridge["conversation"].id
        second_user(bridge["client"])

        response = _send(bridge, conversation_id)

        assert response.status_code == 404
        assert bridge["backend"].doc_name is None


# -- the mock seat can be sent a design ------------------------------------------------------


def _mock(tmp_path):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from catia_bridge.mock_catia import MockCatia

    seat = MockCatia(tmp_path / "catia")
    seat.new_part(name="Plate")
    return seat


class TestTheMockSeatTakesACompiledDesign:
    """A compiled design opens with `catia_sketch_create` and renames what it makes.

    The mock implemented neither, so no design could be built on it and the landing flow had
    nothing to be tested against. These pin the three additions to the behaviour the registry
    documents, so the mock does not drift into accepting what a real seat refuses.
    """

    def test_a_sketch_is_created_empty_drawn_into_by_name_and_padded(self, tmp_path) -> None:
        seat = _mock(tmp_path)
        created = seat.sketch_create(support="XY")
        drawn = seat.sketch_rectangle(sketch=created["feature"], width_mm=60, height_mm=20)
        seat.pad(sketch=created["feature"], length_mm=10)

        assert drawn["feature"] == created["feature"]
        assert seat.measure()["bounding_box_mm"]["size"] == [60.0, 20.0, 10.0]

    def test_a_sketch_that_holds_a_profile_refuses_a_second(self, tmp_path) -> None:
        from catia_bridge.backend import CatiaOperationError

        seat = _mock(tmp_path)
        name = seat.sketch_create(support="XY")["feature"]
        seat.sketch_rectangle(sketch=name, width_mm=60, height_mm=20)

        with pytest.raises(CatiaOperationError, match="already holds a rectangle"):
            seat.sketch_rectangle(sketch=name, width_mm=5, height_mm=5)

    def test_a_named_face_is_refused_because_the_mock_has_no_brep_to_find_one_on(
        self, tmp_path
    ) -> None:
        from catia_bridge.backend import CatiaOperationError

        with pytest.raises(CatiaOperationError, match="not one of the XY, YZ, ZX planes"):
            _mock(tmp_path).sketch_create(support="Pad.1#top")

    def test_the_one_shot_rectangle_still_needs_a_plane_or_a_sketch(self, tmp_path) -> None:
        from catia_bridge.backend import CatiaOperationError

        with pytest.raises(CatiaOperationError, match="give `sketch`"):
            _mock(tmp_path).sketch_rectangle(width_mm=5, height_mm=5)

    def test_a_rename_follows_the_sketch_into_the_features_that_name_it(self, tmp_path) -> None:
        seat = _mock(tmp_path)
        name = seat.sketch_create(support="XY")["feature"]
        seat.sketch_rectangle(sketch=name, width_mm=60, height_mm=20)
        seat.pad(sketch=name, length_mm=10)

        seat.feature_rename(feature=name, name="plate_profile")

        pad = next(f for f in seat.features if f["type"] == "Pad")
        assert pad["sketch"] == "plate_profile"
        assert "plate_profile" in seat.sketches and name not in seat.sketches

    def test_a_rename_refuses_an_unknown_feature_and_a_taken_name(self, tmp_path) -> None:
        from catia_bridge.backend import CatiaOperationError

        seat = _mock(tmp_path)
        first = seat.sketch_create(support="XY")["feature"]
        second = seat.sketch_create(support="XY")["feature"]

        with pytest.raises(CatiaOperationError, match="No feature named"):
            seat.feature_rename(feature="Nothing.9", name="x")
        with pytest.raises(CatiaOperationError, match="already exists"):
            seat.feature_rename(feature=first, name=second)
