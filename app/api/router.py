from fastapi import APIRouter

from app.api.routes import (
    ai,
    auth,
    catia,
    geometry,
    kernel,
    materials,
    media,
    projects,
    simulations,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(projects.router)
api_router.include_router(geometry.router)
api_router.include_router(simulations.router)
api_router.include_router(media.router)
api_router.include_router(materials.router)
api_router.include_router(ai.router)
api_router.include_router(catia.router)
api_router.include_router(kernel.router)
