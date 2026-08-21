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
