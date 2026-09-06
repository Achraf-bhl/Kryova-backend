"""The interface the agent layer calls CATIA through.

`app/ai/tools.py` builds one agent tool per entry of `CATIA_TOOL_SPECS` and
routes it here. Everything that must be true of *every* CATIA call is true
because it happens in `call_catia` and nowhere else:

* the tool exists and its arguments satisfy its schema;
* a device the requesting user owns is actually online;
* the tier is enforced -- destructive calls carry a server-signed approval;
* the device is inside its rate limit;
* a mutating call is checkpointed first, so it can be undone;
* the call is written to the append-only operation log, whether it succeeded,
  failed, timed out, or was refused before it ever left this process.

That last point is the one worth defending. The failure this feature has to be
designed against is not a dramatic one -- it is a parameter quietly set to the
wrong value in week two and noticed in week six. Logging only successes would
lose exactly the calls that explain it.

Sanitising sits here too, on the way back: everything CATIA returns is text
somebody else wrote, and it goes straight into a prompt. See `sanitize.py`.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.rate_limit import RateLimiter
from app.catia import local_bridge
from app.catia.approval import ApprovalError, verify_approval
from app.catia.connection import (
    BridgeBusy,
    BridgeCallFailed,
    BridgeError,
    BridgeGone,
    BridgeTimeout,
    DeviceConnection,
    registry,
)
from app.catia.geometry_import import GeometryImportError, import_step_export
from app.catia.ops.placement import PlacementError, declares_polar, resolve_polar
from app.catia.sanitize import clean_result, clean_text
from app.catia.tool_specs import (
    CATIA_TOOL_SPECS,
    CatiaTier,
    CatiaToolSpec,
    get_spec,
)
from app.catia.transfer import (
    INLINE_TRANSFER_MAX_BYTES,
    ReceivedFile,
    TransferError,
    encode_inline_file,
    receive_inline_file,
)
from app.catia.validation import SchemaError, validate
from app.catia_kb.ui import (
    COMMAND_IDS,
    ButtonRole,
    button_labels,
    resolve_command,
    resolve_workbench,
)
from app.core.config import settings
from app.geometry import backends
from app.media import MediaService, get_media_store
from app.models import Conversation, MediaKind
from app.models.base import utcnow
from app.models.catia import (
    CatiaCheckpoint,
    CatiaDevice,
    CatiaDeviceStatus,
    CatiaDocument,
    CatiaOperation,
)
from app.models.geometry import GeometryVersion
from app.solve.materials import MATERIALS

logger = logging.getLogger(__name__)

__all__ = [
    "CATIA_TOOL_SPECS",
    "offered_tool_specs",
    "CatiaError",
    "CatiaUnavailable",
    "call_catia",
    "catia_available",
    "status_payload",
]


class CatiaUnavailable(RuntimeError):
    """No bridge is connected, or the one that was has gone away.

    Distinct from `CatiaError` because the remedy is different and the agent
    should say so: this one means "start the bridge on the Windows machine",
    never "try a different argument".
    """


class CatiaError(RuntimeError):
    """The tool ran, or was refused, and failed. The message is for the model."""


#: Tools handled entirely on this side. `catia_status` asks a question only the
#: server can answer -- the backend is the source of truth for "is CATIA
#: connected", and asking the device whether the device is reachable is circular.
_SERVER_SIDE_TOOLS = frozenset({"catia_status"})

#: Mutating tools that are *not* auto-checkpointed, and why.
_NO_AUTO_CHECKPOINT = frozenset(
    {
        "catia_new_part",  # there is nothing yet to snapshot
        "catia_product_create",  # nor here: an empty assembly is being started
        "catia_open_document",  # nothing is open yet either
        "catia_checkpoint",  # it is the checkpoint
        "catia_export_step",  # reads the model out; does not change it
        # Saves the document itself before closing it, so a checkpoint taken
        # first would snapshot a state the close does not alter -- it protects
        # nothing, and costs a full file upload on every window the agent tidies
        # away. Worse, `_auto_checkpoint` *refuses the call* when the snapshot
        # fails, which would make the one tool whose job is to clean up a
        # cluttered seat unavailable in exactly the conditions that clutter it.
        # Nothing becomes unrecoverable: the earlier checkpoints still exist, the
        # file stays on the workstation, and the binding row is kept.
        #
        # Note that `Operation.no_auto_checkpoint` is *not* what decides this.
        # The field exists and `registry.no_auto_checkpoint_names()` reads it,
        # but nothing consumes that anywhere -- this set is the only thing that
        # takes effect, so declaring the flag as well would be a second statement
        # of one fact with only one of them true.
        "catia_close_document",
        # The interactive tools, for one reason that applies to all four: a
        # checkpoint is a COM save, and these run precisely when a modal dialog
        # has COM blocked. Requiring one would mean the tools that dismiss a
        # stuck dialog can only run when no dialog is stuck -- and since a
        # failed checkpoint refuses the call, the session would be wedged with
        # no way out but a human hand.
        #
        # The safety they lose is smaller than it looks. `catia_run_command` is
        # checkpointed and it is the only one of the family that starts
        # anything; by the time a dialog is open, the snapshot from before the
        # command that opened it is already recorded, and pressing OK is
        # covered by it.
        "catia_fill_dialog",
        "catia_dialog_action",
        "catia_press_key",
        "catia_select",  # selecting changes nothing
        "catia_switch_workbench",  # nor does changing workbench
    }
)

#: Tools whose call frame does *not* carry the conversation's document, and why.
#: Everything else is scoped: the daemon activates the bound document -- reopening
#: it if CATIA was restarted -- before the operation runs, so a conversation
#: resumed a week later modelling into the part it owns is a property of the
#: system rather than something the model has to remember to arrange.
_UNSCOPED_TOOLS = frozenset(
    {
        "catia_new_part",  # creates the binding; there is nothing to return to
        # Creates a binding too -- a product is a document, and after this call
        # it is the active one (Phase 14). Scoping it would activate the part
        # that was current in order to start the assembly that replaces it as
        # current, which is work done to undo itself.
        "catia_product_create",
        "catia_open_document",  # restores it, and is the only path that carries
        # the stored checkpoint to rebuild a lost file from
        # The other half of that pair, and unscoped for a reason of its own: a
        # scoped call is *activated* first, and `ensure_document` reopens a
        # document CATIA no longer has open. Scoping the close would therefore
        # open a window in order to close it -- absurd on its face, and the exact
        # opposite of what the tool is for. It takes the same `doc_name` /
        # `remote_path` `catia_open_document` does (see `_enrich`) and finds the
        # window itself, so it can report "it was not open" instead of creating
        # one to report closing.
        "catia_close_document",
        # Opens the imported file, which is not the bound one. Note what that
        # leaves unresolved: nothing rebinds the conversation to the imported
        # document, so the next scoped call reattaches to the part that was
        # already bound and the import sits open beside it. That is at least
        # coherent -- before scoping existed, the mutation landed on the
        # imported document while its checkpoint and its log row named the
        # bound one. Deciding whether an import should rebind is a product
        # question, and it is still open.
        "catia_import",
        # The interactive family, for the reason that gets them out of the
        # auto-checkpoint too: they run precisely when a modal dialog has COM
        # blocked, and activating a document is a COM call. Scoping them would
        # mean the tools whose job is to clear a stuck dialog can only run when
        # no dialog is stuck. `session._ensure_document` skips them as well --
        # this is the braces to that belt.
        "catia_describe_dialog",
        "catia_fill_dialog",
        "catia_dialog_action",
        "catia_press_key",
        "catia_list_commands",
    }
)

# Per-device ceilings, mirroring the daemon's own. Both windows exist because
# they catch different things: the minute window catches a runaway agent loop,
# the hour window catches a slow drip that would never trip it.
_ops_per_minute = RateLimiter(max_requests=settings.catia_ops_per_minute, window_seconds=60)
_ops_per_hour = RateLimiter(max_requests=settings.catia_ops_per_minute * 10, window_seconds=3600)

#: Longest string kept in an operation-log row. The log is for reading, and a
#: base64 screenshot in a JSONB column is neither readable nor cheap.
_LOG_STRING_LIMIT = 500


# -- device resolution -------------------------------------------------------


def _owned_devices(db: Session, user_id: str) -> list[CatiaDevice]:
    return list(
        db.scalars(
            select(CatiaDevice).where(
                CatiaDevice.owner_id == user_id,
                CatiaDevice.status == CatiaDeviceStatus.ACTIVE,
            )
        )
    )


def _online(db: Session, user_id: str) -> tuple[CatiaDevice, DeviceConnection] | None:
    for device in _owned_devices(db, user_id):
        connection = registry.get(device.id)
        if connection is not None and connection.user_id == user_id:
            return device, connection
    return None


def _resolve_connection(db: Session, user_id: str) -> tuple[CatiaDevice, DeviceConnection]:
    """The user's online device, or an explanation of why there isn't one.

    The database row is re-checked even though the socket is open: revoking a
    device has to take effect on the next call, not whenever the socket happens
    to drop.

    On a single-machine install the daemon is started here, on demand, and this
    call waits for it. That wait is the whole point: without it the first
    `catia_*` call of a session fails, the model is told CATIA is unavailable,
    and it goes back to asking the user to upload a STEP file -- while the
    daemon it needed finishes connecting a second later.
    """
    found = _online(db, user_id)
    if found is not None:
        return found

    if local_bridge.ensure_started(db, user_id, wait_s=local_bridge.CONNECT_TIMEOUT_S):
        found = _online(db, user_id)
        if found is not None:
            return found

    if local_bridge.is_supported():
        detail = local_bridge.last_error(user_id)
        if detail:
            raise CatiaUnavailable(
                f"The CATIA bridge on this machine is not connected yet: {detail}. "
                "Call open_in_catia to start CATIA -- the bridge attaches to it by "
                "itself within a few seconds -- then run this tool again. Do not ask "
                "the user to pair a workstation or to upload a CAD file."
            )

        # CATIA alive but not answering is a third state, and telling the model
        # to start what is already running is worse than saying nothing: it
        # spends the rest of the turn on an instruction that cannot succeed,
        # and it passes that instruction on to the user. The overwhelmingly
        # common cause is a modal dialog holding COM -- which is exactly what
        # the interactive tools exist to clear, and they are in
        # `OUT_OF_BAND_TOOLS` so they still run while COM is blocked.
        if local_bridge.catia_process_is_running():
            raise CatiaUnavailable(
                "CATIA is running on this machine but is not answering, which "
                "almost always means a modal dialog is waiting for someone to "
                "click it -- an error box, a save prompt, an unknown-command "
                "warning. Do NOT ask the user to start CATIA or the bridge: both "
                "are already running. Call catia_describe_dialog to see what is "
                "on screen, then catia_dialog_action to dismiss it, and run this "
                "tool again. Those tools keep working while COM is blocked, "
                "which is the only time they matter."
            )

        raise CatiaUnavailable(
            "The CATIA bridge on this machine is not connected yet, because CATIA "
            "itself is not running. Call open_in_catia to start CATIA -- the bridge "
            "attaches to it by itself within a few seconds -- then run this tool "
            "again. Do not ask the user to pair a workstation or to upload a CAD file."
        )

    raise CatiaUnavailable(
        "No CATIA workstation is connected to this account. Ask the user to start "
        "the Kryova CATIA bridge on the Windows machine running CATIA (it connects "
        "outbound; nothing needs to be opened on their network). Until then, work "
        "from uploaded geometry instead."
    )


def connected_ui_language(db: Session, user_id: str) -> str | None:
    """Which language this user's connected CATIA is running in, if any.

    The seat's interface language is not a Kryova setting and is not stored: it
    is chosen when CATIA is installed, and the only thing that knows it is the
    daemon sitting beside it. This is the one place that answer is available to
    the rest of the server, and `None` -- no bridge, or a bridge that could not
    tell -- is a normal answer, not a failure.
    """
    found = _online(db, user_id)
    if found is None:
        return None
    return found[1].hello.ui_language or None


def offered_tool_specs(db: Session, user_id: str) -> list[CatiaToolSpec]:
    """The tools worth offering the agent, given the bridge actually connected.

    The registry describes what Kryova knows how to ask for. A given daemon
    implements some subset of that — it may be an older build, or a mock, or
    running against a seat whose licences do not reach every workbench. Handing
    the model the full registry regardless would mean it picks a tool, waits for
    a round trip, and gets "this bridge does not implement it", which costs a
    turn and teaches it nothing about what to do instead.

    So the offered list is the intersection. Two deliberate fallbacks:

    * **No device connected.** Offer everything. The tool has to exist for the
      model to be told *why* it cannot be used, and the dispatcher's own offline
      message ("connect the bridge") is far more useful than the tool silently
      not being there.
    * **A daemon that reported no tool list.** Also offer everything: it
      predates the field, which means it is an older build implementing the
      original vocabulary, and offering it nothing would be a worse guess than
      offering it too much.
    """
    if backends.is_local():
        # The open kernel's coverage is read from its handler table, so it cannot
        # drift from the code. Offering all 201 when 108 work costs the model a
        # turn per miss and teaches it nothing -- the same argument as the bridge
        # intersection below, with a source of truth that is in this process.
        implemented = backends.local_tool_names()
        offered = [spec for spec in CATIA_TOOL_SPECS if spec.name in implemented]
        # Never offer nothing: an unusable kernel must reach the model as a
        # message it can repeat, not as a vocabulary with no geometry in it.
        return offered or list(CATIA_TOOL_SPECS)

    found = _online(db, user_id)
    if found is None:
        return list(CATIA_TOOL_SPECS)
    hello = found[1].hello
    return [
        _without_unavailable_options(spec, hello.unavailable_options(spec.name))
        for spec in CATIA_TOOL_SPECS
        if hello.offers(spec.name)
    ]


def _without_unavailable_options(
    spec: CatiaToolSpec, unavailable: tuple[str, ...]
) -> CatiaToolSpec:
    """`spec` with the options this daemon cannot take removed from its schema.

    The tool-level intersection above stops the model being offered a tool that
    would fail. This is the same argument one level down, and it was measured
    the same way: on ladder prompt H4 run 9 (2026-09-06) the agent asked
    `catia_pad` for `limit`, was correctly refused because the COM method has
    no such parameter, and spent one of its twenty rounds on it. Nineteen tools
    carry that gap, so it is a seam with no report across it rather than one
    tool's oversight.

    An option that is *required* is not stripped -- a tool that cannot take a
    required argument is broken, not narrowed, and hiding the field would turn
    a clear refusal into a call that fails for a reason nothing names. That
    case does not occur today and is left visible on purpose.
    """
    if not unavailable:
        return spec
    parameters = spec.parameters
    properties = dict(parameters.get("properties") or {})
    required = set(parameters.get("required") or ())
    strippable = [name for name in unavailable if name in properties and name not in required]
    if not strippable:
        return spec
    for name in strippable:
        properties.pop(name)
    return replace(spec, parameters={**parameters, "properties": properties})


def catia_available(db: Session, user_id: str) -> bool:
    """True when a tool call could be routed right now.

    Called once per agent turn to build the state block, so it also serves as
    the trigger that gets a local daemon running: it kicks the supervisor
    without waiting, and by the time the model has read the state block and
    chosen a tool the socket is usually already up. `ensure_started` is cheap
    once the daemon is alive and rate-limited when it is not.
    """
    if not settings.catia_enabled:
        return False
    # No device to bring up, no socket to wait for: the kernel is in this
    # process. Answering False here would withhold every geometry tool from a
    # deployment that can build perfectly well.
    if backends.is_local():
        return bool(backends.local_tool_names())
    if any(registry.get(device.id) is not None for device in _owned_devices(db, user_id)):
        return True
    return local_bridge.ensure_started(db, user_id)


def _offline_detail(devices: list[CatiaDevice], user_id: str | None = None) -> str:
    """Why no device is connected, phrased as what to do about it.

    On a single-machine install the honest answer is almost always "CATIA is not
    open yet", not "you have not paired anything" -- the daemon is started here
    and sits waiting for CATIA to appear. Saying the latter sent the assistant
    off asking for a pairing code that nobody needs.
    """
    if local_bridge.is_supported():
        error = local_bridge.last_error(user_id)
        if error:
            return error
        return (
            "The CATIA bridge on this machine is running but has nothing to attach "
            "to yet. It connects by itself as soon as CATIA is open; open_in_catia "
            "starts CATIA."
        )
    if devices:
        return "No workstation is connected."
    return "No workstation has been paired with this account yet."


def status_payload(db: Session, user_id: str, conversation_id: str | None) -> dict[str, Any]:
    """What `catia_status` answers, for the agent and for `GET /catia/status`."""
    # Asking whether the bridge is up is also the moment to bring it up. The
    # panel polls this before any message is sent, and on a fresh account there
    # is no device row until something provisions one -- so the badge read "not
    # connected" until the user had already asked for something, which is the
    # wrong way round for the thing they check *before* asking. Non-blocking:
    # a status call must stay fast, and the supervisor is rate-limited.
    if backends.is_local():
        # Reported as connected because a tool call will succeed, which is what
        # the field means to every reader of it. `backend` is what says the part
        # is being built by the open kernel rather than by a seat -- a result is
        # bound to what produced it, and a status that hid the difference would
        # make that unknowable from the outside.
        coverage = backends.local_coverage()
        return {
            "connected": True,
            "enabled": settings.catia_enabled,
            "backend": "occt",
            "backend_version": backends.backend_version(),
            "paired_devices": 0,
            "operations_implemented": coverage.get("implemented"),
            "operations_declared": coverage.get("declared"),
            "open_documents": backends.session_count(),
            "document": _local_document(conversation_id),
            "detail": (
                "Geometry is being built by the open kernel in this process — no "
                "CATIA seat is involved, and none is needed. Set "
                "GEOMETRY_BACKEND=catia to drive a real seat instead."
            ),
        }

    local_bridge.ensure_started(db, user_id)

    devices = _owned_devices(db, user_id)
    online = [(d, c) for d in devices if (c := registry.get(d.id)) is not None]

    document = None
    if conversation_id:
        bound = _bound_document(db, conversation_id)
        if bound is not None:
            document = {
                "doc_name": clean_text(bound.doc_name),
                "doc_type": bound.doc_type,
                "latest_checkpoint_id": bound.latest_checkpoint_id,
                "bound_at": bound.created_at.isoformat(),
                # The set, so a client can show that an assembly is under way.
                "owned": [
                    {"doc_name": clean_text(d.doc_name), "doc_type": d.doc_type, "active": d.is_active}
                    for d in _owned_documents(db, conversation_id)
                ],
            }

    if not online:
        return {
            "connected": False,
            "enabled": settings.catia_enabled,
            "backend": "catia",
            "paired_devices": len(devices),
            "document": document,
            "detail": _offline_detail(devices, user_id),
        }

    device, connection = online[0]
    return {
        "connected": True,
        "enabled": settings.catia_enabled,
        "backend": "catia",
        "paired_devices": len(devices),
        "device_id": device.id,
        "device_name": clean_text(device.name),
        "hostname": clean_text(connection.hello.hostname),
        "catia_version": clean_text(connection.hello.catia_version, 64),
        "bridge_version": clean_text(connection.hello.bridge_version, 32),
        "mock": connection.hello.mock,
        "capabilities": list(connection.hello.capabilities),
        # Which language that CATIA's menus are in, or empty when the daemon
        # could not tell. Reported rather than hidden because it is what decides
        # whether the assistant can name a menu item in the words the user is
        # actually looking at.
        "ui_language": connection.hello.ui_language,
        "queue_depth": connection.queue_depth,
        "connected_since": connection.connected_at.isoformat(),
        "document": document,
    }


def _local_document(conversation_id: str | None) -> dict[str, Any] | None:
    """What the open kernel is holding for this conversation.

    Reports the in-memory document rather than the `CatiaDocument` row, because
    on this backend the row is not the truth: nothing is saved to disk, the
    document lives in the worker, and a status that quoted a database row would
    keep describing a part that had been evicted.
    """
    if backends.was_evicted(conversation_id):
        return {"doc_name": None, "evicted": True}
    runner = backends.peek_session(conversation_id)
    if runner is None:
        return None
    document = getattr(runner, "document", None)
    if document is None:
        return None
    return {"doc_name": clean_text(getattr(document, "name", "") or ""), "evicted": False}


# -- the public entry point --------------------------------------------------


def call_catia(
    db: Session,
    *,
    user_id: str,
    conversation_id: str | None,
    tool: str,
    arguments: dict[str, Any],
    timeout_s: float | None = None,
) -> dict[str, Any]:
    """Run one CATIA tool for one user and return its data dictionary.

    Raises `CatiaUnavailable` when no bridge could take the call and `CatiaError`
    for everything else. Both messages are written to be read by the model and
    repeated to the user, so they say what to do next.
    """
    started = time.monotonic()
    spec = get_spec(tool)
    device: CatiaDevice | None = None
    tier = spec.tier.value if spec else "unknown"

    try:
        if not settings.catia_enabled:
            raise CatiaUnavailable(
                "The CATIA bridge is switched off on this deployment. Work from "
                "uploaded geometry instead."
            )
        if spec is None:
            known = ", ".join(sorted(s.name for s in CATIA_TOOL_SPECS))
            raise CatiaError(f"{tool!r} is not a CATIA tool. Available tools: {known}.")

        arguments = _normalise(tool, arguments, spec.parameters)
        try:
            validate(arguments, spec.parameters)
        except SchemaError as exc:
            raise CatiaError(f"{tool}: {exc}") from exc

        arguments = _augment(tool, arguments, spec)

        if tool in _SERVER_SIDE_TOOLS:
            data = status_payload(db, user_id, conversation_id)
            _log(
                db,
                user_id=user_id,
                conversation_id=conversation_id,
                device_id=None,
                tool=tool,
                tier=tier,
                arguments=arguments,
                result=data,
                ok=True,
                error=None,
                started=started,
            )
            return data

        # The open kernel runs in this process, so there is no device to resolve,
        # no rate limit to enforce against a shared seat, and no round trip. It is
        # placed here rather than earlier so that a local call gets the same
        # normalisation, schema validation and augmentation a remote one does --
        # the vocabulary is the contract, and a backend that accepted looser
        # arguments would make plans that only build on one of them.
        if backends.is_local():
            data = _execute_locally(
                spec=spec,
                conversation_id=conversation_id,
                arguments=arguments,
            )
            # The binding belongs to the conversation, not to the seat, so the
            # local backend owes the same `CatiaDocument` row `_post_process`
            # writes for a remote one. It never reached that function -- this
            # branch returns first -- so on GEOMETRY_BACKEND=occt
            # `catia_new_part` built the document, reported success, recorded
            # nothing, and every document-scoped tool after it was refused one
            # layer up with "No CATIA document is bound to this conversation".
            # The agent could create a part and then do nothing whatever to it,
            # which is the whole of the open-kernel product path. Measured on
            # the Windows seat, 2026-09-05, driving the real chat endpoint;
            # `tests/test_geometry_backends.py` could not see it because it
            # calls this dispatcher directly and the refusal lives in
            # `app/ai/tools.py`.
            #
            # `device_id` stays NULL. There is no seat to name, and the column
            # has been nullable since revoking a laptop had to leave the record
            # of what was built on it behind.
            if spec.name == "catia_new_part":
                document = _bind_document(
                    db,
                    conversation_id=conversation_id,
                    device=None,
                    doc_name=str(
                        data.get("document") or data.get("doc_name") or arguments.get("name") or "Part"
                    ),
                    remote_path=None,
                    existing=_bound_document(db, conversation_id),
                )
                data = data | {"document_id": document.id}
            _log(
                db,
                user_id=user_id,
                conversation_id=conversation_id,
                device_id=None,
                tool=tool,
                tier=tier,
                arguments=arguments,
                result=data,
                ok=True,
                error=None,
                started=started,
            )
            return data

        device, connection = _resolve_connection(db, user_id)
        _enforce_rate_limit(device.id)
        if spec.tier is CatiaTier.DESTRUCTIVE:
            _enforce_approval(spec, user_id, conversation_id, arguments)

        data = _execute(
            db,
            spec=spec,
            device=device,
            connection=connection,
            user_id=user_id,
            conversation_id=conversation_id,
            arguments=arguments,
            timeout_s=timeout_s,
        )
    except (CatiaError, CatiaUnavailable) as exc:
        _log(
            db,
            user_id=user_id,
            conversation_id=conversation_id,
            device_id=device.id if device else None,
            tool=tool,
            tier=tier,
            arguments=arguments,
            result=None,
            ok=False,
            error=str(exc),
            started=started,
        )
        raise

    _log(
        db,
        user_id=user_id,
        conversation_id=conversation_id,
        device_id=device.id if device else None,
        tool=tool,
        tier=tier,
        arguments=arguments,
        result=data,
        ok=True,
        error=None,
        started=started,
    )
    return data


def _execute_locally(
    *,
    spec: CatiaToolSpec,
    conversation_id: str | None,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Run one call against the in-process OCCT kernel.

    Three failures are translated rather than propagated, because each means
    something specific that the agent must be able to act on differently:

    * **The operation is not implemented in this backend.** Not a geometry error.
      An agent told "that failed" will try to repair a part that is fine; told
      "this backend cannot do it yet", it can use another operation or ask for
      the CATIA seat. It names the coverage so the answer is checkable.
    * **The document was evicted** to bound memory. Silence here would let the
      agent add a feature to an empty document and report success, which is the
      worst outcome available — so the first call after an eviction refuses,
      says so, and clears the flag so a retry starts cleanly.
    * **Anything else** is a real geometry failure and keeps its own message; the
      kernel's errors are already written in this codebase's register.
    """
    from app.kernel.errors import KernelError, OperationNotSupported

    if backends.was_evicted(conversation_id):
        backends.clear_eviction(conversation_id)
        raise CatiaError(
            "The part this conversation was building is no longer in memory — too "
            "many documents were open at once and this one was closed. Nothing was "
            "saved. Start the part again with catia_new_part; the design is still "
            "in the conversation, so the same calls will rebuild it."
        )

    runner = backends.session_for(conversation_id)
    try:
        result = runner(spec.name, arguments)
    except OperationNotSupported as exc:
        coverage = backends.local_coverage()
        implemented = coverage.get("implemented", 0)
        declared = coverage.get("declared", 0)
        raise CatiaError(
            f"{spec.name} is not implemented in the open kernel yet "
            f"({implemented} of {declared} operations are). This is a gap in the "
            "backend, not a problem with the part. Use another operation, or switch "
            "GEOMETRY_BACKEND to catia and connect a seat."
        ) from exc
    except KernelError as exc:
        raise CatiaError(f"{spec.name}: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - an unexpected kernel fault must not 500
        logger.exception("The open kernel failed on %s", spec.name)
        raise CatiaError(
            f"{spec.name} failed unexpectedly in the open kernel: {exc}"
        ) from exc

    return dict(result)


# -- enforcement -------------------------------------------------------------


def _parse_array_strings(
    arguments: dict[str, Any], schema: dict[str, Any] | None
) -> dict[str, Any]:
    """`"[50, 0]"` where an array is declared is that array, sent as text.

    Measured on ladder prompts H4 and H5 on 2026-09-06, on three separate
    runs: the model sent `catia_sketch_line(start="[0, 0]", end="[150, 0]")`
    and was refused with `start must be array, got str`. It then sent the same
    thing again, twice, and one run lost four of its twenty rounds to it. The
    refusal is accurate and it does not help: the model already believes it
    sent a list, because what it wrote *is* the list, and reading the message
    tells it nothing it can act on differently.

    Nothing is guessed here. The string has to parse as JSON and has to yield
    a list; anything else is left exactly as it arrived and refused by the
    validator as before. A list of the wrong length, or of strings where
    numbers are wanted, is still refused too -- validation runs afterwards,
    against the same schema, unchanged. What this removes is one specific
    round trip whose outcome was never in doubt.

    `ast.literal_eval` is deliberately not used: it would also accept Python
    tuples, sets and expressions, and the point is to accept exactly the thing
    the model meant to send and nothing more.
    """
    if not schema:
        return arguments
    properties = schema.get("properties") or {}
    parsed: dict[str, Any] | None = None
    for name, value in arguments.items():
        if not isinstance(value, str):
            continue
        declared = properties.get(name) or {}
        types = declared.get("type")
        wanted = types if isinstance(types, list) else [types]
        if "array" not in wanted:
            continue
        try:
            candidate = json.loads(value)
        except (ValueError, TypeError):
            continue
        if not isinstance(candidate, list):
            continue
        if parsed is None:
            parsed = dict(arguments)
        parsed[name] = candidate
    return parsed if parsed is not None else arguments


def _normalise(
    tool: str, arguments: dict[str, Any], schema: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Fix what the model plainly meant, before the schema is applied.

    Two cases, and both are ones where the intent is not in question:

    `depth_mm` is ignored for a through hole, and its schema refuses zero
    because zero is not a depth. The tool description says to omit it; models
    send `depth_mm: 0, through_all: true` anyway, and observed live that cost
    two rejected calls and most of a turn before the model guessed its way to a
    depth. A zero depth alongside `through_all` is not ambiguous -- the value is
    unused either way -- so it is dropped here instead of being argued about.

    And a coordinate sent as the *text* of a list is that list; see
    `_parse_array_strings`.

    Neither weakens a check. The first removes a field that is unused; the
    second changes a value's Python type to the one the schema already
    demands, and then that schema runs and refuses everything it refused
    before. Anything that would actually widen what is accepted belongs in the
    schema, where it can be read.
    """
    arguments = _parse_array_strings(arguments, schema)
    if tool == "catia_hole" and arguments.get("through_all", True):
        if arguments.get("depth_mm") == 0:
            return {k: v for k, v in arguments.items() if k != "depth_mm"}
    return arguments


def _augment(
    tool: str, arguments: dict[str, Any], spec: CatiaToolSpec | None = None
) -> dict[str, Any]:
    """Add the fields the server supplies and the model may not.

    After validation, and that ordering is the whole point. The model-facing
    schema for `catia_set_material` lists only `material` and sets
    `additionalProperties: false`, which is what makes "the model does not get
    to choose the number every mass is computed from" enforceable. Adding the
    density before that check meant the check rejected the server's own field:
    every live call came back `unknown field(s): density_kg_m3` and the part
    kept CATIA's default 1000 kg/m3, while the unit tests passed because they
    drove the daemon's schema -- which requires the density -- and never this
    path.

    It also *consumes* polar placement, which is the mirror case: the model may
    say `at_radius_mm`/`at_angle_deg`, and what goes on the wire is the `at`
    both backends already take. Resolving it here rather than in each backend
    means the workstation daemon needs no change and — more importantly — there
    is one implementation of the trigonometry and so one angle convention. See
    `app/catia/ops/placement.py` for why the polar spelling exists at all.
    """
    if declares_polar(spec.parameters if spec else {}):
        try:
            arguments = resolve_polar(arguments)
        except PlacementError as exc:
            raise CatiaError(f"{tool}: {exc}") from exc

    if tool == "catia_set_material":
        chosen = MATERIALS.get(str(arguments.get("material", "")))
        if chosen is None:
            raise CatiaError(
                f"{arguments.get('material')!r} is not in the material library. "
                f"Choose one of: {', '.join(sorted(MATERIALS))}."
            )
        return {**arguments, "density_kg_m3": chosen.density_kg_m3}

    return arguments


#: The interactive tools whose arguments the server resolves against the CATIA
#: reference before they go on the wire.
_UI_TOOLS = frozenset({"catia_run_command", "catia_dialog_action", "catia_switch_workbench"})


def _resolve_ui(tool: str, arguments: dict[str, Any], language: str | None) -> dict[str, Any]:
    """Translate the model's English intent into this seat's own words.

    The model names commands, workbenches and button roles in English, because
    that is the vocabulary it and the user share. The workstation is running in
    whatever language it was installed in. This is the seam between the two, and
    it lives here rather than in the daemon for two reasons: the translation
    table is part of the CATIA reference and shipping it to every workstation
    would mean updating them to fix a translation, and the daemon must be able
    to refuse a command on its own terms without trusting anything the server
    resolved (`ui_policy.check` re-checks every candidate).

    The seat's language is not guessed. When the bridge has not reported one,
    `language` is None, the resolver returns the English name alone, and the
    daemon falls back to reading the live menu -- which is correct on every
    installation and merely one round trip slower.
    """
    if tool == "catia_run_command":
        target = resolve_command(str(arguments.get("command", "")), language=language)
        # `candidates` mixes two different kinds of string: published command
        # *ids* (`OpenInNewWnd`) and display *labels* ("Edge Fillet", and the
        # seat's translation of it). Only the first kind may be handed to
        # `StartCommand`. Measured 2026-09-06: a display label CATIA does not
        # recognise as an id does not fail silently -- it raises a modal error
        # box that holds COM and takes the whole seat down, twice in one
        # session. Labels are still sent, because matching them against the
        # live menu is safe and is how the daemon finds the command; they are
        # simply no longer gambled on through StartCommand.
        published = COMMAND_IDS.get(target.key or "")
        payload = {
            **arguments,
            "candidates": list(target.candidates),
            "command_ids": [published] if published else [],
            "command_name": target.name,
            "command_key": target.key or "",
        }
        if target.menu:
            payload["menu_hint"] = [part.strip() for part in target.menu.split(">") if part.strip()]
        return payload

    if tool == "catia_dialog_action":
        # A named button is the model's own string and is passed through
        # untranslated: it read that label off `catia_describe_dialog`, which
        # reported what the dialog really says.
        if arguments.get("button"):
            return arguments
        try:
            role = ButtonRole(str(arguments.get("action", "")))
        except ValueError:  # pragma: no cover - the schema enumerates these
            return arguments
        return {**arguments, "labels": list(button_labels(role, language))}

    target_wb = resolve_workbench(str(arguments.get("workbench", "")), language=language)
    return {
        **arguments,
        "workbench_id": target_wb.workbench_id,
        "workbench_name": target_wb.name,
        "menu_path": list(target_wb.menu_path),
        "licence": target_wb.licence,
    }


def _enforce_rate_limit(device_id: str) -> None:
    if not _ops_per_minute.check(f"catia:min:{device_id}"):
        raise CatiaError(
            f"This workstation has hit its limit of {settings.catia_ops_per_minute} CATIA "
            "operations per minute. Wait a moment before continuing, and prefer one "
            "parameter change over a burst of small edits."
        )
    if not _ops_per_hour.check(f"catia:hour:{device_id}"):
        raise CatiaError(
            f"This workstation has hit its limit of {settings.catia_ops_per_minute * 10} "
            "CATIA operations per hour. Stop and tell the user; something is looping."
        )


def _enforce_approval(
    spec: CatiaToolSpec, user_id: str, conversation_id: str | None, arguments: dict[str, Any]
) -> None:
    token = arguments.get("approval_token")
    # The signature binds the target, so an approval for one checkpoint cannot
    # roll back to another.
    target = str(arguments.get("checkpoint_id") or "")
    try:
        verify_approval(
            token if isinstance(token, str) else "",
            user_id=user_id,
            tool=spec.name,
            conversation_id=conversation_id,
            target=target,
        )
    except ApprovalError as exc:
        raise CatiaError(str(exc)) from exc


# -- execution ---------------------------------------------------------------


def _timeout_for(spec: CatiaToolSpec, override: float | None) -> float:
    if override is not None:
        return override
    return settings.catia_export_timeout_s if spec.long_running else settings.catia_call_timeout_s


def _execute(
    db: Session,
    *,
    spec: CatiaToolSpec,
    device: CatiaDevice,
    connection: DeviceConnection,
    user_id: str,
    conversation_id: str | None,
    arguments: dict[str, Any],
    timeout_s: float | None,
) -> dict[str, Any]:
    document = _bound_document(db, conversation_id)
    # The document this call is *for*, chosen once: the checkpoint below and the
    # send further down must agree on it, or the snapshot filed as this
    # mutation's undo is of some other part.
    target = _target_document(db, spec, document, conversation_id)
    protected = target if target is not None else document

    if spec.mutating and spec.name not in _NO_AUTO_CHECKPOINT and protected is not None:
        # A mutation that could not be checkpointed does not run. Refusing is
        # the whole reason checkpoints exist: an unrecoverable change made
        # because the safety net was unavailable is the worst of both.
        _auto_checkpoint(
            db,
            connection=connection,
            document=protected,
            user_id=user_id,
            label=f"before {spec.name}",
        )

    payload = _enrich(
        db,
        spec=spec,
        document=document,
        arguments=arguments,
        # The daemon tells us what language its CATIA is running in, and it is
        # the only thing that knows: the seat's interface language is chosen at
        # install time on the workstation and appears nowhere on the server.
        language=connection.hello.ui_language or None,
        # Only `catia_import` needs it, to scope an uploaded file to this
        # conversation's project rather than to every project the user owns.
        conversation_id=conversation_id,
    )
    raw = _send(
        connection,
        spec=spec,
        conversation_id=conversation_id,
        arguments=payload,
        timeout_s=_timeout_for(spec, timeout_s),
        # Which document this call is for, so the daemon acts on the
        # conversation's own part rather than on whatever CATIA has in front.
        document=_envelope(target),
        # Forwarded, not re-derived: the daemon refuses a destructive call that
        # arrives without one, and only the server can supply it.
        approval_token=(
            str(arguments.get("approval_token") or "")
            if spec.tier is CatiaTier.DESTRUCTIVE
            else None
        ),
    )
    device.last_seen_at = utcnow()

    return _post_process(
        db,
        spec=spec,
        device=device,
        document=document,
        user_id=user_id,
        conversation_id=conversation_id,
        arguments=arguments,
        raw=raw,
    )


def _send(
    connection: DeviceConnection,
    *,
    spec: CatiaToolSpec,
    conversation_id: str | None,
    arguments: dict[str, Any],
    timeout_s: float,
    approval_token: str | None = None,
    document: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One round trip, with the transport's failures translated for the model."""
    try:
        return connection.call(
            tool=spec.name,
            arguments=arguments,
            conversation_id=conversation_id,
            timeout_s=timeout_s,
            queue_timeout_s=timeout_s,
            approval_token=approval_token,
            document=document,
        )
    except BridgeGone as exc:
        raise CatiaUnavailable(str(exc)) from exc
    except (BridgeBusy, BridgeTimeout, BridgeCallFailed) as exc:
        raise CatiaError(str(exc)) from exc
    except BridgeError as exc:  # pragma: no cover - defensive
        raise CatiaError(f"The CATIA bridge failed: {exc}") from exc


def _bound_document(db: Session, conversation_id: str | None) -> CatiaDocument | None:
    """The document every scoped call is sent to: the conversation's *active* one.

    Phase 14 made a conversation own several documents; this is the one that
    is current. The others are reachable through `_owned_documents`.
    """
    if not conversation_id:
        return None
    return db.scalar(
        select(CatiaDocument).where(
            CatiaDocument.conversation_id == conversation_id,
            CatiaDocument.is_active.is_(True),
        )
    )


def _owned_documents(db: Session, conversation_id: str | None) -> list[CatiaDocument]:
    """Every document the conversation owns, oldest first."""
    if not conversation_id:
        return []
    return list(
        db.scalars(
            select(CatiaDocument)
            .where(CatiaDocument.conversation_id == conversation_id)
            .order_by(CatiaDocument.created_at)
        )
    )


#: The annotation the state block prints after each owned name -- "Shaft (part,
#: active)", "Assembly (product)". On ladder prompt S2 (2026-09-06) the model
#: copied it into `catia_open_document name=`, was told the conversation owned
#: no such document, and spent the round. It meant the document; the
#: annotation is ours, so it is stripped before the name is matched.
_ANNOTATION_RE = re.compile(r"\s*\((?:part|product)(?:,\s*active)?\)$", re.IGNORECASE)


def _owned_document_named(
    db: Session, conversation_id: str | None, name: str
) -> CatiaDocument | None:
    """One of the conversation's documents by name, case-insensitively.

    Exact name first; then the stem of the path the daemon saved it under, so
    `Bracket` still finds the row whose file became `Bracket-2.CATPart`.
    """
    wanted = _ANNOTATION_RE.sub("", name.strip()).strip().lower()
    if not wanted:
        return None
    owned = _owned_documents(db, conversation_id)
    for document in owned:
        if document.doc_name.lower() == wanted:
            return document
    for document in owned:
        stem = (document.remote_path or "").replace("\\", "/").rsplit("/", 1)[-1]
        stem = stem.rsplit(".", 1)[0].lower()
        if stem == wanted:
            return document
    return None


def _activate(db: Session, conversation_id: str | None, target: CatiaDocument) -> None:
    """Make `target` the conversation's active document.

    Two flushes on purpose. The unit of work issues INSERTs before UPDATEs, and
    a partial unique index checks every statement as it lands -- so deactivating
    the current row has to be written before the new active one, or the index
    sees two for one instant and refuses the second.
    """
    for document in _owned_documents(db, conversation_id):
        if document.is_active and document.id != target.id:
            document.is_active = False
    db.flush()
    target.is_active = True
    db.flush()


#: Workbenches whose tools act on a part's geometry and mean nothing on a
#: product. Sent to the assembly, they are refused here with the parts by name
#: rather than on the daemon with "activate the CATPart", which names nothing.
_PART_WORKBENCHES = frozenset({"Sketcher", "Part Design", "Generative Shape Design"})

#: Workbenches whose tools can only ever mean the conversation's product. DMU
#: is the clash check, and a clash check of a single part is not a thing.
_ASSEMBLY_WORKBENCHES = frozenset({"Assembly Design", "DMU Navigator"})


def _target_document(
    db: Session,
    spec: CatiaToolSpec,
    document: CatiaDocument | None,
    conversation_id: str | None,
) -> CatiaDocument | None:
    """The document one call is *for*, or None to leave the call unscoped.

    Chosen once per call and shared by the checkpoint and the send, because the
    checkpoint has to snapshot the document the mutation will change and no
    other -- `_auto_checkpoint` says why that is the worst place to get it
    wrong.

    Note what is *not* here: the model. It never names a document and never sees
    a path, so this is read straight off the binding rows -- which are the only
    thing that knows which documents this conversation has been building.

    **Which row, by workbench** (Phase 14; measured on ladder prompt S2 turn 2,
    2026-09-06). With the product active, `catia_constrain` reached the
    assembly and `catia_list_features` was refused as "not a part"; the agent
    reopened the shaft to read it, and then `catia_constrain` reported the
    assembly "contains: (none)", because the scoped document was now the
    shaft. Six of twenty rounds flip-flopping between two documents that were
    both there, both owned and both needed.

    So an assembly tool -- Assembly Design, or the DMU clash check -- addresses
    the conversation's product whatever is active, because it can mean nothing
    else; and a *mutation* of part geometry sent while a product is active is
    refused here with the parts by name, which is the one thing the daemon's
    refusal could not say. Reads are left alone: `catia_measure` on a product
    is the assembly's mass, and the daemon answers it. Everything else
    addresses the active document, as it always has.
    """
    if spec.name in _UNSCOPED_TOOLS:
        return None
    if spec.workbench in _ASSEMBLY_WORKBENCHES:
        products = [d for d in _owned_documents(db, conversation_id) if d.doc_type == "product"]
        if products:
            return products[-1]
    if document is None:
        return None
    # Mutations only. A read on a product is a legitimate question --
    # `catia_measure` rolls an assembly's mass up through `Product.Analyze` --
    # and one the daemon can answer or refuse itself. What must not reach it
    # is a pad, a pocket or a sketch aimed at a document with no Part.
    if document.doc_type == "product" and spec.workbench in _PART_WORKBENCHES and spec.mutating:
        parts = [d.doc_name for d in _owned_documents(db, conversation_id) if d.doc_type == "part"]
        named = ", ".join(parts) or "none yet -- call catia_new_part"
        raise CatiaError(
            f"{spec.name} works on a part, and the active document is the assembly "
            f"{document.doc_name!r}. This conversation's parts are: {named}. Call "
            "catia_open_document name=<part> to make one of them active, then call "
            f"{spec.name} again."
        )
    return document


def _envelope(document: CatiaDocument | None) -> dict[str, Any] | None:
    """The frame the daemon activates before the operation runs."""
    if document is None:
        return None
    return {"doc_name": document.doc_name, "remote_path": document.remote_path}


def _enrich(
    db: Session,
    *,
    spec: CatiaToolSpec,
    document: CatiaDocument | None,
    arguments: dict[str, Any],
    language: str | None = None,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """Add the server-held context a tool needs, which the model never supplies.

    The model names documents and checkpoints; paths and file bytes are resolved
    here. That is what makes "no filesystem paths from the model" enforceable
    rather than aspirational. The interactive tools are resolved here too, for
    the same reason and one more: their answer depends on the connected device's
    interface language, which is not known until a device has been chosen.
    """
    payload = {k: v for k, v in arguments.items() if k != "approval_token"}

    if spec.name in _UI_TOOLS:
        return _resolve_ui(spec.name, payload, language)

    if spec.name in ("catia_new_part", "catia_product_create"):
        # Measured on ladder prompt S2 turn 2 (2026-09-06): "now make the
        # bushing as a second part" -- and the conversation already owned a
        # finished Bushing from turn 1. `catia_new_part name=Bushing` went
        # through, the daemon saved Bushing-2.CATPart beside it, a second
        # assembly with the same name followed, and the user watched the work
        # start over. Nothing was lost, but nothing was continued either. A
        # name this conversation owns means that document; refused here,
        # before the daemon, with the call that continues it.
        owner = conversation_id or (document.conversation_id if document is not None else None)
        wanted = str(payload.get("name") or "").strip()
        taken = _owned_document_named(db, owner, wanted) if wanted else None
        if taken is not None:
            kind = "an assembly" if taken.doc_type == "product" else "a part"
            raise CatiaError(
                f"This conversation already owns {kind} called {taken.doc_name!r}. To "
                f"continue it, call catia_open_document name={taken.doc_name!r} -- "
                "everything built in it so far is kept. To start a different one, "
                "use a different name."
            )

    if spec.name == "catia_open_document":
        owner = conversation_id or (document.conversation_id if document is not None else None)
        wanted = str(payload.get("name") or "").strip()
        if wanted:
            # Phase 14: one of this conversation's documents by name, and
            # opening it makes it the active one. Resolved here, where the
            # rows are, so the daemon still only ever receives a path it was
            # itself told to save to.
            target = _owned_document_named(db, owner, wanted)
            if target is None:
                names = ", ".join(d.doc_name for d in _owned_documents(db, owner)) or "none"
                raise CatiaError(
                    f"This conversation owns no document called {wanted!r}. It owns: "
                    f"{names}. Use one of those names, or catia_new_part to start one."
                )
            if not target.is_active:
                _activate(db, owner, target)
            document = target
        if document is None:
            raise CatiaError(
                "This conversation has no CATIA document yet. Call catia_new_part to start one."
            )
        payload["doc_name"] = document.doc_name
        payload["remote_path"] = document.remote_path
        # If the workstation lost the file, the daemon reopens from the blob we
        # kept. That is the difference between "resume tomorrow" working and
        # working only until someone cleans their temp directory.
        checkpoint = _latest_checkpoint(db, document)
        if checkpoint is not None:
            payload["fallback_checkpoint"] = _checkpoint_payload(db, checkpoint)

    elif spec.name == "catia_component_add" and payload.get("kind") == "existing":
        # "Add the shaft" names a part this conversation built. The daemon
        # would look for `shaft.CATPart`, and `new_part` may have saved it as
        # `shaft-2.CATPart`; the row knows which, so the name becomes the
        # path here. A name that is not one of ours goes through untouched --
        # the daemon still resolves an open document or a file of its own.
        owner = conversation_id or (document.conversation_id if document is not None else None)
        named = _owned_document_named(db, owner, str(payload.get("document") or ""))
        if named is not None and named.remote_path:
            payload["document"] = named.remote_path

    elif spec.name == "catia_close_document":
        if document is None:
            raise CatiaError(
                "This conversation has no CATIA document, so there is nothing to close."
            )
        # Identity only. No `fallback_checkpoint`: that field exists to *rebuild*
        # a file the workstation lost, and rebuilding a file in order to close its
        # window would be work done to undo itself.
        payload["doc_name"] = document.doc_name
        payload["remote_path"] = document.remote_path

    elif spec.name == "catia_restore":
        if document is None:
            raise CatiaError("This conversation has no CATIA document to restore.")
        checkpoint = db.get(CatiaCheckpoint, arguments.get("checkpoint_id"))
        if checkpoint is None or checkpoint.document_id != document.id:
            raise CatiaError(
                "No checkpoint with that id belongs to this conversation's document. "
                "List the checkpoints and name one of those."
            )
        payload = {"checkpoint": _checkpoint_payload(db, checkpoint)}

    elif spec.name == "catia_import":
        payload.update(_uploaded_file(db, conversation_id, str(arguments.get("file", ""))))

    elif spec.name in {
        "catia_checkpoint",
        "catia_export",
        "catia_export_step",
        "catia_capture_view",
    }:
        payload["max_inline_bytes"] = INLINE_TRANSFER_MAX_BYTES

    return payload


def _uploaded_file(
    db: Session, conversation_id: str | None, name: str
) -> dict[str, Any]:
    """Resolve a named upload to the bytes the daemon will import.

    Scoped to the conversation's own project, which is the access control: a
    model that names another project's file gets "no file called that", the same
    answer it gets for a name that never existed. Anything looser would let one
    prompt read across projects.

    The bytes travel with the call rather than a path, because the daemon runs
    on the engineer's workstation and the upload lives here.
    """
    if not conversation_id:
        raise CatiaError(
            "This conversation is not attached to a project, so it has no uploaded "
            "files to import from."
        )
    conversation = db.get(Conversation, conversation_id)
    project_id = getattr(conversation, "project_id", None)
    if project_id is None:
        raise CatiaError(
            "This conversation is not attached to a project, so it has no uploaded "
            "files to import from."
        )

    versions = list(
        db.scalars(
            select(GeometryVersion)
            .where(GeometryVersion.project_id == project_id)
            .order_by(GeometryVersion.version_number.desc())
        )
    )
    wanted = name.strip().lower()
    match = next(
        (
            version
            for version in versions
            # Both spellings, because the model has seen the name in a file list
            # and may quote it with or without its extension.
            if wanted in {version.filename.lower(), Path(version.filename).stem.lower()}
        ),
        None,
    )
    if match is None:
        available = ", ".join(version.filename for version in versions[:10]) or "(none)"
        raise CatiaError(
            f"This project has no uploaded file called {name!r}. It has: {available}."
        )

    media = _media_service(db)
    try:
        content = encode_inline_file(media.local_path(match.media))
    except (TransferError, OSError) as exc:
        raise CatiaError(
            f"{match.filename} could not be read back from storage to send to the "
            f"workstation. ({exc})"
        ) from exc

    return {
        "content_b64": content,
        "content_hash": match.checksum_sha256,
        "filename": match.filename,
    }


def _latest_checkpoint(db: Session, document: CatiaDocument) -> CatiaCheckpoint | None:
    if document.latest_checkpoint_id:
        return db.get(CatiaCheckpoint, document.latest_checkpoint_id)
    return None


def _checkpoint_payload(db: Session, checkpoint: CatiaCheckpoint) -> dict[str, Any]:
    """A checkpoint as the daemon needs it: its own reference, plus bytes if we have them."""
    payload: dict[str, Any] = {
        "checkpoint_id": checkpoint.id,
        "remote_ref": checkpoint.remote_ref,
        "sha256": checkpoint.digest,
    }
    if checkpoint.media is not None:
        media = _media_service(db)
        try:
            payload["content_b64"] = encode_inline_file(media.local_path(checkpoint.media))
        except (TransferError, OSError) as exc:
            # Not fatal: the daemon still has `remote_ref` and may hold its own
            # copy. Losing the cloud copy is worth a log line, not a refusal.
            logger.warning("Could not ship checkpoint %s to the daemon: %s", checkpoint.id, exc)
    return payload


# -- checkpointing -----------------------------------------------------------


def _media_service(db: Session) -> MediaService:
    return MediaService(db, get_media_store())


def _auto_checkpoint(
    db: Session,
    *,
    connection: DeviceConnection,
    document: CatiaDocument,
    user_id: str,
    label: str,
) -> CatiaCheckpoint:
    spec = get_spec("catia_checkpoint")
    assert spec is not None  # noqa: S101 - the vocabulary is a module constant
    try:
        raw = _send(
            connection,
            spec=spec,
            conversation_id=document.conversation_id,
            arguments={"label": label, "max_inline_bytes": INLINE_TRANSFER_MAX_BYTES},
            timeout_s=settings.catia_call_timeout_s,
            # Scoped to the same document as the mutation it is protecting, and
            # this is the call where getting it wrong is worst. An unscoped
            # checkpoint snapshots whatever CATIA has active; the mutation then
            # reattaches and changes the right part -- leaving a checkpoint of
            # some *other* part filed as this one's undo, so restoring it would
            # overwrite the work rather than recover it.
            document=_envelope(document),
        )
    except (CatiaError, CatiaUnavailable) as exc:
        raise CatiaError(
            f"Refusing to run this change: CATIA could not save a checkpoint first "
            f"({exc}). Fix that before modifying the part -- without a checkpoint the "
            "change cannot be undone."
        ) from exc
    return _record_checkpoint(db, document=document, user_id=user_id, label=label, raw=raw)


def _record_checkpoint(
    db: Session,
    *,
    document: CatiaDocument,
    user_id: str,
    label: str,
    raw: dict[str, Any],
) -> CatiaCheckpoint:
    """Store a snapshot's bytes (when they fit) and write the checkpoint row."""
    media_id: str | None = None
    digest = raw.get("sha256")
    size_bytes = raw.get("size_bytes")

    if raw.get("content_b64"):
        received: ReceivedFile | None = None
        try:
            received = receive_inline_file(raw)
            stored = _media_service(db).store_path(
                owner_id=user_id,
                kind=MediaKind.OTHER,
                path=received.path,
                filename=f"{document.doc_name}.CATPart",
                content_type="application/octet-stream",
                meta={"source": "catia_checkpoint", "document_id": document.id},
            )
            media_id = stored.id
            digest = received.digest
            size_bytes = received.size_bytes
        except TransferError as exc:
            # The daemon's own snapshot still exists, so the checkpoint is
            # recorded and remains usable on that workstation. Say so rather
            # than pretending the cloud copy is there.
            logger.warning("Checkpoint for document %s did not transfer: %s", document.id, exc)
        finally:
            if received is not None:
                received.path.unlink(missing_ok=True)

    checkpoint = CatiaCheckpoint(
        document_id=document.id,
        media_id=media_id,
        digest=str(digest)[:64] if digest else None,
        size_bytes=int(size_bytes) if isinstance(size_bytes, int) else None,
        remote_ref=clean_text(raw.get("remote_ref") or "", 1000) or None,
        label=clean_text(label, 200),
    )
    db.add(checkpoint)
    db.flush()
    document.latest_checkpoint_id = checkpoint.id
    db.flush()
    return checkpoint


# -- post-processing ---------------------------------------------------------


def _post_process(
    db: Session,
    *,
    spec: CatiaToolSpec,
    device: CatiaDevice,
    document: CatiaDocument | None,
    user_id: str,
    conversation_id: str | None,
    arguments: dict[str, Any],
    raw: dict[str, Any],
) -> dict[str, Any]:
    """Turn a daemon result into what the agent sees, with side effects recorded."""
    if spec.name in ("catia_new_part", "catia_product_create"):
        kind = "product" if spec.name == "catia_product_create" else "part"
        document = _bind_document(
            db,
            conversation_id=conversation_id,
            device=device,
            doc_name=str(raw.get("doc_name") or arguments.get("name") or "Part"),
            remote_path=raw.get("remote_path"),
            existing=document,
            doc_type=kind,
        )
        owned = _owned_documents(db, conversation_id)
        result = _clean(raw) | {"document_id": document.id, "doc_type": kind}
        if len(owned) > 1:
            # Say what just happened to the previous document, in the result
            # the model reads, because "started a new part" and "lost the old
            # one" look identical from a `Done`.
            others = ", ".join(d.doc_name for d in owned if d.id != document.id)
            result["note"] = (
                f"{document.doc_name!r} is the active document now. This conversation "
                f"still owns {others}; nothing was discarded. Switch back with "
                "catia_open_document name=<document>."
            )
        return result

    if spec.name == "catia_open_document":
        # `_enrich` may have switched the active document by name, so the row
        # handed in is not necessarily the one that was opened. Re-read.
        document = _bound_document(db, conversation_id) or document
        if document is not None:
            if raw.get("remote_path"):
                document.remote_path = str(raw["remote_path"])
            db.flush()
            return _clean(raw) | {"document_id": document.id}

    # `catia_close_document` deliberately has no branch here: **closing keeps the
    # binding row.** This is the decision that makes the operation safe, and the
    # other way round deadlocks or loses work, so it is worth the paragraph.
    #
    # Clearing the row would leave the CATPart sitting on the workstation with
    # nothing pointing at it: `catia_open_document` is refused without a binding
    # ("this conversation has no CATIA document yet"), so the only tool left is
    # `catia_new_part`, which starts a *different* part. The first part is not
    # deleted -- it is worse than deleted, it is unreachable, and every checkpoint
    # taken of it is orphaned with it (they are keyed on `document_id`). An agent
    # closing a window would silently abandon the work, which is the failure this
    # tool is written not to have.
    #
    # Keeping it is what makes closing reversible: `catia_open_document` reopens
    # from `remote_path`, and any later scoped call reopens it through
    # `ensure_document` without the model having to think about it. Closing is
    # putting the part away, not forgetting it.
    #
    # The deadlock the other direction warns about (`app/ai/tools.py::_call_catia`,
    # where refusing `catia_new_part` on the strength of a stale row wedged a
    # conversation for good) does not apply, and the difference is worth naming: on
    # a seat the row is *not* stale after a close -- the file is still there and
    # `catia_open_document` is offered, so there is always a way back to it. That
    # guard's problem was the open kernel, where the row named a live object that
    # a restart had destroyed and no tool could reopen. This operation is not in
    # `app.kernel.occt.operations.HANDLERS`, so it is never offered on that
    # backend and cannot reach that state -- there is no window there to close,
    # and dropping the in-process document would mean discarding the part rather
    # than putting it away, which is a different operation and must not borrow
    # this one's name.

    if spec.name == "catia_checkpoint":
        if document is None:
            raise CatiaError(
                "This conversation has no CATIA document to checkpoint. Call "
                "catia_new_part or catia_open_document first."
            )
        checkpoint = _record_checkpoint(
            db,
            document=document,
            user_id=user_id,
            label=str(arguments.get("label") or "checkpoint"),
            raw=raw,
        )
        return {
            "checkpoint_id": checkpoint.id,
            "label": checkpoint.label,
            "size_bytes": checkpoint.size_bytes,
            # Named honestly: a checkpoint held only on the workstation is not
            # the same promise as one held in the blob store, and the agent
            # should not tell the user their work is safely backed up when the
            # only copy is on the laptop that might be the thing that fails.
            "stored_in_cloud": checkpoint.media_id is not None,
        }

    if spec.name == "catia_restore":
        return _clean(raw) | {"restored_checkpoint_id": arguments.get("checkpoint_id")}

    if spec.name == "catia_capture_view":
        return _store_capture(db, user_id=user_id, arguments=arguments, raw=raw)

    if spec.name == "catia_export_step":
        return _store_export(
            db,
            user_id=user_id,
            conversation_id=conversation_id,
            arguments=arguments,
            raw=raw,
        )

    return _clean(raw)


def _bind_document(
    db: Session,
    *,
    conversation_id: str | None,
    device: CatiaDevice | None,
    doc_name: str,
    remote_path: Any,
    existing: CatiaDocument | None,
    doc_type: str = "part",
) -> CatiaDocument:
    """Record a document a conversation owns, and make it the active one.

    Two backends, two rules, and the difference is where the document lives:

    * **A seat** (`device` set) keeps every document as a file with a path, so
      a second `catia_new_part` *adds* a row and deactivates the current one.
      Nothing is abandoned -- the earlier part keeps its path and its
      checkpoints and `catia_open_document name=` brings it back. This is what
      makes an assembly buildable at all (Phase 14; ladder prompt S2).
    * **The open kernel** (`device` None) holds one live document in this
      process and `catia_new_part` replaces it, so the row is *updated* in
      place -- the contract `app/ai/tools.py` relies on for the eviction
      recovery, where the row names something that is gone and building it
      again rebinds cleanly rather than leaving a second row.
    """
    if conversation_id is None:
        raise CatiaError(
            "A CATIA document has to belong to a conversation, and this call was made outside one."
        )
    if existing is not None and device is None:
        existing.doc_name = clean_text(doc_name, 255)
        existing.remote_path = str(remote_path) if remote_path else None
        existing.device_id = None
        existing.doc_type = doc_type
        db.flush()
        return existing

    if existing is not None:
        # Deactivate before inserting: the partial unique index on the active
        # row checks each statement as it lands, and the unit of work writes
        # INSERTs ahead of UPDATEs. See `_activate`.
        existing.is_active = False
        db.flush()

    document = CatiaDocument(
        conversation_id=conversation_id,
        device_id=device.id if device is not None else None,
        doc_name=clean_text(doc_name, 255),
        remote_path=str(remote_path) if remote_path else None,
        doc_type=doc_type,
        is_active=True,
    )
    db.add(document)
    db.flush()
    return document


#: What a captured view can actually be. Real CATIA writes JPEG, because
#: `CatCaptureFormat` has no PNG member; the mock daemon writes a PNG it encodes
#: itself. Both arrive here, so the type is read off the name rather than
#: assumed -- serving JPEG bytes labelled `image/png` leaves the browser to
#: guess, and leaves the stored record lying about its own content.
_IMAGE_CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}


def _image_content_type(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    return _IMAGE_CONTENT_TYPES.get(suffix, "application/octet-stream")


def _store_capture(
    db: Session, *, user_id: str, arguments: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    received: ReceivedFile | None = None
    try:
        received = receive_inline_file(raw)
        filename = str(raw.get("filename") or "catia-view.jpg")
        stored = _media_service(db).store_path(
            owner_id=user_id,
            kind=MediaKind.OTHER,
            path=received.path,
            filename=filename,
            content_type=_image_content_type(filename),
            meta={"source": "catia_capture_view", "view": arguments.get("view", "iso")},
        )
    except TransferError as exc:
        raise CatiaError(f"The screenshot did not arrive intact: {exc}") from exc
    finally:
        if received is not None:
            received.path.unlink(missing_ok=True)

    return {
        "media_id": stored.id,
        "view": arguments.get("view", "iso"),
        "label": clean_text(arguments.get("label") or "", 120),
        "width_px": raw.get("width_px"),
        "height_px": raw.get("height_px"),
        "size_bytes": stored.size_bytes,
    }


def _store_export(
    db: Session,
    *,
    user_id: str,
    conversation_id: str | None,
    arguments: dict[str, Any],
    raw: dict[str, Any],
) -> dict[str, Any]:
    conversation = db.get(Conversation, conversation_id) if conversation_id else None
    if conversation is None or conversation.owner_id != user_id:
        raise CatiaError("This call is not attached to one of your conversations.")
    if conversation.project_id is None:
        raise CatiaError(
            "This conversation is not scoped to a project, so there is nowhere to put "
            "the geometry. Create a project first, then export again."
        )

    received: ReceivedFile | None = None
    try:
        received = receive_inline_file(raw)
        version = import_step_export(
            db,
            _media_service(db),
            owner_id=user_id,
            project_id=conversation.project_id,
            path=received.path,
            filename=str(raw.get("filename") or "catia-export.step"),
            note=arguments.get("note"),
        )
    except TransferError as exc:
        raise CatiaError(f"The STEP export did not arrive intact: {exc}") from exc
    except GeometryImportError as exc:
        raise CatiaError(str(exc)) from exc
    finally:
        if received is not None:
            received.path.unlink(missing_ok=True)

    return {
        "project_id": conversation.project_id,
        "geometry_version_id": version.id,
        "version_number": version.version_number,
        "filename": version.filename,
        "size_bytes": version.size_bytes,
        "stats": version.stats,
        "next_step": (
            f"Geometry version {version.version_number} is ready. Build a load case "
            "against it and run a simulation."
        ),
    }


def _clean(raw: dict[str, Any]) -> dict[str, Any]:
    """Sanitised result, minus the transfer plumbing the model has no use for."""
    stripped = {
        key: value for key, value in raw.items() if key not in {"content_b64", "max_inline_bytes"}
    }
    cleaned = clean_result(stripped)
    return cleaned if isinstance(cleaned, dict) else {}


# -- the audit trail ---------------------------------------------------------


#: Never written to the operation log. File payloads because they are huge and
#: unreadable; the approval token because it is a live credential for its five
#: minutes, and an audit table is exactly the wrong place to leave one.
_REDACTED_LOG_KEYS = frozenset(
    {"content_b64", "fallback_checkpoint", "checkpoint", "approval_token"}
)


def _loggable(value: Any, _depth: int = 0) -> Any:
    """Shrink a payload to something worth keeping in a log row forever."""
    if _depth > 6:
        return "…"
    if isinstance(value, dict):
        return {
            key: ("…" if key in _REDACTED_LOG_KEYS else _loggable(v, _depth + 1))
            for key, v in list(value.items())[:64]
        }
    if isinstance(value, (list, tuple)):
        return [_loggable(item, _depth + 1) for item in list(value)[:32]]
    if isinstance(value, str) and len(value) > _LOG_STRING_LIMIT:
        return value[:_LOG_STRING_LIMIT] + "…"
    return value


def _log(
    db: Session,
    *,
    user_id: str,
    conversation_id: str | None,
    device_id: str | None,
    tool: str,
    tier: str,
    arguments: dict[str, Any],
    result: dict[str, Any] | None,
    ok: bool,
    error: str | None,
    started: float,
) -> None:
    """Write one row to the append-only operation log, and commit it.

    Committing here, rather than leaving it to the caller, is deliberate. The
    agent loop rolls back when a turn fails, and an audit trail that disappears
    with the failure it was meant to record is not an audit trail. The row
    describes a call that really was made to a real workstation; that fact does
    not become untrue because the surrounding transaction was abandoned.
    """
    operation = CatiaOperation(
        conversation_id=conversation_id,
        device_id=device_id,
        user_id=user_id,
        tool=tool[:64],
        tier=tier[:16],
        arguments=_loggable(arguments),
        result=_loggable(result) if result is not None else None,
        ok=ok,
        error=error[:2000] if error else None,
        duration_ms=int((time.monotonic() - started) * 1000),
    )
    db.add(operation)
    try:
        db.commit()
    except Exception:  # noqa: BLE001 - logging must never mask the real failure
        logger.exception("Could not write the CATIA operation log for %s", tool)
        db.rollback()
