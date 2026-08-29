"""The CATIA desktop bridge.

The engineer's CATIA runs on a Windows workstation this service cannot reach.
The daemon there dials *out* over a WebSocket and keeps it open, so there is no
inbound port, no localhost HTTP server and nothing for a malicious web page to
find -- and it works behind a corporate NAT with no configuration.

    Browser --HTTPS--> Kryova backend --WebSocket (bridge-initiated)--> daemon --COM--> CATIA
            <--SSE---                 <-----------------------------

Layout:

* `tool_specs` -- the fixed vocabulary the agent may call, with tiers.
* `dispatch`   -- the one entry point (`call_catia`) every call goes through.
* `connection` -- live sockets, the one-call-at-a-time queue, heartbeats.
* `events`     -- daemon events fanned out to browser SSE subscribers.
* `approval`   -- signed approvals for destructive operations.
* `sanitize`   -- CATIA text is untrusted input; this is where that is handled.
* `transfer` / `geometry_import` -- exported STEP becoming a geometry version.

docs/CATIA_BRIDGE_PROTOCOL.md is the wire contract; this package implements it.
"""

from app.catia.dispatch import (
    CATIA_TOOL_SPECS,
    CatiaError,
    CatiaUnavailable,
    call_catia,
    catia_available,
)
from app.catia.tool_specs import CatiaTier, CatiaToolSpec

__all__ = [
    "CATIA_TOOL_SPECS",
    "CatiaError",
    "CatiaTier",
    "CatiaToolSpec",
    "CatiaUnavailable",
    "call_catia",
    "catia_available",
]
