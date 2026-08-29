"""Kryova CATIA bridge daemon.

Runs on the Windows workstation next to CATIA, dials out to Kryova over a
WebSocket, and executes a fixed vocabulary of modelling operations against the
engineer's live CATIA session over COM.

    python -m catia_bridge pair --code ABCD1234 --server https://app.example.com
    python -m catia_bridge run

Modules:

* `__main__`   -- the CLI (`pair`, `run`, `status`)
* `bridge`     -- the outbound WebSocket, reconnect and backoff
* `session`    -- frame handling, re-validation, tier enforcement, watchdog
* `tool_table` -- the daemon's OWN copy of the schemas and tiers
* `validation` -- the daemon's OWN JSON Schema subset validator
* `backend`    -- the interface the two backends implement
* `catia_com`  -- real CATIA V5 over COM (late binding only)
* `mock_catia` -- a complete in-memory CATIA, for testing without a licence
* `config`     -- token storage and working directories

Nothing here imports from the Kryova server. That is deliberate: the daemon must
be able to refuse a call the server asked for, which it cannot meaningfully do
using the server's own definition of what is allowed.
"""

from .config import BRIDGE_VERSION

__all__ = ["BRIDGE_VERSION"]
