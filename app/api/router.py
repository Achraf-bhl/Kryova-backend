from fastapi import APIRouter

from app.api.routes import (
    admin,
    ai,
    auth,
    catia,
    geometry,
    kernel,
    materials,
    media,
    organisations,
    projects,
    simulations,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(admin.router)
api_router.include_router(admin.organisation_audit_router)
api_router.include_router(organisations.router)
api_router.include_router(projects.router)
api_router.include_router(geometry.router)
api_router.include_router(simulations.router)
api_router.include_router(media.router)
api_router.include_router(materials.router)
api_router.include_router(ai.router)
api_router.include_router(catia.router)
api_router.include_router(kernel.router)
