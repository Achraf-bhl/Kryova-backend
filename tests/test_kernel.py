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
        """`up_to_surface` is the one limit still owed, and it says what it needs.

        Matched on **what is missing**, not on the phase number it used to name. The
        reason went stale the moment constructed surfaces shipped — the blocker was never
        having a surface but trimming against one — and a test pinned to `Phase 2.6`
        would have kept the wrong explanation alive by failing when it was corrected.
        """
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "P"})
        runner("catia_sketch_create", {"support": "XY", "name": "s"})
        runner("catia_sketch_rectangle", {"sketch": "s", "width_mm": 10.0, "height_mm": 10.0})

        with pytest.raises(OperationNotSupported, match="trimmed against it"):
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


class TestStiffenersFindTheirOwnBoundaries:
    """A stiffener's profile says where it *runs*, never where it stops.

    That is the whole feature: the gusset between a wall and a base is bounded by the
    wall and the base, so it stays right when either moves. Every test here checks the
    triangle it fills against ½·b·h·t rather than against a recorded volume, because a
    stiffener that extrudes to a plausible length looks identical in a screenshot and is
    a different part.
    """

    @staticmethod
    def _bracket() -> object:
        """An L: base 60×40×10 at the origin, wall 10×40×40 standing on its short end."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Bracket"})

        runner("catia_sketch_create", {"support": "XY", "name": "base"})
        corners = [(0.0, 0.0), (60.0, 0.0), (60.0, 40.0), (0.0, 40.0)]
        for start, end in zip(corners, corners[1:] + corners[:1], strict=True):
            runner("catia_sketch_line", {"sketch": "base", "start": start, "end": end})
        runner("catia_pad", {"sketch": "base", "length_mm": 10.0})

        runner(
            "catia_plane_offset",
            {"reference": "XY", "distance_mm": 10.0, "name": "base_top"},
        )
        runner("catia_sketch_create", {"support": "base_top", "name": "wall"})
        corners = [(0.0, 0.0), (10.0, 0.0), (10.0, 40.0), (0.0, 40.0)]
        for start, end in zip(corners, corners[1:] + corners[:1], strict=True):
            runner("catia_sketch_line", {"sketch": "wall", "start": start, "end": end})
        runner("catia_pad", {"sketch": "wall", "length_mm": 40.0})
        return runner

    @staticmethod
    def _diagonal(runner: object, *, start: tuple[float, float], end: tuple[float, float]) -> None:
        """A line across the corner, on the plane halfway through the bracket's width.

        Drawn on an offset of `ZX` so the plate sits inside the material it braces: on
        `ZX` itself half its thickness would hang off the y=0 face, where there is no
        wall for it to stop against.
        """
        runner("catia_plane_offset", {"reference": "ZX", "distance_mm": 20.0, "name": "mid"})  # type: ignore[operator]
        runner("catia_sketch_create", {"support": "mid", "name": "gusset"})  # type: ignore[operator]
        runner("catia_sketch_line", {"sketch": "gusset", "start": start, "end": end})  # type: ignore[operator]

    #: The bracket's own volume: 60·40·10 base plus 10·40·40 wall.
    BRACKET_MM3 = 60.0 * 40.0 * 10.0 + 10.0 * 40.0 * 40.0

    def test_the_gusset_is_the_triangle_the_walls_close(self) -> None:
        """The profile runs from (10, 30) on the wall to (40, 10) on the base, so the
        void behind it is a right triangle with legs of 20 and 30 mm."""
        runner = self._bracket()
        self._diagonal(runner, start=(30.0, 10.0), end=(10.0, 40.0))

        result = runner("catia_stiffener", {"profile": "gusset", "thickness_mm": 6.0})  # type: ignore[operator]

        assert result["volume_mm3"] == pytest.approx(
            self.BRACKET_MM3 + 0.5 * 20.0 * 30.0 * 6.0, abs=TOL
        )

    def test_thickness_scales_the_gusset_and_nothing_else(self) -> None:
        """Twice the plate is twice the added material — not a different triangle."""
        volumes = []
        for thickness in (3.0, 6.0):
            runner = self._bracket()
            self._diagonal(runner, start=(30.0, 10.0), end=(10.0, 40.0))
            volumes.append(
                runner("catia_stiffener", {"profile": "gusset", "thickness_mm": thickness})[  # type: ignore[operator]
                    "volume_mm3"
                ]
                - self.BRACKET_MM3
            )

        assert volumes[1] == pytest.approx(2 * volumes[0], abs=TOL)

    def test_a_stiffener_grown_away_from_the_part_is_refused(self) -> None:
        """The failure this operation is most likely to have, and the one a silent
        answer would hide: swept the wrong way it meets nothing, and a slab hanging in
        the air is a part nobody would notice was wrong until it was cut."""
        from app.kernel.errors import GeometryError

        runner = self._bracket()
        self._diagonal(runner, start=(30.0, 10.0), end=(10.0, 40.0))

        with pytest.raises(GeometryError, match="never met material"):
            runner(  # type: ignore[operator]
                "catia_stiffener",
                {"profile": "gusset", "thickness_mm": 6.0, "reversed": True},
            )

    def test_reversed_is_what_a_profile_drawn_backwards_needs(self) -> None:
        """The sweep is squared against the chord from start to end, so drawing the same
        line the other way round points it the other way — and `reversed` is the whole of
        the correction. Same triangle, same volume."""
        runner = self._bracket()
        self._diagonal(runner, start=(10.0, 40.0), end=(30.0, 10.0))

        result = runner(  # type: ignore[operator]
            "catia_stiffener",
            {"profile": "gusset", "thickness_mm": 6.0, "reversed": True},
        )

        assert result["volume_mm3"] == pytest.approx(
            self.BRACKET_MM3 + 0.5 * 20.0 * 30.0 * 6.0, abs=TOL
        )

    def test_a_closed_profile_is_not_a_stiffener(self) -> None:
        """A closed profile states its own boundary, which is the one thing a stiffener
        must not do. Padding it would build something, and something is worse here."""
        from app.kernel.errors import GeometryError

        runner = self._bracket()
        runner("catia_sketch_create", {"support": "XY", "name": "closed"})  # type: ignore[operator]
        corners = [(20.0, 5.0), (30.0, 5.0), (30.0, 15.0), (20.0, 15.0)]
        for start, end in zip(corners, corners[1:] + corners[:1], strict=True):
            runner("catia_sketch_line", {"sketch": "closed", "start": start, "end": end})  # type: ignore[operator]

        with pytest.raises(GeometryError, match="needs an open profile"):
            runner("catia_stiffener", {"profile": "closed", "thickness_mm": 6.0})  # type: ignore[operator]

    def test_a_stiffener_needs_material_to_brace(self) -> None:
        from app.kernel import OcctRunner
        from app.kernel.errors import GeometryError

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Empty"})
        runner("catia_sketch_create", {"support": "XY", "name": "gusset"})
        runner("catia_sketch_line", {"sketch": "gusset", "start": (0.0, 0.0), "end": (10.0, 10.0)})

        with pytest.raises(GeometryError, match="braces material that is already there"):
            runner("catia_stiffener", {"profile": "gusset", "thickness_mm": 6.0})


class TestDraftSplitsAtAPartingElement:
    """A two-part mould needs both halves to release, which one taper cannot express.

    Checked against the frustum volume h/3·(a²+ab+b²) on each side of the parting plane,
    so a draft that tapered the whole part one way — the thing this replaced — fails by
    the difference between the two closed forms rather than by looking slightly off.
    """

    ANGLE_DEG = 3.0
    SIDE_MM = 40.0
    HEIGHT_MM = 20.0

    @staticmethod
    def _block() -> object:
        """A 40×40×20 block with a constructed plane through its middle."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Moulding"})
        runner("catia_sketch_create", {"support": "XY", "name": "outline"})
        corners = [(0.0, 0.0), (40.0, 0.0), (40.0, 40.0), (0.0, 40.0)]
        for start, end in zip(corners, corners[1:] + corners[:1], strict=True):
            runner("catia_sketch_line", {"sketch": "outline", "start": start, "end": end})
        runner("catia_pad", {"sketch": "outline", "length_mm": 20.0})
        runner(
            "catia_plane_offset",
            {"reference": "XY", "distance_mm": 10.0, "name": "parting"},
        )
        return runner

    @staticmethod
    def _frustum(bottom: float, top: float, height: float) -> float:
        return height / 3.0 * (bottom**2 + bottom * top + top**2)

    def _walls(self) -> dict[str, object]:
        return {"type": "face", "parallel_to": "z"}

    def test_both_sides_taper_away_from_the_parting_plane(self) -> None:
        runner = self._block()
        drop = 2 * (self.HEIGHT_MM / 2) * math.tan(math.radians(self.ANGLE_DEG))

        result = runner(  # type: ignore[operator]
            "catia_draft",
            {
                "faces": self._walls(),
                "angle_deg": self.ANGLE_DEG,
                "neutral": "parting",
                "parting": "parting",
                "pulling_direction": [0.0, 0.0, 1.0],
            },
        )

        assert result["volume_mm3"] == pytest.approx(
            2 * self._frustum(self.SIDE_MM, self.SIDE_MM - drop, self.HEIGHT_MM / 2),
            abs=TOL,
        )

    def test_a_parted_draft_is_not_the_same_part_as_an_unparted_one(self) -> None:
        """Same faces, same angle, same neutral plane — and a different solid. Without
        the parting element the taper runs the full height in one direction, which is
        the mould that locks."""
        drop = 2 * self.HEIGHT_MM * math.tan(math.radians(self.ANGLE_DEG))
        runner = self._block()

        result = runner(  # type: ignore[operator]
            "catia_draft",
            {
                "faces": self._walls(),
                "angle_deg": self.ANGLE_DEG,
                "neutral": "parting",
                "pulling_direction": [0.0, 0.0, 1.0],
            },
        )

        assert result["volume_mm3"] == pytest.approx(
            self._frustum(self.SIDE_MM + drop / 2, self.SIDE_MM - drop / 2, self.HEIGHT_MM),
            abs=1e-5,
        )

    def test_a_parting_element_that_misses_the_part_is_refused(self) -> None:
        """It splits nothing, so one 'half' is empty — and drafting one side of nothing
        would return the part untouched, reported as a successful draft."""
        from app.kernel.errors import GeometryError

        runner = self._block()
        runner("catia_plane_offset", {"reference": "XY", "distance_mm": 500.0, "name": "far"})  # type: ignore[operator]

        with pytest.raises(GeometryError, match="no material on one side"):
            runner(  # type: ignore[operator]
                "catia_draft",
                {
                    "faces": self._walls(),
                    "angle_deg": self.ANGLE_DEG,
                    "neutral": "parting",
                    "parting": "far",
                },
            )

    def test_the_neutral_element_may_be_a_face_of_the_part(self) -> None:
        """It used to be refused as needing `feature#selector`, which is now built. A
        moulding's neutral element is a face of the moulding far more often than it is
        one of the three origin planes."""
        runner = self._block()
        drop = 2 * self.HEIGHT_MM * math.tan(math.radians(self.ANGLE_DEG))

        result = runner(  # type: ignore[operator]
            "catia_draft",
            {
                "faces": self._walls(),
                "angle_deg": self.ANGLE_DEG,
                "neutral": {"type": "face", "axis": "z", "side": "min"},
            },
        )

        assert result["volume_mm3"] == pytest.approx(
            self._frustum(self.SIDE_MM, self.SIDE_MM - drop, self.HEIGHT_MM), abs=1e-5
        )

    def test_a_reflect_line_draft_still_says_what_it_needs(self) -> None:
        """The parting element is implemented and the reflect line is not — and the
        refusal must not now imply the two were the same gap."""
        from app.kernel.errors import OperationNotSupported

        runner = self._block()

        with pytest.raises(OperationNotSupported, match="silhouette"):
            runner(  # type: ignore[operator]
                "catia_draft",
                {
                    "faces": self._walls(),
                    "angle_deg": self.ANGLE_DEG,
                    "neutral": "XY",
                    "mode": "reflect_line",
                },
            )


class TestSurfacesAreSkinNotMaterial:
    """A surface has area and no volume, and the part does not change when one is built.

    That is the property the whole shape-design layer rests on: a skin is built, joined,
    checked and only then turned into material by `catia_close_surface` or
    `catia_thick_surface`. A surface that quietly became the active body would report a
    part with no solid, which reads like a failed feature rather than like a skin waiting
    to be closed — so the first test here is that nothing happened to the part.

    Every area and volume is checked against the closed form, never against a recorded
    number: a loft that comes out the wrong shape still looks like a loft.
    """

    @staticmethod
    def _sheet() -> object:
        """A 50 × 20 rectangle of skin, drawn as a line and extruded 20 mm up +Z."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Skin"})
        runner("catia_sketch_create", {"support": "XY", "name": "edge"})
        runner("catia_sketch_line", {"sketch": "edge", "start": (0.0, 0.0), "end": (50.0, 0.0)})
        runner(
            "catia_surface_extrude",
            {"profile": "edge", "direction": [0, 0, 1], "length_mm": 20.0, "name": "wall"},
        )
        return runner

    @staticmethod
    def _square(runner: object, name: str = "outline", side: float = 40.0) -> None:
        """A closed square contour, drawn segment by segment on XY."""
        corners = [(0.0, 0.0), (side, 0.0), (side, side), (0.0, side)]
        runner("catia_sketch_create", {"support": "XY", "name": name})  # type: ignore[operator]
        for start, end in zip(corners, corners[1:] + corners[:1], strict=True):
            runner("catia_sketch_line", {"sketch": name, "start": start, "end": end})  # type: ignore[operator]

    def test_building_a_surface_leaves_the_part_without_material(self) -> None:
        runner = self._sheet()

        payload = runner("catia_measure", {})  # type: ignore[operator]

        assert payload["has_solid"] is False
        assert "volume_mm3" not in payload
        assert payload["construction"] == [{"name": "wall", "kind": "surface"}]

    def test_an_extruded_curve_has_the_area_it_swept(self) -> None:
        runner = self._sheet()

        measured = runner("catia_measure_item", {"element": "wall"})  # type: ignore[operator]

        assert measured["area_mm2"] == pytest.approx(50.0 * 20.0, abs=TOL)

    def test_a_symmetric_extrusion_is_the_stated_length_in_total(self) -> None:
        """The same rule `catia_pad` follows: the number is the whole extent, not the
        extent each way. The two must agree or one number means two things."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Sym"})
        runner("catia_sketch_create", {"support": "XY", "name": "edge"})
        runner("catia_sketch_line", {"sketch": "edge", "start": (0.0, 0.0), "end": (50.0, 0.0)})
        runner(
            "catia_surface_extrude",
            {
                "profile": "edge",
                "direction": [0, 0, 1],
                "length_mm": 20.0,
                "symmetric": True,
                "name": "wall",
            },
        )

        measured = runner("catia_measure_item", {"element": "wall"})

        assert measured["area_mm2"] == pytest.approx(50.0 * 20.0, abs=TOL)

    def test_a_revolved_curve_is_a_cylinder_of_the_right_area(self) -> None:
        """2πrh, from a line 10 mm off the axis revolved right round it."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Rev"})
        # ZX places u along Z and v along X, so this is a line at x = 10 from z = 0 to 30.
        runner("catia_sketch_create", {"support": "ZX", "name": "gen"})
        runner("catia_sketch_line", {"sketch": "gen", "start": (0.0, 10.0), "end": (30.0, 10.0)})
        runner("catia_surface_revolve", {"profile": "gen", "axis": "Z", "name": "tube"})

        measured = runner("catia_measure_item", {"element": "tube"})

        assert measured["area_mm2"] == pytest.approx(2 * math.pi * 10.0 * 30.0, abs=1e-9)

    def test_a_flat_fill_is_exact_rather_than_approximated(self) -> None:
        """The reason `_flat_patch` exists. OCCT's filling algorithm fits a B-spline
        through the boundary, so a circular hole patched with it measures 314.1595 mm²
        against πr² = 314.1593 and carries a bounding box half as big again as the disc.
        A flat opening has an exact answer and this takes it."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Disc"})
        runner("catia_sketch_create", {"support": "XY", "name": "rim"})
        runner("catia_sketch_circle", {"sketch": "rim", "diameter_mm": 20.0})
        runner("catia_surface_fill", {"boundary": ["rim"], "name": "disc"})

        measured = runner("catia_measure_item", {"element": "disc"})

        assert measured["area_mm2"] == pytest.approx(math.pi * 100.0, abs=1e-9)

    def test_a_loft_between_two_circles_is_a_frustum(self) -> None:
        """Lateral area π(R + r)·slant, which no other surface through those two
        sections has."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Loft"})
        runner("catia_sketch_create", {"support": "XY", "name": "big"})
        runner("catia_sketch_circle", {"sketch": "big", "diameter_mm": 20.0})
        runner("catia_plane_offset", {"reference": "XY", "distance_mm": 20.0, "name": "top"})
        runner("catia_sketch_create", {"support": "top", "name": "small"})
        runner(
            "catia_sketch_circle", {"sketch": "small", "diameter_mm": 10.0}
        )

        runner("catia_surface_loft", {"sections": ["big", "small"], "name": "side"})

        slant = math.sqrt(20.0**2 + 5.0**2)
        measured = runner("catia_measure_item", {"element": "side"})
        assert measured["area_mm2"] == pytest.approx(math.pi * 15.0 * slant, abs=1e-6)

    def test_an_offset_surface_sits_at_the_stated_radius(self) -> None:
        """A cylinder offset outward by 2 mm is a cylinder of radius 12 — the check that
        the offset went along the normal and by the distance asked for."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Off"})
        runner("catia_sketch_create", {"support": "ZX", "name": "gen"})
        runner("catia_sketch_line", {"sketch": "gen", "start": (0.0, 10.0), "end": (30.0, 10.0)})
        runner("catia_surface_revolve", {"profile": "gen", "axis": "Z", "name": "tube"})

        runner("catia_surface_offset", {"surface": "tube", "distance_mm": 2.0, "name": "outer"})

        measured = runner("catia_measure_item", {"element": "outer"})
        assert measured["area_mm2"] == pytest.approx(2 * math.pi * 12.0 * 30.0, abs=1e-6)

    def test_the_free_boundary_of_a_sheet_is_its_outline(self) -> None:
        runner = self._sheet()

        runner("catia_boundary", {"surface": "wall", "name": "rim"})  # type: ignore[operator]

        measured = runner("catia_measure_item", {"element": "rim"})  # type: ignore[operator]
        assert measured["length_mm"] == pytest.approx(2 * (50.0 + 20.0), abs=TOL)

    def test_extracting_a_face_by_selector_takes_that_face_and_no_other(self) -> None:
        """Where `feature#selector` pays for itself: the top face is named rather than
        indexed, so the extract survives everything that renumbers the part."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Block"})
        self._square(runner, "base", 20.0)
        runner("catia_pad", {"sketch": "base", "length_mm": 40.0, "name": "block"})

        runner("catia_extract", {"elements": ["block#top"], "name": "lid"})

        measured = runner("catia_measure_item", {"element": "lid"})
        assert measured["area_mm2"] == pytest.approx(20.0 * 20.0, abs=TOL)
        assert measured["element"]["entity_count"] == 1


class TestASkinBecomesMaterialOnlyWhenAsked:
    """The seam between shape design and part design, in both directions it is crossed.

    `catia_close_surface` fills a closed skin; `catia_thick_surface` gives an open one a
    wall. Both are checked against the closed form for the solid they should produce,
    because the failure mode here is a solid of plausible size — a fill that bridged a
    gap nobody noticed, or a wall grown on the wrong side.
    """

    #: A truncated cone: bottom radius 10, top radius 5, height 20.
    R_MM, R_TOP_MM, H_MM = 10.0, 5.0, 20.0

    @classmethod
    def _closed_frustum(cls) -> object:
        """A part built entirely as skin — lofted side, two flat caps, sewn together."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Frustum"})
        runner("catia_sketch_create", {"support": "XY", "name": "big"})
        runner(
            "catia_sketch_circle",
            {"sketch": "big", "diameter_mm": 2 * cls.R_MM},
        )
        runner("catia_plane_offset", {"reference": "XY", "distance_mm": cls.H_MM, "name": "top"})
        runner("catia_sketch_create", {"support": "top", "name": "small"})
        runner(
            "catia_sketch_circle",
            {"sketch": "small", "diameter_mm": 2 * cls.R_TOP_MM},
        )
        runner("catia_surface_loft", {"sections": ["big", "small"], "name": "side"})
        runner("catia_surface_fill", {"boundary": ["big"], "name": "bottom"})
        runner("catia_surface_fill", {"boundary": ["small"], "name": "lid"})
        return runner

    @staticmethod
    def _panel() -> object:
        """A 40 × 40 flat patch of skin and nothing else."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Panel"})
        runner("catia_sketch_create", {"support": "XY", "name": "outline"})
        corners = [(0.0, 0.0), (40.0, 0.0), (40.0, 40.0), (0.0, 40.0)]
        for start, end in zip(corners, corners[1:] + corners[:1], strict=True):
            runner("catia_sketch_line", {"sketch": "outline", "start": start, "end": end})
        runner("catia_surface_fill", {"boundary": ["outline"], "name": "patch"})
        return runner

    def test_closing_a_sewn_skin_gives_the_solid_it_encloses(self) -> None:
        """h/3·π(R² + Rr + r²) — the frustum, reached without a single solid feature."""
        runner = self._closed_frustum()

        runner("catia_join", {"elements": ["side", "bottom", "lid"], "name": "skin"})  # type: ignore[operator]
        result = runner("catia_close_surface", {"surface": "skin", "name": "solid"})  # type: ignore[operator]

        expected = (
            self.H_MM
            / 3.0
            * math.pi
            * (self.R_MM**2 + self.R_MM * self.R_TOP_MM + self.R_TOP_MM**2)
        )
        assert result["volume_mm3"] == pytest.approx(expected, abs=1e-6)
        assert result["solid_count"] == 1

    def test_a_skin_that_is_not_closed_is_refused_rather_than_filled(self) -> None:
        """The failure this operation exists to catch. An almost-closed skin filled
        anyway is a solid with a hole in it that measures a plausible volume, which is
        strictly worse than a refusal naming the gap."""
        runner = self._closed_frustum()

        with pytest.raises(GeometryError, match="not closed"):
            runner("catia_close_surface", {"surface": "side", "name": "solid"})  # type: ignore[operator]

    def test_a_thickened_panel_is_the_area_times_the_wall(self) -> None:
        runner = self._panel()

        result = runner("catia_thick_surface", {"surface": "patch", "thickness_mm": 3.0})  # type: ignore[operator]

        assert result["volume_mm3"] == pytest.approx(40.0 * 40.0 * 3.0, abs=1e-6)

    def test_material_grows_along_the_normal_and_the_second_side_grows_back(self) -> None:
        """Both halves of `thickness_mm` / `second_thickness_mm`, checked by where the
        material actually is rather than only by how much of it there is."""
        runner = self._panel()

        result = runner(  # type: ignore[operator]
            "catia_thick_surface",
            {"surface": "patch", "thickness_mm": 3.0, "second_thickness_mm": 2.0},
        )

        assert result["volume_mm3"] == pytest.approx(40.0 * 40.0 * 5.0, abs=1e-5)
        box = result["bounding_box_mm"]
        assert box["min"][2] == pytest.approx(-2.0, abs=1e-5)
        assert box["max"][2] == pytest.approx(3.0, abs=1e-5)

    def test_a_thickened_surface_can_be_fused_into_the_part(self) -> None:
        """The reason `_outward` exists, and it cannot be checked any other way.

        OCCT returns the thickened solid inside-out: its signed volume is negative,
        `BRepCheck_Analyzer` calls it valid — because it is, it is the complement — and a
        later fuse **silently returns the wrong answer** instead of failing. Fusing a
        1,000 mm³ block onto the uncorrected 4,800 mm³ plate measured −4,800: no error,
        block gone. So the check is a fuse, not a volume.
        """
        runner = self._panel()
        runner("catia_thick_surface", {"surface": "patch", "thickness_mm": 3.0})  # type: ignore[operator]

        runner("catia_sketch_create", {"support": "XY", "name": "boss"})  # type: ignore[operator]
        runner(  # type: ignore[operator]
            "catia_sketch_circle",
            {"sketch": "boss", "at": (20.0, 20.0), "diameter_mm": 10.0},
        )
        result = runner("catia_pad", {"sketch": "boss", "length_mm": 8.0})  # type: ignore[operator]

        # The pad starts at z = 0 inside the plate and runs to z = 8, so the material it
        # adds is the 5 mm standing clear of the 3 mm wall.
        added = math.pi * 5.0**2 * 5.0
        assert result["volume_mm3"] == pytest.approx(40.0 * 40.0 * 3.0 + added, abs=1e-4)

    def test_joining_elements_that_do_not_meet_is_refused_by_count(self) -> None:
        """A join that quietly returned two disconnected shells would be found by
        whatever failed next, several operations later — so it says how many pieces are
        left, and `check_connexity: false` is how a design says it meant that."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Apart"})
        for name, x in (("near", 0.0), ("far", 50.0)):
            runner("catia_sketch_create", {"support": "XY", "name": f"{name}_line"})
            runner(
                "catia_sketch_line",
                {"sketch": f"{name}_line", "start": (x, 0.0), "end": (x + 10.0, 0.0)},
            )
            runner(
                "catia_surface_extrude",
                {
                    "profile": f"{name}_line",
                    "direction": [0, 0, 1],
                    "length_mm": 5.0,
                    "name": name,
                },
            )

        with pytest.raises(GeometryError, match="2 separate pieces"):
            runner("catia_join", {"elements": ["near", "far"], "name": "sewn"})

        runner(
            "catia_join",
            {"elements": ["near", "far"], "check_connexity": False, "name": "sewn"},
        )
        assert runner("catia_measure", {})["construction"][-1]["name"] == "sewn"

    def test_a_curve_named_where_a_surface_belongs_is_refused_by_name(self) -> None:
        runner = self._sheet_with_a_curve()

        with pytest.raises(GeometryError, match="is a curve"):
            runner("catia_thick_surface", {"surface": "rim", "thickness_mm": 1.0})  # type: ignore[operator]

    def test_a_surface_named_where_a_curve_belongs_is_refused_by_name(self) -> None:
        runner = self._sheet_with_a_curve()

        with pytest.raises(GeometryError, match="is a surface"):
            runner(  # type: ignore[operator]
                "catia_surface_extrude",
                {"profile": "wall", "direction": [0, 0, 1], "length_mm": 5.0},
            )

    @staticmethod
    def _sheet_with_a_curve() -> object:
        """A sheet called `wall` and its boundary called `rim` — one of each kind."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Both"})
        runner("catia_sketch_create", {"support": "XY", "name": "edge"})
        runner("catia_sketch_line", {"sketch": "edge", "start": (0.0, 0.0), "end": (50.0, 0.0)})
        runner(
            "catia_surface_extrude",
            {"profile": "edge", "direction": [0, 0, 1], "length_mm": 20.0, "name": "wall"},
        )
        runner("catia_boundary", {"surface": "wall", "name": "rim"})
        return runner

    def test_what_a_surface_cannot_do_yet_is_named_rather_than_ignored(self) -> None:
        """An ignored `guides` builds a different shape from the one asked for and says
        nothing about it. Each of these is a real GSD capability, and each refusal names
        what it would take."""
        runner = self._closed_frustum()

        for arguments, expected in (
            ({"sections": ["big", "small"], "guides": ["big"]}, "guides"),
            ({"sections": ["big", "small"], "spine": "big"}, "spine"),
        ):
            with pytest.raises(OperationNotSupported, match=expected):
                runner("catia_surface_loft", arguments)  # type: ignore[operator]

        with pytest.raises(OperationNotSupported, match="continuity"):
            runner(  # type: ignore[operator]
                "catia_surface_fill", {"boundary": ["big"], "continuity": "tangent"}
            )
        with pytest.raises(OperationNotSupported, match="propagation"):
            runner(  # type: ignore[operator]
                "catia_extract", {"elements": ["side"], "propagation": "tangent"}
            )


class TestCuttingSurfacesAgainstEachOther:
    """Split, trim, untrim, disassemble and heal — the operations that shape a skin.

    Every one of these has to answer "which piece did you mean", and CATIA answers it by
    where the user clicked. There is no click here, so the rule is written down and these
    tests are what hold it: pieces are ordered by the signed distance of their centre from
    the cutting plane, `first` is the side its normal points away from. A rule that was
    only *usually* right would pass a screenshot and fail a regeneration.
    """

    @staticmethod
    def _patch(runner: object, name: str, corners: list[tuple[float, float]], plane: str = "XY") -> None:
        """A flat patch of skin on a plane, from a closed contour."""
        outline = f"{name}_outline"
        runner("catia_sketch_create", {"support": plane, "name": outline})  # type: ignore[operator]
        for start, end in zip(corners, corners[1:] + corners[:1], strict=True):
            runner("catia_sketch_line", {"sketch": outline, "start": start, "end": end})  # type: ignore[operator]
        runner("catia_surface_fill", {"boundary": [outline], "name": name})  # type: ignore[operator]

    @classmethod
    def _panel_and_knife(cls) -> object:
        """A 40 × 40 patch on XY, and a big planar knife standing at x = 15."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Cut"})
        cls._patch(runner, "panel", [(0.0, 0.0), (40.0, 0.0), (40.0, 40.0), (0.0, 40.0)])
        runner("catia_plane_offset", {"reference": "YZ", "distance_mm": 15.0, "name": "cut_at"})
        cls._patch(
            runner,
            "knife",
            [(-50.0, -50.0), (50.0, -50.0), (50.0, 50.0), (-50.0, 50.0)],
            plane="cut_at",
        )
        return runner

    def test_which_side_survives_is_the_side_the_rule_names(self) -> None:
        """40 mm cut at 15 gives 600 and 1000, and which is which is not a coin toss."""
        for keep, expected in (("first", 600.0), ("second", 1000.0), ("both", 1600.0)):
            runner = self._panel_and_knife()

            runner(  # type: ignore[operator]
                "catia_split",
                {"element": "panel", "cutting": "knife", "keep": keep, "name": "half"},
            )

            measured = runner("catia_measure_item", {"element": "half"})  # type: ignore[operator]
            assert measured["area_mm2"] == pytest.approx(expected, abs=TOL), keep

    def test_a_side_takes_every_cell_on_it_not_just_one(self) -> None:
        """Two panels joined into one element and cut together make four cells, two a
        side. Keeping the furthest piece rather than every piece on that side would
        silently drop half the material the caller asked to keep."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Pair"})
        self._patch(runner, "near", [(0.0, 0.0), (40.0, 0.0), (40.0, 10.0), (0.0, 10.0)])
        self._patch(runner, "far", [(0.0, 20.0), (40.0, 20.0), (40.0, 30.0), (0.0, 30.0)])
        runner(
            "catia_join",
            {"elements": ["near", "far"], "check_connexity": False, "name": "both"},
        )
        runner("catia_plane_offset", {"reference": "YZ", "distance_mm": 15.0, "name": "cut_at"})
        self._patch(
            runner,
            "knife",
            [(-50.0, -50.0), (50.0, -50.0), (50.0, 50.0), (-50.0, 50.0)],
            plane="cut_at",
        )

        runner(
            "catia_split",
            {"element": "both", "cutting": "knife", "keep": "first", "name": "stubs"},
        )

        # Two strips 15 mm long and 10 mm wide, not one.
        measured = runner("catia_measure_item", {"element": "stubs"})
        assert measured["area_mm2"] == pytest.approx(2 * 15.0 * 10.0, abs=TOL)

    def test_a_cut_that_misses_is_refused_rather_than_returned_whole(self) -> None:
        """The quiet failure: a cutter that does not reach leaves the element untouched,
        and a split that returned it would report success on a part nobody cut."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Miss"})
        self._patch(runner, "panel", [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)])
        runner("catia_plane_offset", {"reference": "YZ", "distance_mm": 90.0, "name": "far_off"})
        self._patch(
            runner,
            "knife",
            [(-50.0, -50.0), (50.0, -50.0), (50.0, 50.0), (-50.0, 50.0)],
            plane="far_off",
        )

        with pytest.raises(GeometryError, match="one piece"):
            runner("catia_split", {"element": "panel", "cutting": "knife", "name": "half"})

    @classmethod
    def _split_cone(cls) -> object:
        """A lofted frustum cut level at half height — both halves have a closed form."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Cone"})
        runner("catia_sketch_create", {"support": "XY", "name": "big"})
        runner("catia_sketch_circle", {"sketch": "big", "diameter_mm": 20.0})
        runner("catia_plane_offset", {"reference": "XY", "distance_mm": 20.0, "name": "top"})
        runner("catia_sketch_create", {"support": "top", "name": "small"})
        runner("catia_sketch_circle", {"sketch": "small", "diameter_mm": 10.0})
        runner("catia_surface_loft", {"sections": ["big", "small"], "name": "cone"})
        runner("catia_plane_offset", {"reference": "XY", "distance_mm": 10.0, "name": "mid"})
        cls._patch(
            runner,
            "knife",
            [(-50.0, -50.0), (50.0, -50.0), (50.0, 50.0), (-50.0, 50.0)],
            plane="mid",
        )
        return runner

    def test_splitting_a_curved_skin_gives_both_frustums(self) -> None:
        """Radius 10 at the bottom, 5 at the top, 7.5 at the cut: the lower band is
        π(10 + 7.5)·slant and the upper π(7.5 + 5)·slant, with the same slant."""
        slant = math.sqrt(10.0**2 + 2.5**2)

        for keep, radii in (("first", (10.0, 7.5)), ("second", (7.5, 5.0))):
            runner = self._split_cone()

            runner(  # type: ignore[operator]
                "catia_split",
                {"element": "cone", "cutting": "knife", "keep": keep, "name": "band"},
            )

            measured = runner("catia_measure_item", {"element": "band"})  # type: ignore[operator]
            assert measured["area_mm2"] == pytest.approx(
                math.pi * sum(radii) * slant, abs=1e-6
            ), keep

    def test_untrim_restores_the_surface_the_cut_took_away(self) -> None:
        """The cut half remembers the surface it was cut from, and untrim brings the
        whole of it back — the same 971.48 mm² the loft had before anything touched it."""
        runner = self._split_cone()
        whole = runner("catia_measure_item", {"element": "cone"})["area_mm2"]  # type: ignore[operator]
        runner(  # type: ignore[operator]
            "catia_split",
            {"element": "cone", "cutting": "knife", "keep": "first", "name": "band"},
        )

        runner("catia_untrim", {"surface": "band", "name": "restored"})  # type: ignore[operator]

        measured = runner("catia_measure_item", {"element": "restored"})  # type: ignore[operator]
        assert measured["area_mm2"] == pytest.approx(whole, abs=1e-9)

    def test_untrimming_an_endless_surface_is_refused_not_built(self) -> None:
        """The trap this operation is built around. A plane's parameter range is
        infinite, and OCCT does **not** refuse it: `MakeFace` reports success and hands
        back a face of area 8 × 10¹⁰⁰, which then flows into a mass, a bounding box and
        any assertion reading them, looking like a measurement the whole way."""
        runner = self._panel_and_knife()

        with pytest.raises(GeometryError, match="runs to infinity"):
            runner("catia_untrim", {"surface": "knife", "name": "endless"})  # type: ignore[operator]

    def test_disassemble_numbers_the_pieces_after_the_element_they_came_from(self) -> None:
        """The operation has no `name` argument because it makes several elements, so the
        names are derived — and derived from the original, which is the only thing that
        says where they came from."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Apart"})
        self._patch(runner, "near", [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)])
        self._patch(runner, "far", [(50.0, 0.0), (60.0, 0.0), (60.0, 10.0), (50.0, 10.0)])
        runner(
            "catia_join",
            {"elements": ["near", "far"], "check_connexity": False, "name": "both"},
        )

        result = runner("catia_disassemble", {"element": "both"})

        assert result["disassembled"] == ["both.1", "both.2"]
        for name in result["disassembled"]:
            assert runner("catia_measure_item", {"element": name})["area_mm2"] == pytest.approx(
                100.0, abs=TOL
            )

    def test_healing_closes_the_gap_join_refuses_and_connect_names_its_size(self) -> None:
        """The pair that makes the analysis worth running: `connect` reports the smallest
        tolerance that would join the pieces, which is exactly the argument `catia_healing`
        takes. An analysis that stopped at "there is a gap" would leave the engineer
        doubling numbers until something worked."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Gap"})
        self._patch(runner, "left", [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)])
        self._patch(runner, "right", [(10.05, 0.0), (20.05, 0.0), (20.05, 10.0), (10.05, 10.0)])

        with pytest.raises(GeometryError, match="2 separate pieces"):
            runner("catia_join", {"elements": ["left", "right"], "name": "sewn"})

        report = runner("catia_surface_analysis", {"kind": "connect", "elements": ["left", "right"]})
        assert report["pieces"] == 2
        assert report["gap_to_close_mm"] == pytest.approx(0.05, abs=1e-9)

        healed = runner(
            "catia_healing",
            {
                "elements": ["left", "right"],
                "merging_distance_mm": report["gap_to_close_mm"] * 2,
                "name": "skin",
            },
        )
        assert healed["pieces"] == 1

    def test_healing_without_a_stated_gap_is_refused(self) -> None:
        """A heal that fell back to join's tight tolerance would report success and close
        nothing, which is the one outcome worse than refusing."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "NoGap"})
        self._patch(runner, "left", [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)])
        self._patch(runner, "right", [(10.05, 0.0), (20.05, 0.0), (20.05, 10.0), (10.05, 10.0)])

        with pytest.raises(GeometryError, match="merging_distance_mm"):
            runner("catia_healing", {"elements": ["left", "right"], "name": "skin"})

    def test_trimming_two_crossing_panels_keeps_a_side_of_each(self) -> None:
        """The difference from split, stated as a number: split discards one side of one
        element, trim keeps a chosen part of both and sews them into one."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Corner"})
        # Flat on XY, 40 × 40 about the origin; upright on ZX, 40 wide × 20 tall.
        self._patch(runner, "flat", [(-20.0, -20.0), (20.0, -20.0), (20.0, 20.0), (-20.0, 20.0)])
        self._patch(
            runner,
            "upright",
            [(-10.0, -20.0), (10.0, -20.0), (10.0, 20.0), (-10.0, 20.0)],
            plane="ZX",
        )

        runner("catia_trim", {"elements": ["flat", "upright"], "name": "corner"})

        # Half of each: 40 × 20 of the flat panel, 40 × 10 of the upright one.
        measured = runner("catia_measure_item", {"element": "corner"})
        assert measured["area_mm2"] == pytest.approx(40.0 * 20.0 + 40.0 * 10.0, abs=TOL)

    def test_a_cut_by_a_non_planar_element_refuses_to_name_a_side(self) -> None:
        """`first` and `second` are defined against a plane. A cutter that has none has no
        sides, and answering anyway would be right about half the time."""
        runner = self._split_cone()
        runner(  # type: ignore[operator]
            "catia_plane_offset", {"reference": "XY", "distance_mm": 5.0, "name": "low"}
        )

        with pytest.raises(GeometryError, match="no single plane"):
            runner(  # type: ignore[operator]
                "catia_split",
                {"element": "knife", "cutting": "cone", "keep": "first", "name": "bit"},
            )

    def test_a_surface_analysis_runs_on_the_surface_not_the_part(self) -> None:
        """The scans already exist and carry their own provenance — this points them at a
        named surface rather than growing a second copy that would drift."""
        runner = self._split_cone()

        report = runner("catia_surface_analysis", {"kind": "curvature", "elements": ["cone"]})  # type: ignore[operator]

        assert report["analysis_kind"] == "curvature"
        assert report["elements"] == ["cone"]
        assert "minimum_convex_radius_mm" in report

    def test_a_rendered_analysis_says_it_belongs_to_the_viewer(self) -> None:
        runner = self._split_cone()

        with pytest.raises(OperationNotSupported, match="picture rather than a number"):
            runner(  # type: ignore[operator]
                "catia_surface_analysis", {"kind": "isophote", "elements": ["cone"]}
            )


class TestWireframeCurvesLiveInSpace:
    """The curves a sketch cannot hold: a helix, a section, an intersection.

    Until these existed every curve in a design came from a planar sketch or a surface
    boundary, so a genuinely 3D path was not expressible at all. Each is checked against
    the closed form for the curve it claims to be, because a helix of the wrong pitch and
    a helix of the right one are the same picture.
    """

    RADIUS_MM, PITCH_MM, HEIGHT_MM = 10.0, 4.0, 20.0

    @classmethod
    def _helix_length(cls, radius: float, pitch: float, height: float) -> float:
        """n·√(pitch² + (2πr)²) — a helix unrolled is the hypotenuse, n times."""
        return (height / pitch) * math.sqrt(pitch**2 + (2 * math.pi * radius) ** 2)

    @staticmethod
    def _spring(**extra: object) -> object:
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Spring"})
        runner("catia_point_at", {"at": [0.0, 10.0, 0.0], "name": "start"})
        runner(
            "catia_curve_helix",
            {
                "axis": "Z",
                "start_point": "start",
                "pitch_mm": 4.0,
                "height_mm": 20.0,
                "name": "coil",
                **extra,
            },
        )
        return runner

    def test_a_helix_has_the_length_a_helix_has(self) -> None:
        """The parameterisation trap this operation is built around. `Geom2d_Line`
        normalises the direction it is given, so sweeping the pcurve from 0 to 2πn builds
        a helix of the right shape and the wrong length — 265.5 mm measured against a
        closed form of 314.8, a 16% error that looks entirely plausible in a screenshot.

        The tolerance is relative because OCCT integrates the length over a B-spline
        approximation of the pcurve; the residual is about 2 parts in 10⁸, which is the
        quadrature and not the construction.
        """
        runner = self._spring()

        measured = runner("catia_measure_item", {"element": "coil"})  # type: ignore[operator]

        expected = self._helix_length(self.RADIUS_MM, self.PITCH_MM, self.HEIGHT_MM)
        assert measured["length_mm"] == pytest.approx(expected, rel=1e-6)

    def test_a_helix_begins_at_the_point_that_named_its_start(self) -> None:
        """The radius comes from the start point, and so does the phase. Letting OCCT
        choose the cylinder's own X gives a helix that is correct in shape and wrong in
        phase — a thread that does not line up with its own runout."""
        runner = self._spring()

        runner(  # type: ignore[operator]
            "catia_curve_extremum",
            {"element": "coil", "direction": [0, 0, 1], "maximum": False, "name": "foot"},
        )

        foot = runner("catia_measure_item", {"element": "foot"})["position_mm"]  # type: ignore[operator]
        assert foot == pytest.approx([0.0, 10.0, 0.0], abs=1e-9)

    def test_a_tapered_helix_opens_by_the_tangent_of_its_angle(self) -> None:
        """r + h·tan(taper) at the top, exactly. A cone's v runs along the slant rather
        than up the axis, so climbing `pitch` per turn in *height* means climbing
        pitch/cos(taper) in v — leave the cosine out and the coil rises too slowly."""
        runner = self._spring(taper_deg=10.0)

        runner(  # type: ignore[operator]
            "catia_curve_extremum",
            {"element": "coil", "direction": [0, 0, 1], "name": "crown"},
        )

        crown = runner("catia_measure_item", {"element": "crown"})["position_mm"]  # type: ignore[operator]
        radius = math.hypot(crown[0], crown[1])
        assert radius == pytest.approx(
            self.RADIUS_MM + self.HEIGHT_MM * math.tan(math.radians(10.0)), abs=1e-6
        )
        assert crown[2] == pytest.approx(self.HEIGHT_MM, abs=1e-6)

    def test_a_helix_carries_a_real_3d_curve_not_only_a_pcurve(self) -> None:
        """An edge built on a surface holds a *parameter* curve, and the 3D curve is a
        separate thing that has to be asked for. Skip `BuildCurves3d` and the edge still
        measures the right length — but it has no 3D curve at all, its bounding box comes
        out 1.7 mm too big in every direction on a 20 mm helix, and anything that sweeps
        along it raises `Standard_NullObject` from somewhere else entirely.

        So the check is the box, which is the cheapest question that can tell the two
        apart. The helix winds on a cylinder of radius 10 from z = 0 to z = 20; the box
        exceeds that only by OCCT's own spline margin.
        """
        runner = self._spring()

        box = runner("catia_measure_item", {"element": "coil"})["bounding_box_mm"]  # type: ignore[operator]

        # OCCT pads a bounding box by its own gap tolerance, so the margins here are
        # 1e-4 and 0.7 mm rather than zero. Without the 3D curve the same box reads
        # -1.667 to 21.667 and 11.667 across, which neither margin comes close to.
        assert box["min"][2] == pytest.approx(0.0, abs=1e-4)
        assert box["max"][2] == pytest.approx(self.HEIGHT_MM, abs=1e-4)
        assert box["max"][0] == pytest.approx(self.RADIUS_MM, abs=0.7)

    def test_a_3d_circle_needs_the_plane_it_lies_in(self) -> None:
        """Through one centre at one radius there is a circle in every plane, so a
        support is not this backend being strict — it is the missing half of the question."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Ring"})

        with pytest.raises(GeometryError, match="needs a support"):
            runner(
                "catia_curve_circle",
                {"kind": "centre_radius", "centre": [0, 0, 0], "radius_mm": 5.0},
            )

    def test_a_circle_and_an_arc_measure_what_they_should(self) -> None:
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Ring"})
        common = {"kind": "centre_radius", "centre": [0, 0, 0], "radius_mm": 12.0, "support": "XY"}

        runner("catia_curve_circle", {**common, "name": "whole"})
        runner(
            "catia_curve_circle",
            {**common, "start_angle_deg": 0.0, "end_angle_deg": 90.0, "name": "quarter"},
        )

        circumference = 2 * math.pi * 12.0
        assert runner("catia_measure_item", {"element": "whole"})["length_mm"] == pytest.approx(
            circumference, abs=1e-9
        )
        assert runner("catia_measure_item", {"element": "quarter"})["length_mm"] == pytest.approx(
            circumference / 4.0, abs=1e-9
        )

    def test_three_points_give_the_whole_circle_not_the_arc_between_them(self) -> None:
        """`GC_MakeArcOfCircle` answers a different question — the arc from the first
        point through the second to the third — so building the circle from it returns
        half of what this operation promises."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Three"})

        runner(
            "catia_curve_circle",
            {"kind": "three_points", "points": [[10, 0, 0], [0, 10, 0], [-10, 0, 0]], "name": "hoop"},
        )

        measured = runner("catia_measure_item", {"element": "hoop"})
        assert measured["length_mm"] == pytest.approx(2 * math.pi * 10.0, abs=1e-9)

    def test_three_points_in_a_line_lie_on_no_circle(self) -> None:
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Flat"})

        with pytest.raises(GeometryError, match="straight line"):
            runner(
                "catia_curve_circle",
                {"kind": "three_points", "points": [[0, 0, 0], [10, 0, 0], [20, 0, 0]], "name": "no"},
            )

    def test_a_polyline_is_the_sum_of_its_segments(self) -> None:
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Path"})
        points = [[0, 0, 0], [10, 0, 0], [10, 10, 0], [10, 10, 10]]

        runner("catia_curve_polyline", {"points": points, "name": "open"})
        runner("catia_curve_polyline", {"points": points, "closed": True, "name": "loop"})

        assert runner("catia_measure_item", {"element": "open"})["length_mm"] == pytest.approx(
            30.0, abs=TOL
        )
        assert runner("catia_measure_item", {"element": "loop"})["length_mm"] == pytest.approx(
            30.0 + math.sqrt(300.0), abs=TOL
        )

    def test_a_spline_passes_through_its_points_rather_than_near_them(self) -> None:
        """Interpolation, not approximation: three collinear points give a straight line
        of exactly their span. A fitted curve would come back a little longer, and nothing
        would say which it had done."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Curve"})

        runner("catia_curve_spline", {"points": [[0, 0, 0], [10, 0, 0], [20, 0, 0]], "name": "line"})

        measured = runner("catia_measure_item", {"element": "line"})
        assert measured["length_mm"] == pytest.approx(20.0, abs=1e-9)

    def test_a_section_takes_the_real_profile_off_the_geometry(self) -> None:
        """A plane through a Ø20 post cuts a circle of exactly 2πr — not a profile
        someone drew to look like one."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Post"})
        runner(
            "catia_surface_primitive",
            {"kind": "cylinder", "radius_mm": 10.0, "length_mm": 30.0, "name": "post"},
        )
        runner("catia_plane_offset", {"reference": "XY", "distance_mm": 15.0, "name": "mid"})

        runner("catia_curve_section", {"element": "post", "plane": "mid", "name": "ring"})

        measured = runner("catia_measure_item", {"element": "ring"})
        assert measured["length_mm"] == pytest.approx(2 * math.pi * 10.0, abs=1e-9)

    def test_a_section_that_misses_is_refused_rather_than_returned_empty(self) -> None:
        """"They do not meet" and "the section is empty" are the same sentence, and only
        one of them is what the caller expected to hear."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Post"})
        runner(
            "catia_surface_primitive",
            {"kind": "cylinder", "radius_mm": 10.0, "length_mm": 30.0, "name": "post"},
        )
        runner("catia_plane_offset", {"reference": "XY", "distance_mm": 90.0, "name": "away"})

        with pytest.raises(GeometryError, match="misses it entirely"):
            runner("catia_curve_section", {"element": "post", "plane": "away", "name": "nothing"})

    def test_two_crossing_surfaces_give_the_seam_between_them(self) -> None:
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Cross"})
        corners = [(-20.0, -20.0), (20.0, -20.0), (20.0, 20.0), (-20.0, 20.0)]
        for name, plane in (("flat", "XY"), ("upright", "ZX")):
            outline = f"{name}_outline"
            runner("catia_sketch_create", {"support": plane, "name": outline})
            for start, end in zip(corners, corners[1:] + corners[:1], strict=True):
                runner("catia_sketch_line", {"sketch": outline, "start": start, "end": end})
            runner("catia_surface_fill", {"boundary": [outline], "name": name})

        runner("catia_curve_intersect", {"elements": ["flat", "upright"], "name": "seam"})

        measured = runner("catia_measure_item", {"element": "seam"})
        assert measured["length_mm"] == pytest.approx(40.0, abs=TOL)

    def test_an_extremum_is_a_point_the_rest_of_the_vocabulary_can_use(self) -> None:
        """It goes into the document's point store, not a second place for points, so
        every operation that takes a point can already find it."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Block"})
        runner("catia_sketch_create", {"support": "XY", "name": "base"})
        runner("catia_sketch_rectangle", {"sketch": "base", "width_mm": 40.0, "height_mm": 20.0})
        runner("catia_pad", {"sketch": "base", "length_mm": 12.0, "name": "block"})

        top = runner("catia_curve_extremum", {"element": "block", "direction": [0, 0, 1], "name": "crown"})
        bottom = runner(
            "catia_curve_extremum",
            {"element": "block", "direction": [0, 0, 1], "maximum": False, "name": "foot"},
        )

        assert top["position_mm"][2] == pytest.approx(12.0, abs=TOL)
        assert bottom["position_mm"][2] == pytest.approx(0.0, abs=TOL)
        # In the point store, so `catia_measure_item` resolves it like any other point.
        assert runner("catia_measure_item", {"element": "crown"})["measured_kind"] == "point"

    def test_what_a_curve_cannot_do_yet_is_named_rather_than_ignored(self) -> None:
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Refuse"})

        for arguments, expected in (
            ({"kind": "bitangent"}, "constraint problem"),
            ({"kind": "tritangent"}, "constraint problem"),
        ):
            with pytest.raises(OperationNotSupported, match=expected):
                runner("catia_curve_circle", arguments)

        with pytest.raises(OperationNotSupported, match="fillet between successive"):
            runner(
                "catia_curve_polyline",
                {"points": [[0, 0, 0], [1, 0, 0]], "radius_mm": 1.0},
            )


class TestAnchorsPointsAndLines:
    """Points and lines derived from geometry rather than typed in.

    The whole value of these is that they follow what they were derived from. A point
    measured once and typed as a coordinate is right until the part changes and wrong
    silently afterwards; `catia_point_on_curve` is right afterwards too. So every test
    here checks the *place*, against the closed form for where that place is.
    """

    @staticmethod
    def _ell() -> object:
        """An L-shaped polyline: 30 mm along +X, then 40 mm along +Y. Total 70."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Anchor"})
        runner(
            "catia_curve_polyline",
            {"points": [[0, 0, 0], [30, 0, 0], [30, 40, 0]], "name": "path"},
        )
        return runner

    @staticmethod
    def _tube() -> object:
        """A cylinder of radius 10 standing 30 mm tall, as a surface."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Tube"})
        runner("catia_sketch_create", {"support": "ZX", "name": "gen"})
        runner("catia_sketch_line", {"sketch": "gen", "start": (0.0, 10.0), "end": (30.0, 10.0)})
        runner("catia_surface_revolve", {"profile": "gen", "axis": "Z", "name": "tube"})
        return runner

    def test_a_point_walks_the_whole_chain_not_just_its_first_edge(self) -> None:
        """The L is 30 mm then 40 mm. Halfway is 35 mm along, which is 5 mm up the second
        leg — and taking the first edge alone would put "halfway" at 15 mm along the
        first, a number nobody would question."""
        runner = self._ell()

        halfway = runner("catia_point_on_curve", {"curve": "path", "ratio": 0.5, "name": "mid"})  # type: ignore[operator]

        assert halfway["position_mm"] == pytest.approx([30.0, 5.0, 0.0], abs=TOL)

    def test_a_point_can_be_placed_by_length_from_either_end(self) -> None:
        runner = self._ell()

        corner = runner("catia_point_on_curve", {"curve": "path", "distance_mm": 30.0, "name": "corner"})  # type: ignore[operator]
        near_top = runner(  # type: ignore[operator]
            "catia_point_on_curve",
            {"curve": "path", "distance_mm": 10.0, "from_end": True, "name": "near_top"},
        )

        assert corner["position_mm"] == pytest.approx([30.0, 0.0, 0.0], abs=TOL)
        assert near_top["position_mm"] == pytest.approx([30.0, 30.0, 0.0], abs=TOL)

    def test_a_ratio_is_arc_length_and_not_a_curve_parameter(self) -> None:
        """A B-spline's parameter is not proportional to its length, so `ratio: 0.5` read
        as a parameter lands at the midpoint of nothing. Four evenly spaced points make
        the two answers coincide only if the walk is by length."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Spline"})
        runner(
            "catia_curve_spline",
            {"points": [[0, 0, 0], [10, 0, 0], [20, 0, 0], [30, 0, 0]], "name": "line"},
        )

        middle = runner("catia_point_on_curve", {"curve": "line", "ratio": 0.5, "name": "mid"})

        assert middle["position_mm"] == pytest.approx([15.0, 0.0, 0.0], abs=1e-6)

    def test_ratio_and_distance_together_are_refused(self) -> None:
        """Two ways of saying the same thing, and which one wins would be left to chance."""
        runner = self._ell()

        with pytest.raises(GeometryError, match="not both"):
            runner(  # type: ignore[operator]
                "catia_point_on_curve",
                {"curve": "path", "ratio": 0.5, "distance_mm": 3.0, "name": "no"},
            )

    def test_a_centre_is_taken_from_the_geometry_that_has_one(self) -> None:
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Centres"})
        runner(
            "catia_curve_circle",
            {"kind": "centre_radius", "centre": [5, 7, 0], "radius_mm": 12.0, "support": "XY", "name": "ring"},
        )
        runner(
            "catia_surface_primitive",
            {"kind": "sphere", "centre": [1, 2, 3], "radius_mm": 4.0, "name": "ball"},
        )

        hub = runner("catia_point_centre", {"element": "ring", "name": "hub"})
        core = runner("catia_point_centre", {"element": "ball", "name": "core"})

        assert hub["position_mm"] == pytest.approx([5.0, 7.0, 0.0], abs=TOL)
        assert core["position_mm"] == pytest.approx([1.0, 2.0, 3.0], abs=TOL)

    def test_a_curve_with_no_centre_says_so(self) -> None:
        """A straight line has no centre, and returning its midpoint would be answering a
        different question with the same confidence."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Straight"})
        runner("catia_point_at", {"at": [0, 0, 0], "name": "a"})
        runner("catia_point_at", {"at": [10, 0, 0], "name": "b"})
        runner("catia_line_between", {"points": ["a", "b"], "name": "ab"})

        with pytest.raises(GeometryError, match="rather than a circle"):
            runner("catia_point_centre", {"element": "ab", "name": "no"})

    def test_lines_are_the_length_they_were_asked_for(self) -> None:
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Lines"})
        runner("catia_point_at", {"at": [0, 0, 0], "name": "a"})
        runner("catia_point_at", {"at": [3, 4, 0], "name": "b"})

        runner("catia_line_between", {"points": ["a", "b"], "name": "plain"})
        runner(
            "catia_line_between",
            {"points": ["a", "b"], "extend_start_mm": 5.0, "extend_end_mm": 10.0, "name": "long"},
        )
        runner("catia_line_direction", {"point": "a", "direction": [0, 0, 1], "length_mm": 12.0, "name": "up"})
        runner(
            "catia_line_direction",
            {"point": "a", "direction": [0, 0, 1], "length_mm": 12.0, "both_sides": True, "name": "both"},
        )

        for name, expected in (("plain", 5.0), ("long", 20.0), ("up", 12.0), ("both", 24.0)):
            measured = runner("catia_measure_item", {"element": name})
            assert measured["length_mm"] == pytest.approx(expected, abs=TOL), name

    def test_a_normal_is_read_at_the_point_not_at_the_face_centre(self) -> None:
        """On a flat wall the two are the same and the difference never shows. On a
        cylinder, the face's centre normal points somewhere else entirely, and the stud
        would come out of the tube at an angle nobody asked for while still looking like
        a normal."""
        runner = self._tube()
        runner("catia_point_at", {"at": [0, 10, 15], "name": "side"})  # type: ignore[operator]

        runner(  # type: ignore[operator]
            "catia_line_normal",
            {"surface": "tube", "point": "side", "length_mm": 5.0, "name": "stud"},
        )

        box = runner("catia_measure_item", {"element": "stud"})["bounding_box_mm"]  # type: ignore[operator]
        # Radially outward from (0, 10, 15) to (0, 15, 15): no run in x, none in z.
        assert box["size"] == pytest.approx([0.0, 5.0, 0.0], abs=1e-6)
        assert box["min"][1] == pytest.approx(10.0, abs=1e-6)

    def test_a_tangent_runs_along_the_curve_where_it_was_asked(self) -> None:
        """At the east point of a circle in XY the tangent is +Y — and nothing about the
        circle's own construction says so, which is why it is measured."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Tangent"})
        runner(
            "catia_curve_circle",
            {"kind": "centre_radius", "centre": [0, 0, 0], "radius_mm": 10.0, "support": "XY", "name": "ring"},
        )
        runner("catia_point_at", {"at": [10, 0, 0], "name": "east"})

        runner("catia_line_tangent", {"curve": "ring", "point": "east", "length_mm": 6.0, "name": "tang"})

        box = runner("catia_measure_item", {"element": "tang"})["bounding_box_mm"]
        assert box["size"] == pytest.approx([0.0, 6.0, 0.0], abs=1e-6)
        assert box["min"][0] == pytest.approx(10.0, abs=1e-6)

    def test_a_point_offset_along_a_curved_surface_stays_on_it(self) -> None:
        """The whole operation. Moving 3 mm along +Y from a point on the axis leaves the
        cylinder; projecting back puts it on the skin, at radius 10 exactly. Without the
        projection it is a point in the air that still reads as being on the surface."""
        runner = self._tube()
        runner("catia_point_at", {"at": [0, 0, 15], "name": "middle"})  # type: ignore[operator]

        placed = runner(  # type: ignore[operator]
            "catia_point_on_surface",
            {
                "surface": "tube",
                "reference": "middle",
                "direction": [0, 1, 0],
                "distance_mm": 3.0,
                "name": "on_skin",
            },
        )

        x, y, _ = placed["position_mm"]
        assert math.hypot(x, y) == pytest.approx(10.0, abs=1e-9)
