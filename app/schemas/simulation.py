from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from app.models.simulation import JobStatus
from app.solve.conduction import ThermalCase, TransientThermalCase
from app.solve.openfoam.case import FlowCase
from app.solve.types import LoadCase, Material
from app.verify.standards import NOT_VALIDATED

#: Which case each analysis reads. Everything absent reads a `load_case`.
CASE_FOR_ANALYSIS: dict[str, str] = {
    "thermal-conduction": "thermal_case",
    "thermal-transient": "transient_case",
    "flow-laminar": "flow_case",
}

_WHAT_IT_READS: dict[str, str] = {
    "load_case": "the fixtures, the loads and the material are what it solves.",
    "thermal_case": "the conductivity and the boundary conditions are the whole of what it solves.",
    "transient_case": (
        "the conductivity, the heat capacity, the boundary conditions, the starting "
        "temperature and the time span are the whole of what it solves."
    ),
    "flow_case": (
        "the fluid, the inlet and outlet faces, the cell size and any wall heat are the "
        "whole of what it solves."
    ),
}

_WHY_REFUSED: dict[str, str] = {
    "load_case": (
        "It reads no fixture, no force and no modulus, so one supplied here would be "
        "ignored while looking like part of the model."
    ),
    "thermal_case": (
        "It does not solve for a steady temperature field in the part, so a conductivity "
        "and boundary conditions supplied here would be ignored while looking like part "
        "of the model."
    ),
    "transient_case": (
        "It has no time axis, so a duration, a time step and a starting temperature "
        "supplied here would be ignored while looking like part of the model."
    ),
    "flow_case": (
        "It solves nothing about a fluid, so an inlet, an outlet and a fluid supplied "
        "here would be ignored while looking like part of the model."
    ),
}

_WHERE_IT_BELONGS: dict[str, str] = {
    "load_case": "Ask for a structural analysis if the loads are what you want solved.",
    "thermal_case": (
        "Set analysis to 'thermal-conduction', or use load_case.delta_t_k for a uniform "
        "temperature change applied to a structural run."
    ),
    "transient_case": "Set analysis to 'thermal-transient' to solve how the field evolves.",
    "flow_case": "Set analysis to 'flow-laminar' to solve the flow through the part.",
}


class TemperatureSource(BaseModel):
    """A finished thermal run whose temperature field a structural run is to carry.

    The coupling seam `LinearStaticSolver.solve(temperatures=)` has had since
    2026-09-09, made reachable from a request. The field is not copied onto this
    run: it is read from the thermal run's stored archive, and the digest of that
    archive is recorded on the row when the run is queued, so the provenance of
    every stress names the exact temperatures that produced it.
    """

    simulation_id: str
    #: For a transient source, which stored time sample; omitted means the final
    #: one. Refused on a steady source, which has no time axis.
    step: int | None = Field(default=None, ge=0)
    #: Absolute kelvin at which the part is unstrained. **Required, with no
    #: default**: every thermal stress is proportional to `T - T_ref`, so a
    #: reference nobody chose is a whole answer nobody chose.
    reference_temperature_k: float = Field(gt=0)


class SimulationCreate(BaseModel):
    geometry_version: int | None = Field(
        default=None,
        description="Geometry version to analyse. Defaults to the project's latest.",
    )
    load_case: LoadCase | None = Field(
        default=None,
        description=(
            "Fixtures, loads and material. Required for every analysis except the "
            "two thermal ones and the flow, which have none of the three — "
            "'thermal-conduction' takes a thermal_case, 'thermal-transient' a "
            "transient_case and 'flow-laminar' a flow_case instead."
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
    transient_case: TransientThermalCase | None = Field(
        default=None,
        description=(
            "Conductivity, density, specific heat, boundary conditions, a uniform "
            "starting temperature, a duration and a time step, for a "
            "'thermal-transient' run. Its own field rather than a looser "
            "thermal_case: a steady case handed a density and a duration would "
            "silently drop both, and the run would answer a question nobody asked."
        ),
    )
    flow_case: FlowCase | None = Field(
        default=None,
        description=(
            "A fluid, an inlet and an outlet named by face selector, a cell_size_mm and "
            "optionally the heat the flow carries, for a 'flow-laminar' run. The part's "
            "geometry is taken as the fluid region — a duct drawn as a solid — and every "
            "boundary face the inlet and outlet do not select is a no-slip wall."
        ),
    )
    temperature_from: TemperatureSource | None = Field(
        default=None,
        description=(
            "A finished thermal-conduction or thermal-transient run in this project "
            "whose temperature field this solid run carries as thermal expansion. "
            "It must have analysed the same geometry version with the same "
            "element_size_mm and element_order, because a field is bound to the mesh "
            "it was solved on; and the load_case must carry no uniform delta_t_k, "
            "which would count the expansion twice."
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
    analysis: Literal[
        "solid",
        "plane-stress",
        "plane-strain",
        "thermal-conduction",
        "thermal-transient",
        "flow-laminar",
    ] = Field(
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
            "is no time integration behind it. 'thermal-transient' is the one that "
            "answers 'how long until': it takes a transient_case and steps the "
            "temperature field forward in time with backward Euler. 'flow-laminar' "
            "takes the part as the inside of a duct and solves steady laminar flow "
            "through it in OpenFOAM — a pressure drop, and with heat the outlet "
            "temperature and the heat carried: it takes a flow_case, and refuses a "
            "Reynolds number above 2000 because there is no turbulence model."
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
        conductivity and every boundary condition in it; a steady run handed a
        `transient_case` would ignore a duration. Each is the shape of mistake
        that produces a plausible answer to a question nobody asked.
        """
        wanted = CASE_FOR_ANALYSIS.get(self.analysis, "load_case")
        supplied = {
            name
            for name in ("load_case", "thermal_case", "transient_case", "flow_case")
            if getattr(self, name) is not None
        }
        if wanted not in supplied:
            raise ValueError(
                f"A {self.analysis} analysis needs a {wanted}: {_WHAT_IT_READS[wanted]}"
            )
        for extra in sorted(supplied - {wanted}):
            raise ValueError(
                f"A {self.analysis} analysis takes no {extra}. {_WHY_REFUSED[extra]} "
                f"{_WHERE_IT_BELONGS[extra]}"
            )
        return self

    @model_validator(mode="after")
    def _a_temperature_field_goes_to_one_solid_grid(self) -> "SimulationCreate":
        """Where a borrowed temperature field can be honoured, and only there.

        A plane model is a cross-section and the field was solved on a solid; a
        convergence study remeshes at every grid and the field is bound to one
        mesh; a thermal run has no expansion to apply it to; and a case with its
        own `delta_t_k` would add a uniform change on top of the field.
        """
        if self.temperature_from is None:
            return self
        if self.analysis != "solid":
            raise ValueError(
                f"temperature_from applies only to a solid analysis; a {self.analysis} "
                "run cannot carry a temperature field solved on a tetrahedral mesh."
            )
        if self.grids != 1:
            raise ValueError(
                "temperature_from cannot be combined with a convergence study: every "
                "grid is a different mesh and the field is bound to the one it was "
                "solved on. Ask for one grid."
            )
        if self.load_case is not None and self.load_case.delta_t_k:
            raise ValueError(
                "This load_case carries a uniform delta_t_k and temperature_from names a "
                "temperature field. Adding them would count the expansion twice; drop "
                "delta_t_k and fold any uniform offset into the thermal run."
            )
        return self

    @model_validator(mode="after")
    def _a_flow_mesh_has_no_element_order(self) -> "SimulationCreate":
        """A flow run reads the part's boundary at its corners, so the order is moot.

        The tet mesh is only where the duct's closed surface comes from; OpenFOAM
        meshes the fluid itself, and a quadratic tet's midside nodes never reach
        it. So the order defaults to 1 on a flow run — the mesh that is actually
        used — and an explicit 2 is refused rather than recorded against a result
        it did not influence.
        """
        if self.analysis != "flow-laminar":
            return self
        if "element_order" in self.model_fields_set and self.element_order != 1:
            raise ValueError(
                "element_order does not apply to a flow-laminar run: OpenFOAM meshes the "
                "fluid itself and reads the part's surface at its corners, so a quadratic "
                "mesh would change nothing. Drop element_order, and set flow_case.cell_size_mm "
                "for the flow's resolution."
            )
        self.element_order = 1
        return self

    @model_validator(mode="after")
    def _thickness_matches_the_analysis(self) -> "SimulationCreate":
        """Refused at the boundary rather than defaulted deeper in.

        Every stress in a plane run scales with the thickness, so a thickness
        nobody chose is a whole answer nobody chose. And a thickness supplied
        alongside `solid` is a misunderstanding worth naming: it would be
        silently ignored, and the engineer would believe it had been used.
        """
        if self.analysis in ("solid", "thermal-conduction", "thermal-transient", "flow-laminar"):
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
    transient_case: dict[str, Any] | None = None
    flow_case: dict[str, Any] | None = None
    temperature_source: dict[str, Any] | None = None
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

    @computed_field  # type: ignore[prop-decorator]
    @property
    def validation(self) -> str:
        """That no number in `result` has been validated (master plan 20.3).

        Returned by the API rather than written by each client, the argument
        `attachments.UNVERIFIED_NOTE` makes: a statement with two wordings has
        two standards. Computed rather than a field so nothing can pass a
        different one in.
        """
        return NOT_VALIDATED


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


class SurfaceTemperature(BaseModel):
    """The temperature on a thermal run's surface, sized for a 3D viewer.

    A sibling of `SurfaceField` rather than a mode of it: a structural surface has
    displacements to deform by and a stress to colour by, and a temperature
    surface has neither. `time_s`, `step` and `step_count` are present on a
    transient run and `None` on a steady one, which has no time axis to index.
    """

    node_positions: list[list[float]]
    triangles: list[list[int]]
    temperatures_k: list[float]
    #: Over every node of the sample, interior included — see the route.
    min_temperature_k: float
    max_temperature_k: float
    time_s: float | None = None
    step: int | None = None
    step_count: int | None = None


class MaterialList(BaseModel):
    materials: list[Material]
