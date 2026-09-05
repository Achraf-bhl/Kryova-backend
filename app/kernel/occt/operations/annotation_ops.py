"""Things declared on the part rather than cut into it — today, `catia_thread`.

**A thread is an annotation and this backend keeps it one.** CATIA models a thread as a
property of a cylindrical face: it drives the drawing callout, the tapping operation and
the fastener that goes in, and it deliberately does not change the mass. Cutting a real
helix would change every measurement the part reports, for a shape nobody inspects and a
regeneration cost paid on every rebuild.

So this operation records, and says plainly that it recorded — the same honesty rule that
makes a mock mass announce it is a mock. What it must never do is report a mass that has
silently lost the material a helix would have removed, or claim geometry it did not build.

**The face is still checked.** A thread on a face that is not cylindrical is meaningless,
and an M10 thread on a Ø6 hole is a mistake worth catching at the moment it is made
rather than at the machine.

Named `annotation_ops` rather than `annotations` for the same class of reason
`document_ops` is not `document`, and one worse: every module here opens with
`from __future__ import annotations`, so importing a sibling called `annotations` binds
the name to the future flag and every attribute lookup on it fails at import.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from app.kernel.errors import GeometryError
from app.kernel.occt.classify import cylinder_diameter_mm
from app.kernel.occt.operations.context import BuildContext
from app.kernel.occt.selectors import select_faces
from app.kernel.threads import ThreadSpec, parse_designation

THREAD = "catia_thread"

#: How far a threaded face's diameter may sit outside the band the designation implies.
#:
#: The band itself runs from the tapping-drill size to the nominal diameter, which is
#: where every real threaded cylinder lands — an internal thread is cut into a hole
#: drilled to the minor diameter, an external one is turned to about the nominal. This
#: margin only absorbs the clearance and tolerance a real drawing carries on top.
DIAMETER_MARGIN_MM: Final = 0.5


def thread(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Declare a thread or tap on an existing cylindrical face."""
    document = context.require_document()
    shape = context.require_shape(THREAD)

    selector = arguments.get("face")
    if not selector:
        raise GeometryError(
            f"{THREAD} needs `face` naming the cylindrical face to thread — a bore is "
            'usually reached with the predicate {"cylindrical": true, "diameter_mm": D}, '
            "and catia_list_faces shows what the part offers."
        )

    faces = select_faces(shape, selector, tool=THREAD, document=document)
    diameter = _cylinder_diameter(faces, selector)
    spec = parse_designation(
        str(arguments.get("designation") or ""), pitch_mm=arguments.get("pitch_mm")
    )
    is_tap = True if arguments.get("tap") is None else bool(arguments.get("tap"))
    _check_fit(spec, diameter, is_tap=is_tap)

    record: dict[str, Any] = {
        # The selector verbatim, so a predicate stays a predicate: stringifying
        # `{"cylindrical": true}` would put a Python repr into the payload and make the
        # identity of the threaded face depend on dict ordering.
        "face": selector if isinstance(selector, str) else dict(selector),
        "face_diameter_mm": round(diameter, 6),
        "tap": is_tap,
        "left_handed": bool(arguments.get("left_handed")),
        **spec.to_dict(),
    }
    depth = arguments.get("depth_mm")
    if depth is not None:
        record["depth_mm"] = float(depth)
    document.add_thread(record)

    return {
        "thread": record,
        "threads": [dict(entry) for entry in document.threads],
        "note": (
            "Recorded as an annotation on the face, which is what a thread is in CATIA. "
            "No helix was cut, so the part's mass is unchanged."
        ),
        **document.measure(detail=context.detail),
    }


def _cylinder_diameter(faces: list[Any], selector: Any) -> float:
    """The diameter of the one cylindrical face the selector named."""
    if len(faces) != 1:
        raise GeometryError(
            f"{THREAD} threads one face, and {str(selector)!r} names {len(faces)}. A "
            "thread belongs to a single cylinder — name it precisely, or call "
            f"{THREAD} once per face."
        )

    diameter = cylinder_diameter_mm(faces[0])
    if diameter is None:
        raise GeometryError(
            f"{str(selector)!r} is not a cylindrical face, so it cannot carry a thread. "
            "Threads sit on the wall of a hole or the outside of a shaft."
        )
    return diameter


def _check_fit(spec: ThreadSpec, diameter_mm: float, *, is_tap: bool) -> None:
    """Refuse a designation that cannot belong to this cylinder.

    Skipped rather than guessed when the designation was not understood: an unrecognised
    thread already reports itself as unrecognised, and inventing a band to test it
    against would turn "I do not know this designation" into "this designation is wrong".
    """
    if not spec.is_understood:
        return

    nominal = spec.nominal_diameter_mm
    minor = spec.minor_diameter_mm()
    if nominal is None or minor is None:  # pragma: no cover - guarded by is_understood
        return

    low, high = minor - DIAMETER_MARGIN_MM, nominal + DIAMETER_MARGIN_MM
    if low <= diameter_mm <= high:
        return

    kind = "tapped hole" if is_tap else "threaded shank"
    raise GeometryError(
        f"{spec.designation} as a {kind} belongs on a cylinder between {minor:.3f} mm "
        f"(tapping drill) and {nominal:.3f} mm (nominal), and this face is "
        f"{diameter_mm:.3f} mm across. Either the face is the wrong one or the thread "
        "size is."
    )


__all__ = ["DIAMETER_MARGIN_MM", "THREAD", "thread"]
