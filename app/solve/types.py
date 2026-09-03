"""Load case definition and result types.

Units are the self-consistent mm-N-MPa system: lengths in mm, forces in N,
moduli and stresses in MPa. Displacements come back in mm.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Material(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    youngs_modulus_mpa: float = Field(gt=0)
    poissons_ratio: float = Field(gt=-1.0, lt=0.5)
    yield_strength_mpa: float = Field(gt=0)
    density_kg_m3: float = Field(gt=0)
    #: Coefficient of linear thermal expansion, per kelvin. Optional because a
    #: static or modal run never touches it, and a caller supplying a custom
    #: material for a stress check should not have to look one up; the thermal
    #: solver refuses by name when it is missing rather than assuming a value.
    #: Per kelvin, not per micro-kelvin: 23.6e-6 for aluminium, not 23.6.
    thermal_expansion_per_k: float | None = Field(default=None, gt=0, lt=1e-2)


class FaceSelector(BaseModel):
    """The extreme face of the part along one axis, e.g. 'the bottom'."""

    type: Literal["face"] = "face"
    axis: Literal["x", "y", "z"]
    side: Literal["min", "max"]
    tolerance: float = Field(
        default=0.01, gt=0, le=1.0, description="Band depth as a fraction of the bbox size"
    )


class BoxSelector(BaseModel):
    """Every node inside an axis-aligned box, in mm."""

    type: Literal["box"] = "box"
    min: tuple[float, float, float]
    max: tuple[float, float, float]


Selector = Annotated[FaceSelector | BoxSelector, Field(discriminator="type")]


class Fixture(BaseModel):
    """A restrained region.

    By default all three translations are held (an encastre clamp). Restraining
    a subset makes a roller or a symmetry plane -- e.g. `dofs=["z"]` on a flat
    face lets the part slide in that plane while holding it out of plane, which
    is how a real support usually behaves and how symmetry is exploited to cut
    a model down.
    """

    where: Selector
    dofs: list[Literal["x", "y", "z"]] = Field(default=["x", "y", "z"], min_length=1)
    name: str | None = None


class Load(BaseModel):
    """A force spread over a region, given as a total force vector in N.

    The total is distributed over the selected surface by tributary area, so
    refining the mesh does not change the applied load.
    """

    where: Selector
    force_n: tuple[float, float, float]
    name: str | None = None


class LoadCase(BaseModel):
    name: str = "Load case"
    material: Material
    fixtures: list[Fixture] = Field(min_length=1)
    loads: list[Load] = Field(min_length=1)
    #: Uniform temperature change over the whole part, in kelvin. None is the
    #: isothermal case and costs nothing -- no thermal term is assembled. A
    #: value needs `thermal_expansion_per_k` on the material, and the solver
    #: says so by name rather than assuming one. See `app/solve/thermal.py`.
    delta_t_k: float | None = Field(default=None, ge=-2000.0, le=5000.0)


class ModalCase(BaseModel):
    """What to solve for a natural-frequency run.

    No loads, deliberately. A modal analysis asks what the structure does when
    nothing pushes it, so a `LoadCase` with its mandatory loads is the wrong
    shape -- passing one would invite the caller to believe the forces
    influenced the answer.

    Fixtures are optional here, and that is also deliberate. A free-free modal
    run is a real and useful analysis: it is how you check a part in isolation,
    and its first six modes come out at zero because they are the rigid-body
    motions. `LoadCase` requires at least one fixture because a static solve
    without one is singular; this one does not.
    """

    name: str = "Modal analysis"
    material: Material
    fixtures: list[Fixture] = Field(default_factory=list)
    modes: int = Field(default=6, ge=1, le=100)


class ModalResult(BaseModel):
    """Summary of a modal run. Mode shapes are large and stay out of the DB."""

    frequencies_hz: list[float]
    rigid_body_modes: int
    mass_kg: float
    volume_mm3: float
    node_count: int
    element_count: int
    solve_seconds: float
    warnings: list[str] = Field(default_factory=list)

    @property
    def fundamental_hz(self) -> float | None:
        """The lowest non-rigid frequency -- the one an engineer means by "the"
        natural frequency. None when every mode found was a rigid-body one."""
        elastic = self.frequencies_hz[self.rigid_body_modes :]
        return elastic[0] if elastic else None

    def summary(self) -> dict[str, Any]:
        return self.model_dump() | {"fundamental_hz": self.fundamental_hz}


class BucklingCase(BaseModel):
    """What to solve for a linear buckling run.

    Loads are required and fixtures are required, unlike `ModalCase`: buckling
    is a property of a structure *under load*, and the answer is a multiplier on
    the loads given here. Halve the load and the factor doubles; the buckling
    shape does not change.
    """

    name: str = "Buckling"
    material: Material
    fixtures: list[Fixture] = Field(min_length=1)
    loads: list[Load] = Field(min_length=1)
    modes: int = Field(default=3, ge=1, le=50)


class BucklingResult(BaseModel):
    """Summary of a linear buckling run. Mode shapes stay out of the DB."""

    load_factors: list[float]
    mass_kg: float
    volume_mm3: float
    node_count: int
    element_count: int
    solve_seconds: float
    warnings: list[str] = Field(default_factory=list)

    @property
    def critical_load_factor(self) -> float | None:
        """Multiply the applied loads by this to reach the buckling load.

        Below 1 means the structure buckles under the load as given. None when
        no positive factor was found, which means it does not buckle under this
        load in this direction -- reverse the load and ask again.
        """
        positive = [factor for factor in self.load_factors if factor > 0.0]
        return positive[0] if positive else None

    def summary(self) -> dict[str, Any]:
        return self.model_dump() | {"critical_load_factor": self.critical_load_factor}


class StaticResult(BaseModel):
    """Summary of a linear static run. Full fields stay out of the DB."""

    max_displacement_mm: float
    max_displacement_node: int
    max_von_mises_mpa: float
    max_von_mises_element: int
    factor_of_safety: float
    yields: bool
    mass_kg: float
    volume_mm3: float
    node_count: int
    element_count: int
    solve_seconds: float
    warnings: list[str] = Field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return self.model_dump()


class SolverError(RuntimeError):
    """The model could not be solved as posed."""
