"""Kryova's tools over MCP revision 2026-07-28, and the same trust boundary as HTTP (E23.3).

The first half drives `app.ai.mcp.handle` with a fake host and opens no database. The second
goes through the real route, so authentication, ownership and the `ToolBox` are the product's.

**Written on Linux on 2026-09-15 and not run there**, at the user's instruction that the
Windows machine runs the tests.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.ai import mcp
from app.models import Conversation, User
from tests.typing import AuthenticatedTestClient

VERSION = mcp.PROTOCOL_VERSION


def _meta(**extra: Any) -> dict[str, Any]:
    return {
        mcp.META_PROTOCOL_VERSION: VERSION,
        mcp.META_CLIENT_CAPABILITIES: {},
        "io.modelcontextprotocol/clientInfo": {"name": "test-client", "version": "1"},
        **extra,
    }


def _message(method: str, params: dict[str, Any] | None = None, request_id: Any = 1) -> dict[str, Any]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    body["params"] = {"_meta": _meta(), **(params or {})}
    return body


def _headers(method: str, name: str | None = None, version: str = VERSION) -> dict[str, str]:
    headers = {"MCP-Protocol-Version": version, "Mcp-Method": method}
    if name is not None:
        headers["Mcp-Name"] = name
    return headers


class FakeHost:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], bool]] = []

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            mcp.tool_definition("measure", "Measuring", "Measure the part.", {}),
            mcp.tool_definition(
                "pad",
                "Padding",
                "Pad a sketch.",
                {"type": "object", "properties": {"length_mm": {"type": "number"}}, "required": ["length_mm"]},
            ),
        ]

    def call_tool(self, name: str, arguments: dict[str, Any], *, allow_mutations: bool) -> mcp.ToolOutcome:
        self.calls.append((name, arguments, allow_mutations))
        if name == "measure":
            return mcp.ToolOutcome(ok=True, value={"volume_mm3": 1000.0})
        if name == "pad":
            if not allow_mutations:
                return mcp.ToolOutcome(ok=False, message="pad changes state and needs confirmation.")
            return mcp.ToolOutcome(ok=True, value={"feature": "Pad.1"})
        raise mcp.UnknownTool(name)


def _handle(body: Any, headers: dict[str, str], host: FakeHost | None = None) -> mcp.Reply:
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    return mcp.handle(raw, headers, host or FakeHost(), server_version="test")


class TestTheEnvelope:
    def test_discover_names_the_version_the_tools_and_the_server(self) -> None:
        reply = _handle(_message("server/discover"), _headers("server/discover"))
        assert reply.status == 200
        result = reply.body["result"]  # type: ignore[index]
        assert result["resultType"] == "complete"
        assert result["supportedVersions"] == [VERSION]
        assert result["capabilities"] == {"tools": {}}
        assert result["_meta"][mcp.META_SERVER_INFO]["name"] == "kryova"
        assert reply.body["id"] == 1  # type: ignore[index]

    def test_body_that_is_not_json_is_a_parse_error(self) -> None:
        reply = _handle(b"{not json", _headers("tools/list"))
        assert (reply.status, reply.body["error"]["code"]) == (400, mcp.PARSE_ERROR)  # type: ignore[index]

    def test_a_batch_is_refused(self) -> None:
        reply = _handle([_message("tools/list")], _headers("tools/list"))
        assert reply.body["error"]["code"] == mcp.INVALID_REQUEST  # type: ignore[index]

    def test_a_notification_is_accepted_with_no_body(self) -> None:
        reply = _handle({"jsonrpc": "2.0", "method": "notifications/whatever"}, {})
        assert (reply.status, reply.body) == (202, None)

    @pytest.mark.parametrize("bad_id", [None, True, 1.5, {"a": 1}])
    def test_an_id_that_is_not_a_string_or_integer_is_refused(self, bad_id: Any) -> None:
        reply = _handle(_message("tools/list", request_id=bad_id), _headers("tools/list"))
        assert reply.body["error"]["code"] == mcp.INVALID_REQUEST  # type: ignore[index]

    def test_an_unknown_method_is_404_with_method_not_found(self) -> None:
        reply = _handle(_message("prompts/list"), _headers("prompts/list"))
        assert (reply.status, reply.body["error"]["code"]) == (404, mcp.METHOD_NOT_FOUND)  # type: ignore[index]


class TestEveryRequestCarriesItsOwnMetadata:
    @pytest.mark.parametrize("missing", [mcp.META_PROTOCOL_VERSION, mcp.META_CLIENT_CAPABILITIES])
    def test_a_missing_required_meta_field_is_invalid_params(self, missing: str) -> None:
        body = _message("tools/list")
        del body["params"]["_meta"][missing]
        reply = _handle(body, _headers("tools/list"))
        assert (reply.status, reply.body["error"]["code"]) == (400, mcp.INVALID_PARAMS)  # type: ignore[index]
        assert missing in reply.body["error"]["message"]  # type: ignore[index]

    def test_no_meta_at_all_is_invalid_params(self) -> None:
        reply = _handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, _headers("tools/list"))
        assert reply.body["error"]["code"] == mcp.INVALID_PARAMS  # type: ignore[index]

    def test_an_unsupported_version_lists_what_is_supported(self) -> None:
        body = _message("tools/list")
        body["params"]["_meta"][mcp.META_PROTOCOL_VERSION] = "1900-01-01"
        reply = _handle(body, _headers("tools/list", version="1900-01-01"))
        assert reply.status == 400
        error = reply.body["error"]  # type: ignore[index]
        assert error["code"] == mcp.UNSUPPORTED_PROTOCOL_VERSION
        assert error["data"] == {"supported": [VERSION], "requested": "1900-01-01"}

    def test_a_legacy_initialize_is_told_which_version_to_speak(self) -> None:
        body = {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}}
        reply = _handle(body, {})
        error = reply.body["error"]  # type: ignore[index]
        assert error["code"] == mcp.UNSUPPORTED_PROTOCOL_VERSION
        assert error["data"]["supported"] == [VERSION]
        assert VERSION in error["message"]


class TestTheHeadersMustAgreeWithTheBody:
    def test_a_missing_protocol_version_header(self) -> None:
        reply = _handle(_message("tools/list"), {"Mcp-Method": "tools/list"})
        assert (reply.status, reply.body["error"]["code"]) == (400, mcp.HEADER_MISMATCH)  # type: ignore[index]

    def test_a_method_header_that_names_another_method(self) -> None:
        reply = _handle(_message("tools/list"), _headers("server/discover"))
        assert reply.body["error"]["code"] == mcp.HEADER_MISMATCH  # type: ignore[index]

    def test_header_names_are_case_insensitive(self) -> None:
        headers = {"mcp-protocol-version": VERSION, "MCP-METHOD": "tools/list"}
        assert _handle(_message("tools/list"), headers).status == 200

    def test_a_call_needs_the_name_header_to_match(self) -> None:
        body = _message("tools/call", {"name": "measure", "arguments": {}})
        assert _handle(body, _headers("tools/call")).body["error"]["code"] == mcp.HEADER_MISMATCH  # type: ignore[index]
        assert _handle(body, _headers("tools/call", "pad")).body["error"]["code"] == mcp.HEADER_MISMATCH  # type: ignore[index]

    def test_a_base64_name_header_is_decoded_before_comparing(self) -> None:
        encoded = "=?base64?" + base64.b64encode(b"measure").decode() + "?="
        body = _message("tools/call", {"name": "measure", "arguments": {}})
        assert _handle(body, _headers("tools/call", encoded)).status == 200

    def test_a_malformed_base64_sentinel_is_a_mismatch(self) -> None:
        body = _message("tools/call", {"name": "measure", "arguments": {}})
        reply = _handle(body, _headers("tools/call", "=?base64?!!!?="))
        assert reply.body["error"]["code"] == mcp.HEADER_MISMATCH  # type: ignore[index]


class TestTools:
    def test_list_returns_the_hosts_tools_on_one_page(self) -> None:
        reply = _handle(_message("tools/list"), _headers("tools/list"))
        result = reply.body["result"]  # type: ignore[index]
        assert [tool["name"] for tool in result["tools"]] == ["measure", "pad"]
        assert "nextCursor" not in result

    def test_a_tool_with_no_parameters_is_written_as_the_spec_recommends(self) -> None:
        tool = mcp.tool_definition("t", "T", "d", {"type": "object", "properties": {}, "required": []})
        assert tool["inputSchema"] == {"type": "object", "additionalProperties": False}

    def test_a_cursor_this_server_never_issued_is_refused(self) -> None:
        reply = _handle(_message("tools/list", {"cursor": "abc"}), _headers("tools/list"))
        assert reply.body["error"]["code"] == mcp.INVALID_PARAMS  # type: ignore[index]

    def test_a_call_returns_text_and_structured_content(self) -> None:
        body = _message("tools/call", {"name": "measure", "arguments": {}})
        result = _handle(body, _headers("tools/call", "measure")).body["result"]  # type: ignore[index]
        assert result["isError"] is False
        assert result["structuredContent"] == {"volume_mm3": 1000.0}
        assert json.loads(result["content"][0]["text"]) == {"volume_mm3": 1000.0}

    def test_an_unknown_tool_is_a_protocol_error(self) -> None:
        body = _message("tools/call", {"name": "nope", "arguments": {}})
        reply = _handle(body, _headers("tools/call", "nope"))
        assert reply.body["error"] == {"code": mcp.INVALID_PARAMS, "message": "Unknown tool: nope"}  # type: ignore[index]

    def test_a_refusal_is_a_tool_execution_error_with_the_tools_sentence(self) -> None:
        host = FakeHost()
        body = _message("tools/call", {"name": "pad", "arguments": {"length_mm": 5}})
        result = _handle(body, _headers("tools/call", "pad"), host).body["result"]  # type: ignore[index]
        assert result["isError"] is True
        assert "needs confirmation" in result["content"][0]["text"]
        assert host.calls == [("pad", {"length_mm": 5}, False)]

    def test_consent_to_mutate_is_read_from_meta_and_only_as_true(self) -> None:
        for value, expected in [(True, True), ("true", False), (1, False)]:
            host = FakeHost()
            body = _message("tools/call", {"name": "pad", "arguments": {"length_mm": 5}})
            body["params"]["_meta"][mcp.META_ALLOW_MUTATIONS] = value
            _handle(body, _headers("tools/call", "pad"), host)
            assert host.calls[0][2] is expected

    def test_arguments_that_are_not_an_object_are_invalid_params(self) -> None:
        body = _message("tools/call", {"name": "measure", "arguments": [1]})
        reply = _handle(body, _headers("tools/call", "measure"))
        assert reply.body["error"]["code"] == mcp.INVALID_PARAMS  # type: ignore[index]


class TestTopLevelArgumentChecks:
    SCHEMA = {"type": "object", "properties": {"a": {}, "b": {}}, "required": ["a"]}

    def test_complete_arguments_have_no_problem(self) -> None:
        assert mcp.missing_or_unknown_arguments(self.SCHEMA, {"a": 1}) == ""

    def test_missing_and_unknown_are_both_named(self) -> None:
        problem = mcp.missing_or_unknown_arguments(self.SCHEMA, {"c": 1})
        assert "missing required argument(s): a" in problem
        assert "unknown argument(s): c" in problem


# -- through the real route ---------------------------------------------------------------

API = "/api/v1"


@pytest.fixture
def account(db_session: Session, auth_client: AuthenticatedTestClient) -> User:
    user = db_session.get(User, auth_client.get(f"{API}/auth/me").json()["id"])
    assert user is not None
    return user


@pytest.fixture
def conversation(db_session: Session, account: User) -> Conversation:
    row = Conversation(owner_id=account.id, title="Over MCP")
    db_session.add(row)
    # Committed, not flushed: the route commits after a tool call, and the session's savepoint
    # mode means a later rollback would otherwise take an uncommitted row with it.
    db_session.commit()
    return row


def _post(client: Any, conversation_id: str, body: dict[str, Any], name: str | None = None) -> Any:
    return client.post(
        f"{API}/mcp/conversations/{conversation_id}",
        content=json.dumps(body),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **_headers(body["method"], name),
        },
    )


class TestTheRouteIsTheSameTrustBoundary:
    def test_no_token_is_401(self, client: Any, db_session: Session) -> None:
        response = client.post(
            f"{API}/mcp/conversations/any", content=json.dumps(_message("tools/list")), headers=_headers("tools/list")
        )
        assert response.status_code == 401

    def test_another_users_conversation_is_404(
        self, auth_client: AuthenticatedTestClient, db_session: Session
    ) -> None:
        from app.core.security import hash_password

        stranger = User(email="elsewhere@kryova.dev", hashed_password=hash_password("a-long-enough-password"))
        db_session.add(stranger)
        db_session.flush()
        theirs = Conversation(owner_id=stranger.id, title="Theirs")
        db_session.add(theirs)
        db_session.flush()
        assert _post(auth_client, theirs.id, _message("tools/list")).status_code == 404

    def test_an_origin_not_in_cors_origins_is_403(
        self, auth_client: AuthenticatedTestClient, conversation: Conversation
    ) -> None:
        response = auth_client.post(
            f"{API}/mcp/conversations/{conversation.id}",
            content=json.dumps(_message("tools/list")),
            headers={**_headers("tools/list"), "Origin": "https://attacker.example"},
        )
        assert response.status_code == 403

    def test_get_is_405(self, auth_client: AuthenticatedTestClient, conversation: Conversation) -> None:
        assert auth_client.get(f"{API}/mcp/conversations/{conversation.id}").status_code == 405


class TestTheRouteOffersTheToolBox:
    def test_tools_list_is_exactly_the_toolbox_vocabulary(
        self,
        auth_client: AuthenticatedTestClient,
        db_session: Session,
        account: User,
        conversation: Conversation,
    ) -> None:
        from app.ai.tools import ToolBox

        response = _post(auth_client, conversation.id, _message("tools/list"))
        assert response.status_code == 200, response.text
        names = [tool["name"] for tool in response.json()["result"]["tools"]]
        expected = sorted(tool.name for tool in ToolBox(db=db_session, user=account, conversation=conversation).every_tool())
        assert names == expected
        assert "list_materials" in names
        assert "draft_load_case" not in names

    def test_a_read_only_tool_runs_through_the_toolbox(
        self, auth_client: AuthenticatedTestClient, conversation: Conversation
    ) -> None:
        body = _message("tools/call", {"name": "list_materials", "arguments": {}})
        result = _post(auth_client, conversation.id, body, "list_materials").json()["result"]
        assert result["isError"] is False
        assert result["structuredContent"]["materials"]

    def test_a_mutating_tool_without_consent_refuses_in_the_toolboxs_words(
        self, auth_client: AuthenticatedTestClient, conversation: Conversation
    ) -> None:
        body = _message("tools/call", {"name": "delete_simulation", "arguments": {"simulation_id": "x"}})
        result = _post(auth_client, conversation.id, body, "delete_simulation").json()["result"]
        assert result["isError"] is True
        assert "confirmation" in result["content"][0]["text"]

    def test_an_undeclared_argument_is_named_before_the_handler_runs(
        self, auth_client: AuthenticatedTestClient, conversation: Conversation
    ) -> None:
        body = _message("tools/call", {"name": "list_materials", "arguments": {"grade": "S355"}})
        result = _post(auth_client, conversation.id, body, "list_materials").json()["result"]
        assert result["isError"] is True
        assert "unknown argument(s): grade" in result["content"][0]["text"]
