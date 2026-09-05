"""The OCCT geometry kernel: naming that survives regeneration, and honest numbers.

Master plan Phase 1. Each test is named after the failure it prevents rather than the
function it calls, matching the design suite.

The two claims that matter, and why they are tested the way they are:

* **Persistent naming survives a rebuild.** This is the whole of Layer B's promise. The
  test regenerates a part at different dimensions and checks the name resolves to the
  *new* corresponding face — verified by area against a closed-form value, not against
  a recorded number, so a change that silently resolves to the wrong face fails.
* **Measurements are right, not merely stable.** Volumes and areas are checked against
  closed-form solutions (π r² h, and so on) exactly as the solver tests are, because
  a geometry kernel that agrees with its own previous output is not verified.

These tests **skip** where OCCT is absent rather than fail: `app/kernel/` imports
everywhere by contract, and the rest of the suite must keep running on a machine
without a 700 MB dependency. That is the same reason the CATIA bridge tests do not
demand Windows.
"""

import math

import pytest

from app.kernel import available
from app.kernel.errors import GeometryError, NamingError, OperationNotSupported

pytestmark = pytest.mark.skipif(
    not available(), reason="OCCT (cadquery-ocp) is not installed in this environment"
)

TOL = 1e-6


# -- the contract that holds with or without a kernel -------------------------


def test_the_package_imports_without_the_kernel_installed() -> None:
    """`app/kernel/` must import on a machine that will never have OCCT.

    Not marked skipif on purpose — this is the one thing that must hold everywhere,
    and it is what keeps the other 2,849 tests runnable without the dependency.
    """
    import app.kernel  # noqa: F401
    from app.kernel.occt import binding

    assert isinstance(binding.available(), bool)


test_the_package_imports_without_the_kernel_installed.pytestmark = []  # type: ignore[attr-defined]


class TestMeasurementIsVerifiedAgainstClosedForm:
    def test_a_cylinder_has_the_volume_the_formula_says(self) -> None:
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Rod"})
        result = runner(
            "catia_surface_primitive",
            {"kind": "cylinder", "radius_mm": 20.0, "length_mm": 50.0},
        )

        assert result["volume_mm3"] == pytest.approx(math.pi * 20.0**2 * 50.0, abs=TOL)

    def test_a_sphere_has_the_area_the_formula_says(self) -> None:
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Ball"})
        result = runner("catia_surface_primitive", {"kind": "sphere", "radius_mm": 10.0})

        assert result["surface_area_mm2"] == pytest.approx(4 * math.pi * 100.0, abs=1e-4)
        assert result["volume_mm3"] == pytest.approx(4 / 3 * math.pi * 1000.0, abs=1e-4)

    def test_mass_is_kilograms_and_converts_exactly_once(self) -> None:
        """mm³ × kg/m³ × 1e-9 = kg. The codebase's only unit conversion."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Rod"})
        runner("catia_set_material", {"material": "steel-1018"})
        result = runner(
            "catia_surface_primitive",
            {"kind": "cylinder", "radius_mm": 20.0, "length_mm": 50.0},
        )

        volume = math.pi * 400.0 * 50.0
        assert result["mass_kg"] == pytest.approx(volume * 7870.0 * 1e-9, abs=1e-9)

    def test_a_part_with_no_material_reports_the_mass_as_provisional(self) -> None:
        """Never invent a density: a mass from a guessed density looks measured."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Rod"})
        result = runner("catia_surface_primitive", {"kind": "sphere", "radius_mm": 5.0})

        assert "mass_kg" not in result
        assert result["mass_is_provisional"] is True

    def test_the_bounding_box_is_tight(self) -> None:
        """OCCT's default box is conservative; a loose one passes envelope checks it shouldn't."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Ball"})
        result = runner("catia_surface_primitive", {"kind": "sphere", "radius_mm": 10.0})

        assert result["bounding_box_mm"]["size"] == pytest.approx([20.0, 20.0, 20.0], abs=1e-6)

    def test_density_comes_from_the_solvers_material_table(self) -> None:
        """One source of truth for a physical constant, not a copy that can drift.

        A second table would eventually disagree with `app.solve.materials`, and the
        symptom is a part that weighs one thing in the geometry report and another in
        the simulation built from it.
        """
        from app.kernel import OcctRunner
        from app.solve.materials import MATERIALS

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Rod"})
        result = runner("catia_set_material", {"material": "titanium-ti6al4v"})

        assert result["density_kg_m3"] == pytest.approx(
            MATERIALS["titanium-ti6al4v"].density_kg_m3
        )

    def test_a_server_supplied_density_wins_over_the_lookup(self) -> None:
        """`density_kg_m3` is a server-supplied field on the operation; honour it."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Rod"})
        result = runner(
            "catia_set_material", {"material": "steel-1018", "density_kg_m3": 7900.0}
        )

        assert result["density_kg_m3"] == pytest.approx(7900.0)


class TestPersistentNamingSurvivesRegeneration:
    """The claim Layer B rests on, and the reason Phase 1 is eight months."""

    @staticmethod
    def _slab(doc_root, dx: float, dy: float, dz: float, radius: float):
        """Build box→fillet into a fresh document, returning what the test needs."""
        from app.kernel.occt.binding import symbol
        from app.kernel.occt.naming import evolution_of, record_derived, record_primitive
        from app.kernel.occt.topology import edges, endpoints, shape_list

        box = symbol("BRepPrimAPI_MakeBox")(dx, dy, dz).Shape()
        record_primitive(doc_root["box"].labels, box)

        # The vertical edge nearest the origin, chosen geometrically. Picking edges[3]
        # would name a *different* edge on a differently-sized box and make the whole
        # test meaningless.
        vertical = []
        for edge in edges(box):
            points = endpoints(edge)
            if len(points) == 2 and abs(points[0][2] - points[1][2]) > 1e-9:
                mid_x = (points[0][0] + points[1][0]) / 2
                mid_y = (points[0][1] + points[1][1]) / 2
                vertical.append((mid_x**2 + mid_y**2, edge))
        target = min(vertical, key=lambda pair: pair[0])[1]

        maker = symbol("BRepFilletAPI_MakeFillet")(box)
        maker.Add(radius, target)
        maker.Build()
        solid = maker.Shape()

        modified, generated = evolution_of(maker, box)
        record_derived(
            doc_root["fillet"].labels,
            result=solid,
            source=box,
            modified=modified,
            generated=generated,
        )
        made = shape_list(maker.Generated(target))
        return solid, (symbol("TopoDS").Face_s(made[0]) if made else None)

    def test_a_named_face_still_resolves_after_the_part_changes_size(self) -> None:
        """The decisive test. A fillet face is named, the part is rebuilt bigger, and
        the name must find the *new* fillet face — checked by area against the
        closed-form quarter-cylinder, so resolving to the wrong face fails."""
        from app.kernel.occt.document import PartDocument
        from app.kernel.occt.metrology import face_area_mm2

        doc = PartDocument(name="Slab")
        parts = {
            "box": doc.add_feature("slab.block", "catia_surface_primitive"),
            "fillet": doc.add_feature("slab.corner", "catia_fillet"),
        }

        solid, fillet_face = self._slab(parts, 100.0, 60.0, 40.0, 8.0)
        assert fillet_face is not None
        assert face_area_mm2(fillet_face) == pytest.approx(
            2 * math.pi * 8.0 * 40.0 / 4, abs=1e-6
        ), "sanity: a quarter-cylinder of radius 8 and height 40"

        doc.names.record("slab.corner.face", fillet_face, solid)

        # Same labels, new dimensions — a real parametric rebuild.
        solid2, fillet_face2 = self._slab(parts, 140.0, 80.0, 55.0, 8.0)
        expected = 2 * math.pi * 8.0 * 55.0 / 4

        recovered = doc.names.resolve("slab.corner.face", doc.feature_labels())
        from app.kernel.occt.binding import symbol

        assert face_area_mm2(symbol("TopoDS").Face_s(recovered)) == pytest.approx(
            expected, abs=1e-6
        ), "the name resolved, but to the wrong face"

    def test_resolving_a_name_that_was_never_recorded_says_so(self) -> None:
        from app.kernel.occt.document import PartDocument

        doc = PartDocument(name="Empty")
        with pytest.raises(NamingError, match="never recorded"):
            doc.names.resolve("nothing.here", doc.feature_labels())

    def test_a_feature_keeps_its_labels_across_a_rebuild(self) -> None:
        """Rule 2: fresh labels make Solve() succeed while resolving to nothing."""
        from app.kernel.occt.document import PartDocument

        doc = PartDocument(name="Slab")
        first = doc.add_feature("slab.block", "catia_surface_primitive")
        again = doc.add_feature("slab.block", "catia_surface_primitive")

        assert first is again
        assert first.labels.all() == again.labels.all()
        assert len(doc) == 1

    def test_rebuilding_a_feature_with_a_different_operation_is_refused(self) -> None:
        from app.kernel.occt.document import PartDocument

        doc = PartDocument(name="Slab")
        doc.add_feature("slab.block", "catia_surface_primitive")

        with pytest.raises(GeometryError, match="different feature"):
            doc.add_feature("slab.block", "catia_fillet")

    def test_the_two_evolution_kinds_go_to_different_labels(self) -> None:
        """Rule 1: OCAF raises "not same evolution" if they share one."""
        from app.kernel.occt.document import PartDocument

        doc = PartDocument(name="Slab")
        feature = doc.add_feature("f", "catia_fillet")
        labels = feature.labels

        entries = {label.Tag() for label in labels.all()}
        assert len(entries) == 3, "shape, modified and generated must be distinct labels"


class TestTheRunnerHonoursTheCallRunnerContract:
    def test_a_mutating_call_reports_the_feature_name_and_the_post_state(self) -> None:
        """`app.design.execute` binds late names from `feature`; assertions read the rest."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "P"})
        result = runner("catia_surface_primitive", {"kind": "sphere", "radius_mm": 5.0})

        assert result["feature"] == "SurfacePrimitive.1"
        assert result["has_solid"] is True
        assert "volume_mm3" in result and "bounding_box_mm" in result

    def test_feature_names_imitate_catias_numbering(self) -> None:
        """A design must not behave differently depending on which kernel ran."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "P"})
        first = runner(
            "catia_surface_primitive",
            {"kind": "cylinder", "radius_mm": 10.0, "length_mm": 30.0},
        )
        second = runner("catia_fillet", {"radius_mm": 1.0, "edges": "horizontal"})

        assert first["feature"] == "SurfacePrimitive.1"
        assert second["feature"] == "Fillet.1"

    def test_an_unfilletable_edge_is_reported_with_advice_not_a_cpp_exception(self) -> None:
        """OCCT raises Standard_Failure out of Build(); it must never reach the agent raw."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "P"})
        runner("catia_surface_primitive", {"kind": "sphere", "radius_mm": 5.0})

        with pytest.raises(GeometryError, match="cannot take this feature|larger than"):
            runner("catia_fillet", {"radius_mm": 0.5, "edges": "all"})

    def test_rename_gives_the_feature_the_designs_own_name(self) -> None:
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "P"})
        made = runner("catia_surface_primitive", {"kind": "sphere", "radius_mm": 5.0})
        renamed = runner(
            "catia_feature_rename", {"feature": made["feature"], "name": "plate.body"}
        )

        assert renamed["feature"] == "plate.body"
        assert "plate.body" in renamed["features"]

    def test_an_unimplemented_operation_is_a_known_gap_not_a_failure(self) -> None:
        """Coverage is meaningless if "not built yet" and "broken" look the same."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        with pytest.raises(OperationNotSupported) as caught:
            runner("catia_multi_section_solid", {})

        assert caught.value.tool == "catia_multi_section_solid"
        assert "does not support" in str(caught.value)

    def test_building_before_opening_a_document_says_what_to_do(self) -> None:
        from app.kernel import OcctRunner

        runner = OcctRunner()
        with pytest.raises(GeometryError, match="catia_new_part"):
            runner("catia_surface_primitive", {"kind": "sphere", "radius_mm": 1.0})

    def test_an_unknown_material_is_refused_rather_than_defaulted(self) -> None:
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "P"})
        with pytest.raises(GeometryError, match="No density is known"):
            runner("catia_set_material", {"material": "unobtanium"})

    def test_a_fillet_too_large_for_the_geometry_fails_with_advice(self) -> None:
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "P"})
        runner("catia_surface_primitive", {"kind": "cylinder", "radius_mm": 5.0,
                                           "length_mm": 10.0})
        with pytest.raises(GeometryError, match="reduce it"):
            runner("catia_fillet", {"radius_mm": 500.0, "edges": "all"})

    def test_supported_tools_is_the_honest_coverage_number(self) -> None:
        from app.kernel import OcctRunner

        supported = OcctRunner().supported_tools()

        assert "catia_new_part" in supported
        assert "catia_measure" in supported
        assert len(supported) < 201, "coverage must not overclaim"


class TestTheWholePathFromSpecToAssertion:
    """Phase 1's actual proof: a design compiles, builds on OCCT, and is measured.

    Nothing in `app/design/` is told which kernel ran. That is Decision 1 working, and
    it is the property every later phase is priced against.
    """

    @staticmethod
    def _rod(length_mm: float = 50.0):
        from app.design import DesignSpec, FeatureSpec, Parameter, Unit, expr

        return DesignSpec.of(
            "Rod",
            material="steel-1018",
            parameters=[
                Parameter("radius_mm", Unit.MM, value=20.0),
                Parameter("length_mm", Unit.MM, value=length_mm),
                Parameter("fillet_mm", Unit.MM, expression="radius_mm / 10"),
            ],
            features=[
                FeatureSpec(
                    "rod.body",
                    "catia_surface_primitive",
                    {
                        "kind": "cylinder",
                        "radius_mm": expr("radius_mm"),
                        "length_mm": expr("length_mm"),
                    },
                    note="The shaft blank.",
                ),
                FeatureSpec(
                    "rod.ends",
                    "catia_fillet",
                    {"radius_mm": expr("fillet_mm"), "edges": "horizontal"},
                    note="Break the sharp ends.",
                ),
            ],
        )

    def test_a_compiled_design_builds_on_the_open_kernel(self) -> None:
        from app.design import compile_spec, execute_plan
        from app.kernel import OcctRunner

        report = execute_plan(compile_spec(self._rod()), OcctRunner())

        assert report.ok, report.failure
        assert report.features_built() == ("rod.body", "rod.ends")

    def test_the_late_bound_name_resolves_from_what_the_kernel_reported(self) -> None:
        """The compiler emits a rename whose target is only known at run time."""
        from app.design import compile_spec, execute_plan
        from app.kernel import OcctRunner

        report = execute_plan(compile_spec(self._rod()), OcctRunner())

        assert report.created["rod.body"] == "SurfacePrimitive.1"

    def test_the_built_part_measures_what_the_formula_says(self) -> None:
        from app.design import compile_spec, execute_plan
        from app.kernel import OcctRunner

        report = execute_plan(compile_spec(self._rod()), OcctRunner())
        measured = report.last_result()

        # Fillets remove material, so the solid is a little under the raw cylinder.
        raw = math.pi * 20.0**2 * 50.0
        assert measured["volume_mm3"] < raw
        assert measured["volume_mm3"] == pytest.approx(raw, rel=0.01)
        assert measured["bounding_box_mm"]["size"][2] == pytest.approx(50.0, abs=TOL)

    def test_the_last_call_still_carries_the_measurements(self) -> None:
        """A plan usually *ends* on a rename; a bare acknowledgement there would make
        every assertion on the design UNMEASURED."""
        from app.design import compile_spec, execute_plan
        from app.kernel import OcctRunner

        plan = compile_spec(self._rod())
        assert plan.tools()[-1] == "catia_feature_rename", "fixture assumption"

        report = execute_plan(plan, OcctRunner())

        assert "mass_kg" in report.last_result()
        assert "volume_mm3" in report.last_result()

    def test_assertions_run_against_real_geometry(self) -> None:
        from app.design import Assertion, check_assertions, compile_spec, execute_plan
        from app.kernel import OcctRunner

        plan = compile_spec(self._rod())
        report = execute_plan(plan, OcctRunner())

        checked = check_assertions(
            [
                Assertion("mass budget", "mass_kg", "<=", 0.5),
                Assertion("length", "bounding_box_mm.size[2]", "==", 50.0, tolerance=0.01),
            ],
            report.last_result(),
            parameters=plan.parameters,
        )

        assert checked.ok, checked.summary()

    def test_an_unmeasurable_claim_is_reported_not_passed(self) -> None:
        """The kernel does not report wall thickness yet, and must not pretend to."""
        from app.design import Assertion, check_assertions, compile_spec, execute_plan
        from app.kernel import OcctRunner

        report = execute_plan(compile_spec(self._rod()), OcctRunner())
        checked = check_assertions(
            [Assertion("wall", "min_wall_mm", ">=", 3.0)], report.last_result()
        )

        assert len(checked.unmeasured) == 1
        assert not checked.ok

    def test_changing_a_parameter_changes_the_geometry_it_reaches(self) -> None:
        """Regeneration is the point of Layer B: edit the spec, rebuild, re-measure."""
        from app.design import compile_spec, execute_plan
        from app.kernel import OcctRunner

        short = execute_plan(compile_spec(self._rod(50.0)), OcctRunner()).last_result()
        long = execute_plan(compile_spec(self._rod(90.0)), OcctRunner()).last_result()

        assert long["bounding_box_mm"]["size"][2] == pytest.approx(90.0, abs=TOL)
        assert long["mass_kg"] == pytest.approx(short["mass_kg"] * 90 / 50, rel=0.02)

    def test_a_geometry_failure_comes_back_as_a_named_build_failure(self) -> None:
        """The executor's contract: a kernel refusal is an outcome, not a crash."""
        from app.design import DesignSpec, FeatureSpec, compile_spec, execute_plan
        from app.kernel import OcctRunner

        design = DesignSpec.of(
            "Bad",
            features=[
                FeatureSpec(
                    "part.body",
                    "catia_surface_primitive",
                    {"kind": "cylinder", "radius_mm": 5.0, "length_mm": 10.0},
                ),
                FeatureSpec("part.edges", "catia_fillet",
                            {"radius_mm": 400.0, "edges": "all"}),
            ],
        )

        report = execute_plan(compile_spec(design), OcctRunner())

        assert not report.ok
        assert report.failure is not None
        assert report.failure.feature == "part.edges"
        assert "part.body" in report.features_built()


class TestTheOperationTableIsHonestAboutItself:
    """Coverage has to be a measured number, or it is a claim."""

    def test_every_handler_names_a_real_registry_operation(self) -> None:
        """A typo here would be a handler that can never be called — live-looking dead
        code, and a silent hole in the coverage figure."""
        from app.kernel.occt.operations import unknown_handler_names

        assert unknown_handler_names() == ()

    def test_coverage_counts_against_the_declared_vocabulary(self) -> None:
        from app.catia.ops import registry
        from app.kernel import OcctRunner

        numbers = OcctRunner.coverage()

        assert numbers["declared"] == len(registry.OPERATIONS_BY_NAME)
        assert numbers["implemented"] == len(OcctRunner.supported_tools())
        assert numbers["implemented"] + numbers["remaining"] == numbers["declared"]

    def test_the_backend_reports_a_real_version_for_provenance(self) -> None:
        """"unknown" would put a hole in the chain exactly where determinism is claimed."""
        from app.kernel import OcctRunner

        version = OcctRunner.backend_version()

        assert version.startswith("OCCT ")
        assert version != "OCCT unknown"


class TestDetailLevelsControlWhatIsComputed:
    """Measuring integrates over the whole shape; at 10⁵ operations that is the run."""

    def test_a_cheaper_level_omits_the_expensive_quantities(self) -> None:
        from app.kernel import Detail, OcctRunner

        runner = OcctRunner(detail=Detail.SHAPE)
        runner("catia_new_part", {"name": "P"})
        result = runner("catia_surface_primitive", {"kind": "sphere", "radius_mm": 5.0})

        assert result["face_count"] >= 1
        assert "volume_mm3" not in result
        assert "bounding_box_mm" not in result

    def test_an_explicit_measure_still_returns_the_full_payload(self) -> None:
        """A caller who asked to measure wants the numbers, whatever the batch default."""
        from app.kernel import Detail, OcctRunner

        runner = OcctRunner(detail=Detail.SHAPE)
        runner("catia_new_part", {"name": "P"})
        runner("catia_surface_primitive", {"kind": "sphere", "radius_mm": 5.0})

        measured = runner("catia_measure", {})

        assert "volume_mm3" in measured
        assert "bounding_box_mm" in measured

    def test_inertia_is_never_computed_speculatively(self) -> None:
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "P"})
        runner("catia_surface_primitive", {"kind": "sphere", "radius_mm": 5.0})

        assert "inertia_tensor_mm5" not in runner("catia_measure", {})
        assert "inertia_tensor_mm5" in runner("catia_measure", {"include_inertia": True})

    def test_measuring_twice_without_a_change_reuses_the_result(self) -> None:
        """The cache is keyed on the feature's shape, which is immutable once built."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "P"})
        runner("catia_surface_primitive", {"kind": "sphere", "radius_mm": 5.0})

        assert runner("catia_measure", {}) == runner("catia_measure", {})

    def test_a_returned_payload_cannot_corrupt_the_cache(self) -> None:
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "P"})
        runner("catia_surface_primitive", {"kind": "sphere", "radius_mm": 5.0})

        first = runner("catia_measure", {})
        first["volume_mm3"] = -1.0

        assert runner("catia_measure", {})["volume_mm3"] > 0


class TestSelectorsComeFromTheSharedVocabulary:
    def test_the_supported_words_are_drawn_from_the_registry_vocabulary(self) -> None:
        """A second list of selector words here would quietly diverge from the schema."""
        from app.catia.ops import vocabulary
        from app.kernel.occt.selectors import supported_words

        for word in supported_words():
            assert word in vocabulary.EDGE_SELECTORS

    def test_every_vocabulary_word_is_either_supported_or_refused_by_name(self) -> None:
        """No word may fall through and quietly select everything."""
        from app.catia.ops import vocabulary
        from app.kernel.occt.selectors import supported_words, unsupported_words

        accounted = set(supported_words()) | set(unsupported_words())
        assert set(vocabulary.EDGE_SELECTORS) == accounted

    def test_every_vocabulary_word_is_now_decidable(self) -> None:
        """Phase 2.1 closed the last gap; this is what stops it reopening.

        `convex`, `concave`, `top` and `bottom` all used to refuse. They are answered by
        predicates now, and a word that quietly went back to being undecidable would
        show up as a design that validates and then selects nothing.
        """
        from app.kernel.occt.selectors import unsupported_words

        assert unsupported_words() == ()

    def test_convex_selects_the_outside_corners_it_names(self) -> None:
        """The word used to be refused. It must now do the thing, not merely be accepted.

        A pocketed block has convex edges on its outline and concave ones where the
        pocket meets the floor; filleting "convex" must leave the concave ones alone, or
        the word is decorative.
        """
        from app.kernel import OcctRunner
        from app.kernel.occt.selectors import select_edges

        runner = OcctRunner()
        runner("catia_new_part", {"name": "P"})
        runner("catia_sketch_create", {"support": "XY", "name": "s"})
        runner("catia_sketch_rectangle", {"sketch": "s", "width_mm": 40.0, "height_mm": 30.0})
        runner("catia_pad", {"sketch": "s", "length_mm": 20.0})
        runner("catia_sketch_create", {"support": "XY", "name": "p"})
        runner("catia_sketch_rectangle", {"sketch": "p", "width_mm": 20.0, "height_mm": 10.0})
        runner("catia_pocket", {"sketch": "p", "depth_mm": 5.0})

        shape = runner.document.shape
        convex = select_edges(shape, "convex")
        concave = select_edges(shape, "concave")

        assert convex, "the outline of the block is convex"
        assert concave, "the pocket floor meets its walls concavely"
        assert not any(
            a.IsSame(b) for a in convex for b in concave
        ), "no edge is both convex and concave"

    def test_an_unknown_selector_word_is_refused_by_name(self) -> None:
        """A word outside the vocabulary must never fall through to selecting everything."""
        from app.kernel import OcctRunner
        from app.kernel.occt.selectors import select_edges

        runner = OcctRunner()
        runner("catia_new_part", {"name": "P"})
        runner("catia_surface_primitive", {"kind": "cylinder", "radius_mm": 5.0,
                                           "length_mm": 20.0})

        with pytest.raises(GeometryError, match="not a selector word"):
            select_edges(runner.document.shape, "diagonal")


class TestConformanceComparison:
    """`same_shape_within` is what the two-backend harness (Phase 1.5) runs on."""

    def test_identical_measurements_disagree_about_nothing(self) -> None:
        from app.kernel import OcctRunner, compare

        runner = OcctRunner()
        runner("catia_new_part", {"name": "P"})
        a = dict(runner("catia_surface_primitive", {"kind": "sphere", "radius_mm": 5.0}))

        assert compare(a, dict(a), tolerance=1e-9) == []

    def test_a_divergence_names_the_quantity_that_differs(self) -> None:
        """Returning the keys, not a bool, is what makes a divergence a finding."""
        from app.kernel import compare

        a = {"volume_mm3": 100.0, "face_count": 6, "has_solid": True}
        b = {"volume_mm3": 105.0, "face_count": 6, "has_solid": True}

        assert compare(a, b, tolerance=1e-6) == ["volume_mm3"]

    def test_a_differing_face_count_is_caught_even_when_volumes_match(self) -> None:
        """Two shapes can weigh the same and be built differently."""
        from app.kernel import compare

        a = {"volume_mm3": 100.0, "face_count": 6}
        b = {"volume_mm3": 100.0, "face_count": 7}

        assert "face_count" in compare(a, b, tolerance=1e-6)


class TestSketchesAndSolidFeatures:
    """The operations a real part is made of — and the reason 1.3 needed no solver yet.

    Every profile in the registry's vocabulary is dimension-driven (a rectangle takes a
    width and a height), so it is fully determined by its arguments and there is nothing
    for a constraint solver to solve. `catia_sketch_constrain` is the one operation that
    genuinely needs PlaneGCS, and it refuses with that reason.
    """

    @staticmethod
    def _plate(runner, width=120.0, height=80.0, thickness=8.0):
        runner("catia_new_part", {"name": "Plate"})
        runner("catia_set_material", {"material": "aluminium-6061-t6"})
        runner("catia_sketch_create", {"support": "XY", "name": "outline"})
        runner("catia_sketch_rectangle",
               {"sketch": "outline", "width_mm": width, "height_mm": height})
        return runner("catia_pad", {"sketch": "outline", "length_mm": thickness})

    def test_a_pad_has_the_volume_the_dimensions_say(self) -> None:
        from app.kernel import OcctRunner

        result = self._plate(OcctRunner())

        assert result["volume_mm3"] == pytest.approx(120.0 * 80.0 * 8.0, abs=TOL)
        assert result["face_count"] == 6

    def test_a_through_pocket_removes_exactly_its_own_volume(self) -> None:
        from app.kernel import OcctRunner

        runner = OcctRunner()
        self._plate(runner)
        runner("catia_sketch_create", {"support": "XY", "name": "bore"})
        runner("catia_sketch_circle", {"sketch": "bore", "diameter_mm": 30.0})

        result = runner("catia_pocket", {"sketch": "bore", "through_all": True})

        removed = math.pi * 15.0**2 * 8.0
        assert result["volume_mm3"] == pytest.approx(120.0 * 80.0 * 8.0 - removed, abs=1e-6)

    def test_a_symmetric_pad_is_the_stated_length_in_total(self) -> None:
        """Extruding the full length each way would make the part twice as thick."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "P"})
        runner("catia_sketch_create", {"support": "XY", "name": "s"})
        runner("catia_sketch_rectangle", {"sketch": "s", "width_mm": 10.0, "height_mm": 10.0})

        result = runner("catia_pad", {"sketch": "s", "length_mm": 20.0, "symmetric": True})

        assert result["bounding_box_mm"]["size"][2] == pytest.approx(20.0, abs=TOL)

    def test_a_rectangle_is_centred_on_its_placement_point(self) -> None:
        """`at` means the centre everywhere else in the vocabulary; a corner-anchored
        rectangle would put every profile half a width out."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "P"})
        runner("catia_sketch_create", {"support": "XY", "name": "s"})
        runner("catia_sketch_rectangle",
               {"sketch": "s", "width_mm": 20.0, "height_mm": 10.0, "at": [0.0, 0.0]})
        result = runner("catia_pad", {"sketch": "s", "length_mm": 5.0})

        box = result["bounding_box_mm"]
        assert box["min"][0] == pytest.approx(-10.0, abs=TOL)
        assert box["max"][0] == pytest.approx(10.0, abs=TOL)

    def test_a_polygon_has_the_area_the_formula_says(self) -> None:
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Hex"})
        runner("catia_sketch_create", {"support": "XY", "name": "s"})
        runner("catia_sketch_polygon", {"sketch": "s", "sides": 6, "diameter_mm": 20.0})
        result = runner("catia_pad", {"sketch": "s", "length_mm": 10.0})

        # Regular polygon inscribed in radius r: area = (1/2) n r² sin(2π/n).
        area = 0.5 * 6 * 10.0**2 * math.sin(2 * math.pi / 6)
        assert result["volume_mm3"] == pytest.approx(area * 10.0, abs=1e-6)

    def test_a_shaft_revolves_the_profile(self) -> None:
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Ring"})
        runner("catia_sketch_create", {"support": "XY", "name": "s"})
        runner("catia_sketch_rectangle",
               {"sketch": "s", "width_mm": 4.0, "height_mm": 10.0, "at": [20.0, 0.0]})
        result = runner("catia_shaft", {"sketch": "s"})

        # Pappus: a section of area A whose centroid sits at radius R sweeps 2πR·A.
        assert result["volume_mm3"] == pytest.approx(2 * math.pi * 20.0 * 40.0, abs=1e-3)

    def test_a_pocket_that_would_consume_the_part_is_refused(self) -> None:
        from app.kernel import OcctRunner

        runner = OcctRunner()
        self._plate(runner, width=20.0, height=20.0)
        runner("catia_sketch_create", {"support": "XY", "name": "big"})
        runner("catia_sketch_rectangle",
               {"sketch": "big", "width_mm": 200.0, "height_mm": 200.0})

        with pytest.raises(GeometryError, match="removed everything|no solid"):
            runner("catia_pocket", {"sketch": "big", "through_all": True})

    def test_cutting_into_an_empty_part_says_what_to_do(self) -> None:
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "P"})
        runner("catia_sketch_create", {"support": "XY", "name": "s"})
        runner("catia_sketch_rectangle", {"sketch": "s", "width_mm": 5.0, "height_mm": 5.0})

        with pytest.raises(GeometryError, match="has none yet"):
            runner("catia_pocket", {"sketch": "s", "depth_mm": 2.0})

    def test_a_feature_naming_an_unknown_sketch_is_refused(self) -> None:
        from app.kernel import OcctRunner
        from app.kernel.errors import NamingError

        runner = OcctRunner()
        runner("catia_new_part", {"name": "P"})

        with pytest.raises(NamingError, match="No sketch called"):
            runner("catia_pad", {"sketch": "nowhere", "length_mm": 5.0})

    def test_drawing_into_an_ambiguous_sketch_is_refused(self) -> None:
        """Guessing between open sketches puts the profile on the wrong plane."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "P"})
        runner("catia_sketch_create", {"support": "XY", "name": "a"})
        runner("catia_sketch_create", {"support": "YZ", "name": "b"})

        with pytest.raises(GeometryError, match="several"):
            runner("catia_sketch_rectangle", {"width_mm": 5.0, "height_mm": 5.0})

    def test_constraining_free_geometry_names_the_missing_solver(self) -> None:
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "P"})
        runner("catia_sketch_create", {"support": "XY", "name": "s"})

        with pytest.raises(OperationNotSupported, match="PlaneGCS"):
            runner("catia_sketch_constrain", {"sketch": "s"})

    def test_reopening_a_sketch_starts_from_an_empty_profile_list(self) -> None:
        """A regeneration re-runs sketch_create; keeping the old profiles would double
        the outline and silently give the pad two overlapping boundaries."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "P"})
        runner("catia_sketch_create", {"support": "XY", "name": "s"})
        runner("catia_sketch_rectangle", {"sketch": "s", "width_mm": 10.0, "height_mm": 10.0})
        reopened = runner("catia_sketch_create", {"support": "XY", "name": "s"})

        assert reopened["profiles"] == 0

    def test_an_unsupported_pad_limit_is_named_not_silently_ignored(self) -> None:
        """`up_to_surface` is the one limit still owed, and it says what it needs."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "P"})
        runner("catia_sketch_create", {"support": "XY", "name": "s"})
        runner("catia_sketch_rectangle", {"sketch": "s", "width_mm": 10.0, "height_mm": 10.0})

        with pytest.raises(OperationNotSupported, match="Phase 2.6"):
            runner("catia_pad", {"sketch": "s", "length_mm": 5.0, "limit": "up_to_surface"})

    def test_a_limit_stops_the_extrusion_where_the_geometry_says(self) -> None:
        """Master plan 2.5. Two 10 mm plates with a 20 mm gap: a post drawn on the lower
        one stops at the upper one, and a bore drilled from the top breaks out of the
        upper plate rather than continuing into the lower.

        `up_to_next` means the opposite thing for the two, and both are checked here:
        a pad grows *until it reaches* the next wall, a pocket cuts *until it leaves*
        the one it is in. One meaning for both would make every pocket stop the instant
        it touched the face it was drilling.
        """
        from app.kernel import OcctRunner

        def two_plates():
            runner = OcctRunner()
            runner("catia_new_part", {"name": "P"})
            runner("catia_sketch_create", {"support": "XY", "name": "a"})
            runner("catia_sketch_rectangle", {"sketch": "a", "width_mm": 60.0, "height_mm": 60.0})
            runner("catia_pad", {"sketch": "a", "length_mm": 10.0, "name": "lower"})
            runner("catia_plane_offset", {"reference": "XY", "distance_mm": 30.0, "name": "high"})
            runner("catia_sketch_create", {"support": "high", "name": "b"})
            runner("catia_sketch_rectangle", {"sketch": "b", "width_mm": 60.0, "height_mm": 60.0})
            runner("catia_pad", {"sketch": "b", "length_mm": 10.0, "name": "upper"})
            runner("catia_plane_offset", {"reference": "XY", "distance_mm": 10.0, "name": "mid"})
            return runner

        base = 60.0 * 60.0 * 10.0 * 2.0
        bore_area = math.pi * 5.0**2

        runner = two_plates()
        runner("catia_sketch_create", {"support": "mid", "name": "c"})
        runner("catia_sketch_circle", {"sketch": "c", "diameter_mm": 10.0})
        result = runner("catia_pad", {"sketch": "c", "limit": "up_to_next", "name": "post"})
        assert result["volume_mm3"] == pytest.approx(base + bore_area * 20.0, rel=1e-9)

        runner = two_plates()
        runner("catia_plane_offset", {"reference": "XY", "distance_mm": 25.0, "name": "stop"})
        runner("catia_sketch_create", {"support": "mid", "name": "c"})
        runner("catia_sketch_circle", {"sketch": "c", "diameter_mm": 10.0})
        result = runner(
            "catia_pad",
            {"sketch": "c", "limit": "up_to_plane", "up_to": "stop", "name": "post"},
        )
        assert result["volume_mm3"] == pytest.approx(base + bore_area * 15.0, rel=1e-9)

        runner = two_plates()
        runner("catia_plane_offset", {"reference": "XY", "distance_mm": 40.0, "name": "top"})
        runner("catia_sketch_create", {"support": "top", "name": "d"})
        runner("catia_sketch_circle", {"sketch": "d", "diameter_mm": 10.0})
        result = runner(
            "catia_pocket",
            {"sketch": "d", "limit": "up_to_next", "reversed": True, "name": "bore"},
        )
        assert result["volume_mm3"] == pytest.approx(base - bore_area * 10.0, rel=1e-9)

        runner = two_plates()
        runner("catia_plane_offset", {"reference": "XY", "distance_mm": 40.0, "name": "top"})
        runner("catia_sketch_create", {"support": "top", "name": "d"})
        runner("catia_sketch_circle", {"sketch": "d", "diameter_mm": 10.0})
        result = runner(
            "catia_pocket",
            {"sketch": "d", "limit": "up_to_last", "reversed": True, "name": "bore"},
        )
        assert result["volume_mm3"] == pytest.approx(base - bore_area * 20.0, rel=1e-9)

    def test_a_limit_is_measured_from_the_sketch_not_from_the_world_origin(self) -> None:
        """The bug this pins, which shipped for exactly one test run: the extent of the
        material ahead was measured with the two-argument `gp_Trsf.SetTransformation`,
        which leaves the result in world coordinates. A post bridging a 20 mm gap from a
        sketch at z=10 came out 30 mm long — wrong by exactly the sketch's height.

        Built on ZX so the extrusion direction is **not** world Z: measuring absolute Z
        would then not merely offset the answer, it would measure a different axis.
        """
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "P"})
        runner("catia_sketch_create", {"support": "ZX", "name": "a"})
        runner("catia_sketch_rectangle", {"sketch": "a", "width_mm": 60.0, "height_mm": 60.0})
        runner("catia_pad", {"sketch": "a", "length_mm": 10.0, "name": "wall"})
        runner("catia_plane_offset", {"reference": "ZX", "distance_mm": 30.0, "name": "far"})
        runner("catia_sketch_create", {"support": "far", "name": "b"})
        runner("catia_sketch_rectangle", {"sketch": "b", "width_mm": 60.0, "height_mm": 60.0})
        runner("catia_pad", {"sketch": "b", "length_mm": 10.0, "name": "far_wall"})

        runner("catia_plane_offset", {"reference": "ZX", "distance_mm": 10.0, "name": "inner"})
        runner("catia_sketch_create", {"support": "inner", "name": "c"})
        runner("catia_sketch_circle", {"sketch": "c", "diameter_mm": 10.0})
        result = runner("catia_pad", {"sketch": "c", "limit": "up_to_next", "name": "post"})

        base = 60.0 * 60.0 * 10.0 * 2.0
        assert result["volume_mm3"] == pytest.approx(
            base + math.pi * 5.0**2 * 20.0, rel=1e-9
        )


class TestDeterminism:
    """Master plan 1.6 / roadmap I5: same spec plus same version ⇒ same geometry."""

    @staticmethod
    def _build(thickness: float = 8.0):
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "P"})
        runner("catia_set_material", {"material": "steel-1018"})
        runner("catia_sketch_create", {"support": "XY", "name": "s"})
        runner("catia_sketch_rectangle", {"sketch": "s", "width_mm": 40.0, "height_mm": 25.0})
        return runner("catia_pad", {"sketch": "s", "length_mm": thickness})

    def test_the_same_design_built_twice_has_the_same_digest(self) -> None:
        from app.kernel import geometry_digest

        assert geometry_digest(self._build()) == geometry_digest(self._build())

    def test_a_changed_dimension_changes_the_digest(self) -> None:
        """A digest that did not move would make every staleness check useless."""
        from app.kernel import geometry_digest

        assert geometry_digest(self._build(8.0)) != geometry_digest(self._build(9.0))

    def test_a_digest_needs_a_full_measurement(self) -> None:
        """A digest over a cheap payload would collide between different parts."""
        from app.kernel import Detail, OcctRunner, geometry_digest

        runner = OcctRunner(detail=Detail.SHAPE)
        runner("catia_new_part", {"name": "P"})
        cheap = runner("catia_surface_primitive", {"kind": "sphere", "radius_mm": 5.0})

        with pytest.raises(ValueError, match="full measurement"):
            geometry_digest(cheap)

    def test_the_environment_records_what_produced_the_geometry(self) -> None:
        """"Same version" is half the claim, so the version has to be recorded."""
        from app.kernel import environment

        recorded = environment()

        assert recorded["kernel"].startswith("OCCT ")
        assert recorded["binding"] == "cadquery-ocp"
        assert recorded["digest_decimals"] == "6"


class TestBackendConformance:
    """Master plan 1.5: one plan, two backends, and what differs between them."""

    @staticmethod
    def _plan():
        from app.design import DesignSpec, FeatureSpec, compile_spec, ref

        return compile_spec(
            DesignSpec.of(
                "Plate",
                material="steel-1018",
                features=[
                    FeatureSpec("p.profile", "catia_sketch_create", {"support": "XY"}),
                    FeatureSpec("p.outline", "catia_sketch_rectangle",
                                {"sketch": ref("p.profile"), "width_mm": 30.0,
                                 "height_mm": 20.0}),
                    FeatureSpec("p.body", "catia_pad",
                                {"sketch": ref("p.profile"), "length_mm": 5.0}),
                ],
            )
        )

    def test_two_runs_of_the_same_backend_agree(self) -> None:
        from app.kernel import OcctRunner, compare_backends

        result = compare_backends(
            self._plan(), OcctRunner(), OcctRunner(), left_name="a", right_name="b"
        )

        assert result.agrees
        assert result.divergences == ()
        assert "built the same geometry" in result.summary()

    def test_a_missing_operation_is_coverage_not_a_failure(self) -> None:
        """Counting a gap as a failure makes the coverage figure useless."""
        from app.kernel import OcctRunner, compare_backends
        from app.kernel.errors import OperationNotSupported

        def cannot_do_anything(tool, arguments):
            raise OperationNotSupported(tool, backend="stub")

        result = compare_backends(
            self._plan(), OcctRunner(), cannot_do_anything, right_name="stub"
        )

        assert not result.agrees
        assert not result.comparable
        assert result.unsupported["stub"]
        assert result.failures == {}, "a coverage gap is not a failure"

    def test_a_real_divergence_names_the_quantity(self) -> None:
        """"volume disagrees" localises a bug; False starts a bisect."""
        from app.kernel import OcctRunner, compare_backends

        def wrong_volume(tool, arguments):
            result = dict(OcctRunner.__call__(runner, tool, arguments))
            if "volume_mm3" in result:
                result["volume_mm3"] += 10.0
            return result

        runner = OcctRunner()
        result = compare_backends(
            self._plan(), OcctRunner(), wrong_volume, right_name="drifted"
        )

        assert "volume_mm3" in result.divergences
        assert "DISAGREE" in result.summary()


class TestPredicateSelection:
    """Master plan 2.1 — pointing at geometry by what it is, not by its index.

    The vocabulary deliberately mirrors `app/solve/types.py`'s region selectors, which
    is a project rule: selection is geometric, never by face id.
    """

    @staticmethod
    def _block(width=40.0, depth=30.0, height=20.0):
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Block"})
        runner("catia_set_material", {"material": "steel-1018"})
        runner("catia_sketch_create", {"support": "XY", "name": "s"})
        runner("catia_sketch_rectangle",
               {"sketch": "s", "width_mm": width, "height_mm": depth})
        runner("catia_pad", {"sketch": "s", "length_mm": height})
        return runner

    def test_sub_shapes_are_counted_once_not_once_per_owner(self) -> None:
        """`TopExp_Explorer` visits each edge once per adjoining face.

        A closed box explores as 24 edges and 48 vertices instead of 12 and 8, which
        silently doubled `edge_count`, corrupted the determinism digest that hashes it,
        and fed every edge to a fillet twice.
        """
        from app.kernel.occt.binding import symbol
        from app.kernel.occt.topology import edges, faces, vertices

        box = symbol("BRepPrimAPI_MakeBox")(40.0, 30.0, 20.0).Shape()

        assert len(faces(box)) == 6
        assert len(edges(box)) == 12
        assert len(vertices(box)) == 8

    def test_edges_can_be_chosen_by_length(self) -> None:
        from app.kernel.occt.selectors import select_edges

        runner = self._block()
        long_edges = select_edges(runner.document.shape, {"longer_than_mm": 35.0}, tool="t")

        assert len(long_edges) == 4, "only the four 40 mm edges are longer than 35"

    def test_faces_can_be_chosen_by_which_way_they_face(self) -> None:
        from app.kernel.occt.selectors import select_faces

        runner = self._block()
        up = select_faces(runner.document.shape, {"normal": "+z"}, tool="t")

        assert len(up) == 1

    def test_a_bore_can_be_chosen_by_its_diameter(self) -> None:
        """The question a real design asks: 'the Ø10 holes', not 'face 7'."""
        from app.kernel import OcctRunner
        from app.kernel.occt.selectors import select_faces

        runner: OcctRunner = self._block()
        runner("catia_sketch_create", {"support": "XY", "name": "bore"})
        runner("catia_sketch_circle", {"sketch": "bore", "diameter_mm": 10.0})
        runner("catia_pocket", {"sketch": "bore", "through_all": True})

        matched = select_faces(
            runner.document.shape, {"cylindrical": True, "diameter_mm": 10.0}, tool="t"
        )

        assert len(matched) == 1

    def test_at_the_top_means_lying_in_the_top_plane_not_reaching_it(self) -> None:
        """A side wall runs from bottom to top, so it *touches* z-max.

        Testing only the near end selected the top face and all four sides; a shell
        asked to open 'the top' opened five faces and left the bottom slab.
        """
        from app.kernel.occt.selectors import select_faces

        runner = self._block()
        top = select_faces(runner.document.shape, {"axis": "z", "side": "max"}, tool="t")

        assert len(top) == 1

    def test_every_edge_of_a_box_is_convex(self) -> None:
        from app.kernel.occt.selectors import select_edges

        runner = self._block()

        assert len(select_edges(runner.document.shape, "convex", tool="t")) == 12

    def test_a_pocket_creates_concave_edges(self) -> None:
        """Convexity has to distinguish material from void, which needs orientation."""
        from app.kernel import OcctRunner
        from app.kernel.occt.selectors import select_edges

        runner: OcctRunner = self._block()
        runner("catia_sketch_create", {"support": "XY", "name": "c"})
        runner("catia_sketch_rectangle",
               {"sketch": "c", "width_mm": 20.0, "height_mm": 15.0, "at": [5.0, 3.0]})
        runner("catia_pocket", {"sketch": "c", "depth_mm": 8.0})

        concave = select_edges(runner.document.shape, "concave", tool="t")
        convex = select_edges(runner.document.shape, "convex", tool="t")

        assert len(concave) == 8, "four pocket walls and four ceiling edges"
        assert len(convex) == 16

    def test_a_predicate_that_matches_nothing_is_an_error(self) -> None:
        """A feature applied to nothing reports success and leaves a part that is wrong
        in a way no assertion about that feature can catch."""
        from app.kernel.occt.selectors import select_edges

        runner = self._block()

        with pytest.raises(GeometryError, match="matched no"):
            select_edges(runner.document.shape, {"longer_than_mm": 5000.0}, tool="t")

    def test_a_face_predicate_handed_to_an_edge_argument_is_refused(self) -> None:
        from app.kernel.selection import parse

        with pytest.raises(GeometryError, match="cannot select edges"):
            parse({"type": "face", "normal": "+z"}, kind="edge")

    def test_an_unknown_predicate_field_is_refused_not_ignored(self) -> None:
        from app.kernel.selection import parse

        with pytest.raises(GeometryError, match="does not accept"):
            parse({"longer_then_mm": 10.0}, kind="edge")

    def test_axis_and_side_must_be_given_together(self) -> None:
        from app.kernel.selection import parse

        with pytest.raises(GeometryError, match="go together"):
            parse({"axis": "z"}, kind="face")

    def test_every_vocabulary_word_is_now_decidable(self) -> None:
        """`convex`, `concave`, `top` and `bottom` were refused before Phase 2.1."""
        from app.kernel.occt.selectors import unsupported_words

        assert unsupported_words() == ()

    def test_the_words_are_defined_in_terms_of_the_predicates(self) -> None:
        """One evaluator to be right, rather than two that agree until they do not."""
        from app.kernel.occt.selectors import select_edges

        runner = self._block()
        by_word = select_edges(runner.document.shape, "top", tool="t")
        by_predicate = select_edges(
            runner.document.shape, {"axis": "z", "side": "max"}, tool="t"
        )

        assert len(by_word) == len(by_predicate) == 4


class TestPerEntityParameters:
    """Master plan 2.3 — a radius per edge, not one radius for everything."""

    @staticmethod
    def _block():
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Block"})
        runner("catia_sketch_create", {"support": "XY", "name": "s"})
        runner("catia_sketch_rectangle", {"sketch": "s", "width_mm": 40.0, "height_mm": 30.0})
        runner("catia_pad", {"sketch": "s", "length_mm": 20.0})
        return runner

    def test_each_selected_edge_can_take_its_own_size(self) -> None:
        runner = self._block()

        result = runner(
            "catia_fillet", {"radius_mm": [1.0, 2.0, 3.0, 4.0], "edges": "vertical"}
        )

        assert result["has_solid"] is True

    def test_a_single_number_still_applies_to_every_edge(self) -> None:
        runner = self._block()

        result = runner("catia_fillet", {"radius_mm": 2.0, "edges": "vertical"})

        assert result["has_solid"] is True

    def test_a_mismatched_list_length_is_refused(self) -> None:
        """Padding or truncating would fillet some edges at a size nobody chose."""
        runner = self._block()

        with pytest.raises(GeometryError, match="must match the selection"):
            runner("catia_fillet", {"radius_mm": [1.0, 2.0], "edges": "vertical"})


class TestFaceSelectiveShell:
    """Opening named faces — refused before Phase 2.1 made face selection possible."""

    def test_shelling_with_the_top_open_leaves_the_walls(self) -> None:
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Box"})
        runner("catia_sketch_create", {"support": "XY", "name": "s"})
        runner("catia_sketch_rectangle", {"sketch": "s", "width_mm": 40.0, "height_mm": 30.0})
        solid = runner("catia_pad", {"sketch": "s", "length_mm": 20.0})

        result = runner(
            "catia_shell", {"thickness_mm": 2.0, "faces": {"axis": "z", "side": "max"}}
        )

        # 40x30x20 hollowed to 2 mm walls with the top removed leaves the outer volume
        # less the 36x26x18 cavity.
        assert solid["volume_mm3"] == pytest.approx(24000.0, abs=TOL)
        assert result["volume_mm3"] == pytest.approx(24000.0 - 36.0 * 26.0 * 18.0, abs=1e-4)

    def test_shelling_without_naming_faces_hollows_it_completely(self) -> None:
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Box"})
        runner("catia_sketch_create", {"support": "XY", "name": "s"})
        runner("catia_sketch_rectangle", {"sketch": "s", "width_mm": 40.0, "height_mm": 30.0})
        runner("catia_pad", {"sketch": "s", "length_mm": 20.0})

        result = runner("catia_shell", {"thickness_mm": 2.0})

        assert result["has_solid"] is True


class TestDrawnCurvesChainIntoContours:
    """Lines, arcs and splines drawn one at a time become one profile.

    An agent emits `catia_sketch_line` four times, not one call with four corners, and
    the registry's own summary promises that chains. Without it each call would leave a
    loose edge, the sketch would hold no closed profile, and the pad that follows would
    fail on a square anyone can see is closed.
    """

    def test_four_lines_close_into_a_profile_a_pad_extrudes(self) -> None:
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Chained"})
        runner("catia_sketch_create", {"support": "XY", "name": "outline"})
        corners = [(0.0, 0.0), (20.0, 0.0), (20.0, 20.0), (0.0, 20.0)]
        outcomes = [
            runner(
                "catia_sketch_line",
                {"sketch": "outline", "start": list(start), "end": list(end)},
            )["outcome"]
            for start, end in zip(corners, corners[1:] + corners[:1], strict=True)
        ]

        result = runner("catia_pad", {"sketch": "outline", "length_mm": 5.0})

        assert outcomes == ["started", "extended", "extended", "closed"]
        assert result["volume_mm3"] == pytest.approx(20.0 * 20.0 * 5.0, abs=TOL)

    def test_an_open_run_is_not_a_profile(self) -> None:
        """Three sides of a square are not a square, and a pad must say so rather than
        close the gap with an edge nobody drew."""
        from app.kernel import OcctRunner
        from app.kernel.errors import GeometryError

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Open"})
        runner("catia_sketch_create", {"support": "XY", "name": "outline"})
        for start, end in (
            ((0.0, 0.0), (20.0, 0.0)),
            ((20.0, 0.0), (20.0, 20.0)),
            ((20.0, 20.0), (0.0, 20.0)),
        ):
            runner("catia_sketch_line", {"sketch": "outline", "start": start, "end": end})

        with pytest.raises(GeometryError, match="no closed profile"):
            runner("catia_pad", {"sketch": "outline", "length_mm": 5.0})

    def test_a_line_and_an_arc_close_a_half_disc(self) -> None:
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "D"})
        runner("catia_sketch_create", {"support": "XY", "name": "outline"})
        runner("catia_sketch_line", {"sketch": "outline", "start": [0.0, -10.0], "end": [0.0, 10.0]})
        runner(
            "catia_sketch_arc",
            {
                "sketch": "outline",
                "centre": [0.0, 0.0],
                "radius_mm": 10.0,
                "start_angle_deg": 90.0,
                "end_angle_deg": -90.0,
            },
        )

        result = runner("catia_pad", {"sketch": "outline", "length_mm": 4.0})

        assert result["volume_mm3"] == pytest.approx(math.pi * 100.0 / 2 * 4.0, rel=1e-4)

    def test_a_closed_polyline_needs_no_repeated_point(self) -> None:
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Poly"})
        runner("catia_sketch_create", {"support": "XY", "name": "outline"})
        runner(
            "catia_sketch_polyline",
            {
                "sketch": "outline",
                "points": [[0.0, 0.0], [20.0, 0.0], [20.0, 20.0], [0.0, 20.0]],
                "closed": True,
            },
        )

        result = runner("catia_pad", {"sketch": "outline", "length_mm": 5.0})

        assert result["volume_mm3"] == pytest.approx(2000.0, abs=TOL)

    def test_an_ellipse_is_a_closed_profile(self) -> None:
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Ellipse"})
        runner("catia_sketch_create", {"support": "XY", "name": "outline"})
        runner(
            "catia_sketch_ellipse",
            {
                "sketch": "outline",
                "centre": [0.0, 0.0],
                "major_radius_mm": 20.0,
                "minor_radius_mm": 10.0,
            },
        )

        result = runner("catia_pad", {"sketch": "outline", "length_mm": 3.0})

        assert result["volume_mm3"] == pytest.approx(math.pi * 20.0 * 10.0 * 3.0, rel=1e-4)

    def test_a_swapped_pair_of_ellipse_radii_is_named(self) -> None:
        from app.kernel import OcctRunner
        from app.kernel.errors import GeometryError

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Ellipse"})
        runner("catia_sketch_create", {"support": "XY", "name": "outline"})

        with pytest.raises(GeometryError, match="Swap them"):
            runner(
                "catia_sketch_ellipse",
                {
                    "sketch": "outline",
                    "centre": [0.0, 0.0],
                    "major_radius_mm": 10.0,
                    "minor_radius_mm": 20.0,
                },
            )

    def test_a_drawn_axis_is_what_a_shaft_turns_about(self) -> None:
        """A profile away from the sketch origin needs an axis, and there is nothing to
        infer one from: the same profile about two lines is two different parts."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Ring"})
        runner("catia_sketch_create", {"support": "XY", "name": "section"})
        runner(
            "catia_sketch_rectangle",
            {"sketch": "section", "width_mm": 4.0, "height_mm": 6.0, "at": [20.0, 0.0]},
        )
        runner("catia_sketch_axis", {"sketch": "section", "start": [0.0, -10.0], "end": [0.0, 10.0]})

        result = runner("catia_shaft", {"sketch": "section"})

        # Pappus: the 4x6 section sweeps a circle of radius 20 about the drawn axis.
        assert result["volume_mm3"] == pytest.approx(4.0 * 6.0 * 2 * math.pi * 20.0, rel=1e-4)


class TestSweptFeatures:
    """`catia_rib` and `catia_slot` — a section dragged along a path.

    Verified against Pappus's theorem rather than against recorded output: a section of
    known area swept along a known path has a volume that can be written down, and a
    sweep that silently slid or rotated the profile would not reproduce it.
    """

    @staticmethod
    def _straight_path(runner):
        runner("catia_sketch_create", {"support": "XY", "name": "path"})
        runner("catia_sketch_line", {"sketch": "path", "start": [0.0, 0.0], "end": [50.0, 0.0]})

    def test_a_rib_along_a_straight_guide_is_section_times_length(self) -> None:
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Bar"})
        self._straight_path(runner)
        runner("catia_sketch_create", {"support": "YZ", "name": "section"})
        runner(
            "catia_sketch_rectangle",
            {"sketch": "section", "width_mm": 10.0, "height_mm": 10.0},
        )

        result = runner("catia_rib", {"profile": "section", "centre_curve": "path"})

        assert result["volume_mm3"] == pytest.approx(10.0 * 10.0 * 50.0, abs=TOL)

    def test_a_rib_round_a_bend_matches_pappus(self) -> None:
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Bend"})
        runner("catia_sketch_create", {"support": "XY", "name": "path"})
        runner(
            "catia_sketch_arc",
            {
                "sketch": "path",
                "centre": [0.0, 0.0],
                "radius_mm": 30.0,
                "start_angle_deg": 0.0,
                "end_angle_deg": 90.0,
            },
        )
        runner("catia_sketch_create", {"support": "ZX", "name": "section"})
        runner("catia_sketch_circle", {"sketch": "section", "diameter_mm": 10.0, "at": [0.0, 30.0]})

        result = runner("catia_rib", {"profile": "section", "centre_curve": "path"})

        # π·5² × (2π·30 / 4) = 375π².
        assert result["volume_mm3"] == pytest.approx(375.0 * math.pi**2, rel=1e-4)

    def test_a_second_profile_in_the_sketch_bores_the_sweep(self) -> None:
        """A sketch's later profiles are holes in its first — the rule a pad already
        follows — which is what makes a pipe run with a bore one rib rather than two."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Pipe"})
        runner("catia_sketch_create", {"support": "XY", "name": "path"})
        runner("catia_sketch_line", {"sketch": "path", "start": [0.0, 0.0], "end": [40.0, 0.0]})
        runner("catia_sketch_create", {"support": "YZ", "name": "section"})
        runner("catia_sketch_circle", {"sketch": "section", "diameter_mm": 20.0})
        runner("catia_sketch_circle", {"sketch": "section", "diameter_mm": 14.0})

        result = runner("catia_rib", {"profile": "section", "centre_curve": "path"})

        assert result["volume_mm3"] == pytest.approx(math.pi * (100.0 - 49.0) * 40.0, rel=1e-4)

    def test_a_slot_removes_exactly_what_the_rib_would_have_added(self) -> None:
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Slotted"})
        runner("catia_sketch_create", {"support": "XY", "name": "base"})
        runner("catia_sketch_rectangle", {"sketch": "base", "width_mm": 60.0, "height_mm": 40.0})
        runner("catia_pad", {"sketch": "base", "length_mm": 20.0, "name": "block"})
        runner("catia_sketch_create", {"support": "XY", "name": "path", "origin": [0.0, 0.0, 20.0]})
        runner("catia_sketch_line", {"sketch": "path", "start": [-30.0, 0.0], "end": [30.0, 0.0]})
        # A YZ frame's own axes are (world Y, world Z, world X) and `origin` shifts along
        # those, so world (-30, 0, 20) is written [0, 20, -30].
        runner(
            "catia_sketch_create",
            {"support": "YZ", "name": "channel", "origin": [0.0, 20.0, -30.0]},
        )
        runner("catia_sketch_rectangle", {"sketch": "channel", "width_mm": 8.0, "height_mm": 8.0})

        result = runner("catia_slot", {"profile": "channel", "centre_curve": "path"})

        # Half the 8x8 section is above the top face, so 8x4 is removed over 60 mm.
        assert result["volume_mm3"] == pytest.approx(60.0 * 40.0 * 20.0 - 8.0 * 4.0 * 60.0, abs=TOL)

    def test_the_section_is_swept_from_where_it_was_drawn(self) -> None:
        """OCCT will happily slide a profile onto the spine and turn it normal to the
        path. An eccentric rib — a section deliberately offset from its guide — is a real
        shape, and repairing it silently would build a different part from the drawing
        with nothing downstream able to tell."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Eccentric"})
        self._straight_path(runner)
        runner("catia_sketch_create", {"support": "YZ", "name": "section"})
        runner(
            "catia_sketch_rectangle",
            {"sketch": "section", "width_mm": 10.0, "height_mm": 10.0, "at": [0.0, 20.0]},
        )

        result = runner("catia_rib", {"profile": "section", "centre_curve": "path"})

        box = result["bounding_box_mm"]
        centre_z = (box["min"][2] + box["max"][2]) / 2.0
        assert centre_z == pytest.approx(20.0, abs=TOL)
        assert result["volume_mm3"] == pytest.approx(10.0 * 10.0 * 50.0, abs=TOL)

    def test_a_path_sketch_holding_several_curves_is_refused_by_name(self) -> None:
        """Picking the first of several would build a rib along a path the author did
        not choose, and nothing downstream could tell."""
        from app.kernel import OcctRunner
        from app.kernel.errors import GeometryError

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Ambiguous"})
        runner("catia_sketch_create", {"support": "XY", "name": "path"})
        runner("catia_sketch_line", {"sketch": "path", "start": [0.0, 0.0], "end": [50.0, 0.0]})
        runner("catia_sketch_line", {"sketch": "path", "start": [0.0, 20.0], "end": [50.0, 20.0]})
        runner("catia_sketch_create", {"support": "YZ", "name": "section"})
        runner("catia_sketch_rectangle", {"sketch": "section", "width_mm": 10.0, "height_mm": 10.0})

        with pytest.raises(GeometryError, match="needs one curve to follow"):
            runner("catia_rib", {"profile": "section", "centre_curve": "path"})


class TestThreadsAreAnnotations:
    """A thread drives the callout and the tap, and changes no geometry.

    Modelling the helix would change every measurement the part reports for a shape
    nobody inspects. What must never happen is the reverse of the honesty rule: a mass
    that has quietly lost material a helix would have removed, or a pitch invented for a
    designation this code cannot read.
    """

    @staticmethod
    def _tapped_block(runner):
        runner("catia_new_part", {"name": "Tapped"})
        runner("catia_sketch_create", {"support": "XY", "name": "base"})
        runner("catia_sketch_rectangle", {"sketch": "base", "width_mm": 40.0, "height_mm": 40.0})
        runner("catia_pad", {"sketch": "base", "length_mm": 20.0, "name": "block"})
        return runner(
            "catia_hole_at",
            {
                "face": "block#top",
                "at": [0.0, 0.0],
                "diameter_mm": 8.5,
                "depth_mm": 15.0,
                "name": "tap_hole",
            },
        )

    def test_declaring_a_thread_leaves_the_mass_alone(self) -> None:
        from app.kernel import OcctRunner

        runner = OcctRunner()
        before = self._tapped_block(runner)

        after = runner(
            "catia_thread", {"face": {"cylindrical": True}, "designation": "M10"}
        )

        assert after["volume_mm3"] == pytest.approx(before["volume_mm3"], abs=TOL)
        assert after["thread"]["pitch_mm"] == pytest.approx(1.5)
        assert after["thread"]["minor_diameter_mm"] == pytest.approx(8.37625)

    def test_a_designation_that_cannot_fit_the_face_is_refused(self) -> None:
        from app.kernel import OcctRunner
        from app.kernel.errors import GeometryError

        runner = OcctRunner()
        self._tapped_block(runner)

        with pytest.raises(GeometryError, match="tapping drill"):
            runner("catia_thread", {"face": {"cylindrical": True}, "designation": "M20"})

    def test_an_unreadable_designation_reports_no_pitch_rather_than_a_guess(self) -> None:
        from app.kernel import OcctRunner

        runner = OcctRunner()
        self._tapped_block(runner)

        result = runner(
            "catia_thread", {"face": {"cylindrical": True}, "designation": "1/4-20 UNC"}
        )

        assert "pitch_mm" not in result["thread"]
        assert "ISO metric" in result["thread"]["unrecognised"]

    def test_a_second_thread_on_one_face_replaces_the_first(self) -> None:
        """A cylinder carries one thread, and a regeneration re-runs the call — appending
        would give the part one more thread on every rebuild."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        self._tapped_block(runner)
        runner("catia_thread", {"face": {"cylindrical": True}, "designation": "M10"})

        result = runner(
            "catia_thread", {"face": {"cylindrical": True}, "designation": "M10x1.25"}
        )

        assert len(result["threads"]) == 1
        assert result["threads"][0]["pitch_mm"] == pytest.approx(1.25)

    def test_every_number_a_thread_reports_is_in_the_measurement_contract(self) -> None:
        """A quantity in a payload that the contract does not describe is one a design
        can assert on with no stated unit or meaning."""
        from app.kernel import OcctRunner, contract

        runner = OcctRunner()
        self._tapped_block(runner)

        result = runner(
            "catia_thread", {"face": {"cylindrical": True}, "designation": "M10"}
        )

        assert contract.undocumented_paths(result) == ()

    def test_a_thread_needs_a_cylinder(self) -> None:
        from app.kernel import OcctRunner
        from app.kernel.errors import GeometryError

        runner = OcctRunner()
        self._tapped_block(runner)

        with pytest.raises(GeometryError, match="not a cylindrical face"):
            runner("catia_thread", {"face": "block#top", "designation": "M10"})
