"""A minimal but genuinely valid AP214 STEP writer for a rectangular solid.

The mock backend has to produce a *real* STEP file, not a placeholder with the
right first line. The whole point of mock mode is that the rest of the system --
the blob store, `geometry.inspect`, gmsh's OpenCASCADE importer, the mesher, the
solver, the viewer -- runs unmodified on a Linux machine with no CATIA anywhere.
A file that only satisfies the extension check would stop that chain at the
mesher, which is precisely where the interesting bugs are.

So this emits a closed manifold B-rep: eight vertices, twelve edges, six planar
faces, in a `MANIFOLD_SOLID_BREP` inside an `ADVANCED_BREP_SHAPE_REPRESENTATION`
with a millimetre unit context. OpenCASCADE reads it, gmsh meshes it, and the
solver solves it.

It is deliberately only a box. A general B-rep writer is a project in itself,
and the mock's job is to be *faithful to the protocol*, not to be a modelling
kernel -- the geometry that matters is produced by real CATIA on Windows.
"""

from datetime import datetime, timezone


class _Entities:
    """Accumulates `#n = ENTITY(...)` lines and hands out ids."""

    def __init__(self) -> None:
        self._lines: list[str] = []
        self._next = 1

    def add(self, body: str) -> int:
        ref = self._next
        self._next += 1
        self._lines.append(f"#{ref} = {body};")
        return ref

    def render(self) -> str:
        return "\n".join(self._lines)


def _f(value: float) -> str:
    """STEP reals must carry a decimal point; `1` is a syntax error where `1.` is not."""
    text = repr(float(value))
    return text if ("." in text or "e" in text or "E" in text) else text + "."


def write_box_step(
    *,
    size_mm: tuple[float, float, float],
    origin_mm: tuple[float, float, float] = (0.0, 0.0, 0.0),
    part_name: str = "Part",
) -> str:
    """A complete STEP AP214 document containing one axis-aligned box."""
    ent = _Entities()
    width, depth, height = (max(float(v), 1e-3) for v in size_mm)
    x0, y0, z0 = origin_mm

    # -- direction and placement primitives ---------------------------------
    d_x = ent.add("DIRECTION('',(1.,0.,0.))")
    d_y = ent.add("DIRECTION('',(0.,1.,0.))")
    d_z = ent.add("DIRECTION('',(0.,0.,1.))")
    d_nx = ent.add("DIRECTION('',(-1.,0.,0.))")
    d_ny = ent.add("DIRECTION('',(0.,-1.,0.))")
    d_nz = ent.add("DIRECTION('',(0.,0.,-1.))")
    axis_dir = {"+x": d_x, "-x": d_nx, "+y": d_y, "-y": d_ny, "+z": d_z, "-z": d_nz}

    def point(x: float, y: float, z: float) -> int:
        return ent.add(f"CARTESIAN_POINT('',({_f(x)},{_f(y)},{_f(z)}))")

    # -- the eight corners, indexed by (i, j, k) in 0/1 ----------------------
    corner: dict[tuple[int, int, int], int] = {}
    vertex: dict[tuple[int, int, int], int] = {}
    for i in (0, 1):
        for j in (0, 1):
            for k in (0, 1):
                pid = point(x0 + i * width, y0 + j * depth, z0 + k * height)
                corner[(i, j, k)] = pid
                vertex[(i, j, k)] = ent.add(f"VERTEX_POINT('',#{pid})")

    # -- the twelve edges ----------------------------------------------------
    edges: dict[tuple[tuple[int, int, int], tuple[int, int, int]], int] = {}

    def edge(a: tuple[int, int, int], b: tuple[int, int, int]) -> int:
        """An EDGE_CURVE from a to b, created once and reused in both faces."""
        if (a, b) in edges:
            return edges[(a, b)]
        axis = [n for n in range(3) if a[n] != b[n]]
        direction = axis_dir[("+" if b[axis[0]] > a[axis[0]] else "-") + "xyz"[axis[0]]]
        vector = ent.add(f"VECTOR('',#{direction},1.)")
        line = ent.add(f"LINE('',#{corner[a]},#{vector})")
        curve = ent.add(f"EDGE_CURVE('',#{vertex[a]},#{vertex[b]},#{line},.T.)")
        edges[(a, b)] = curve
        return curve

    def oriented(a: tuple[int, int, int], b: tuple[int, int, int]) -> int:
        """One use of an edge in a loop, with the sense it is traversed in.

        Reusing the same EDGE_CURVE with `.F.` for the reverse direction is what
        makes the shell topologically closed -- two faces sharing an edge must
        share the *entity*, not merely its coordinates, or OpenCASCADE stitches
        nothing and reports an open shell.
        """
        if (a, b) in edges:
            return ent.add(f"ORIENTED_EDGE('',*,*,#{edges[(a, b)]},.T.)")
        if (b, a) in edges:
            return ent.add(f"ORIENTED_EDGE('',*,*,#{edges[(b, a)]},.F.)")
        return ent.add(f"ORIENTED_EDGE('',*,*,#{edge(a, b)},.T.)")

    # -- the six faces, each a loop of four corners, wound outward ----------
    faces: list[int] = []
    face_specs: list[tuple[str, tuple[int, int, int], list[tuple[int, int, int]]]] = [
        # (outward normal, corner the plane sits on, loop in outward-CCW order)
        ("-z", (0, 0, 0), [(0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 0, 0)]),
        ("+z", (0, 0, 1), [(0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]),
        ("-y", (0, 0, 0), [(0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)]),
        ("+y", (0, 1, 0), [(0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0)]),
        ("-x", (0, 0, 0), [(0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0)]),
        ("+x", (1, 0, 0), [(1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1)]),
    ]
    for normal, anchor, loop in face_specs:
        # A reference direction perpendicular to the normal; any will do, and
        # picking it by axis avoids a degenerate placement.
        reference = "+x" if normal[1] != "x" else "+y"
        placement = ent.add(
            f"AXIS2_PLACEMENT_3D('',#{corner[anchor]},#{axis_dir[normal]},#{axis_dir[reference]})"
        )
        plane = ent.add(f"PLANE('',#{placement})")
        oriented_edges = [oriented(loop[n], loop[(n + 1) % len(loop)]) for n in range(len(loop))]
        edge_loop = ent.add("EDGE_LOOP('',(" + ",".join(f"#{e}" for e in oriented_edges) + "))")
        bound = ent.add(f"FACE_OUTER_BOUND('',#{edge_loop},.T.)")
        faces.append(ent.add(f"ADVANCED_FACE('',(#{bound}),#{plane},.T.)"))

    shell = ent.add("CLOSED_SHELL('',(" + ",".join(f"#{f}" for f in faces) + "))")
    origin = point(x0, y0, z0)
    solid_placement = ent.add(f"AXIS2_PLACEMENT_3D('',#{origin},#{d_z},#{d_x})")
    brep = ent.add(f"MANIFOLD_SOLID_BREP('{_escape(part_name)}',#{shell})")

    # -- units and the representation context -------------------------------
    length_unit = ent.add("( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.) )")
    angle_unit = ent.add("( NAMED_UNIT(*) PLANE_ANGLE_UNIT() SI_UNIT($,.RADIAN.) )")
    solid_unit = ent.add("( NAMED_UNIT(*) SI_UNIT($,.STERADIAN.) SOLID_ANGLE_UNIT() )")
    uncertainty = ent.add(
        f"UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-07),#{length_unit},"
        "'distance_accuracy_value','confusion accuracy')"
    )
    context = ent.add(
        "( GEOMETRIC_REPRESENTATION_CONTEXT(3) "
        f"GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT((#{uncertainty})) "
        f"GLOBAL_UNIT_ASSIGNED_CONTEXT((#{length_unit},#{angle_unit},#{solid_unit})) "
        "REPRESENTATION_CONTEXT('Context','3D') )"
    )
    shape = ent.add(
        f"ADVANCED_BREP_SHAPE_REPRESENTATION('',(#{solid_placement},#{brep}),#{context})"
    )

    # -- product structure ---------------------------------------------------
    app_context = ent.add("APPLICATION_CONTEXT('automotive design')")
    ent.add(
        "APPLICATION_PROTOCOL_DEFINITION('international standard',"
        f"'automotive_design',2000,#{app_context})"
    )
    product_context = ent.add(f"PRODUCT_CONTEXT('',#{app_context},'mechanical')")
    safe_name = _escape(part_name)
    product = ent.add(f"PRODUCT('{safe_name}','{safe_name}','',(#{product_context}))")
    formation = ent.add(f"PRODUCT_DEFINITION_FORMATION('','',#{product})")
    definition_context = ent.add(
        f"PRODUCT_DEFINITION_CONTEXT('part definition',#{app_context},'design')"
    )
    definition = ent.add(f"PRODUCT_DEFINITION('design','',#{formation},#{definition_context})")
    definition_shape = ent.add(f"PRODUCT_DEFINITION_SHAPE('','',#{definition})")
    ent.add(f"SHAPE_DEFINITION_REPRESENTATION(#{definition_shape},#{shape})")

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    return (
        "ISO-10303-21;\n"
        "HEADER;\n"
        "FILE_DESCRIPTION(('Kryova CATIA bridge export'),'2;1');\n"
        f"FILE_NAME('{safe_name}.step','{stamp}',('Kryova'),('Kryova'),"
        "'Kryova CATIA bridge','','');\n"
        "FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));\n"
        "ENDSEC;\n"
        "DATA;\n"
        f"{ent.render()}\n"
        "ENDSEC;\n"
        "END-ISO-10303-21;\n"
    )


def _escape(text: str) -> str:
    """STEP strings are single-quoted; a literal quote is doubled."""
    return "".join(c for c in text if c.isprintable()).replace("'", "''")[:80] or "Part"
