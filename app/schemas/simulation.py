from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.simulation import JobStatus
from app.solve.conduction import ThermalCase
from app.solve.types import LoadCase, Material


class SimulationCreate(BaseModel):
    geometry_version: int | None = Field(
        default=None,
        description="Geometry version to analyse. Defaults to the project's latest.",
    )
    load_case: LoadCase | None = Field(
        default=None,
        description=(
            "Fixtures, loads and material. Required for every analysis except "
            "'thermal-conduction', which has none of the three — it takes a "
            "thermal_case instead."
        ),
    )
    thermal_case: ThermalCase | None = Field(
        default=None,
        description=(
            "Conductivity, boundary conditions and any volumetric source, for a "
            "'thermal-conduction' run. It is a sibling of load_case rather than part "
            "of it: a steady conduction solve reads no fixture, no force and no "
            "modulus, and the answer is a temperature field rather than a stress."
        ),
    )
    element_size_mm: float | None = Field(
        default=None, gt=0, description="Target element size. Defaults to an automatic size."
    )
    element_order: Literal[1, 2] = Field(
        default=2,
        description=(
            "2 for quadratic tets (tet10, the default), 1 for linear (tet4). "
            "Quadratic elements are far more accurate in bending at the same "
            "element count, at roughly 2.5x the degrees of freedom and solve "
            "time. Drop to 1 only for a quick shape check on a chunky part."
        ),
    )
    """
    **Defaulted to 2 on 2026-09-11, changed from 1, and it is a defect fix rather
    than a preference.**

    Measured through the GUI on the Windows seat: a 200 x 40 x 10 mm mild steel
    cantilever, 300 N on the free end, solved three times from the identical
    request on the default mesh. Beam theory gives 90.0 MPa and 1.171 mm.

        order   elements   min quality   peak stress        tip deflection
        tet4         808         0.062   140.6 MPa 1.56x    0.314 mm  0.27x
        tet4         809         0.490    50.4 MPa 0.56x    0.321 mm  0.27x
        tet4         829         0.419    68.8 MPa 0.76x    0.331 mm  0.28x
        tet10       3686         0.416    77.8 MPa 0.86x    1.145 mm  0.98x
        tet10      10824         0.387    73.6 MPa 0.82x    1.129 mm  0.96x

    Linear tets got the **deflection wrong by a factor of 3.6, systematically**,
    and scattered the peak stress by **2.8x across three identical runs** — 50
    to 141 MPa, all three reported to the user as a verdict against a stated 150
    MPa limit. Quadratic lands within 2-4% of beam theory on the quantity that
    converges and is stable on the one that does not.

    Slivers are not the explanation: the 50.4 MPa run had none and a minimum
    quality of 0.49. Linear tetrahedra are simply too stiff in bending, which
    the mesher's own docstring has said all along.

    **The argument that settles it is that this product already knew.** Every
    NAFEMS benchmark in `app/verify/nafems.py` passes `element_order=2`
    explicitly — so Kryova validated itself with quadratic elements and served
    customers linear ones. A verification product cannot ship a default whose
    answers it would not accept from itself.

    The cost is real and is stated rather than hidden: roughly 2.5x the degrees
    of freedom and solve time. `MAX_ELEMENTS` still bounds the mesh, and a
    caller who wants the cheap answer can still ask for it.
    """
    analysis: Literal["solid", "plane-stress", "plane-strain", "thermal-conduction"] = Field(
        default="solid",
        description=(
            "Which idealisation to solve. 'solid' meshes the body with tetrahedra. "
            "The two plane options need a planar face in the z = 0 plane and a "
            "thickness_mm; they solve in seconds where the solid takes minutes, and "
            "they are different physics rather than a cheaper approximation — plane "
            "stress lets the material contract through the thickness (a flat plate "
            "loaded in its own plane), plane strain holds it (a slice of something "
            "long). 'thermal-conduction' solves for a steady temperature field "
            "instead of a displacement: it takes a thermal_case and no load_case, "
            "and it answers 'how hot does it get', never 'how long until' — there "
            "is no time integration behind it."
        ),
    )
    thickness_mm: float | None = Field(
        default=None,
        gt=0,
        description=(
            "Out-of-plane thickness for a plane analysis, in mm. Required for "
            "'plane-stress' and 'plane-strain' and refused for 'solid', whose "
            "geometry already carries a thickness."
        ),
    )

    grids: int = Field(
        default=1,
        ge=1,
        le=5,
        description=(
            "How many successively finer meshes to solve. 1 is a single run. 3 or more "
            "makes it a convergence study: the same case is solved on each grid and the "
            "peak stress is assessed with a Grid Convergence Index, so the result can "
            "say how far the answer would move on a finer mesh. The size you give is "
            "treated as the coarsest grid, so a study costs more time than a single run "
            "and never more memory. Capped at 5 because the finest grid costs about "
            "1.4^(3*(grids-1)) times the coarsest — 5 is already 64x."
        ),
    )

    @model_validator(mode="after")
    def _two_grids_cannot_form_a_study(self) -> "SimulationCreate":
        """Two grids is the shape that looks like a study and is not one.

        A Grid Convergence Index needs three: two grids give a difference, and a
        difference alone cannot separate "the answer moved a little because it
        is nearly converged" from "the answer moved a little because these two
        meshes happen to be similar". Refused by name rather than silently
        promoted to three, because the caller asked for a specific amount of
        work and should be told it does not buy the thing they wanted.
        """
        if self.grids == 2:
            raise ValueError(
                "A convergence study needs at least three grids: two give a difference "
                "and no way to tell a converging answer from a coincidence. Ask for 1 "
                "(a single run) or 3 or more."
            )
        return self

    @model_validator(mode="after")
    def _the_case_matches_the_analysis(self) -> "SimulationCreate":
        """Exactly one case, and it is the one the analysis can read.

        Refused at the boundary rather than defaulted deeper in, for
        `thickness_mm`'s reason one line down. A conduction run handed a
        `load_case` would silently ignore a material and a set of fixtures the
        engineer chose; a structural run handed a `thermal_case` would ignore a
        conductivity and every boundary condition in it. Both are the shape of
        mistake that produces a plausible answer to a question nobody asked.
        """
        if self.analysis == "thermal-conduction":
            if self.thermal_case is None:
                raise ValueError(
                    "A thermal-conduction analysis needs a thermal_case: the "
                    "conductivity and the boundary conditions are the whole of what "
                    "it solves."
                )
            if self.load_case is not None:
                raise ValueError(
                    "A thermal-conduction analysis takes no load_case. It reads no "
                    "fixture, no force and no modulus, so one supplied here would be "
                    "ignored while looking like part of the model. Ask for a "
                    "structural analysis if the loads are what you want solved."
                )
            return self
        if self.thermal_case is not None:
            raise ValueError(
                f"A {self.analysis} analysis takes no thermal_case; it solves for "
                "displacement and stress, not temperature. Set analysis to "
                "'thermal-conduction', or use load_case.delta_t_k for a uniform "
                "temperature change applied to a structural run."
            )
        if self.load_case is None:
            raise ValueError(
                f"A {self.analysis} analysis needs a load_case: the fixtures, the "
                "loads and the material are what it solves."
            )
        return self

    @model_validator(mode="after")
    def _thickness_matches_the_analysis(self) -> "SimulationCreate":
        """Refused at the boundary rather than defaulted deeper in.

        Every stress in a plane run scales with the thickness, so a thickness
        nobody chose is a whole answer nobody chose. And a thickness supplied
        alongside `solid` is a misunderstanding worth naming: it would be
        silently ignored, and the engineer would believe it had been used.
        """
        if self.analysis in ("solid", "thermal-conduction"):
            if self.thickness_mm is not None:
                raise ValueError(
                    "thickness_mm applies only to a plane analysis; a solid takes its "
                    "thickness from the geometry. Drop it, or set analysis to "
                    "'plane-stress' or 'plane-strain'."
                )
        elif self.thickness_mm is None:
            raise ValueError(
                f"A {self.analysis} analysis needs thickness_mm: every stress it "
                "reports scales with the out-of-plane thickness, so it is asked for "
                "rather than assumed."
            )
        return self


class SimulationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    geometry_version_id: str
    status: JobStatus
    solver: str
    load_case: dict[str, Any] | None
    thermal_case: dict[str, Any] | None
    element_size_mm: float | None
    element_order: int
    grids: int
    analysis: str
    thickness_mm: float | None
    mesh_stats: dict[str, Any] | None
    #: Where a running job has got to (P5 task 2): `{stage, detail, index,
    #: total, at}`. `None` until the run reports, and on any terminal job, where
    #: the status already says what happened. It carries no percentage inside a
    #: stage on purpose — see `app/simulation/progress.py`.
    progress: dict[str, Any] | None = None
    result: dict[str, Any] | None
    fields_media_id: str | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class SurfaceField(BaseModel):
    """The deformable surface and its stress values, sized for a 3D viewer.

    Only boundary nodes and triangles are sent: the interior of a volume mesh is
    never drawn, and shipping it would multiply the payload for nothing.
    """

    node_positions: list[list[float]]
    triangles: list[list[int]]
    displacements: list[list[float]]
    von_mises_mpa: list[float]
    max_von_mises_mpa: float
    max_displacement_mm: float


class MaterialList(BaseModel):
    materials: list[Material]
