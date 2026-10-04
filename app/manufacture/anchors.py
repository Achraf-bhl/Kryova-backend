"""Where a GD&T frame's leader lands: feature identity → geometry → a point on a view.

Master plan E17 task 1's last open item. Until this module, feature control frames
reached the sheet as a **table keyed on the feature's name in words**, because nothing
resolved that name to anything on a view. The table is correct and is what an inspection
plan reads; what it cannot do is point at the thing it is talking about.

**The chain, and why each hop is the one it is.**

*Feature identity → geometric entity.* A `FeatureControlFrame.feature` is free text by
design — `app/rules/gdt.py` says so, and it is right: a datum is established by something
a person puts an indicator on, and that is a sentence, not a handle. So the binding is
**supplied**, never guessed, as a `Selector` per feature name. A `Selector` is this
codebase's existing answer to "which face": `catia_list_faces` returns one per face, and
the whole of `app/solve/selection.py` is built on the rule that regions are named by
geometry and **never by face id, because face ids are meaningless across a re-export**.
Reusing it means a leader survives the rebuild that renumbers every face.

**What this module deliberately does not do is guess.** There is no string matching from
`"base face"` to a downward normal. A frame whose feature has no supplied selector gets
no leader and stays in the table, exactly as before; a selector matching *several* faces
is refused by name rather than pointed at whichever came back first. Both refusals are
the point: a leader drawn at a plausible-looking place that is not the feature is worse
than no leader, because a reader believes it.

*Geometric entity → projection.* The face's own `centre_mm`, projected into the view's
own basis. **Verified against HLR rather than derived a second time**: `project_point`
uses `View.right()` and `View.frame_up()`, and on a 120x80x12 block the eight corners
project to exactly the extent `HLRBRep` reports for front, top and right (measured
2026-09-22). That matters because `app/render/project.py` carries two sign corrections
that cancelled each other for a day — a second, independent projection here would be the
third place to get that wrong.

*Which view.* A leader must land on a view where the face is actually facing the reader.
`View.direction` points **from the eye towards the part**, so a face is turned towards
the eye when `normal · direction < 0`, and most squarely when that product is most
negative. A frame whose feature faces away in every available view gets no leader and
says so, rather than pointing at a silhouette the feature is behind.

**The gap above this module, stated plainly.** `NameRegistry` in `app/kernel/occt/
naming.py` is built to resolve a design's semantic names to geometry after a rebuild, and
**nothing in `app/` or `tests/` calls its `record()`** — checked 2026-09-22. Until some
operation records the names it creates, the selector map here has to come from the caller.
When that lands, `anchors_for` takes its input from the registry instead and nothing else
in this chain changes, which is why the seam is a mapping rather than a lookup.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.manufacture.errors import DrawingError

#: How close two unit normals must be to count as the same face direction. A
#: generous 1e-6 on a dot product: the numbers come from OCCT's own surface
#: evaluation and from a JSON round trip, and the question being asked is "is
#: this the same planar face", not "how parallel are these".
NORMAL_TOLERANCE: float = 1e-6


@dataclass(frozen=True)
class FeatureAnchor:
    """One feature, the point a leader may touch, and which way that surface faces.

    `point_mm` is the face's own centre in part coordinates — not a corner and not a
    bounding-box guess — so it moves when the part does, which is what makes the
    attachment survive a parameter change rather than a coordinate surviving it.
    """

    feature: str
    point_mm: tuple[float, float, float]
    normal: tuple[float, float, float]


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    return sum(float(a) * float(b) for a, b in zip(left, right, strict=True))


def project_point(
    point_mm: Sequence[float], view: Any
) -> tuple[float, float]:
    """One part-coordinate point in the view's own 2-D millimetres.

    The same frame `app/render/project.py` hands back from HLR, reached through the
    `View`'s declared basis rather than by rebuilding OCCT's `gp_Ax2` convention. The
    two agree exactly on a block's eight corners in front, top and right; see the module
    docstring for why that check exists at all.
    """
    return (_dot(point_mm, view.right()), _dot(point_mm, view.frame_up()))


def faces_towards(normal: Sequence[float], view: Any) -> float:
    """How squarely `normal` faces this view's eye. Positive means visible.

    `View.direction` points from the eye towards the part, so a surface turned towards
    the reader has a normal opposing it. Returned as a number rather than a bool so a
    caller can pick the *most* face-on view instead of the first acceptable one.
    """
    return -_dot(normal, view.direction)


def match_faces(
    faces: Sequence[Mapping[str, Any]], selector: Mapping[str, Any]
) -> list[Mapping[str, Any]]:
    """Every face in `faces` that `selector` names. Order preserved.

    Matches on the selector's own fields rather than on identity: a planar selector
    carries a normal, and a face matches when its normal is that one. Anything the
    selector does not state is not compared, so a selector naming only a normal matches
    every face with that normal — which is why the caller must treat more than one match
    as an ambiguity rather than a result.
    """
    wanted = selector.get("normal")
    if wanted is None:
        raise DrawingError(
            "A feature selector for a leader must name a normal — that is how "
            "`catia_list_faces` names a planar face, and it is the one form that "
            "survives the re-export that renumbers every face id. Got: "
            f"{dict(selector)!r}."
        )
    matched: list[Mapping[str, Any]] = []
    for face in faces:
        normal = face.get("normal")
        if normal is None:
            continue
        if all(abs(float(a) - float(b)) <= NORMAL_TOLERANCE for a, b in zip(normal, wanted, strict=True)):
            matched.append(face)
    return matched


def anchors_for(
    features: Mapping[str, Mapping[str, Any]],
    faces: Sequence[Mapping[str, Any]],
) -> tuple[tuple[FeatureAnchor, ...], dict[str, str]]:
    """Resolve each named feature to one anchor, or say why it could not be.

    Returns the anchors that resolved and, beside them, a reason per feature that did
    not. **Both halves are returned rather than raising**, because a drawing with one
    unresolvable frame must still be produced — the frame is tabulated, which is what
    happened to every frame before this module existed. Losing the whole sheet over one
    leader would be a worse answer than the one that was already acceptable.

    `features` maps the feature name a frame carries to the selector that names its
    geometry. The caller supplies it; nothing here infers one from a name.
    """
    anchors: list[FeatureAnchor] = []
    unresolved: dict[str, str] = {}

    for feature, selector in features.items():
        try:
            matched = match_faces(faces, selector)
        except DrawingError as exc:
            unresolved[feature] = str(exc)
            continue

        if not matched:
            unresolved[feature] = (
                f"no face of this part matches the selector given for {feature!r}. It "
                "may have been removed by a later feature, or the selector may name a "
                "direction this part has no face in."
            )
            continue
        if len(matched) > 1:
            areas = ", ".join(f"{float(one.get('area_mm2', 0.0)):.1f} mm2" for one in matched)
            unresolved[feature] = (
                f"the selector given for {feature!r} matches {len(matched)} faces "
                f"({areas}), so which one the frame is about is not decided. A leader "
                "to whichever came back first would be pointing at geometry nobody "
                "chose. Narrow the selector."
            )
            continue

        face = matched[0]
        centre = face.get("centre_mm")
        normal = face.get("normal")
        if centre is None or normal is None:
            unresolved[feature] = (
                f"the face matched for {feature!r} reports no centre or no normal, so "
                "there is no point to lead to."
            )
            continue
        anchors.append(
            FeatureAnchor(
                feature=feature,
                point_mm=(float(centre[0]), float(centre[1]), float(centre[2])),
                normal=(float(normal[0]), float(normal[1]), float(normal[2])),
            )
        )

    return tuple(anchors), unresolved


def best_view(anchor: FeatureAnchor, views: Sequence[Any]) -> Any | None:
    """The view this anchor's surface faces most squarely, or None if it faces none.

    None rather than a least-bad choice: a leader onto a view the feature is *behind*
    points at a silhouette, and the reader has no way to tell that from a leader onto
    the face itself.
    """
    facing = [(faces_towards(anchor.normal, view), view) for view in views]
    visible = [(score, view) for score, view in facing if score > NORMAL_TOLERANCE]
    if not visible:
        return None
    return max(visible, key=lambda pair: pair[0])[1]


__all__ = [
    "FeatureAnchor",
    "NORMAL_TOLERANCE",
    "anchors_for",
    "best_view",
    "faces_towards",
    "match_faces",
    "project_point",
]
