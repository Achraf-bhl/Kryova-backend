"""Walking an OCCT shape: sub-shapes, counts, and the history of an operation.

`TopExp_Explorer` is a stateful cursor with `More`/`Current`/`Next`. Hand-rolling that
loop at each call site is how one of them eventually forgets `Next()` and hangs the
worker, so it is written once, here.

**Traversal is the hot path.** A plan for a machine is 10⁵–10⁶ operations and most of
them explore the shape they just built. Two things follow, and both are deliberate:

* `count` never materialises a list — it advances the cursor and counts. Building a
  list of 40,000 faces to call `len()` on it is the kind of waste that only shows up
  once a real assembly is loaded.
* `census` answers every count in **one** traversal per kind rather than three separate
  ones, because that is what the measurement payload actually needs.

Sub-shape *lists* are returned as lists rather than generators on purpose: the explorer
holds a cursor into the shape, and a half-consumed generator outliving its call is a
class of bug that is very hard to see afterwards.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Final

from app.kernel.errors import MeasurementError
from app.kernel.occt.binding import require, symbol

#: OCCT shape kinds this package walks, in the spelling `TopAbs_ShapeEnum` uses.
FACE: Final = "FACE"
EDGE: Final = "EDGE"
VERTEX: Final = "VERTEX"
SOLID: Final = "SOLID"
SHELL: Final = "SHELL"
WIRE: Final = "WIRE"
COMPOUND: Final = "COMPOUND"


def _enum(kind: str) -> Any:
    return getattr(symbol("TopAbs_ShapeEnum"), f"TopAbs_{kind}")


def explore(shape: Any, kind: str) -> list[Any]:
    """Every **distinct** sub-shape of `kind`, in a stable order.

    Uses `TopExp.MapShapes` rather than `TopExp_Explorer`, and the difference is not a
    detail: **the explorer visits a sub-shape once per parent that owns it.** On a
    closed solid every edge is shared by two faces and every vertex by three, so a box
    explores as 24 edges and 48 vertices instead of 12 and 8.

    That silently corrupted three things before it was caught: `edge_count` in every
    measurement (and therefore the determinism digest that hashes it), the edge lists
    fed to fillet and chamfer (each edge added twice), and any per-entity parameter list
    matched against the selection. The map de-duplicates by shape identity, which is
    what "every edge of this part" means to everyone except the explorer.

    The order is the map's insertion order — deterministic for a given shape, which is
    what per-entity parameters (Phase 2.3) are matched against.
    """
    require()
    mapping = symbol("TopTools_IndexedMapOfShape")()
    symbol("TopExp").MapShapes_s(shape, _enum(kind), mapping)
    return [mapping.FindKey(index) for index in range(1, mapping.Extent() + 1)]


def explore_oriented(shape: Any, kind: str) -> list[Any]:
    """Sub-shapes **as this parent carries them**, orientation included, duplicates kept.

    The deliberate opposite of `explore`. A face's boundary edges carry the orientation
    that says which way its loop runs, and that orientation is the only thing from which
    "into this face" can be computed — it is what convexity classification needs. The
    de-duplicating map discards it, because it identifies shapes by `IsSame`, which
    ignores orientation by design.

    Use `explore` for "every edge of this part"; use this for "this face's own boundary".
    """
    require()
    explorer = symbol("TopExp_Explorer")(shape, _enum(kind))
    found: list[Any] = []
    while explorer.More():
        found.append(explorer.Current())
        explorer.Next()
    return found


def count(shape: Any, kind: str) -> int:
    """How many distinct sub-shapes of `kind`, without materialising them.

    De-duplicated for the same reason `explore` is — a doubled edge count is a wrong
    number, not a cheaper one.
    """
    require()
    mapping = symbol("TopTools_IndexedMapOfShape")()
    symbol("TopExp").MapShapes_s(shape, _enum(kind), mapping)
    return int(mapping.Extent())


def census(shape: Any) -> dict[str, int]:
    """Face, edge and solid counts — the topology half of a measurement payload."""
    return {
        "face_count": count(shape, FACE),
        "edge_count": count(shape, EDGE),
        "solid_count": count(shape, SOLID),
    }


def faces(shape: Any) -> list[Any]:
    cast = symbol("TopoDS").Face_s
    return [cast(s) for s in explore(shape, FACE)]


def edges(shape: Any) -> list[Any]:
    cast = symbol("TopoDS").Edge_s
    return [cast(s) for s in explore(shape, EDGE)]


def vertices(shape: Any) -> list[Any]:
    cast = symbol("TopoDS").Vertex_s
    return [cast(s) for s in explore(shape, VERTEX)]


def has_solid(shape: Any) -> bool:
    """Does this shape enclose anything?

    The one place `TopExp_Explorer` is still the right tool: it answers from the first
    hit without walking the shape, and "is there at least one" cannot be confused by
    duplicates the way a count can.
    """
    require()
    explorer = symbol("TopExp_Explorer")(shape, _enum(SOLID))
    return bool(explorer.More())


def connected_pieces(shape: Any) -> int:
    """How many disconnected pieces a shape is in.

    A sewing returns **one** shape whatever happened, and what says whether its parts
    actually meet is its *type*: OCCT promotes touching faces into a SHELL and leaves
    untouched ones side by side in a COMPOUND. So a compound's direct children are the
    pieces, and anything else is a single piece.

    `explore` cannot answer this. It flattens, so two disconnected shells and one shell
    made of two faces are indistinguishable through it — which is how a connexity check
    written as a shell count reported "0 pieces" for two sheets that never met.

    Counted rather than returned as a boolean because "left 3 pieces" is a diagnosis and
    "not connected" is a shrug.
    """
    require()
    if shape.ShapeType() != _enum(COMPOUND):
        return 1
    iterator = symbol("TopoDS_Iterator")(shape)
    pieces = 0
    while iterator.More():
        pieces += 1
        iterator.Next()
    return pieces


def compound(shapes: Iterable[Any]) -> Any:
    """Several shapes as one, for an algorithm that takes a single argument.

    A compound is the only way to hand OCCT "these four faces" as one operand, so it is
    what both feature-restricted selection and multi-entity measurement are built on.
    It is a container and nothing more: no boolean is performed, so overlapping members
    stay overlapping and the result encloses no volume of its own.
    """
    require()
    builder = symbol("BRep_Builder")()
    result = symbol("TopoDS_Compound")()
    builder.MakeCompound(result)
    for shape in shapes:
        builder.Add(result, shape)
    return result


def point_of(vertex: Any) -> tuple[float, float, float]:
    point = symbol("BRep_Tool").Pnt_s(vertex)
    return (point.X(), point.Y(), point.Z())


def endpoints(edge: Any) -> list[tuple[float, float, float]]:
    return [point_of(v) for v in vertices(edge)]


def shape_list(topo_list: Any) -> list[Any]:
    """`TopTools_ListOfShape` → python list.

    The binding exposes `First`/`Last`/`Extent` but no iterator, so a list longer than
    two is not reachable through this API. Every caller here asks about one operation's
    Modified/Generated result for one sub-shape, where the count is 0 or 1 in practice.
    The limit is *stated and enforced* rather than assumed: a longer list raises instead
    of silently dropping the middle, which would corrupt a naming history in a way that
    only shows up as a name resolving to the wrong face much later.
    """
    extent = topo_list.Extent()
    if extent == 0:
        return []
    if extent == 1:
        return [topo_list.First()]
    if extent == 2:
        return [topo_list.First(), topo_list.Last()]
    raise MeasurementError(
        f"An OCCT shape list holds {extent} entries and this binding exposes only "
        "First/Last, so the middle would be silently lost. Widen "
        "app/kernel/occt/topology.shape_list with a real iterator before relying on an "
        "operation that returns this many results."
    )


__all__ = [
    "COMPOUND",
    "EDGE",
    "FACE",
    "SHELL",
    "SOLID",
    "VERTEX",
    "WIRE",
    "census",
    "compound",
    "connected_pieces",
    "count",
    "edges",
    "endpoints",
    "explore",
    "explore_oriented",
    "faces",
    "has_solid",
    "point_of",
    "shape_list",
    "vertices",
]
