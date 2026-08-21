from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

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


class SimulationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    geometry_version_id: str
    status: JobStatus
    solver: str
    load_case: dict[str, Any]
    element_size_mm: float | None
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
