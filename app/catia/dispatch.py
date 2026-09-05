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

import logging
import time
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
from app.catia_kb.ui import ButtonRole, button_labels, resolve_command, resolve_workbench
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
        "catia_open_document",  # nothing is open yet either
        "catia_checkpoint",  # it is the checkpoint
        "catia_export_step",  # reads the model out; does not change it
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
        "catia_open_document",  # restores it, and is the only path that carries
        # the stored checkpoint to rebuild a lost file from
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
        raise CatiaUnavailable(
            "The CATIA bridge on this machine is not connected yet"
            + (f": {detail}" if detail else ", because CATIA itself is not running")
            + ". Call open_in_catia to start CATIA -- the bridge attaches to it by "
            "itself within a few seconds -- then run this tool again. Do not ask "
            "the user to pair a workstation or to upload a CAD file."
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
    return [spec for spec in CATIA_TOOL_SPECS if hello.offers(spec.name)]


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
        bound = db.scalar(
            select(CatiaDocument).where(CatiaDocument.conversation_id == conversation_id)
        )
        if bound is not None:
            document = {
                "doc_name": clean_text(bound.doc_name),
                "latest_checkpoint_id": bound.latest_checkpoint_id,
                "bound_at": bound.created_at.isoformat(),
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

        arguments = _normalise(tool, arguments)
        try:
            validate(arguments, spec.parameters)
        except SchemaError as exc:
            raise CatiaError(f"{tool}: {exc}") from exc

        arguments = _augment(tool, arguments)

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


def _normalise(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Drop a field the model was told to omit, rather than failing the call.

    `depth_mm` is ignored for a through hole, and its schema refuses zero
    because zero is not a depth. The tool description says to omit it; models
    send `depth_mm: 0, through_all: true` anyway, and observed live that cost
    two rejected calls and most of a turn before the model guessed its way to a
    depth. A zero depth alongside `through_all` is not ambiguous -- the value is
    unused either way -- so it is dropped here instead of being argued about.

    This only ever removes a field. Anything that could weaken a check belongs
    in the schema, not in a normaliser that runs before it.
    """
    if tool == "catia_hole" and arguments.get("through_all", True):
        if arguments.get("depth_mm") == 0:
            return {k: v for k, v in arguments.items() if k != "depth_mm"}
    return arguments


def _augment(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
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
    """
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
        payload = {
            **arguments,
            "candidates": list(target.candidates),
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

    if spec.mutating and spec.name not in _NO_AUTO_CHECKPOINT and document is not None:
        # A mutation that could not be checkpointed does not run. Refusing is
        # the whole reason checkpoints exist: an unrecoverable change made
        # because the safety net was unavailable is the worst of both.
        _auto_checkpoint(
            db,
            connection=connection,
            document=document,
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
        document=_document_scope(spec, document),
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
    if not conversation_id:
        return None
    return db.scalar(select(CatiaDocument).where(CatiaDocument.conversation_id == conversation_id))


def _document_scope(
    spec: CatiaToolSpec, document: CatiaDocument | None
) -> dict[str, Any] | None:
    """The document envelope for one call, or None to leave the call unscoped.

    Note what is *not* here: the model. It never names a document and never sees
    a path, so this is read straight off the binding row -- which is the only
    thing that knows which part this conversation has been building.
    """
    if document is None or spec.name in _UNSCOPED_TOOLS:
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

    if spec.name == "catia_open_document":
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
            document=_document_scope(spec, document),
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
    if spec.name == "catia_new_part":
        document = _bind_document(
            db,
            conversation_id=conversation_id,
            device=device,
            doc_name=str(raw.get("doc_name") or arguments.get("name") or "Part"),
            remote_path=raw.get("remote_path"),
            existing=document,
        )
        return _clean(raw) | {"document_id": document.id}

    if spec.name == "catia_open_document" and document is not None:
        if raw.get("remote_path"):
            document.remote_path = str(raw["remote_path"])
        db.flush()
        return _clean(raw) | {"document_id": document.id}

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
    device: CatiaDevice,
    doc_name: str,
    remote_path: Any,
    existing: CatiaDocument | None,
) -> CatiaDocument:
    if conversation_id is None:
        raise CatiaError(
            "A CATIA document has to belong to a conversation, and this call was made outside one."
        )
    if existing is not None:
        # One document per conversation is a unique constraint, so rebinding is
        # an update, never a second row.
        existing.doc_name = clean_text(doc_name, 255)
        existing.remote_path = str(remote_path) if remote_path else None
        existing.device_id = device.id
        db.flush()
        return existing

    document = CatiaDocument(
        conversation_id=conversation_id,
        device_id=device.id,
        doc_name=clean_text(doc_name, 255),
        remote_path=str(remote_path) if remote_path else None,
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
