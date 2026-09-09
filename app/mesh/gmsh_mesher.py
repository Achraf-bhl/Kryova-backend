"""Gmsh-backed meshing of uploaded CAD files: tetrahedra from a solid body,
triangles from a face authored in the z = 0 plane.

Gmsh is a process-global singleton and is not thread-safe, so every call here
goes through `gmsh_session`, which holds the module lock for the whole model's
lifetime. FastAPI runs sync endpoints in a threadpool, which would otherwise let
two requests corrupt each other's model. Meshing belongs on a job queue anyway;
the lock is the correctness floor, not the plan.
"""

import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import numpy as np

from app.geometry.inspect import is_binary_stl
from app.mesh.gmsh_session import gmsh_session, staged_with_extension
from app.mesh.planar import (
    _PLANARITY_TOLERANCE_MM,
    TRI6_EDGES,
    TriMesh,
    planar_quality,
)
from app.mesh.structural import ShellMesh, shell_quality
from app.mesh.types import TET10_EDGES, MeshError, TetMesh, quality

_TET4_ELEMENT_TYPE = 4
_TET10_ELEMENT_TYPE = 11
_NODES_PER_ELEMENT = {_TET4_ELEMENT_TYPE: 4, _TET10_ELEMENT_TYPE: 10}
_ELEMENT_TYPE_FOR_ORDER = {1: _TET4_ELEMENT_TYPE, 2: _TET10_ELEMENT_TYPE}

_TRI3_ELEMENT_TYPE = 2
_TRI6_ELEMENT_TYPE = 9
_NODES_PER_TRIANGLE = {_TRI3_ELEMENT_TYPE: 3, _TRI6_ELEMENT_TYPE: 6}
_TRI_ELEMENT_TYPE_FOR_ORDER = {1: _TRI3_ELEMENT_TYPE, 2: _TRI6_ELEMENT_TYPE}

_QUAD4_ELEMENT_TYPE = 3
_QUAD8_ELEMENT_TYPE = 16
#: Never asked for — CalculiX's S8R is the 8-node element. Recognised anyway so
#: that a mesh carrying one is *named* rather than skipped as an unknown type:
#: this is what gmsh returns when `Mesh.SecondOrderIncomplete` is not set, and
#: the only useful error is the one that says so.
_QUAD9_ELEMENT_TYPE = 10

#: Gmsh's element type for each shell face shape and order, and how many nodes
#: it carries. `(face_shape, element_order)` rather than two nested tables so a
#: shape that gains an order cannot acquire half an entry.
_SHELL_ELEMENT_TYPE: dict[str, dict[int, int]] = {
    "tri": {1: _TRI3_ELEMENT_TYPE, 2: _TRI6_ELEMENT_TYPE},
    "quad": {1: _QUAD4_ELEMENT_TYPE, 2: _QUAD8_ELEMENT_TYPE},
}
_NODES_PER_SHELL_FACE = {
    _TRI3_ELEMENT_TYPE: 3,
    _TRI6_ELEMENT_TYPE: 6,
    _QUAD4_ELEMENT_TYPE: 4,
    _QUAD8_ELEMENT_TYPE: 8,
    _QUAD9_ELEMENT_TYPE: 9,
}
#: What to call each in an error message. Gmsh's own names, so a reader can look
#: the number up.
_SHELL_ELEMENT_NAME = {
    _TRI3_ELEMENT_TYPE: "3-node triangle",
    _TRI6_ELEMENT_TYPE: "6-node triangle",
    _QUAD4_ELEMENT_TYPE: "4-node quadrilateral",
    _QUAD8_ELEMENT_TYPE: "8-node quadrilateral",
    _QUAD9_ELEMENT_TYPE: "9-node quadrilateral",
}

# Default target: this many elements along the bounding-box diagonal. Enough to
# resolve a simple part in seconds; refinement is a user-facing knob.
_DEFAULT_ELEMENTS_ALONG_DIAGONAL = 20

# STL is a bag of triangles with no topology. These control how gmsh infers
# faces from it: dihedral angles sharper than this start a new surface.
_STL_FEATURE_ANGLE_DEG = 40.0
_STL_CURVE_ANGLE_DEG = 180.0

# Binary STL: 80 bytes of free-form comment before the triangle count.
_STL_HEADER_BYTES = 80
# Must not begin with a NUL, and must not begin with "solid".
_STAGED_STL_HEADER = b"Kryova staged binary STL".ljust(_STL_HEADER_BYTES, b" ")


def generate_tet_mesh(
    path: Path,
    file_format: str,
    element_size_mm: float | None = None,
    element_order: int = 1,
) -> tuple[TetMesh, dict[str, Any]]:
    """Mesh a CAD file into tetrahedra.

    Returns the mesh and its quality summary. `element_size_mm` overrides the
    automatic target size; `element_order` is 1 for tet4 or 2 for tet10.
    """
    if element_order not in _ELEMENT_TYPE_FOR_ORDER:
        raise MeshError(f"element_order must be 1 or 2, got {element_order}")

    with _named_for_gmsh(path, file_format) as readable, gmsh_session() as gmsh:
        _load(gmsh, readable, file_format)
        _set_element_size(gmsh, element_size_mm)
        try:
            gmsh.model.mesh.generate(3)
            if element_order == 2:
                # Straight edges: gmsh otherwise pulls midside nodes onto the
                # CAD surface, which buys geometric fidelity at the cost of an
                # element whose volume no longer equals the volume of its four
                # corners -- and every geometric quantity here (volume, mass,
                # the boundary the viewer draws) is computed from those corners.
                gmsh.option.setNumber("Mesh.SecondOrderLinear", 1)
                gmsh.model.mesh.setOrder(2)
        except Exception as exc:  # gmsh raises bare Exception subclasses
            raise MeshError(f"Meshing failed: {exc}") from exc
        mesh = _extract(gmsh, element_order)

    stats = quality(mesh)
    stats["mesher"] = "gmsh"
    return mesh, stats


def generate_tri_mesh(
    path: Path,
    file_format: str,
    element_size_mm: float | None = None,
    element_order: int = 1,
) -> tuple[TriMesh, dict[str, Any]]:
    """Mesh a planar CAD file into triangles, for a plane stress or plane strain model.

    Returns the mesh and its quality summary. `element_size_mm` overrides the
    automatic target size; `element_order` is 1 for tri3 or 2 for tri6.

    The face must be authored in the z = 0 plane. A plane model is an
    idealisation of a cross-section rather than a shell, so a face that arrives
    anywhere else is refused by name and never projected -- see
    `_assert_lies_in_the_z_zero_plane`.
    """
    if element_order not in _TRI_ELEMENT_TYPE_FOR_ORDER:
        raise MeshError(f"element_order must be 1 or 2, got {element_order}")

    with _named_for_gmsh(path, file_format) as readable, gmsh_session() as gmsh:
        _load_surface(gmsh, readable, file_format)
        _set_element_size(gmsh, element_size_mm)
        try:
            gmsh.model.mesh.generate(2)
            if element_order == 2:
                # Straight edges, for the same reason as the tet path: gmsh
                # otherwise pulls midside nodes onto the CAD curve, and every
                # geometric quantity here (area, the boundary edges a traction
                # is distributed over) is computed from the three corners.
                gmsh.option.setNumber("Mesh.SecondOrderLinear", 1)
                gmsh.model.mesh.setOrder(2)
        except Exception as exc:  # gmsh raises bare Exception subclasses
            raise MeshError(f"Meshing failed: {exc}") from exc
        mesh = _extract_tri(gmsh, element_order)

    stats = planar_quality(mesh)
    stats["mesher"] = "gmsh"
    return mesh, stats


def generate_shell_mesh(
    path: Path,
    file_format: str,
    element_size_mm: float | None = None,
    element_order: int = 1,
    face_shape: str = "tri",
) -> tuple[ShellMesh, dict[str, Any]]:
    """Mesh a curved surface in three dimensions into a `ShellMesh`.

    Returns the mesh and its quality summary. `element_size_mm` overrides the
    automatic target size, `element_order` is 1 or 2, and `face_shape` is
    `"tri"` (S3/S6) or `"quad"` (S4/S8R).

    **This is the producer `app/mesh/structural.py` says does not exist**, and it
    is a different thing from `generate_tri_mesh` in the one way that matters: a
    plane model is a cross-section and is refused anywhere but z = 0, whereas a
    shell is a surface *in space* and the whole point of it is to be curved. The
    two therefore cannot share a loader, and the z = 0 assertion must not be
    reached from here.

    `face_shape="quad"` asks gmsh to recombine, and quadrilaterals are worth
    asking for on a bending problem: a shell benchmark such as NAFEMS LE3 exists
    to expose membrane locking, and a linear triangle locks hardest. See
    `_extract_shell` for what happens when recombination only half succeeds.
    """
    if element_order not in (1, 2):
        raise MeshError(f"element_order must be 1 or 2, got {element_order}")
    if face_shape not in _SHELL_ELEMENT_TYPE:
        raise MeshError(f"face_shape must be 'tri' or 'quad', got {face_shape!r}")

    with _named_for_gmsh(path, file_format) as readable, gmsh_session() as gmsh:
        _load_shell_surface(gmsh, readable, file_format)
        _set_element_size(gmsh, element_size_mm)
        try:
            if face_shape == "quad":
                gmsh.option.setNumber("Mesh.RecombineAll", 1)
            gmsh.model.mesh.generate(2)
            if element_order == 2:
                # Straight edges, for the third time and the same reason: every
                # geometric quantity a shell carries -- `face_areas`, and so the
                # mass and the tributary area a load is spread over -- is
                # computed from the corner nodes, so a midside node pulled onto
                # the CAD surface would make the element's own area disagree
                # with the area it is loaded and weighed by.
                gmsh.option.setNumber("Mesh.SecondOrderLinear", 1)
                # Serendipity, not Lagrange. Without this gmsh writes a 9-node
                # quadrilateral with a bubble node at the centre, and CalculiX's
                # S8R is the 8-node element: the centre node would arrive as a
                # ninth column that `ShellMesh` refuses, which is the good case,
                # or be silently dropped, which is not.
                gmsh.option.setNumber("Mesh.SecondOrderIncomplete", 1)
                gmsh.model.mesh.setOrder(2)
        except Exception as exc:  # gmsh raises bare Exception subclasses
            raise MeshError(f"Meshing failed: {exc}") from exc
        mesh = _extract_shell(gmsh, element_order, face_shape)

    _refuse_a_closed_surface(mesh)
    stats = shell_quality(mesh)
    stats["mesher"] = "gmsh"
    return mesh, stats


def _refuse_a_closed_surface(mesh: ShellMesh) -> None:
    """Refuse a mesh that encloses a volume, whatever file it came from.

    `_load_shell_surface` refuses a *solid body*, but it can only do that where
    the file has topology to ask about. **An STL has none** — it is a bag of
    triangles, `getEntities(3)` is empty for a watertight solid exported as one,
    and the check there is skipped for STL anyway. A STEP carrying sewn faces
    that happen to close is the same case with a different extension.

    So the honest test is on the geometry rather than the file: a surface that
    encloses a volume has **every edge shared by exactly two faces**, and an open
    one has a boundary. Meshing a closed surface as a shell would hand back the
    boundary of the solid with the caller's thickness smeared over it — the
    hollow-shell substitution `_load_shell_surface` describes, arrived at by the
    one route that check cannot see. Measured: a 50x30x20 box exported to STL
    went straight through this function and reported an area of exactly
    6200 mm^2, the closed boundary, before this guard existed.
    """
    edges = np.sort(
        np.stack([mesh.faces[:, [a, b]] for a, b in mesh.edge_table], axis=1).reshape(-1, 2),
        axis=1,
    )
    _, counts = np.unique(edges, axis=0, return_counts=True)
    if (counts == 2).all():
        raise MeshError(
            f"This surface is closed — all {len(counts)} of its edges are shared by two "
            "faces, so it encloses a volume rather than idealising one. A shell model is "
            "meshed from the mid-surface of a thin wall, which has a boundary; meshing a "
            "closed surface would give a hollow shell of your chosen thickness in place of "
            "the body the file describes. Export the mid-surface, or run a solid analysis."
        )


@contextmanager
def _named_for_gmsh(path: Path, file_format: str) -> Iterator[Path]:
    """Present the file to gmsh under a name and header it can actually read.

    Two things trip gmsh up. The extension is handled by
    `staged_with_extension`; the second is STL-specific:

    Its STL sniffer walks the file looking for a line it can classify as
    "solid" (ASCII) or not (binary), but skips any line starting with a NUL. A
    binary STL with the conventional zeroed 80-byte header and no 0x0A byte
    anywhere -- which happens whenever the coordinates are NUL-heavy, e.g.
    exact powers of two -- has no such line, so gmsh reaches EOF and reports
    only "Error loading". Replacing the header with text costs one copy and
    makes the file classify immediately.
    """
    rewrite = (
        _copy_with_text_header if file_format == "stl" and _header_defeats_gmsh(path) else None
    )
    with staged_with_extension(path, file_format, copy=rewrite) as staged:
        yield staged


def _header_defeats_gmsh(path: Path) -> bool:
    """True for a binary STL whose header starts with a NUL (see above)."""
    with path.open("rb") as fh:
        first = fh.read(1)
    return bool(first) and first[0] == 0 and is_binary_stl(path)


def _copy_with_text_header(source: Path, target: Path) -> None:
    """Copy an STL, replacing only its 80-byte comment header.

    The header is free-form by convention and carries no geometry, so this
    changes nothing a mesher cares about.
    """
    with source.open("rb") as src, target.open("wb") as dst:
        src.seek(_STL_HEADER_BYTES)
        dst.write(_STAGED_STL_HEADER)
        shutil.copyfileobj(src, dst, length=4 * 1024 * 1024)


def _load(gmsh, path: Path, file_format: str) -> None:
    try:
        gmsh.merge(str(path))
    except Exception as exc:
        raise MeshError(f"Gmsh could not read the file: {exc}") from exc

    if file_format == "stl":
        _build_volume_from_triangles(gmsh)
        return

    # STEP and IGES arrive with real topology. If the file carried only surfaces
    # there is nothing to fill with tets, and that is worth saying plainly.
    if not gmsh.model.getEntities(3):
        raise MeshError(
            "The file contains no solid body, only surfaces. Structural analysis "
            "needs a closed solid."
        )


def _build_volume_from_triangles(gmsh) -> None:
    """Turn a raw STL triangle soup into a meshable solid."""
    try:
        gmsh.model.mesh.classifySurfaces(
            np.radians(_STL_FEATURE_ANGLE_DEG),
            True,
            True,
            np.radians(_STL_CURVE_ANGLE_DEG),
        )
        gmsh.model.mesh.createGeometry()
        surfaces = [entity[1] for entity in gmsh.model.getEntities(2)]
        if not surfaces:
            raise MeshError("No surfaces could be recovered from the STL")
        loop = gmsh.model.geo.addSurfaceLoop(surfaces)
        gmsh.model.geo.addVolume([loop])
        gmsh.model.geo.synchronize()
    except MeshError:
        raise
    except Exception as exc:
        raise MeshError(
            f"The STL could not be closed into a solid ({exc}). Non-watertight meshes "
            "must be repaired before analysis."
        ) from exc


def _load_surface(gmsh, path: Path, file_format: str) -> None:
    """The 2-D counterpart of `_load`: what is needed is a face, not a volume."""
    try:
        gmsh.merge(str(path))
    except Exception as exc:
        raise MeshError(f"Gmsh could not read the file: {exc}") from exc

    if file_format == "stl":
        _build_surfaces_from_triangles(gmsh)
    elif gmsh.model.getEntities(3):
        # Meshing the boundary of a solid would succeed and hand back a closed
        # shell -- a surface in three dimensions, which is not a plane model and
        # would be refused a step later for being off z = 0. Say so here, where
        # the reason is still legible.
        raise MeshError(
            "The file contains a solid body. A plane stress or plane strain model is "
            "meshed from a single face lying in z = 0; export the cross-section as a "
            "face, or run a solid analysis instead."
        )

    if not gmsh.model.getEntities(2):
        raise MeshError(
            "The file contains no surface to mesh. A plane model needs a face; a file "
            "holding only points and curves has nothing to fill with triangles."
        )


def _build_surfaces_from_triangles(gmsh) -> None:
    """Recover surface topology from a raw STL triangle soup.

    The first half of `_build_volume_from_triangles` and no more: a plane model
    has no volume to close, so there is no surface loop to build.
    """
    try:
        gmsh.model.mesh.classifySurfaces(
            np.radians(_STL_FEATURE_ANGLE_DEG),
            True,
            True,
            np.radians(_STL_CURVE_ANGLE_DEG),
        )
        gmsh.model.mesh.createGeometry()
        gmsh.model.geo.synchronize()
    except Exception as exc:
        raise MeshError(
            f"No surface could be recovered from the STL ({exc}). A plane model is "
            "meshed from a face; check that the file is not empty or self-intersecting."
        ) from exc


def _set_element_size(gmsh, element_size_mm: float | None) -> None:
    if element_size_mm is not None:
        if element_size_mm <= 0:
            raise MeshError("element_size_mm must be positive")
        target = element_size_mm
    else:
        lo = np.array(gmsh.model.getBoundingBox(-1, -1)[:3])
        hi = np.array(gmsh.model.getBoundingBox(-1, -1)[3:])
        diagonal = float(np.linalg.norm(hi - lo))
        if not np.isfinite(diagonal) or diagonal <= 0:
            raise MeshError("The model has no measurable extent")
        target = diagonal / _DEFAULT_ELEMENTS_ALONG_DIAGONAL

    gmsh.option.setNumber("Mesh.MeshSizeMin", target * 0.25)
    gmsh.option.setNumber("Mesh.MeshSizeMax", target)
    # Ignore sizes baked into the CAD file; the target above is the intent.
    gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 0)
    gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 0)


def _extract(gmsh, element_order: int) -> TetMesh:
    node_tags, coordinates, _ = gmsh.model.mesh.getNodes()
    if len(node_tags) == 0:
        raise MeshError("Meshing produced no nodes")
    nodes = np.asarray(coordinates, dtype=np.float64).reshape(-1, 3)

    # Gmsh tags are 1-based and may have gaps; remap them to dense indices.
    tags = np.asarray(node_tags, dtype=np.int64)
    lookup = np.full(int(tags.max()) + 1, -1, dtype=np.int64)
    lookup[tags] = np.arange(len(tags), dtype=np.int64)

    wanted = _ELEMENT_TYPE_FOR_ORDER[element_order]
    element_types, _, element_nodes = gmsh.model.mesh.getElements(3)
    for element_type, connectivity in zip(element_types, element_nodes, strict=True):
        if element_type != wanted:
            continue
        elements = lookup[
            np.asarray(connectivity, dtype=np.int64).reshape(-1, _NODES_PER_ELEMENT[wanted])
        ]
        if (elements < 0).any():
            raise MeshError("Meshing produced an element referencing an unknown node")
        mesh = _drop_unused_nodes(nodes, elements)
        if element_order == 2:
            _assert_midside_ordering(mesh)
        return mesh

    raise MeshError("Meshing produced no tetrahedra; the geometry may not be a closed solid")


def _drop_unused_nodes(nodes: np.ndarray, elements: np.ndarray) -> TetMesh:
    """Gmsh keeps surface-only nodes in its node list; the solver would read
    them as unconstrained free DOFs and make the system singular."""
    used, inverse = np.unique(elements, return_inverse=True)
    renumbered = inverse.reshape(elements.shape).astype(np.int64)
    return TetMesh(
        nodes=nodes[used],
        tets=renumbered[:, :4],
        midside=renumbered[:, 4:] if elements.shape[1] == 10 else None,
    )


def _assert_midside_ordering(mesh: TetMesh) -> None:
    """Confirm gmsh's midside node order still matches `TET10_EDGES`.

    The solver's shape functions are written against that table, and a silent
    disagreement would not crash -- it would return a plausible, wrong stiffness
    matrix. `Mesh.SecondOrderLinear` puts each midside node exactly halfway
    along its edge, so the check is a coordinate comparison.
    """
    assert mesh.midside is not None
    corners = mesh.nodes[mesh.tets]
    expected = np.stack([0.5 * (corners[:, a] + corners[:, b]) for a, b in TET10_EDGES], axis=1)
    actual = mesh.nodes[mesh.midside]
    lo, hi = mesh.bounding_box
    scale = float(np.linalg.norm(hi - lo)) or 1.0
    if not np.allclose(actual, expected, atol=1e-6 * scale):
        raise MeshError(
            "Gmsh returned tet10 nodes in an unexpected order; the quadratic "
            "element formulation cannot be trusted against this mesh."
        )


def _extract_tri(gmsh, element_order: int) -> TriMesh:
    """The 2-D counterpart of `_extract`, element for element."""
    node_tags, coordinates, _ = gmsh.model.mesh.getNodes()
    if len(node_tags) == 0:
        raise MeshError("Meshing produced no nodes")
    nodes = np.asarray(coordinates, dtype=np.float64).reshape(-1, 3)

    # Gmsh tags are 1-based and may have gaps; remap them to dense indices.
    tags = np.asarray(node_tags, dtype=np.int64)
    lookup = np.full(int(tags.max()) + 1, -1, dtype=np.int64)
    lookup[tags] = np.arange(len(tags), dtype=np.int64)

    wanted = _TRI_ELEMENT_TYPE_FOR_ORDER[element_order]
    element_types, _, element_nodes = gmsh.model.mesh.getElements(2)
    for element_type, connectivity in zip(element_types, element_nodes, strict=True):
        if element_type != wanted:
            continue
        elements = lookup[
            np.asarray(connectivity, dtype=np.int64).reshape(-1, _NODES_PER_TRIANGLE[wanted])
        ]
        if (elements < 0).any():
            raise MeshError("Meshing produced an element referencing an unknown node")
        mesh = _drop_unused_tri_nodes(nodes, elements)
        if element_order == 2:
            _assert_tri_midside_ordering(mesh)
        return mesh

    raise MeshError("Meshing produced no triangles; the geometry may not contain a face")


def _drop_unused_tri_nodes(nodes: np.ndarray, elements: np.ndarray) -> TriMesh:
    """The 2-D counterpart of `_drop_unused_nodes`, with the planarity check.

    The check has to happen here rather than after the mesh is built, because
    `TriMesh.__post_init__` refuses the same array first and its message is
    about the data structure -- the caller needs to hear about the file.
    """
    used, inverse = np.unique(elements, return_inverse=True)
    kept = nodes[used]
    _assert_lies_in_the_z_zero_plane(kept)
    renumbered = inverse.reshape(elements.shape).astype(np.int64)
    return TriMesh(
        nodes=kept,
        tris=renumbered[:, :3],
        midside=renumbered[:, 3:] if elements.shape[1] == 6 else None,
    )


def _assert_lies_in_the_z_zero_plane(nodes: np.ndarray) -> None:
    """Refuse a mesh that came back anywhere but z = 0, naming where it is.

    `app.mesh.planar` carries the third coordinate only so the geometric
    selectors keep working; the plane model itself is a cross-section, not a
    shell. Gmsh returns a face where the file put it, so an offset or rotated
    face must be refused rather than projected: projecting an offset face would
    silently move the geometry, and projecting a face in x = 0 would collapse it
    to a line. The threshold is `planar`'s own, so this fires before
    `TriMesh.__post_init__` does and never after it.
    """
    out_of_plane = float(np.abs(nodes[:, 2]).max(initial=0.0))
    if out_of_plane <= _PLANARITY_TOLERANCE_MM:
        return
    raise MeshError(
        f"A plane model must be authored in the z = 0 plane; this face was meshed in "
        f"{_describe_plane(nodes)}, up to {out_of_plane:g} mm off it. Move the face onto "
        "z = 0 in the CAD file and export it again. Meshing will not project it for you, "
        "because projecting changes the geometry rather than repositioning it."
    )


def _describe_plane(nodes: np.ndarray) -> str:
    """Where a non-conforming mesh actually is, in words, for the error above."""
    lo = nodes.min(axis=0)
    hi = nodes.max(axis=0)
    extent = hi - lo
    span = float(extent.max(initial=0.0)) or 1.0
    flat = np.flatnonzero(extent <= 1e-6 * span)
    if len(flat) == 1:
        axis = "xyz"[int(flat[0])]
        return f"the plane {axis} = {0.5 * float(lo[flat[0]] + hi[flat[0]]):g} mm"
    return (
        f"a plane that is not parallel to z = 0 (its z runs from {float(lo[2]):g} to "
        f"{float(hi[2]):g} mm)"
    )


def _load_shell_surface(gmsh, path: Path, file_format: str) -> None:
    """`_load_surface` without the plane-model assumption.

    The difference is the solid case. A plane model refuses a solid because
    meshing its boundary yields a closed surface in three dimensions, which is
    not a cross-section. A shell model refuses it for the opposite reason: the
    boundary of a solid *is* a surface this could mesh, and meshing it would
    silently substitute a hollow shell of the given thickness for the solid body
    the file describes — a different structure, stiffer or softer than the real
    one depending on the thickness, and one that would solve and report numbers.
    So the solid is refused by name here rather than idealised on the caller's
    behalf; turning a solid into a mid-surface is a modelling decision.
    """
    try:
        gmsh.merge(str(path))
    except Exception as exc:
        raise MeshError(f"Gmsh could not read the file: {exc}") from exc

    if file_format == "stl":
        _build_surfaces_from_triangles(gmsh)
    elif gmsh.model.getEntities(3):
        raise MeshError(
            "The file contains a solid body. A shell model is meshed from the "
            "mid-surface it idealises, so export that surface, or run a solid "
            "analysis instead. Meshing the boundary of the solid would give a "
            "hollow shell of your chosen thickness rather than the body in the file."
        )

    if not gmsh.model.getEntities(2):
        raise MeshError(
            "The file contains no surface to mesh. A shell model needs a face; a file "
            "holding only points and curves has nothing to cover with elements."
        )


def _extract_shell(gmsh, element_order: int, face_shape: str) -> ShellMesh:
    """The shell counterpart of `_extract_tri`, plus the mixed-mesh guard.

    **The guard is the reason this is not a copy of `_extract_tri`.** Asking
    gmsh to recombine is a request, not a guarantee: on an awkward surface it
    returns quadrilaterals where it managed and triangles where it did not.
    `_extract_tri` can loop past an unwanted element type safely because nothing
    it meshes produces one, but here the unwanted type is *part of the same
    surface* — so taking only the wanted rows would hand back a mesh with holes
    in it, covering less area than the face, and every downstream reading
    (`area_mm2`, the tributary area a load is spread over, the mass) would be
    quietly short. `ShellMesh` refuses to mix the two shapes; this refuses to
    silently drop one.
    """
    node_tags, coordinates, _ = gmsh.model.mesh.getNodes()
    if len(node_tags) == 0:
        raise MeshError("Meshing produced no nodes")
    nodes = np.asarray(coordinates, dtype=np.float64).reshape(-1, 3)

    # Gmsh tags are 1-based and may have gaps; remap them to dense indices.
    tags = np.asarray(node_tags, dtype=np.int64)
    lookup = np.full(int(tags.max()) + 1, -1, dtype=np.int64)
    lookup[tags] = np.arange(len(tags), dtype=np.int64)

    wanted = _SHELL_ELEMENT_TYPE[face_shape][element_order]
    element_types, _, element_nodes = gmsh.model.mesh.getElements(2)
    found: dict[int, np.ndarray] = {}
    unknown: dict[int, int] = {}
    for element_type, connectivity in zip(element_types, element_nodes, strict=True):
        if len(connectivity) == 0:
            continue
        width = _NODES_PER_SHELL_FACE.get(int(element_type))
        if width is None:
            # Counted rather than skipped, for the same reason the mixed-type
            # guard below refuses rather than trims: an element this function
            # does not recognise is still area it would drop on the floor.
            unknown[int(element_type)] = len(connectivity)
            continue
        rows = np.asarray(connectivity, dtype=np.int64).reshape(-1, width)
        # Appended, not assigned. Measured on gmsh 4.15.2, `getElements(2)` with
        # the default tag returns one block per element *type* aggregated over
        # every surface, so today there is exactly one block each and a plain
        # assignment would be correct. It is written this way so that a gmsh
        # that ever grouped per entity would merge the blocks instead of
        # silently keeping only the last surface's elements.
        previous = found.get(int(element_type))
        found[int(element_type)] = rows if previous is None else np.vstack([previous, rows])

    if unknown:
        listed = ", ".join(f"{count} of gmsh type {kind}" for kind, count in sorted(unknown.items()))
        raise MeshError(
            f"Meshing produced element types this mesher does not recognise ({listed}). "
            "Keeping only the ones it knows would return a surface with holes in it that "
            "still reports a plausible area."
        )

    if wanted not in found:
        # Name what *did* come back rather than guessing why. The 9-node
        # quadrilateral is the one that actually turns up here: it is what gmsh
        # writes when `Mesh.SecondOrderIncomplete` is off, and diagnosing that
        # as "recombination failed, try triangles" sends the reader to the
        # opposite end of the function from the cause.
        instead = ", ".join(
            f"{len(rows)} {_SHELL_ELEMENT_NAME[kind]}s" for kind, rows in sorted(found.items())
        )
        raise MeshError(
            f"Meshing produced no {_SHELL_ELEMENT_NAME[wanted]}s"
            + (f", but did produce {instead}. " if instead else ". ")
            + (
                "Gmsh recombined nothing on this surface; try face_shape='tri'."
                if face_shape == "quad" and not instead
                else "The geometry may not contain a face."
            )
        )

    others = {kind: len(rows) for kind, rows in found.items() if kind != wanted}
    if others:
        listed = ", ".join(
            f"{count} {_SHELL_ELEMENT_NAME[kind]}s" for kind, count in sorted(others.items())
        )
        raise MeshError(
            f"Meshing produced {len(found[wanted])} {_SHELL_ELEMENT_NAME[wanted]}s and "
            f"also {listed}. A shell mesh carries one element type, so keeping only the "
            "first would return a surface with holes in it that still reports a plausible "
            "area. Re-mesh with face_shape='tri', or use a smaller element_size_mm so "
            "recombination succeeds everywhere."
        )

    elements = lookup[found[wanted]]
    if (elements < 0).any():
        raise MeshError("Meshing produced an element referencing an unknown node")

    corners = 3 if face_shape == "tri" else 4
    used, inverse = np.unique(elements, return_inverse=True)
    renumbered = inverse.reshape(elements.shape).astype(np.int64)
    mesh = ShellMesh(
        nodes=nodes[used],
        faces=renumbered[:, :corners],
        midside=renumbered[:, corners:] if element_order == 2 else None,
    )
    if element_order == 2:
        _assert_shell_midside_ordering(mesh)
    return mesh


def _assert_shell_midside_ordering(mesh: ShellMesh) -> None:
    """Confirm gmsh's midside order matches `ShellMesh.edge_table`.

    **`app/mesh/structural.py` predicted this would need a permutation and it
    does not, which is a measurement rather than a reprieve.** That module
    adopted *CalculiX's* midside order rather than gmsh's, on the grounds that
    there was no producer to disagree with, and named the debt: a future gmsh
    shell mesher must permute at the boundary. Measured against gmsh 4.15.2 the
    permutation is the identity for both shapes — gmsh's 6-node triangle numbers
    its midside nodes 0-1, 1-2, 0-2 against CalculiX's 0-1, 1-2, 2-0, and those
    are the same three node *pairs* written with the last one wound the other
    way, which selects the same node. The 8-node quadrilateral agrees outright.

    So the permutation stays absent because it is checked here every time, not
    because the two were assumed to agree. This assertion is what makes that
    difference real: `Mesh.SecondOrderLinear` puts each midside node exactly
    halfway along its edge, so a disagreement is visible as a coordinate, and a
    gmsh release that renumbered would fail here rather than return a
    plausible, wrong stiffness matrix.
    """
    assert mesh.midside is not None
    corners = mesh.nodes[mesh.faces]
    expected = np.stack(
        [0.5 * (corners[:, a] + corners[:, b]) for a, b in mesh.edge_table], axis=1
    )
    actual = mesh.nodes[mesh.midside]
    lo, hi = mesh.bounding_box
    scale = float(np.linalg.norm(hi - lo)) or 1.0
    # `rtol=0` deliberately. `np.allclose`'s default `rtol=1e-5` is relative to
    # the absolute *coordinate*, not to the element, so on a part authored far
    # from the origin -- a small component exported in assembly coordinates, the
    # ordinary CATIA case -- the tolerance grows with the part's position and
    # swallows the very swap this exists to catch. Measured: a 1 mm element with
    # midside slots 1 and 2 deliberately exchanged is detected at x = 0 and at
    # x = 1e3, and passes silently at x = 1e5. The `atol` term is the real
    # budget and has four orders of headroom over a midside offset of about
    # `diagonal / 20`.
    if not np.allclose(actual, expected, rtol=0.0, atol=1e-6 * scale):
        raise MeshError(
            f"Gmsh returned {mesh.element_type} nodes in an order that does not match "
            "this codebase's shell edge table, so the element CalculiX is handed would "
            "have two of its midside nodes swapped. The deck would solve and the answer "
            "would be wrong."
        )


def _assert_tri_midside_ordering(mesh: TriMesh) -> None:
    """Confirm gmsh's midside node order still matches `TRI6_EDGES`.

    The same failure mode as `_assert_midside_ordering` and the same argument:
    the plane shape functions are written against that table, and a silent
    disagreement returns a plausible, wrong stiffness matrix rather than an
    error. `Mesh.SecondOrderLinear` puts each midside node exactly halfway along
    its edge, so the check is a coordinate comparison.
    """
    assert mesh.midside is not None
    corners = mesh.nodes[mesh.tris]
    expected = np.stack([0.5 * (corners[:, a] + corners[:, b]) for a, b in TRI6_EDGES], axis=1)
    actual = mesh.nodes[mesh.midside]
    lo, hi = mesh.bounding_box
    scale = float(np.linalg.norm(hi - lo)) or 1.0
    if not np.allclose(actual, expected, atol=1e-6 * scale):
        raise MeshError(
            "Gmsh returned tri6 nodes in an unexpected order; the quadratic plane "
            "element formulation cannot be trusted against this mesh."
        )
