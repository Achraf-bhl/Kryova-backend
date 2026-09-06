"""How the daemon spells an assembly reference -- pinned where Linux can pin it.

Measured on ladder prompt S2, 2026-09-06, the first assembly built on the seat.
Two coincidence constraints between the shaft's and the bushing's origin planes
-- exactly the geometry a coincidence takes -- were refused by `AddBiEltCst`,
and the refusal blamed the geometry. The references were the problem: built
from the component's own CATPart, they carried no instance path. What resolves
is a name on the product, `{root}/{instance}/!{localised element}`, and only
for the three origin planes -- measured 2026-09-02 and written down in the
memory, and not read.

The same run showed the constraint-type table was guessed on every row
(coincidence sent 1, which is offset). The COM calls themselves are Windows-only;
what is pinned here is everything decided before one is made.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from catia_bridge.backend import CatiaOperationError  # noqa: E402
from catia_bridge.com import assembly  # noqa: E402


class TestTheConstraintEnumIsTheMeasuredOne:
    """Read back from the names CATIA assigned, V5-R33 FR, 2026-09-02."""

    def test_coincidence_is_two_and_offset_is_one(self) -> None:
        assert assembly._CONSTRAINT_TYPES["coincidence"] == 2
        assert assembly._CONSTRAINT_TYPES["offset"] == 1

    def test_the_rest(self) -> None:
        assert assembly._CONSTRAINT_TYPES["angle"] == 6
        assert assembly._CONSTRAINT_TYPES["parallel"] == 8
        assert assembly._CONSTRAINT_TYPES["perpendicular"] == 11
        assert assembly._CONSTRAINT_TYPES["fix"] == 0


class TestTheReferencePath:
    def test_root_slash_instance_bang_element(self) -> None:
        assert (
            assembly._reference_path("PGS-ASSEMBLY", "Shaft", "Plan yz")
            == "PGS-ASSEMBLY/Shaft/!Plan yz"
        )


class TestOriginPlanesAreRecognisedInAnySpelling:
    @pytest.mark.parametrize(
        "spelling", ["YZ", "yz", "Plan yz", "yz plane", "Plane.YZ", "ZY", "PLAN YZ"]
    )
    def test_every_spelling_of_yz(self, spelling: str) -> None:
        assert assembly._origin_plane_attribute(spelling) == "PlaneYZ"

    def test_the_other_two(self) -> None:
        assert assembly._origin_plane_attribute("Plan xy") == "PlaneXY"
        assert assembly._origin_plane_attribute("XZ") == "PlaneZX"

    def test_other_geometry_is_not_a_plane(self) -> None:
        assert assembly._origin_plane_attribute("Pad.1") is None
        assert assembly._origin_plane_attribute("Révolution.1") is None


class _Stub:
    """Enough of a context for the refusals decided before any COM call."""

    def _product(self) -> Any:
        return object()


class TestWhatIsRefusedBeforeCom:
    def test_a_bare_component_reference(self) -> None:
        with pytest.raises(CatiaOperationError, match="cannot reference a component itself"):
            assembly.AssemblyMixin._assembly_reference(_Stub(), "Shaft")

    def test_the_refusal_says_how_to_spell_it(self) -> None:
        with pytest.raises(CatiaOperationError, match="'Shaft/YZ'"):
            assembly.AssemblyMixin._assembly_reference(_Stub(), "Shaft")

    def test_fix_together(self) -> None:
        with pytest.raises(CatiaOperationError, match="fix_together is not available"):
            assembly.AssemblyMixin.constrain(
                _Stub(), kind="fix_together", elements=["Shaft", "Bushing"]
            )

    def test_component_fix_together(self) -> None:
        with pytest.raises(CatiaOperationError, match="not available on a CATIA seat"):
            assembly.AssemblyMixin.component_fix(
                _Stub(), components=["Shaft", "Bushing"], together=True
            )


class TestOnlyDimensionedKindsCarryAValue:
    def test_a_coincidence_has_no_dimension(self) -> None:
        assert "coincidence" not in assembly._DIMENSIONED
        assert {"offset", "angle"} <= assembly._DIMENSIONED
