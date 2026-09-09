from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.simulation import JobStatus
from app.solve.types import LoadCase, Material


class SimulationCreate(BaseModel):
    geometry_version: int | None = Field(
        default=None,
        description="Geometry version to analyse. Defaults to the project's latest.",
    )
    load_case: LoadCase
    element_size_mm: float | None = Field(
        default=None, gt=0, description="Target element size. Defaults to an automatic size."
    )
    element_order: Literal[1, 2] = Field(
        default=1,
        description=(
            "1 for linear tets, 2 for quadratic (tet10). Quadratic elements are "
            "far more accurate in bending at the same element count, at roughly "
            "2.5x the degrees of freedom and solve time."
        ),
    )
    analysis: Literal["solid", "plane-stress", "plane-strain"] = Field(
        default="solid",
        description=(
            "Which idealisation to solve. 'solid' meshes the body with tetrahedra. "
            "The two plane options need a planar face in the z = 0 plane and a "
            "thickness_mm; they solve in seconds where the solid takes minutes, and "
            "they are different physics rather than a cheaper approximation — plane "
            "stress lets the material contract through the thickness (a flat plate "
            "loaded in its own plane), plane strain holds it (a slice of something "
            "long)."
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
    def _thickness_matches_the_analysis(self) -> "SimulationCreate":
        """Refused at the boundary rather than defaulted deeper in.

        Every stress in a plane run scales with the thickness, so a thickness
        nobody chose is a whole answer nobody chose. And a thickness supplied
        alongside `solid` is a misunderstanding worth naming: it would be
        silently ignored, and the engineer would believe it had been used.
        """
        if self.analysis == "solid":
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
    load_case: dict[str, Any]
    element_size_mm: float | None
    element_order: int
    grids: int
    analysis: str
    thickness_mm: float | None
    mesh_stats: dict[str, Any] | None
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
