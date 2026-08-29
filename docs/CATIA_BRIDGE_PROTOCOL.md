# Kryova ↔ CATIA Bridge Protocol (v1)

Single source of truth for the cloud→desktop CATIA integration. The backend, the
agent tool layer, the bridge daemon and the frontend all code against this file.

## Topology — the bridge dials OUT

```
 Browser ──HTTPS──► Kryova backend ──WebSocket (bridge-initiated)──► Windows daemon ──COM──► CATIA V5
         ◄──SSE────                 ◄────────────────────────────────
```

The daemon opens an **outbound** WebSocket to the backend and keeps it alive.
There is **no inbound port on the engineer's machine**, no localhost HTTP server,
no `:9100`. This kills the DNS-rebinding class of attack (CVE-2025-66414 shape)
and works behind any corporate NAT/firewall without configuration.

The browser never talks to the daemon. The backend is the only source of truth
for "is CATIA connected".

## Pairing and authentication

1. User clicks *Connect CATIA* → `POST /api/v1/catia/devices` → server returns a
   one-time **pairing code** (8 chars, 10-minute TTL, single use).
2. User runs `kryova-catia-bridge pair --code ABCD1234` on the Windows box.
   Daemon calls `POST /api/v1/catia/devices/pair` → receives a long-lived
   **device token** (stored in `%APPDATA%\Kryova\bridge.json`, chmod-equivalent
   restricted). Only the SHA-256 of the token is stored server-side.
3. Daemon connects: `GET /api/v1/catia/bridge/ws` with
   `Authorization: Bearer <device_token>`. The token is **never** a URL query
   parameter (it would land in access logs).

A device belongs to exactly one user. Tool calls are routed only to devices
owned by the requesting user — the same 404-not-403 posture as the rest of the API.

## Wire format

All frames are JSON text frames.

### daemon → server (first frame after connect)
```json
{"type":"hello","catia_version":"V5-6R2021","bridge_version":"1.0.0","mock":false,
 "hostname":"WS-ENG-04","capabilities":["part","sketch","measure","export","capture"]}
```

### server → daemon (first frame after `hello`)
```json
{"type":"ready","device_id":"<uuid>","heartbeat_seconds":20}
```
The daemon may ignore it; it exists so a client can distinguish "the socket was
accepted" from "the server has registered this device and will route calls".

### server → daemon (invoke a tool)
```json
{"type":"call","id":"<uuid4>","tool":"catia_pad","conversation_id":"<uuid>",
 "arguments":{"sketch":"Sketch.1","length_mm":12.0}}
```

Destructive tools carry the approval token **on the frame**, not in
`arguments`:
```json
{"type":"call","id":"<uuid4>","tool":"catia_restore","conversation_id":"<uuid>",
 "arguments":{"checkpoint":{"checkpoint_id":"…","remote_ref":"…","content_b64":"…"}},
 "approval_token":"1767225600.7Hn…"}
```
The daemon cannot verify the signature — it holds no server secret — and is not
meant to. What it enforces is that a call whose tier *its own table* says is
destructive arrives with a token at all. Only the server can put one on the
wire, so a confused or compromised agent stream cannot manufacture a destructive
call the server never signed. The token stays out of `arguments` so it never has
to appear in a tool's parameter schema, which is the model's interface.

`arguments` as sent is not always `arguments` as the model wrote them. The
server resolves what the model is deliberately never given (see "No filesystem
paths from the model" below) and adds a small, per-tool, enumerated set of
fields the daemon accepts alongside the schema:

| Tool | Server-added fields |
|---|---|
| `catia_open_document` | `doc_name`, `remote_path`, `fallback_checkpoint` |
| `catia_restore` | `checkpoint` (replaces the model's `checkpoint_id`) |
| `catia_checkpoint`, `catia_export_step`, `catia_capture_view` | `max_inline_bytes` |

Anything else is rejected by the daemon's schema check.

### daemon → server (tool finished)
```json
{"type":"result","id":"<uuid4>","ok":true,"data":{"feature":"Pad.1","mass_kg":0.42}}
{"type":"result","id":"<uuid4>","ok":false,"error":"No active document open in CATIA"}
```

### daemon → server (unsolicited event; relayed to the browser over SSE)
```json
{"type":"event","event":"parameters_changed","data":{"changed":["Length"]}}
```
Event names: `document_opened`, `document_saved`, `geometry_changed`,
`parameters_changed`, `checkpoint_created`, `export_completed`, `catia_lost`.

### heartbeat
Server sends `{"type":"ping","t":<epoch>}` every 20 s; daemon replies
`{"type":"pong","t":<same>}`. Two missed pongs ⇒ server marks the device
offline and fails in-flight calls with `CatiaUnavailable`.

## Concurrency: exactly one call at a time

CATIA's automation surface is effectively single-threaded (COM STA; the core
does not parallelise). The server therefore keeps **one in-flight call per
device** and queues the rest. Every call carries a timeout (default 30 s, 180 s
for `catia_export_step`). On timeout the call is failed and the queue advances —
a modal dialog blocking CATIA must not wedge the whole session forever.

## Conversation ↔ document binding (the product mechanic)

Each conversation owns at most one CATIA document.

- **New conversation**, first geometry tool call ⇒ the agent calls
  `catia_new_part`, the daemon creates an empty CATPart, and the server records
  a `CatiaDocument` row bound to that conversation.
- **Resumed conversation** ⇒ the agent calls `catia_open_document` with the
  conversation's stored document; the daemon reopens the saved file (or restores
  the latest checkpoint blob if the local file is gone).

This is what makes "come back tomorrow and keep building" work.

## File transfer: inline, with a ceiling

The daemon sits behind a corporate NAT and dials out, so it has no route to the
blob store and cannot be handed an upload URL without also being handed a
credential and a reachable host. Files therefore ride **inside the result frame**,
base64-encoded, under `content_b64` with `sha256` and `size_bytes` beside them.
The server verifies the digest before storing.

The ceiling is **64 MiB decoded** (`app/catia/transfer.INLINE_TRANSFER_MAX_BYTES`),
sent to the daemon as `max_inline_bytes`. Above it:

* `catia_export_step` fails with an actionable message (export a simplified
  representation, or upload the file to Kryova directly);
* `catia_checkpoint` returns `inline: false, content_b64: null` and the server
  records the checkpoint with `media_id` NULL and the daemon-side `remote_ref`
  only. The mutation still proceeds, and the checkpoint is honestly labelled
  `stored_in_cloud: false` — a snapshot held only on the workstation is a real
  snapshot, just not a backup.

When exports of hundreds of megabytes matter, the fix is a
device-token-authenticated upload endpoint the daemon can PUT to, not a larger
constant.

## Checkpoints

Never trust CATIA's in-session undo for agent safety. Before every **mutating**
tool call the daemon saves the active document and the server records a
checkpoint (document copy in the content-addressed blob store). `catia_restore`
rolls back to any checkpoint. The op log (`catia_operations`) is an append-only
audit trail; the transcript of ops is a replayable script.

Two refinements the implementation adds:

* **A mutation whose checkpoint fails does not run.** An unrecoverable change
  made because the safety net was unavailable is the worst of both outcomes.
* **Four mutating tools are not auto-checkpointed**, because there is nothing to
  snapshot or nothing changes: `catia_new_part`, `catia_open_document`,
  `catia_checkpoint` itself, and `catia_export_step` (which reads the model out
  and does not modify it).

A checkpoint exists in up to two places and either can restore the part: the
blob store (survives the laptop being reimaged) and the daemon's own snapshot
directory (survives an oversize document). `catia_open_document` is sent the
latest stored checkpoint as `fallback_checkpoint`, so reopening works even when
the workstation has lost the file — which is what makes "come back tomorrow"
survive more than a clean temp directory.

## Approval tiers — enforced at the BRIDGE, not just the UI

| Tier | Ops | Enforcement |
|---|---|---|
| `read` | status, list parameters, measure, capture view | runs freely |
| `write` | new part, sketch, pad, pocket, hole, fillet, chamfer, set parameter, update, export | needs `allow_mutations` on the turn; auto-checkpointed first |
| `destructive` | restore checkpoint | needs a per-call **approval token** signed by the server after an explicit user click |

In v1 the only destructive tool is `catia_restore`. There is deliberately no
delete-feature or overwrite-document tool: neither is needed to build a part,
and every tool that exists is a tool that has to be defended.

The daemon re-validates the tier of every incoming call against its own table
(`scripts/catia_bridge/tool_table.py`), which is a separate copy from the
server's `app/catia/tool_specs.py`. The tier is **never read off the wire**: a
frame that says `"tier": "read"` for `catia_restore` is still destructive. A
compromised or confused agent stream cannot escalate by lying about the tier.

The approval token is minted by `POST /catia/approvals` and its HMAC binds
`(user_id, tool, conversation_id, checkpoint_id)` together, so an approval the
user granted for one rollback cannot be replayed against another. TTL 5 minutes.

## Security invariants

1. **No arbitrary code execution tool.** `SystemService.Evaluate` (arbitrary
   VBScript) is never exposed. The tool vocabulary is a fixed allowlist,
   re-validated at the daemon against a JSON Schema per tool.
2. **CATIA-derived text is untrusted input.** Part names, parameter comments and
   file metadata are sanitised before entering the prompt (control characters
   stripped, length-capped, wrapped in a delimiter that the system prompt
   declares carries no authority). Indirect prompt injection through tool
   results is the documented attack.
3. **No filesystem paths from the model.** The daemon resolves all paths inside
   its own working directory; the model names documents, never paths.
4. **Rate limits** per device: 60 ops/minute, 600/hour.
5. Daemon runs as the desktop user, never elevated.

## Python interface the agent layer codes against

Provided by `app/catia/dispatch.py`:

```python
class CatiaUnavailable(RuntimeError): ...   # no bridge connected
class CatiaError(RuntimeError): ...         # the tool ran and failed

def catia_available(db: Session, user_id: str) -> bool: ...

def call_catia(
    db: Session, *, user_id: str, conversation_id: str | None,
    tool: str, arguments: dict[str, Any], timeout_s: float | None = None,
) -> dict[str, Any]: ...

CATIA_TOOL_SPECS: list[CatiaToolSpec]  # .name .description .parameters .tier
```

`app/ai/tools.py` builds one agent `Tool` per entry of `CATIA_TOOL_SPECS`,
marking `mutating=True` for the `write` and `destructive` tiers
(`CatiaToolSpec.mutating` is exactly that predicate).

`call_catia` returns the tool's data dictionary and raises only those two
exception types. `CatiaUnavailable` means "no bridge could take this call" and
the remedy is to start the daemon; `CatiaError` means "the call was refused or it
ran and failed" and the remedy is a different call. Both messages are written to
be repeated to the user verbatim.

`catia_status` never reaches a device: the backend is the source of truth for
"is CATIA connected", and asking the device whether the device is reachable is
circular. It is answered from `app/catia/dispatch.status_payload`, which also
serves `GET /catia/status`.

## The tool vocabulary (v1)

Semantic operations, never raw coordinates — LLMs are documented to fail on
sketch-plane origins, 3D transforms and reference frames. Coordinate maths lives
inside the tools.

| Tool | Tier | Purpose |
|---|---|---|
| `catia_status` | read | Is a bridge connected? Which CATIA version? |
| `catia_new_part` | write | Create an empty CATPart and bind it to the conversation |
| `catia_open_document` | write | Reopen the conversation's document (resume) |
| `catia_list_parameters` | read | Named parameters with values and units |
| `catia_set_parameter` | write | Set one named parameter (mm/deg/kg) |
| `catia_sketch_rectangle` | write | Centred rectangle on a named plane |
| `catia_sketch_circle` | write | Centred circle on a named plane |
| `catia_pad` | write | Extrude a sketch by a length |
| `catia_pocket` | write | Cut a sketch through/by a depth |
| `catia_hole` | write | Hole on a face at a named position |
| `catia_fillet` | write | Edge fillet by radius |
| `catia_chamfer` | write | Edge chamfer by length/angle |
| `catia_measure` | read | Mass, volume, bounding box, centre of gravity |
| `catia_capture_view` | read | Screenshot the viewport → blob store (the agent looks at its own work) |
| `catia_export_step` | write | Export STEP → upload → **new Kryova geometry version** |
| `catia_checkpoint` | write | Save + snapshot the document |
| `catia_restore` | destructive | Roll back to a checkpoint |
| `catia_update` | write | Force a part update / rebuild |

Every mutating tool returns rich post-state — updated feature list, bounding box
and mass — not `"ok"`. The agent is prompted to `catia_measure` and
`catia_capture_view` after mutations and to react to what it sees.

`catia_export_step` is the seam that closes the loop:
**chat → CATIA geometry → STEP → geometry version → mesh → solve → interpret →
propose change → apply in CATIA → re-run.**

## Mock mode (how this is testable on Linux today)

The daemon runs with `--mock` when `pywin32`/CATIA are absent. Mock mode
implements the **entire protocol** against an in-memory part model: parameters,
features, a synthetic bounding box and mass, a real (tiny) STEP export and a
generated PNG for `catia_capture_view`. Every server-side path, every agent
tool, every UI state and the full test suite therefore run on Linux; only the
COM calls themselves are unverified until a Windows session.
