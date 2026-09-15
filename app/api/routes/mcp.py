"""The MCP endpoint: one conversation's tools, over the protocol the incumbent chose (E23.3).

`app/ai/mcp.py` is the wire format and has the reading record for the specification. This
module is only the binding, and its rule is that **MCP is another caller, not another trust
boundary**:

* **Authentication** is `CurrentUser`, the same dependency every other route uses, so a bearer
  token, impersonation's read-only rule, CSRF for cookie callers, maintenance mode and the RLS
  tenant scope all apply unchanged. No token is 401.
* **Ownership** is the chat API's own `_owned_conversation`: another user's conversation is 404,
  never 403.
* **Rate limiting** is its own budget, `mcp_requests_per_minute`, keyed per principal like
  every other limit.
* **The `Origin` header** is checked against `cors_origins` before anything else, because the
  transport says a server MUST, against DNS rebinding. Absent is allowed (a non-browser client
  sends none); present and not listed is 403.
* **The tools are `ToolBox`'s**, built with the same collaborators the chat route wires, so a
  tool reached through MCP and the same tool reached by the agent cannot drift apart.

**One endpoint per conversation**, `POST /mcp/conversations/{conversation_id}`. The spec wants a
single endpoint per server and no protocol session; a CATIA document and a design belong to one
conversation (see CLAUDE.md, *A conversation acts on the document it owns*), so the conversation
is the server, and its id travels on every request in the URL. GET and DELETE are answered 405
by the router, which is what the spec asks of a modern server receiving legacy traffic.

**`draft_load_case` is not offered here.** It calls Kryova's own model to turn a sentence into a
load case; an MCP caller brings its own model, and building the provider would put a health
check against Ollama in front of every `tools/list`. `ToolBox` withholds that tool when it has
no provider, so it is absent rather than offered and refused.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse

from app.ai import mcp
from app.ai.tools import ToolBox, ToolError, tool_label
from app.api.deps import (
    CurrentUser,
    DbSession,
    JobQueueDep,
    MediaServiceDep,
    MediaStoreDep,
    SessionScopeDep,
)
from app.api.rate_limit import RateLimit
from app.api.routes.ai import _owned_conversation
from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp", tags=["mcp"])

#: `serverInfo.version` is required to be a string and this service has no release number.
SERVER_VERSION = "unreleased"

_mcp_rate_limit = RateLimit("mcp", settings.mcp_requests_per_minute, window_seconds=60)


def _origin_guard(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin is None:
        return
    allowed = {entry.strip().rstrip("/") for entry in settings.cors_origins if entry.strip()}
    if origin.rstrip("/") not in allowed:
        raise _OriginRefused(origin)


class _OriginRefused(Exception):
    def __init__(self, origin: str) -> None:
        super().__init__(origin)
        self.origin = origin


async def _raw_body(request: Request) -> bytes:
    """The body as sent. Parsed by `mcp.handle`, so a bad body is -32700 rather than a 422."""
    return await request.body()


class ToolBoxHost:
    """`mcp.ToolHost` over one conversation's `ToolBox`."""

    def __init__(self, toolbox: ToolBox, db: Any) -> None:
        self._toolbox = toolbox
        self._db = db
        self._tools = {tool.name: tool for tool in toolbox.every_tool()}

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            mcp.tool_definition(name, tool_label(name), tool.description, tool.parameters)
            for name, tool in sorted(self._tools.items())
        ]

    def call_tool(
        self, name: str, arguments: dict[str, Any], *, allow_mutations: bool
    ) -> mcp.ToolOutcome:
        tool = self._tools.get(name)
        if tool is None:
            raise mcp.UnknownTool(name)
        problem = mcp.missing_or_unknown_arguments(tool.parameters or {}, arguments)
        if problem:
            return mcp.ToolOutcome(ok=False, message=f"Bad arguments for {name}: {problem}.")
        # Committed whether the tool worked or refused, as the agent loop does: a refused CATIA
        # call is still a row in the operation log, and that log is the record of what was done.
        try:
            value = self._toolbox.call(name, arguments, allow_mutations=allow_mutations)
        except ToolError as exc:
            self._db.commit()
            return mcp.ToolOutcome(ok=False, message=str(exc))
        self._db.commit()
        return mcp.ToolOutcome(ok=True, value=value)


@router.post(
    "/conversations/{conversation_id}",
    dependencies=[Depends(_mcp_rate_limit)],
    responses={202: {"description": "A notification was accepted."}},
)
def mcp_endpoint(
    conversation_id: str,
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
    queue: JobQueueDep,
    session_scope: SessionScopeDep,
    store: MediaStoreDep,
    media: MediaServiceDep,
    raw: Annotated[bytes, Depends(_raw_body)],
) -> Response:
    """One MCP message for this conversation's tools. See `app/ai/mcp.py` for the protocol."""
    try:
        _origin_guard(request)
    except _OriginRefused as refused:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content=mcp.error_body(
                None, mcp.INVALID_REQUEST, f"Origin {refused.origin!r} is not allowed."
            ),
        )
    conversation = _owned_conversation(db, current_user, conversation_id)
    toolbox = ToolBox(
        db=db,
        user=current_user,
        project_id=conversation.project_id,
        conversation=conversation,
        job_queue=queue,
        session_scope=session_scope,
        media_store=store,
        media=media,
    )
    try:
        reply = mcp.handle(
            raw, request.headers, ToolBoxHost(toolbox, db), server_version=SERVER_VERSION
        )
    except Exception:
        logger.exception("MCP request failed for conversation %s", conversation.id)
        db.rollback()
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=mcp.error_body(None, mcp.INTERNAL_ERROR, "Unexpected server error."),
        )
    # The agent adopts a project a tool created; so does this caller.
    if conversation.project_id is None and toolbox.project_id:
        conversation.project_id = toolbox.project_id
        db.commit()
    if reply.body is None:
        return Response(status_code=reply.status)
    return JSONResponse(status_code=reply.status, content=reply.body)
