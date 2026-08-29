"""CATIA bridge endpoints: pairing, the daemon's socket, status and events.

Two audiences share this router and they authenticate differently.

**The browser** uses the ordinary session cookie and the CSRF header, like every
other route here, and sees devices, status and an event stream scoped to the
signed-in user.

**The daemon** has no session. It redeems a pairing code once, over an
unauthenticated but rate-limited endpoint, and thereafter presents a device
token in an `Authorization: Bearer` header -- including on the WebSocket
upgrade. The token is never a query parameter: query strings are written to
access logs, browser history and proxy logs by default, and a long-lived
credential that ends up in three log files is not a credential.

Cross-user access is 404, never 403, exactly as everywhere else: device ids must
not be enumerable across accounts.
"""

import asyncio
import json
import logging
import secrets
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request, WebSocket, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketDisconnect

from app.api.deps import CurrentUser, DbSession
from app.api.rate_limit import RateLimiter
from app.catia.approval import mint_approval
from app.catia.connection import (
    HELLO_TIMEOUT_S,
    BridgeError,
    BridgeHello,
    DeviceConnection,
    registry,
)
from app.catia.dispatch import status_payload
from app.catia.events import KNOWN_EVENTS, bus
from app.catia.sanitize import clean_result, clean_text
from app.catia.tool_specs import CATIA_TOOL_SPECS, CatiaTier
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import hash_token
from app.models.base import utcnow
from app.models.catia import CatiaCheckpoint, CatiaDevice, CatiaDeviceStatus, CatiaDocument

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/catia", tags=["catia"])

#: Redeeming a pairing code is unauthenticated, so it is the one endpoint an
#: attacker can grind. Eight characters from a 32-symbol alphabet is 2^40
#: possibilities; ten attempts a minute per address makes exhausting it take
#: longer than the ten-minute window by a factor of about ten million.
pairing_limiter = RateLimiter(max_requests=10, window_seconds=60)

#: Crockford-style: no I, L, O, U, so a code read off a screen and typed into a
#: terminal cannot be mistyped into a different valid code.
_CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_CODE_LENGTH = 8

#: How long an SSE stream waits before emitting a comment, so proxies and
#: browsers can tell an idle connection from a dead one.
_SSE_KEEPALIVE_S = 15.0


def _new_pairing_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))


def _client_ip(request: Request) -> str:
    """Rate-limit key. `X-Forwarded-For` is only believed behind a known proxy.

    A header any client can write is not an identity, so trusting it
    unconditionally means an attacker rotating the header has no rate limit at
    all. Mirrors the posture `trust_proxy_headers` documents in config.
    """
    if settings.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for", "")
        hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
        index = settings.trusted_proxy_count
        if len(hops) >= index:
            return hops[-index]
    return request.client.host if request.client else "unknown"


# -- schemas -----------------------------------------------------------------


class DeviceCreate(BaseModel):
    name: str = Field(
        min_length=1,
        max_length=120,
        description="How the user will recognise this workstation, e.g. 'Office desktop'.",
    )


class DeviceRead(BaseModel):
    id: str
    name: str
    hostname: str | None
    status: str
    online: bool
    catia_version: str | None
    bridge_version: str | None
    is_mock: bool
    last_seen_at: datetime | None
    created_at: datetime


class DeviceCreated(BaseModel):
    """The one and only time the pairing code is shown."""

    device: DeviceRead
    pairing_code: str
    pairing_expires_at: datetime
    command: str = Field(description="The exact command to run on the Windows workstation.")


class PairRequest(BaseModel):
    code: str = Field(min_length=4, max_length=16)
    hostname: str | None = Field(default=None, max_length=255)
    bridge_version: str | None = Field(default=None, max_length=32)


class PairResponse(BaseModel):
    device_id: str
    device_name: str
    #: Shown once. Only its SHA-256 is stored.
    device_token: str
    expires_at: datetime
    websocket_path: str


class ApprovalRequest(BaseModel):
    tool: str = Field(description="The destructive tool the user is approving.")
    conversation_id: str | None = None
    checkpoint_id: str = Field(min_length=1, max_length=36)


class ApprovalResponse(BaseModel):
    approval_token: str
    expires_in_seconds: int


class CheckpointRead(BaseModel):
    id: str
    label: str
    size_bytes: int | None
    stored_in_cloud: bool
    created_at: datetime


def _device_read(device: CatiaDevice) -> DeviceRead:
    connection = registry.get(device.id)
    return DeviceRead(
        id=device.id,
        name=device.name,
        # Every one of these is peer-supplied; none reaches a page unsanitised.
        hostname=clean_text(device.hostname or "") or None,
        status=str(device.status),
        online=connection is not None,
        catia_version=clean_text(device.catia_version or "", 64) or None,
        bridge_version=clean_text(device.bridge_version or "", 32) or None,
        is_mock=device.is_mock,
        last_seen_at=device.last_seen_at,
        created_at=device.created_at,
    )


def _owned_device(db: Session, user_id: str, device_id: str) -> CatiaDevice:
    device = db.get(CatiaDevice, device_id)
    if device is None or device.owner_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="CATIA device not found")
    return device


# -- device management (browser) ---------------------------------------------


@router.post("/devices", response_model=DeviceCreated, status_code=status.HTTP_201_CREATED)
def create_device(payload: DeviceCreate, db: DbSession, current_user: CurrentUser) -> DeviceCreated:
    """Register a workstation and issue a single-use pairing code.

    The code is returned once and never again: it is a bearer credential for
    exactly as long as it lives, and an endpoint that could re-read it would
    make its ten-minute lifetime meaningless.
    """
    expires_at = utcnow() + timedelta(minutes=settings.catia_pairing_code_ttl_minutes)
    device = CatiaDevice(
        owner_id=current_user.id,
        name=payload.name.strip(),
        pairing_code=_new_pairing_code(),
        pairing_expires_at=expires_at,
        status=CatiaDeviceStatus.PENDING,
    )
    db.add(device)
    db.commit()
    db.refresh(device)

    return DeviceCreated(
        device=_device_read(device),
        pairing_code=device.pairing_code or "",
        pairing_expires_at=expires_at,
        command=f"kryova-catia-bridge pair --code {device.pairing_code}",
    )


@router.get("/devices", response_model=list[DeviceRead])
def list_devices(db: DbSession, current_user: CurrentUser) -> list[DeviceRead]:
    """The user's paired workstations, newest first."""
    devices = db.scalars(
        select(CatiaDevice)
        .where(CatiaDevice.owner_id == current_user.id)
        .order_by(CatiaDevice.created_at.desc())
    )
    return [_device_read(device) for device in devices]


@router.delete("/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_device(device_id: str, db: DbSession, current_user: CurrentUser) -> None:
    """Revoke a workstation's token and hang up on it if it is connected.

    The row is kept rather than deleted: the operation log points at it, and an
    audit trail whose devices vanish answers "who did this" with a null.
    """
    device = _owned_device(db, current_user.id, device_id)
    device.status = CatiaDeviceStatus.REVOKED
    device.revoked_at = utcnow()
    device.token_hash = None
    device.pairing_code = None
    db.commit()

    # Revocation has to bite immediately. Leaving the socket open until its next
    # heartbeat means a revoked laptop keeps working for twenty seconds.
    connection = registry.get(device_id)
    if connection is not None:
        connection.close("was revoked")


# -- pairing (daemon, unauthenticated by session) ----------------------------


@router.post("/devices/pair", response_model=PairResponse)
def pair_device(payload: PairRequest, request: Request, db: DbSession) -> PairResponse:
    """Exchange a pairing code for a long-lived device token.

    Authenticated by the code itself -- the daemon has no session and the user
    is standing at the Windows machine, not the browser. The code is consumed
    whether or not anything later fails, so it is single-use in fact and not
    merely by intention.
    """
    if not pairing_limiter.check(f"catia-pair:{_client_ip(request)}"):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many pairing attempts. Wait a minute and try again.",
        )

    code = payload.code.strip().upper()
    device = db.scalar(select(CatiaDevice).where(CatiaDevice.pairing_code == code))
    invalid = HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=(
            "That pairing code is not valid, has already been used, or has expired. "
            "Generate a fresh one in Kryova and run the pair command again."
        ),
    )
    if device is None or device.status is CatiaDeviceStatus.REVOKED:
        raise invalid
    if device.pairing_expires_at is None or device.pairing_expires_at < utcnow():
        raise invalid

    token = secrets.token_urlsafe(48)
    device.token_hash = hash_token(token)
    device.token_expires_at = utcnow() + timedelta(days=settings.catia_device_token_ttl_days)
    device.status = CatiaDeviceStatus.ACTIVE
    # Consumed. A code that survived redemption would be a second, weaker path
    # to a device token for as long as it lived.
    device.pairing_code = None
    device.pairing_expires_at = None
    if payload.hostname:
        device.hostname = payload.hostname[:255]
    if payload.bridge_version:
        device.bridge_version = payload.bridge_version[:32]
    db.commit()

    return PairResponse(
        device_id=device.id,
        device_name=device.name,
        device_token=token,
        expires_at=device.token_expires_at,
        websocket_path=f"{settings.api_v1_prefix}/catia/bridge/ws",
    )


# -- the daemon's socket -----------------------------------------------------


def _authenticate_device(db: Session, header: str | None) -> CatiaDevice | None:
    if not header or not header.lower().startswith("bearer "):
        return None
    token = header.split(" ", 1)[1].strip()
    if not token:
        return None
    device = db.scalar(select(CatiaDevice).where(CatiaDevice.token_hash == hash_token(token)))
    if device is None or device.status is not CatiaDeviceStatus.ACTIVE:
        return None
    if device.token_expires_at is not None and device.token_expires_at < utcnow():
        return None
    return device


@router.websocket("/bridge/ws")
async def bridge_socket(websocket: WebSocket) -> None:
    """The bridge daemon's long-lived connection.

    Authenticated by `Authorization: Bearer <device_token>` on the upgrade. A
    query-parameter token is refused by omission -- it is never read -- because
    it would be written to every access log between here and the workstation.

    This handler owns its own database session. It outlives any request, so it
    cannot borrow one, exactly like a background job.
    """
    header = websocket.headers.get("authorization")
    with SessionLocal() as db:
        device = _authenticate_device(db, header)
        if device is None:
            # 1008 policy violation, before accept: nothing about the failure
            # distinguishes "unknown token" from "revoked device".
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        device_id, owner_id = device.id, device.owner_id

    await websocket.accept()

    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=HELLO_TIMEOUT_S)
        hello = BridgeHello.parse(json.loads(raw))
    except (TimeoutError, json.JSONDecodeError, BridgeError, WebSocketDisconnect) as exc:
        logger.info("CATIA device %s failed its handshake: %s", device_id, exc)
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    connection = DeviceConnection(
        device_id=device_id,
        user_id=owner_id,
        hello=hello,
        loop=asyncio.get_running_loop(),
    )
    displaced = registry.register(connection)
    if displaced is not None:
        # A laptop that slept and woke reconnects before the old socket's
        # heartbeat has noticed. Newest wins.
        displaced.close("was replaced by a newer connection")

    with SessionLocal() as db:
        stored = db.get(CatiaDevice, device_id)
        if stored is not None:
            stored.hostname = hello.hostname or stored.hostname
            stored.catia_version = hello.catia_version
            stored.bridge_version = hello.bridge_version
            stored.is_mock = hello.mock
            stored.last_seen_at = utcnow()
            db.commit()

    await websocket.send_text(
        json.dumps({"type": "ready", "device_id": device_id, "heartbeat_seconds": 20})
    )
    bus.publish(
        owner_id,
        _envelope("bridge_connected", {"device_id": device_id, "mock": hello.mock}),
    )

    sender = asyncio.create_task(connection.run_sender(websocket))
    heartbeat = asyncio.create_task(connection.run_heartbeat())
    try:
        while connection.is_open:
            event = connection.handle_frame(await websocket.receive_text())
            if event is not None:
                _relay(owner_id, device_id, event)
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001 - one bad socket must not take the worker down
        logger.exception("CATIA bridge socket for device %s failed", device_id)
    finally:
        connection.close("disconnected")
        registry.unregister(connection)
        for task in (sender, heartbeat):
            task.cancel()
        bus.publish(owner_id, _envelope("catia_lost", {"device_id": device_id}))
        with SessionLocal() as db:
            stored = db.get(CatiaDevice, device_id)
            if stored is not None:
                stored.last_seen_at = utcnow()
                db.commit()


def _envelope(event: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"event": event, "at": utcnow().isoformat(), "data": data}


def _relay(user_id: str, device_id: str, frame: dict[str, Any]) -> None:
    """Publish a daemon event, dropping anything outside the vocabulary.

    The browser switches on the event name, so an unknown one is either a
    version skew or a daemon doing something it was not asked to. Both are
    better dropped here than rendered.
    """
    name = str(frame.get("event") or "")
    if name not in KNOWN_EVENTS:
        logger.info("Dropping unknown CATIA event %r from device %s", name[:64], device_id)
        return
    data = frame.get("data")
    payload = clean_result(data) if isinstance(data, dict) else {}
    bus.publish(user_id, _envelope(name, {"device_id": device_id, **payload}))


# -- direct COM bridge compatibility helpers ----------------------------------

from typing import Annotated
from fastapi import Body
from app.api.deps import MediaServiceDep, OwnedProject
from app.catia.bridge import (
    CATIABridgeError,
    CatiaStatus,
    get_status,
    launch,
    list_open_documents,
    new_part,
)


class CatiaStatusRead(BaseModel):
    running: bool
    version: str | None = None
    open_documents: int = 0
    active_document: str | None = None
    detail: str | None = Field(
        default=None, description="Why CATIA is unavailable, and what to do about it."
    )


class CatiaDocumentRead(BaseModel):
    name: str
    path: str | None
    doc_type: str


class LaunchRequest(BaseModel):
    new_part: bool = Field(
        default=True, description="Also open an empty CATPart to model in."
    )


class SyncRequest(BaseModel):
    note: str | None = Field(default=None, max_length=500)


def _as_read(status_obj: CatiaStatus) -> CatiaStatusRead:
    return CatiaStatusRead(
        running=status_obj.running,
        version=status_obj.version,
        open_documents=status_obj.document_count,
        active_document=status_obj.active_document,
        detail=status_obj.detail,
    )


def _unavailable(exc: CATIABridgeError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))


# -- browser-facing status and events ----------------------------------------


@router.get("/status")
def catia_status(
    db: DbSession,
    current_user: CurrentUser,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """Always 200: 'CATIA is not here' is a state the UI renders, not a failure.

    Returns the daemon status_payload when a WebSocket device is connected
    (keys: connected, paired_devices, catia_version, mock, device_id, …).
    Falls back to a direct COM probe when no daemon device is online, adding
    running/version/open_documents aliases for backward compat.
    """
    payload = status_payload(db, current_user.id, conversation_id)
    if payload.get("connected"):
        # Daemon path: return the rich daemon payload with COM-compat aliases.
        return {
            **payload,
            "running": True,
            "version": payload.get("catia_version"),
            "open_documents": 1 if payload.get("document") else 0,
            "active_document": (payload.get("document") or {}).get("doc_name"),
        }
    # No daemon device online: probe the local COM bridge and return both shapes.
    com = get_status()
    # The daemon payload's detail ("No workstation has been paired…" /
    # "No workstation is connected.") is always more actionable for the user
    # than the COM probe result ("not on Windows"), so prefer it.
    detail = payload.get("detail") or com.detail
    return {
        **payload,
        "running": com.running,
        "version": com.version,
        "open_documents": com.document_count,
        "active_document": com.active_document,
        "detail": detail,
    }


@router.get("/tools")
def list_tools(current_user: CurrentUser) -> dict[str, Any]:
    """The tool vocabulary and its tiers, so the UI can label what the agent did.

    Served rather than duplicated in the frontend: a tier the UI has wrong is a
    destructive operation shown as routine.
    """
    return {
        "tools": [
            {
                "name": spec.name,
                "tier": str(spec.tier),
                "mutating": spec.mutating,
                "description": spec.description,
            }
            for spec in CATIA_TOOL_SPECS
        ]
    }


@router.get("/conversations/{conversation_id}/checkpoints", response_model=list[CheckpointRead])
def list_checkpoints(
    conversation_id: str, db: DbSession, current_user: CurrentUser
) -> list[CheckpointRead]:
    """Checkpoints for a conversation's document, newest first.

    This is what a rollback UI lists, and what the user picks from before the
    approval token below is minted.
    """
    document = db.scalar(
        select(CatiaDocument).where(CatiaDocument.conversation_id == conversation_id)
    )
    if document is None or document.conversation.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No CATIA document for that conversation"
        )
    checkpoints = db.scalars(
        select(CatiaCheckpoint)
        .where(CatiaCheckpoint.document_id == document.id)
        .order_by(CatiaCheckpoint.created_at.desc())
        .limit(100)
    )
    return [
        CheckpointRead(
            id=checkpoint.id,
            label=checkpoint.label,
            size_bytes=checkpoint.size_bytes,
            stored_in_cloud=checkpoint.media_id is not None,
            created_at=checkpoint.created_at,
        )
        for checkpoint in checkpoints
    ]


@router.post("/approvals", response_model=ApprovalResponse)
def create_approval(
    payload: ApprovalRequest, db: DbSession, current_user: CurrentUser
) -> ApprovalResponse:
    """Sign an approval for one destructive operation, after the user clicks.

    The signature binds user, tool, conversation and target together, so an
    approval granted for one rollback cannot be replayed against another. It is
    minted only for tools that are actually destructive: minting one for a
    routine call would train the UI to ask for approval it does not need.
    """
    spec = next((s for s in CATIA_TOOL_SPECS if s.name == payload.tool), None)
    if spec is None or spec.tier is not CatiaTier.DESTRUCTIVE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{payload.tool!r} is not a destructive CATIA tool and needs no approval.",
        )

    checkpoint = db.get(CatiaCheckpoint, payload.checkpoint_id)
    if checkpoint is None or checkpoint.document.conversation.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Checkpoint not found")

    from app.catia.approval import APPROVAL_TTL_S

    return ApprovalResponse(
        approval_token=mint_approval(
            user_id=current_user.id,
            tool=payload.tool,
            conversation_id=payload.conversation_id,
            target=payload.checkpoint_id,
        ),
        expires_in_seconds=APPROVAL_TTL_S,
    )


@router.get("/events")
def catia_events(current_user: CurrentUser) -> StreamingResponse:
    """Server-Sent Events: daemon activity for the signed-in user.

    Scoped per user at the bus, so a subscriber can only ever receive its own
    events. Emits an SSE comment every 15 s of silence -- without it a proxy
    reaps an idle stream and the UI shows a bridge that went quiet as a bridge
    that went away.
    """
    subscription = bus.subscribe(current_user.id)

    def stream() -> Iterator[str]:
        # A first frame immediately, so the client knows the stream is live
        # rather than merely accepted.
        yield f"data: {json.dumps(_envelope('stream_open', {}))}\n\n"
        try:
            for event in subscription.listen(_SSE_KEEPALIVE_S):
                if event is None:
                    yield ": keepalive\n\n"
                else:
                    yield f"data: {json.dumps(event, default=str)}\n\n"
        finally:
            subscription.close()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "cache-control": "no-cache",
            # Without this nginx buffers the stream and every event arrives at
            # once, at the end, which is the opposite of the point.
            "x-accel-buffering": "no",
        },
    )


# -- direct COM bridge compatibility endpoints -------------------------------



@router.get("/documents", response_model=list[CatiaDocumentRead])
def catia_documents(current_user: CurrentUser) -> list[CatiaDocumentRead]:
    try:
        return [
            CatiaDocumentRead(name=doc.name, path=doc.path, doc_type=doc.doc_type)
            for doc in list_open_documents()
        ]
    except CATIABridgeError as exc:
        raise _unavailable(exc) from exc


@router.post("/launch", response_model=CatiaStatusRead)
def catia_launch(
    current_user: CurrentUser,
    payload: Annotated[LaunchRequest, Body()] = LaunchRequest(),
) -> CatiaStatusRead:
    """Start CATIA and put it on screen, optionally with a fresh part."""
    try:
        result = launch(visible=True)
        if payload.new_part:
            new_part()
            result = get_status()
    except CATIABridgeError as exc:
        raise _unavailable(exc) from exc
    return _as_read(result)


@router.post(
    "/projects/{project_id}/sync",
    status_code=status.HTTP_201_CREATED,
)
def sync_geometry(
    project: OwnedProject,
    db: DbSession,
    current_user: CurrentUser,
    media: MediaServiceDep,
    payload: Annotated[SyncRequest, Body()] = SyncRequest(),
) -> dict:
    """Export the active CATIA document into the project as a geometry version.

    Shares its implementation with the agent tool, so the button and the
    assistant cannot drift apart.
    """
    from app.ai.tools import ToolBox, ToolError

    toolbox = ToolBox(db=db, user=current_user, project_id=project.id)
    try:
        return toolbox.call(
            "sync_geometry_from_catia",
            {"project_id": project.id, "note": payload.note},
            allow_mutations=True,
        )
    except ToolError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

