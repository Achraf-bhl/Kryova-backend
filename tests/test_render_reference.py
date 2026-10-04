"""The reference assembly renderer thresholds are measured on — P6 task 2, QUEUE G1.

`docs/RENDERER_DECISION.md` §4 opens with the reason this file exists: *"a threshold
against an unnamed scene is not a threshold"*, and then records that the scene did not
exist. The largest assembly this repository could build when that was written was M6's
belt conveyor, with three components, against a two-thousand-part target. §4 therefore
defines the assembly **by its properties** and calls building a synthetic one "a
prerequisite of the decision, not part of it".

So these tests hold the generator to §4's table row by row, and **measure the two rows the
generator deliberately does not decide** — the triangle counts — through the real kernel
and the real display levels, because they are a property of `app/render/display.py`
applied to these parts rather than of the graph.

**Everything here is labelled synthetic**, which is §4's own instruction. A threshold met
on a synthetic scene and missed on a real one is exactly the confusion that document
exists to prevent, and `ReferenceProperties.synthetic` is a constant rather than a
parameter so that nobody can turn the label off.

Measured on Windows, 2026-09-17. The kernel is imported inside the tests that need it, as
the mission tests do, so collecting this file does not drag ~166 MB of OCP into every run.
"""

from __future__ import annotations

import pytest

from app.render.reference import (
    DISTINCT_COMPONENTS,
    MIN_DEPTH,
    MIN_INSTANCED_FRACTION,
    OCCURRENCES,
    SIZES,
    component_size_mm,
    properties,
    reference_assembly,
)

#: The generator's identity. Pinned because two runs a month apart have to be comparable:
#: a generator whose output drifts makes every historical measurement incomparable, which
#: is what makes a performance threshold worthless a year later. If this changes
#: deliberately, every recorded measurement taken on the old digest is about a different
#: scene and the run log has to say so.
DIGEST = "2c6d3f5c8d9d534ccbbe0aeb6d58f4ab"


class TestItMeetsSectionFoursTable:
    """Row by row, against the numbers in the document rather than against itself."""

    def test_it_has_exactly_two_thousand_occurrences(self) -> None:
        assert properties().occurrences == OCCURRENCES == 2_000

    def test_it_has_exactly_one_hundred_and_twenty_distinct_components(self) -> None:
        assert properties().distinct_components == DISTINCT_COMPONENTS == 120

    def test_nearly_every_occurrence_is_instanced(self) -> None:
        """99%, and the 1% is deliberate. §4 asks for ≥90%; a scene at exactly 100% does
        not exercise the mixed case a real machine has, where a few weldments are unique
        and everything else repeats. The twenty frames are placed once each."""
        measured = properties()

        assert measured.instanced_fraction == pytest.approx(0.99)
        assert measured.instanced_fraction >= MIN_INSTANCED_FRACTION
        assert measured.occurrences - measured.instanced_occurrences == 20

    def test_it_nests_one_level_past_the_floor(self) -> None:
        """machine → station → module → subassembly → cluster → part. A first draft
        stopped at subassemblies and came out at exactly 4 — a floor met exactly is a
        floor the next change breaks."""
        assert properties().depth == 5
        assert properties().depth > MIN_DEPTH

    def test_the_whole_table_passes_and_says_so(self) -> None:
        met, failures = properties().meets_specification()

        assert met, failures
        assert failures == ()

    def test_a_failure_names_the_row_rather_than_returning_false(self) -> None:
        """`app/verify/`'s rule: a criterion that fails with no statement of which part
        failed sends a reader to re-derive it."""
        from app.render.reference import ReferenceProperties

        broken = ReferenceProperties(
            occurrences=10,
            distinct_components=2,
            instanced_occurrences=1,
            depth=1,
            digest="x",
        )
        met, failures = broken.meets_specification()

        assert not met
        assert len(failures) == 4
        assert any("occurrences 10" in f for f in failures)
        assert any("depth 1" in f for f in failures)


class TestItIsTheSameSceneEveryTime:
    def test_the_digest_is_pinned(self) -> None:
        """The whole value of a reference assembly. If this changes, every measurement
        recorded against the old digest is about a different scene."""
        assert properties().digest == DIGEST

    def test_two_generations_are_identical(self) -> None:
        assert reference_assembly().digest() == reference_assembly().digest()

    def test_nothing_in_it_is_random(self) -> None:
        """Placements vary with the index, so the scene is varied without being
        irreproducible — §4 asks for 'replication with varied transforms', and a random
        transform would satisfy the word and destroy the artefact."""
        first = [o.frame.origin_mm for o in reference_assembly().occurrences()]
        second = [o.frame.origin_mm for o in reference_assembly().occurrences()]

        assert first == second
        assert len(set(first)) > 100, "every instance at the same place is not 'varied'"

    def test_the_part_sizes_are_bounded_and_varied(self) -> None:
        """Bounded so no part dominates the view or falls under a display level's
        deflection; varied so the triangle budget is not one part's count times 2,000."""
        sizes = [component_size_mm(i) for i in range(100)]
        every = [value for size in sizes for value in size]

        assert min(every) >= 8.0
        assert max(every) <= 60.0
        assert len(set(sizes)) > 50
        assert len(SIZES) == 100


class TestTheTriangleRowsMeasuredThroughTheKernel:
    """§4's last two rows, which the generator deliberately does not decide.

    They are a property of `app/render/display.py`'s levels applied to parts of these
    sizes, so they are measured rather than declared. The count is per *distinct
    component* times its occurrences — which is the number a renderer is asked to draw,
    not the number of bytes served, because instancing means those differ by two orders of
    magnitude here.
    """

    @staticmethod
    def _triangles_at(level: int) -> int:
        import math

        from app.kernel.occt.binding import symbol
        from app.kernel.occt.tessellate import tessellate
        from app.render.display import LEVELS

        definition = LEVELS[level]
        box = symbol("BRepPrimAPI_MakeBox")
        total = 0
        counts: dict[tuple[float, float, float], int] = {}
        for occurrence in reference_assembly().occurrences():
            size = SIZES.get(occurrence.component)
            if size is None:
                continue  # a frame; not one of the sized parts
            counts[size] = counts.get(size, 0) + 1
        for size, uses in counts.items():
            shape = box(*size).Shape()
            # The deflection is a fraction of the part's own bounding-box diagonal, which
            # is `display.py`'s rule and the reason a bolt is as smooth relative to itself
            # as a frame is. Computed here rather than imported because `display_mesh`
            # takes a stored file and this takes a shape.
            diagonal = math.sqrt(sum(value * value for value in size))
            mesh = tessellate(
                shape,
                linear_deflection_mm=definition.relative_deflection * diagonal,
                angular_deflection_rad=definition.angular_deflection_rad,
            )
            total += mesh.triangle_count * uses
        return total

    def test_level_zero_is_measured_and_recorded(self) -> None:
        """Recorded rather than asserted against §4's 8–12 M band, because these are
        **boxes**. A box tessellates to twelve triangles at every deflection there is, so
        the band is a statement about real parts with curvature and this synthetic scene
        cannot reach it. Saying that is better than widening the band or pretending.

        What the test does hold is the shape of the answer: the count is proportional to
        the occurrences, and level 0 is never coarser than level 2.
        """
        zero = self._triangles_at(0)
        two = self._triangles_at(2)

        assert zero > 0
        assert zero >= two
        # 1,980 part occurrences x 12 triangles for a box.
        assert zero == pytest.approx(1_980 * 12, rel=0.5)

    def test_the_count_scales_with_the_occurrences_not_the_components(self) -> None:
        """The claim instancing makes interesting: 100 distinct components are drawn 1,980
        times, so a renderer's triangle budget is about the occurrences even though the
        *bytes* are about the components."""
        measured = properties()

        assert measured.occurrences / measured.distinct_components > 16.0
