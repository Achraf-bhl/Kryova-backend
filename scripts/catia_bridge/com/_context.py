"""What every COM mixin is allowed to assume about the object it is mixed into.

`catia_com.CatiaCom` grew past 2 400 lines covering the original 39 tools. The
registry is 200 and still growing, so continuing to add methods there would
produce a file nobody can hold in their head. Instead each workbench gets a
mixin module, and this declares the seam between them.

The rule the seam enforces: **a mixin may use what is declared here and nothing
else.** No reaching into another mixin's helpers, no assuming a private
attribute exists. When two mixins need the same helper it moves here, which is
how this file stays a contract rather than a junk drawer.

Everything is `# pragma: no cover` for the same reason the rest of the COM
backend is: it only runs on Windows with a licensed CATIA. `mock_catia.py` is
what the test suite exercises.
"""

from __future__ import annotations

from typing import Any, Protocol

from ..backend import CatiaOperationError

#: CATIA's reference planes, in the order `Part.OriginElements` exposes them.
ORIGIN_PLANES = {"XY": "PlaneXY", "YZ": "PlaneYZ", "ZX": "PlaneZX"}

#: Which origin plane a named bounding-box face lies parallel to. A face is
#: drilled or sketched along its own normal, so the plane is the one that
#: normal is perpendicular to: top and bottom are ±Z, hence XY.
FACE_PLANES = {
    "top": "XY",
    "bottom": "XY",
    "front": "ZX",
    "back": "ZX",
    "left": "YZ",
    "right": "YZ",
}

#: Which axis and sign each named face sits on, in the part's own frame.
FACE_AXES = {
    "top": ("z", 1),
    "bottom": ("z", -1),
    "front": ("y", -1),
    "back": ("y", 1),
    "left": ("x", -1),
    "right": ("x", 1),
}


class ComContext(Protocol):
    """The subset of `CatiaCom` a workbench mixin may rely on."""

    def _document(self) -> Any: ...
    def _part(self) -> Any: ...
    def _body(self) -> Any: ...
    def _feature_list(self) -> list[dict[str, Any]]: ...
    def _feature_result(self, name: str) -> dict[str, Any]: ...
    def _bounding_box(self) -> tuple[float, float, float, float, float, float] | None: ...
    def _discard(self, document: Any, part: Any, feature: Any) -> None: ...
    def _solid_volume(self) -> float: ...


def direction_of(part: Any, vector: Any) -> Any:  # pragma: no cover - Windows only
    """A `HybridShapeDirection` from a three-component list.

    Refuses a zero vector rather than passing it to CATIA, which accepts it and
    then fails later inside whatever feature consumed it — with an error naming
    the feature rather than the direction, which is the wrong place to look.
    """
    x, y, z = (float(component) for component in vector)
    if x == 0.0 and y == 0.0 and z == 0.0:
        raise CatiaOperationError(
            "A direction of [0, 0, 0] has no direction. Give a vector with at least "
            "one non-zero component; its length does not matter."
        )
    return part.HybridShapeFactory.AddNewDirectionByCoord(x, y, z)


def geometrical_set(part: Any, name: str = "") -> Any:  # pragma: no cover - Windows only
    """The geometrical set new construction geometry goes into.

    Wireframe and surfaces cannot live loose in a part the way solid features
    can — they need a hybrid body to hold them. Reusing one rather than creating
    a set per element is what keeps the tree readable after fifty operations.
    """
    bodies = part.HybridBodies
    wanted = name or "Kryova Construction"
    for index in range(1, int(bodies.Count) + 1):
        body = bodies.Item(index)
        if str(body.Name) == wanted:
            return body
    created = bodies.Add()
    try:
        created.Name = wanted
    except Exception:  # noqa: BLE001 - a name clash is cosmetic, not fatal
        pass
    return created


def resolve_element(part: Any, name: str) -> Any:  # pragma: no cover - Windows only
    """Find any named element — a sketch, feature, surface, curve, point, plane.

    `Part.FindObjectByName` is the one call that searches every collection at
    once, which is what lets a single `element_reference` parameter accept all
    of them. It raises on a miss, and the message it raises is unusable, so the
    miss is turned into one that names what *is* there.
    """
    try:
        found = part.FindObjectByName(name)
    except Exception:
        found = None
    if found is not None:
        return found

    raise CatiaOperationError(
        f"Nothing in this part is named {name!r}. Call catia_list_features to see "
        "what exists, and use a name exactly as it is reported there."
    )


def resolve_support(context: ComContext, support: str) -> Any:  # pragma: no cover - Windows only
    """A plane reference from an origin-plane name, an element name, or a face.

    Resolution order is origin plane, then named element, then bounding-box
    face, and it is worth knowing it is that way round: a user-created plane
    called "top" would otherwise shadow the bounding-box face of the same name,
    and the origin planes can never be shadowed at all.
    """
    part = context._part()

    attribute = ORIGIN_PLANES.get(support.upper())
    if attribute is not None:
        return getattr(part.OriginElements, attribute)

    try:
        return resolve_element(part, support)
    except CatiaOperationError:
        pass

    if support.lower() in FACE_PLANES:
        return named_face_plane(context, support.lower())

    raise CatiaOperationError(
        f"{support!r} is not a plane. Use 'XY', 'YZ' or 'ZX', the name of a plane you "
        f"created, or one of {', '.join(sorted(FACE_PLANES))}."
    )


def named_face_plane(context: ComContext, face: str) -> Any:  # pragma: no cover - Windows only
    """A plane lying on a named face of the part's bounding box.

    Built as an offset from the parallel origin plane, at the distance the
    bounding box says that face sits at. That is an approximation with a real
    limit worth stating: it is the *plane of* the face, not the face itself, so
    it is right for sketching on and wrong for anything that needs the face's
    actual boundary. Operations in the second category take a face reference
    from `catia_list_faces` instead.
    """
    box = context._bounding_box()
    if box is None:
        raise CatiaOperationError(
            "Could not measure the part, so its named faces cannot be located. "
            "Build some geometry first, or name a plane directly."
        )
    axis, sign = FACE_AXES[face]
    index = {"x": 0, "y": 1, "z": 2}[axis]
    offset = box[index + 3] if sign > 0 else box[index]

    part = context._part()
    base = getattr(part.OriginElements, ORIGIN_PLANES[FACE_PLANES[face]])
    plane = part.HybridShapeFactory.AddNewPlaneOffset(base, float(offset), False)
    geometrical_set(part).AppendHybridShape(plane)
    part.Update()
    return plane


def append_and_name(part: Any, element: Any, name: str = "") -> str:  # pragma: no cover
    """Put a construction element in the tree, name it, and return the name.

    Naming matters more here than it does for solid features: a plane the model
    created is referenced by name in the very next call, and CATIA's own
    generated names ("Plane.7") are stable but tell the model nothing about
    which of its four planes it just made.
    """
    geometrical_set(part).AppendHybridShape(element)
    if name:
        try:
            element.Name = name
        except Exception:  # noqa: BLE001 - a rejected name is cosmetic
            pass
    part.Update()
    return str(element.Name)


def reference_to(part: Any, element: Any) -> Any:  # pragma: no cover - Windows only
    """A `Reference` for an element, which most factory calls want in place of it."""
    return part.CreateReferenceFromObject(element)
