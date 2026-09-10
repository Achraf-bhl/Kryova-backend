from fastapi import APIRouter

from app.api.routes import (
    admin,
    ai,
    attachments,
    auth,
    billing,
    catia,
    designs,
    gates,
    geometry,
    handbook,
    kernel,
    materials,
    media,
    organisations,
    platform,
    projects,
    sharing,
    simulations,
    trust,
)

api_router = APIRouter()
api_router.include_router(auth.router)
# Readable signed out, and deliberately not behind `get_current_user`: it is
# the endpoint that *explains* a maintenance window, so it must not be one of
# the things a maintenance window refuses.
api_router.include_router(platform.router)
api_router.include_router(admin.router)
api_router.include_router(admin.organisation_audit_router)
api_router.include_router(organisations.router)
api_router.include_router(gates.router)
api_router.include_router(billing.router)
api_router.include_router(projects.router)
api_router.include_router(sharing.router)
api_router.include_router(geometry.router)
api_router.include_router(simulations.router)
api_router.include_router(media.router)
api_router.include_router(materials.router)
api_router.include_router(ai.router)
api_router.include_router(designs.router)
api_router.include_router(attachments.router)
api_router.include_router(catia.router)
api_router.include_router(kernel.router)
# Public and unauthenticated on purpose -- see each module's docstring.
api_router.include_router(trust.router)
# The docs site and the status page (P10.2, P10.4). Public for the same reason
# `trust` is: documentation behind a login can only be read by people who
# already bought, and a status page only its operator can read is a private
# dashboard. `status` is the one public route that takes a `DbSession`, and
# `routes/handbook.py` argues why.
api_router.include_router(handbook.router)
api_router.include_router(handbook.status_router)
# `sharing.public_router` is the only route in the service that answers with no
# principal at all. It takes a token and no id, so there is nothing for a caller
# to substitute; `core/sharing.resolve` is the whole gate.
api_router.include_router(sharing.public_router)
