from fastapi import APIRouter, HTTPException, status

from app.schemas import MaterialList
from app.solve.materials import MATERIALS
from app.solve.types import Material

router = APIRouter(prefix="/materials", tags=["materials"])


@router.get("", response_model=MaterialList)
def list_materials() -> MaterialList:
    """The built-in material library. A load case may also carry its own values."""
    return MaterialList(materials=list(MATERIALS.values()))


@router.get("/{name}", response_model=Material)
def read_material(name: str) -> Material:
    material = MATERIALS.get(name)
    if material is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown material")
    return material
