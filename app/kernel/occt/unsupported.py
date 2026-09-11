"""Arguments the open kernel is handed, declares, and cannot honour.

**An argument the schema advertises is a promise the agent reads and acts on.**
The registry in `app/catia/ops/` is the single declaration every surface is built
from — the model's tool schema, the validator, the daemon and the documentation —
so a parameter that appears there has been described to the model in words it
will believe. Honouring it on one backend and ignoring it on the other is a
product that behaves differently depending on a setting nobody in the
conversation can see.

**Measured on 2026-09-11.** A differential sweep — build the part without the
argument, build it again with a meaningfully different value, compare the
geometry — found **eighteen** advertised arguments the OCCT backend read on
nobody's behalf. (A differential is only as good as the difference: `target_body`
was first recorded here on a comparison against a value that was already the
default, which measured nothing. The full suite caught it.) Each returned `ok`,
with a feature name and a full set of `measured` provenance describing geometry
that was not what was asked for:

* `catia_translate(direction=[1,0,0], distance_mm=50)` moved the part **1 mm**.
  `distance_mm` is *required*, and the raw direction vector was being used as the
  whole displacement. Fixed rather than refused — see `transforms.translate`.
* `catia_pad(thin=True, thickness_mm=3)` on a 100x60 profile returned a **solid**
  pad of 120,000 mm3 where a 3 mm wall is about 18,480 — six and a half times
  the material, silently.
* `catia_hole_at(thread='M6x1')` produced a plain clearance hole. A tapped hole
  and a clearance hole are different parts to make.
* `catia_pad(second_length_mm=30)` extruded one side only: 120,000 mm3 where a
  two-sided pad is 300,000.

`plane` on the sketch primitives and `distance_mm` on `catia_translate` were
**implemented** instead, because their contracts were small and their absence
produced wrong geometry rather than a missing capability. `target_body` on
`catia_boolean` is checked in its own handler, not here, because the value that
is harmless for it is not a constant — it is whatever body happens to be active,
and only the document knows that. **That is the boundary of what this table can
express**, and it is worth knowing where it is. What is left here is capability
the kernel genuinely does not have (master plan E1 task 3's long tail), and the
honest response to being asked for it is to say so.

**Why refuse rather than warn.** `app/catia/` warns at length that over-refusal is
its own failure mode — the agent's recovery from a refusal is to try something
else, and a wrongly refused call becomes a wrongly built part. That argument does
not apply to an argument that is *already* being ignored: the part is wrong
either way, and the only question is whether anybody is told. A refusal names the
gap and the agent can route around it; silence produces a confident wrong number,
which Decision 3 exists to prevent.

**This table may only shrink.** Every entry is a capability somebody could
implement, and `tests/test_kernel_unsupported_arguments.py` pins the count so an
entry cannot be added quietly to make a new gap legal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Mapping

from app.kernel.errors import OperationNotSupported


@dataclass(frozen=True, slots=True)
class Unhonoured:
    """One advertised argument this backend cannot act on.

    `harmless` is the value for which ignoring the argument changes nothing —
    almost always the documented default. Supplying it is not an error, because
    a model that spells out a default has asked for nothing it will not get;
    supplying anything else is a request this backend cannot meet.

    `None` means there is no such value and any supplied value is a real request.
    """

    reason: str
    harmless: Any = None

    def is_meaningful(self, value: Any) -> bool:
        return value is not None and value != self.harmless


#: What OCCT does not do with an argument it is handed. Tool -> argument -> why.
#:
#: Each reason says what the argument would have done and what to do instead,
#: because a refusal the agent cannot act on costs a turn and teaches it nothing.
IGNORED: Final[dict[str, dict[str, Unhonoured]]] = {
    "catia_pad": {
        "thin": Unhonoured(
            "a thin-walled pad needs the profile offset inwards and the core removed, "
            "which this kernel does not build yet. Pad it solid and hollow it with "
            "catia_shell",
            harmless=False,
        ),
        "second_length_mm": Unhonoured(
            "a two-sided pad is not built here — only the first extent is used. Pad "
            "from a plane placed at the far end instead"
        ),
    },
    "catia_pocket": {
        "thin": Unhonoured(
            "a thin-walled cut needs the profile offset inwards, which this kernel "
            "does not build yet. Cut the full profile",
            harmless=False,
        ),
        "second_length_mm": Unhonoured(
            "a two-sided pocket is not cut here — only the first depth is used. Cut "
            "twice from the two faces"
        ),
    },
    "catia_shaft": {
        "thin": Unhonoured(
            "a thin-walled revolve is not built here. Revolve it solid and hollow it "
            "with catia_shell",
            harmless=False,
        ),
        "second_angle_deg": Unhonoured(
            "revolving in the second direction as well is not built here — only "
            "angle_deg is swept. Give the whole sweep as angle_deg"
        ),
    },
    "catia_groove": {
        "second_angle_deg": Unhonoured(
            "revolving the cut in the second direction as well is not built here — "
            "only angle_deg is swept. Give the whole sweep as angle_deg"
        ),
    },
    "catia_chamfer": {
        "second_length_mm": Unhonoured(
            "an asymmetric chamfer needs a second setback this kernel does not apply "
            "— it would be cut equal-sided at length_mm. Give length_mm with "
            "angle_deg for an asymmetric chamfer"
        ),
    },
    "catia_fillet_edges": {
        "edge_relimitation": Unhonoured(
            "trimming the fillet back to the edge ends is not controlled here; OCCT "
            "relimits by its own rule. Drop the argument to accept that",
            harmless=False,
        ),
    },
    "catia_hole_at": {
        "thread": Unhonoured(
            "a thread is not cut or annotated here, so the hole would come out as a "
            "plain clearance hole of diameter_mm with nothing recording that it "
            "should be tapped. Drill the tapping diameter and carry the thread on "
            "the drawing"
        ),
        "thread_depth_mm": Unhonoured(
            "there is no thread on this backend for a depth to apply to — see "
            "`thread`"
        ),
    },
    "catia_pattern_circular": {
        "radius_mm": Unhonoured(
            "the pattern radius is taken from the seed feature's own position and "
            "cannot be overridden here. Place the seed feature at the radius you "
            "want"
        ),
    },
    "catia_list_parameters": {
        "include_dimensions": Unhonoured(
            "this backend has no CATIA feature dimensions to include — it reports the "
            "design's own parameters only. Drop the argument to get those",
            harmless=False,
        ),
    },
    "catia_bill_of_materials": {
        "recursive": Unhonoured(
            "the bill of materials is always fully expanded here; a single-level "
            "listing is not built. Drop the argument to get the full expansion",
            harmless=True,
        ),
    },
}


def refuse_unhonoured(tool: str, arguments: Mapping[str, Any]) -> None:
    """Refuse a call that asks for something this backend will not do.

    Raised *before* the handler, so nothing is built and no feature is named —
    a half-applied operation would be worse than the silence this replaces.

    Only one argument is reported even when several are unhonoured, because the
    fix for the first is usually the fix for the rest and a wall of refusals
    reads as "this tool does not work" rather than "this argument does not".
    """
    unhonoured = IGNORED.get(tool)
    if not unhonoured:
        return
    for name, entry in unhonoured.items():
        if name in arguments and entry.is_meaningful(arguments[name]):
            # The *argument* is the subject, not the operation: `catia_pad` works
            # perfectly well and saying it "is not supported yet" would send the
            # agent looking for another way to extrude. `OperationNotSupported`
            # documents `subject` as naming a capability within an operation for
            # exactly this.
            raise OperationNotSupported(f"`{name}` on {tool}", entry.reason)


def ignored_argument_count() -> int:
    """How many advertised arguments this backend still cannot honour.

    Pinned by a test that may only be lowered — see this module's docstring.
    """
    return sum(len(one) for one in IGNORED.values())


__all__ = ["IGNORED", "Unhonoured", "ignored_argument_count", "refuse_unhonoured"]
