"""Is this shape actually well-formed? — the `validity` arm of `catia_analysis_part`.

The registry's own summary says what this is for: *"finds the small self-intersections
that make an export fail with an unhelpful message"*. A B-rep can be topologically
complete and geometrically broken — a face whose surface does not match its edges, a wire
that does not close, a solid whose shell is inside out. Nothing downstream reports these
usefully. A mesher fails with a message about an element, a STEP export fails with a
message about a translator, and the actual cause is a face nobody looked at.

**This runs before anything expensive, not after it fails.** A validity check on a part is
cheap next to meshing it, and the alternative is diagnosing a solver error backwards.

**Invalid sub-shapes are counted by kind, not just totalled.** "Three invalid faces" and
"three invalid edges" want different responses — the first usually means a surface came
out wrong, the second usually means a boolean left a seam — and a single number cannot
distinguish them.
"""

from __future__ import annotations

from typing import Any

from app.kernel.interrogation import ValidityReport
from app.kernel.occt.binding import require, symbol
from app.kernel.occt.topology import EDGE, FACE, SHELL, SOLID, WIRE, explore


def check_validity(shape: Any) -> ValidityReport:
    """Run OCCT's own consistency checker over the shape and every sub-shape.

    The top-level `IsValid()` is the answer to "can I trust this"; the per-kind counts
    are the answer to "what do I look at". Both come from one analyser, because
    constructing it is the expensive part and it caches its results.
    """
    require()

    analyser = symbol("BRepCheck_Analyzer")(shape)
    valid = bool(analyser.IsValid())

    invalid: dict[str, int] = {}
    if not valid:
        # Only walked when something is wrong. On a valid part this loop would visit
        # every sub-shape to confirm what the top-level call already said.
        for kind in (FACE, EDGE, WIRE, SHELL, SOLID):
            count = sum(
                1 for sub in explore(shape, kind) if not analyser.IsValid(sub)
            )
            if count:
                invalid[kind.lower()] = count

    return ValidityReport(valid=valid, invalid_by_kind=invalid)


__all__ = ["check_validity"]
