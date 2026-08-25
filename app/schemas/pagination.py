from typing import Generic, TypeVar

from pydantic import BaseModel, Field

from app.schemas.geometry import GeometryVersionRead
from app.schemas.project import ProjectRead
from app.schemas.simulation import SimulationRead

ItemT = TypeVar("ItemT")


class Page(BaseModel, Generic[ItemT]):
    total: int = Field(ge=0)
    page: int = Field(gt=0)
    page_size: int = Field(gt=0)
    items: list[ItemT]

ProjectPage = Page[ProjectRead]
GeometryVersionPage = Page[GeometryVersionRead]
SimulationPage = Page[SimulationRead]
