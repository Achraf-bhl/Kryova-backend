"""Kryova's tool registry spoken as an MCP server -- master plan E23.3.

The incumbent named MCP as its third-party integration surface, so a customer's own agent can
drive Kryova through a protocol somebody else standardises. **It is another caller, not another
trust boundary**: the route in `app/api/routes/mcp.py` authenticates, scopes and rate-limits
exactly as the HTTP API does, and every call lands in the same `ToolBox.call` the chat agent
uses. This module is the wire format only, with no session and no FastAPI import, so every rule
below is testable without a database.

**Written against MCP revision 2026-07-28**, read on 2026-09-15 from
https://modelcontextprotocol.io/specification/2026-07-28 -- the base protocol (`/basic`), version
negotiation (`/basic/versioning`), Streamable HTTP (`/basic/transports/streamable-http`),
`server/discover` (`/server/discover`) and tools (`/server/tools`). Nothing here is from memory
of an earlier revision, and that matters: 2026-07-28 removed the `initialize` handshake and
protocol-level sessions. What that revision says, and what this module does with it:

* **Stateless.** Every request carries `_meta["io.modelcontextprotocol/protocolVersion"]` and
  `_meta["io.modelcontextprotocol/clientCapabilities"]`; a request missing either is refused
  with `-32602` and HTTP 400. Nothing is remembered between requests. The conversation a call
  acts on is named in the endpoint's path, which is the "explicit identifier the client passes
  on each request" the spec asks for state to be.
* **Headers mirror the body and must agree with it.** `MCP-Protocol-Version` and `Mcp-Method` on
  every request, `Mcp-Name` on `tools/call`. Missing or different is `-32020` HeaderMismatch with
  HTTP 400. A base64 sentinel value (`=?base64?...?=`) is decoded before comparing.
* **Versions.** Only 2026-07-28 is served. Any other is `-32022` with `supported` and
  `requested`, HTTP 400. A legacy `initialize` gets the same error naming the supported
  version, because the spec says a modern-only server SHOULD name its versions there: a legacy
  client has no fall-forward and this is its only diagnostic.
* **One message per POST.** A JSON array is refused as an invalid request. A notification (no
  `id`) is accepted with 202 and no body; the core protocol defines none over HTTP.
* **Methods.** `server/discover` (MUST), `tools/list`, `tools/call`. Anything else is `-32601`
  with HTTP 404, which the spec requires so a client can tell a modern server's unknown method
  from a legacy server's missing endpoint.
* **Two kinds of failure, as the tools page separates them.** An unknown tool or a malformed
  call is a protocol error (`-32602`). A tool that ran and refused -- a `ToolError`, bad
  arguments, a mutation without consent -- is a result with `isError: true` carrying the tool's
  own sentence, because that is the text a model can correct itself from.

**Mutations need the caller to say so, per request.** The chat API refuses a mutating tool
unless the user's request carries `allow_mutations`; here the same consent is the `_meta` key
`kryova/allowMutations: true` (a vendor prefix under the spec's key-name rules). Without it the
tool's own refusal comes back as a tool execution error, exactly as the agent would see it.

**Input validation is partial, and says so.** The spec says servers MUST validate tool inputs.
No JSON Schema library is installed, so `missing_or_unknown_arguments` checks the top level --
every `required` name present, no name the schema does not declare -- and each handler checks
types and values the way it already does for the chat agent. A nested type error is therefore
reported by the handler, not by a schema validator.

Not here: SSE responses (every reply is `application/json`, which the spec permits),
`subscriptions/listen`, `listChanged`, resources, prompts, and progress notifications.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

PROTOCOL_VERSION = "2026-07-28"
SUPPORTED_VERSIONS: tuple[str, ...] = (PROTOCOL_VERSION,)

META_PROTOCOL_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CLIENT_CAPABILITIES = "io.modelcontextprotocol/clientCapabilities"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"
#: Kryova's own `_meta` key: this request may run tools that change state.
META_ALLOW_MUTATIONS = "kryova/allowMutations"

HEADER_PROTOCOL_VERSION = "mcp-protocol-version"
HEADER_METHOD = "mcp-method"
HEADER_NAME = "mcp-name"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
HEADER_MISMATCH = -32020
UNSUPPORTED_PROTOCOL_VERSION = -32022

SERVER_NAME = "kryova"
INSTRUCTIONS = (
    "Kryova designs, analyses and documents machine parts. Every tool acts on the one "
    "conversation named in this endpoint's URL. A tool that changes state needs "
    f"`_meta[\"{META_ALLOW_MUTATIONS}\"]: true` on the request; without it the tool refuses "
    "in words. A measurement that could not be made comes back UNMEASURED, which is not a pass."
)

_SENTINEL_OPEN = "=?base64?"
_SENTINEL_CLOSE = "?="


class UnknownTool(LookupError):
    """The host has no tool by that name."""


@dataclass(frozen=True)
class ToolOutcome:
    """What a tool did: a value, or the sentence it refused with."""

    ok: bool
    value: Any = None
    message: str = ""


class ToolHost(Protocol):
    """What the protocol needs from whoever holds the tools."""

    def list_tools(self) -> list[dict[str, Any]]:
        """MCP `Tool` objects, in a deterministic order."""
        ...

    def call_tool(
        self, name: str, arguments: dict[str, Any], *, allow_mutations: bool
    ) -> ToolOutcome:
        """Run one tool. Raises `UnknownTool` for a name it does not hold."""
        ...


@dataclass(frozen=True)
class Reply:
    """An HTTP status and the JSON body to send, or no body."""

    status: int
    body: dict[str, Any] | None


def error_body(request_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "error": {"code": code, "message": message}}
    if request_id is not None:
        body["id"] = request_id
    if data is not None:
        body["error"]["data"] = data
    return body


def _result(request_id: Any, payload: dict[str, Any], server_version: str) -> Reply:
    result = {
        "resultType": "complete",
        **payload,
        "_meta": {META_SERVER_INFO: {"name": SERVER_NAME, "version": server_version}},
    }
    return Reply(200, {"jsonrpc": "2.0", "id": request_id, "result": result})


def _fail(request_id: Any, status: int, code: int, message: str, data: Any = None) -> Reply:
    return Reply(status, error_body(request_id, code, message, data))


def decode_header_value(value: str) -> str | None:
    """A header value as the body would spell it, or None when the sentinel is malformed."""
    if value.startswith(_SENTINEL_OPEN) and value.endswith(_SENTINEL_CLOSE):
        encoded = value[len(_SENTINEL_OPEN) : -len(_SENTINEL_CLOSE)]
        try:
            return base64.b64decode(encoded, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            return None
    return value


def tool_definition(
    name: str, title: str, description: str, parameters: dict[str, Any]
) -> dict[str, Any]:
    """One MCP `Tool`. A schema with no parameters is written as the spec recommends."""
    schema = dict(parameters) if parameters else {}
    if not schema.get("properties"):
        schema = {"type": "object", "additionalProperties": False}
    return {"name": name, "title": title, "description": description, "inputSchema": schema}


def missing_or_unknown_arguments(schema: Mapping[str, Any], arguments: Mapping[str, Any]) -> str:
    """The top-level argument problems in one sentence, or an empty string."""
    declared = set((schema.get("properties") or {}).keys())
    required = [name for name in schema.get("required") or [] if name not in arguments]
    unknown = sorted(set(arguments) - declared)
    problems = []
    if required:
        problems.append(f"missing required argument(s): {', '.join(required)}")
    if unknown:
        problems.append(
            f"unknown argument(s): {', '.join(unknown)}; this tool takes "
            f"{', '.join(sorted(declared)) or 'no arguments'}"
        )
    return "; ".join(problems)


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def handle(
    raw: bytes,
    headers: Mapping[str, str],
    host: ToolHost,
    *,
    server_version: str,
) -> Reply:
    """One POST to the MCP endpoint, start to finish.

    `headers` may use any case; names are compared case-insensitively, values exactly.
    """
    lowered = {key.lower(): value for key, value in headers.items()}
    try:
        message = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return _fail(None, 400, PARSE_ERROR, "The body is not JSON.")
    if isinstance(message, list):
        return _fail(
            None, 400, INVALID_REQUEST, "Send one JSON-RPC message per POST; batches are not accepted."
        )
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _fail(None, 400, INVALID_REQUEST, 'A JSON-RPC 2.0 message needs "jsonrpc": "2.0".')
    method = message.get("method")
    if not isinstance(method, str):
        return _fail(message.get("id"), 400, INVALID_REQUEST, "A request needs a method name.")

    if "id" not in message:
        # A notification. The core protocol defines none over Streamable HTTP, so there is
        # nothing to act on; accepting it is what the transport asks for.
        return Reply(202, None)
    request_id = message["id"]
    if isinstance(request_id, bool) or not isinstance(request_id, (str, int)):
        return _fail(None, 400, INVALID_REQUEST, "A request id must be a string or an integer, never null.")

    params = message.get("params", {})
    if not isinstance(params, dict):
        return _fail(request_id, 400, INVALID_PARAMS, "params must be an object.")

    if method == "initialize":
        requested = params.get("protocolVersion")
        return _fail(
            request_id,
            400,
            UNSUPPORTED_PROTOCOL_VERSION,
            f"This server speaks MCP {', '.join(SUPPORTED_VERSIONS)} only, which has no initialize "
            "handshake: send each request with its protocol version in _meta.",
            {"supported": list(SUPPORTED_VERSIONS), "requested": requested},
        )

    meta = params.get("_meta")
    if not isinstance(meta, dict):
        return _fail(request_id, 400, INVALID_PARAMS, "params._meta is required on every request.")
    version = meta.get(META_PROTOCOL_VERSION)
    if not isinstance(version, str):
        return _fail(request_id, 400, INVALID_PARAMS, f"_meta is missing {META_PROTOCOL_VERSION}.")
    if not isinstance(meta.get(META_CLIENT_CAPABILITIES), dict):
        return _fail(request_id, 400, INVALID_PARAMS, f"_meta is missing {META_CLIENT_CAPABILITIES}.")

    header_version = lowered.get(HEADER_PROTOCOL_VERSION)
    if header_version != version:
        return _fail(
            request_id,
            400,
            HEADER_MISMATCH,
            f"Header mismatch: MCP-Protocol-Version {header_version!r} does not match body value "
            f"{version!r}.",
        )
    header_method = lowered.get(HEADER_METHOD)
    if header_method != method:
        return _fail(
            request_id,
            400,
            HEADER_MISMATCH,
            f"Header mismatch: Mcp-Method {header_method!r} does not match body value {method!r}.",
        )
    if version not in SUPPORTED_VERSIONS:
        return _fail(
            request_id,
            400,
            UNSUPPORTED_PROTOCOL_VERSION,
            "Unsupported protocol version",
            {"supported": list(SUPPORTED_VERSIONS), "requested": version},
        )

    if method == "server/discover":
        return _result(
            request_id,
            {
                "supportedVersions": list(SUPPORTED_VERSIONS),
                "capabilities": {"tools": {}},
                "instructions": INSTRUCTIONS,
            },
            server_version,
        )
    if method == "tools/list":
        if params.get("cursor") is not None:
            # Every tool is returned on one page and no nextCursor is ever issued, so any
            # cursor is one this server did not hand out.
            return _fail(request_id, 400, INVALID_PARAMS, "Unknown cursor: the tool list is one page.")
        return _result(request_id, {"tools": host.list_tools()}, server_version)
    if method == "tools/call":
        return _call(request_id, params, meta, lowered, host, server_version)
    return _fail(request_id, 404, METHOD_NOT_FOUND, f"Method not found: {method}")


def _call(
    request_id: Any,
    params: dict[str, Any],
    meta: dict[str, Any],
    headers: Mapping[str, str],
    host: ToolHost,
    server_version: str,
) -> Reply:
    name = params.get("name")
    if not isinstance(name, str) or not name:
        return _fail(request_id, 400, INVALID_PARAMS, "tools/call needs params.name.")
    header_name = headers.get(HEADER_NAME)
    decoded = None if header_name is None else decode_header_value(header_name)
    if decoded != name:
        return _fail(
            request_id,
            400,
            HEADER_MISMATCH,
            f"Header mismatch: Mcp-Name {header_name!r} does not match body value {name!r}.",
        )
    arguments = params.get("arguments", {})
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return _fail(request_id, 400, INVALID_PARAMS, "params.arguments must be an object.")
    allow_mutations = meta.get(META_ALLOW_MUTATIONS) is True
    try:
        outcome = host.call_tool(name, arguments, allow_mutations=allow_mutations)
    except UnknownTool:
        return _fail(request_id, 400, INVALID_PARAMS, f"Unknown tool: {name}")
    if not outcome.ok:
        return _result(
            request_id,
            {"content": [{"type": "text", "text": outcome.message}], "isError": True},
            server_version,
        )
    value = _json_safe(outcome.value)
    payload: dict[str, Any] = {
        "content": [{"type": "text", "text": json.dumps(value)}],
        "isError": False,
    }
    if isinstance(value, dict):
        payload["structuredContent"] = value
    return _result(request_id, payload, server_version)


__all__ = [
    "HEADER_MISMATCH",
    "INSTRUCTIONS",
    "INTERNAL_ERROR",
    "INVALID_PARAMS",
    "INVALID_REQUEST",
    "META_ALLOW_MUTATIONS",
    "META_CLIENT_CAPABILITIES",
    "META_PROTOCOL_VERSION",
    "METHOD_NOT_FOUND",
    "PARSE_ERROR",
    "PROTOCOL_VERSION",
    "Reply",
    "SUPPORTED_VERSIONS",
    "ToolHost",
    "ToolOutcome",
    "UNSUPPORTED_PROTOCOL_VERSION",
    "UnknownTool",
    "decode_header_value",
    "error_body",
    "handle",
    "missing_or_unknown_arguments",
    "tool_definition",
]
