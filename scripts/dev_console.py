"""A live client for a running dev backend -- send a turn, watch it happen.

Companion to `scripts/create_admin.py` (the account) and `scripts/shot.ps1`
(the picture): this is the third leg, the conversation itself. Verifying a
change to the agent loop, a provider, or the CATIA bridge used to mean
hand-rolling `curl` for login, cookies and CSRF, a second terminal for
`tail -f` on the server log, and eyeballing which lines in the second
terminal belonged to which request in the first. `send` does both in one
process: the SSE stream on the left of the merge, the backend's own log
lines on the right, interleaved by the time they actually happened.

Dev-only, deliberately. It logs in over HTTP exactly the way the browser
does -- no bypass, no service token, nothing this script can do that a
person with the password could not -- and everything it prints was already
true of the running process; this only saves the two terminals and the
manual interleaving. It has no place in a deployed image and does not
belong on `AI_PROVIDER=none` or a machine that is not `localhost`.

    venv\\Scripts\\python.exe scripts\\create_admin.py
    venv\\Scripts\\python.exe scripts\\dev_console.py send "what is 2+2"
    venv\\Scripts\\python.exe scripts\\dev_console.py send "make a 40mm cube" --mutate --log C:\\path\\backend.log
    venv\\Scripts\\python.exe scripts\\dev_console.py conversations
    venv\\Scripts\\python.exe scripts\\dev_console.py forget "buil a random easy thing"

The session (cookies + CSRF token) is cached in `.dev_session.json` next to
this file -- gitignored, and refused outright against anything but a local
host, the same guard `create_admin.py` uses and for the same reason: this
file is a bearer credential, and the blast radius of it leaking is an
account, not a read-only report.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

DEFAULT_SERVER = "http://localhost:8000"
SESSION_PATH = Path(__file__).with_name(".dev_session.json")

#: Hosts this script will act against without a fight. Matches
#: `create_admin.py::LOCAL_HOSTS` -- a session file is a bearer credential,
#: and pointing this at a real deployment is the same mistake that guard
#: exists to catch, just one script over.
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _require_local(server: str) -> None:
    host = (urlparse(server).hostname or "").lower()
    if host not in LOCAL_HOSTS:
        raise SystemExit(
            f"Refusing: {server!r} is not a local host. This script signs in and "
            "keeps a live session on disk -- it is dev tooling for a machine you "
            "are sitting at, not something to point at a real deployment."
        )


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]


def _print(channel: str, text: str) -> None:
    # One shared timestamp format across both channels is the entire point --
    # without it, "which happened first" is a manual cross-reference between
    # two terminals, which is the exact friction this script exists to remove.
    for line in text.splitlines() or [""]:
        print(f"{_ts()} [{channel}] {line}", flush=True)


class DevSession:
    """A logged-in `httpx.Client`, cached to disk between invocations.

    Not a general HTTP client wrapper: it knows exactly the two headers the
    real frontend's `lib/api-client.ts` adds by hand (cookies, `x-csrf-token`)
    and nothing else, on purpose -- the same contract a browser tab holds, so
    a bug that only shows up under the real auth flow shows up here too.
    """

    def __init__(self, server: str):
        self.server = server.rstrip("/")
        self.client = httpx.Client(base_url=self.server, timeout=30.0)
        self.csrf: str | None = None

    @classmethod
    def load_or_login(cls, server: str, email: str, password: str) -> "DevSession":
        session = cls(server)
        if SESSION_PATH.exists():
            try:
                saved = json.loads(SESSION_PATH.read_text())
                if saved.get("server") == server:
                    for cookie in saved["cookies"]:
                        session.client.cookies.set(
                            cookie["name"], cookie["value"],
                            domain=cookie.get("domain") or "", path=cookie.get("path") or "/",
                        )
                    session.csrf = saved["csrf"]
                    who = session.client.get("/api/v1/auth/me")
                    if who.status_code == 200:
                        return session
            except (json.JSONDecodeError, KeyError, httpx.HTTPError):
                pass  # Fall through to a fresh login; a stale cache is not fatal.
        session.login(email, password)
        return session

    def login(self, email: str, password: str) -> None:
        response = self.client.post(
            "/api/v1/auth/login", data={"username": email, "password": password}
        )
        response.raise_for_status()
        body = response.json()
        if "challenge_token" in body:
            raise SystemExit(
                "This account has two-factor auth enabled; dev_console has no "
                "prompt for a second factor. Use an account without MFA."
            )
        self.csrf = body["csrf_token"]
        SESSION_PATH.write_text(
            json.dumps(
                {
                    "server": self.server,
                    "cookies": [
                        {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
                        for c in self.client.cookies.jar
                    ],
                    "csrf": self.csrf,
                }
            )
        )

    def _headers(self) -> dict[str, str]:
        return {"x-csrf-token": self.csrf or ""}

    def stream_chat(
        self, message: str, *, conversation_id: str | None, allow_mutations: bool
    ) -> None:
        payload: dict[str, Any] = {"message": message, "allow_mutations": allow_mutations}
        if conversation_id:
            payload["conversation_id"] = conversation_id
        with self.client.stream(
            "POST", "/api/v1/ai/chat/stream", json=payload, headers=self._headers(), timeout=120.0
        ) as response:
            if response.status_code != 200:
                response.read()
                _print("http", f"{response.status_code} {response.text[:300]}")
                return
            for line in response.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                event = json.loads(line[len("data: ") :])
                _print("event", _describe(event))

    def conversations(self) -> list[dict[str, Any]]:
        response = self.client.get("/api/v1/ai/conversations", headers=self._headers())
        response.raise_for_status()
        return list(response.json().get("items", []))

    def delete_conversation(self, conversation_id: str) -> None:
        response = self.client.delete(
            f"/api/v1/ai/conversations/{conversation_id}", headers=self._headers()
        )
        response.raise_for_status()


def _describe(event: dict[str, Any]) -> str:
    """One readable line per SSE event -- the shape the real UI renders,
    not the raw JSON, because the raw JSON is what the frontend never shows
    a human either and reading it is not the same check as reading the
    conversation."""
    kind = event.get("type")
    if kind == "start":
        return f"start  conversation={event.get('conversation_id')}"
    if kind == "thinking":
        return f"thinking  step {event.get('step')}/{event.get('max_steps')}"
    if kind == "narration":
        return f"says  {event.get('content')!r}"
    if kind == "tool_start":
        return f"tool -> {event.get('tool')}  {json.dumps(event.get('arguments'))}"
    if kind == "tool_end":
        ok = "ok" if event.get("ok") else "FAILED"
        return f"tool <- {event.get('tool')} [{ok}] {event.get('duration_ms')}ms  {event.get('summary')}"
    if kind == "message":
        return f"MESSAGE  {event.get('content')!r}"
    if kind == "verification":
        return f"verification  {json.dumps(event.get('outstanding'))}"
    if kind == "error":
        return f"ERROR  {event.get('message')}"
    if kind == "done":
        return (
            f"done  stop_reason={event.get('stop_reason')} steps={event.get('steps')} "
            f"tokens={event.get('prompt_tokens')}+{event.get('completion_tokens')}"
        )
    if kind == "title":
        return f"title  {event.get('title')!r}"
    return json.dumps(event)


def _tail(path: Path, stop: threading.Event) -> None:
    """Print lines appended to `path` after this process started watching.

    Starts at end-of-file on purpose: this is a live view for one `send`
    call, not a log reader, and replaying everything the server has ever
    logged would bury the lines that turn actually produced.
    """
    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except OSError as exc:
        _print("log", f"could not open {path}: {exc}")
        return
    with handle:
        handle.seek(0, 2)
        while not stop.is_set():
            line = handle.readline()
            if not line:
                time.sleep(0.15)
                continue
            _print("log", line.rstrip("\n"))


def cmd_send(args: argparse.Namespace) -> int:
    _require_local(args.server)
    session = DevSession.load_or_login(args.server, args.email, args.password)

    stop = threading.Event()
    tailer: threading.Thread | None = None
    if args.log:
        tailer = threading.Thread(target=_tail, args=(Path(args.log), stop), daemon=True)
        tailer.start()

    try:
        session.stream_chat(
            args.message, conversation_id=args.conversation, allow_mutations=args.mutate
        )
    finally:
        if tailer:
            # The last log lines for this turn are usually written just after
            # the SSE stream closes (the request-timing line, a background
            # bridge reconnect); give the tailer a moment to catch them rather
            # than cutting off mid-turn.
            time.sleep(0.5)
            stop.set()
            tailer.join(timeout=1.0)
    return 0


def cmd_conversations(args: argparse.Namespace) -> int:
    _require_local(args.server)
    session = DevSession.load_or_login(args.server, args.email, args.password)
    for conv in session.conversations():
        print(
            f"{conv['conversation_id']}  {conv.get('title') or '(untitled)'}  "
            f"{conv.get('updated_at', '')}"
        )
    return 0


def cmd_forget(args: argparse.Namespace) -> int:
    """Delete every conversation whose title contains `args.match` (case-
    insensitive). The sidebar's own delete is a hover-revealed icon with no
    keyboard path and no bulk action -- fine for a person clearing one chat,
    tedious for a dev loop that starts a new one every run."""
    _require_local(args.server)
    session = DevSession.load_or_login(args.server, args.email, args.password)
    needle = args.match.lower()
    matched = [c for c in session.conversations() if needle in (c.get("title") or "").lower()]
    if not matched:
        print(f"No conversation title contains {args.match!r}.")
        return 0
    for conv in matched:
        session.delete_conversation(conv["conversation_id"])
        print(f"Deleted {conv['conversation_id']}  {conv.get('title')!r}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--server", default=DEFAULT_SERVER)
    # A dedicated identity, not admin@admin.com: that account is whatever a
    # person at this desk is signed into right now (the desktop app, a real
    # session), and create_admin.py resets the password of whatever account
    # it is pointed at -- fine for provisioning a throwaway, wrong for a
    # session someone else is mid-conversation on.
    parser.add_argument("--email", default="dev-console@kryova.dev")
    parser.add_argument("--password", default="devconsole")
    sub = parser.add_subparsers(dest="command", required=True)

    send = sub.add_parser("send", help="Send one message and stream the turn live.")
    send.add_argument("message")
    send.add_argument("--conversation", default=None, help="Continue this conversation id.")
    send.add_argument("--mutate", action="store_true", help="Set allow_mutations=true.")
    send.add_argument("--log", default=None, help="Tail this backend log file alongside the stream.")
    send.set_defaults(handler=cmd_send)

    conversations = sub.add_parser("conversations", help="List this account's conversations.")
    conversations.set_defaults(handler=cmd_conversations)

    forget = sub.add_parser("forget", help="Delete conversations whose title contains MATCH.")
    forget.add_argument("match")
    forget.set_defaults(handler=cmd_forget)

    args = parser.parse_args(argv)
    handler: Any = args.handler
    return int(handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
