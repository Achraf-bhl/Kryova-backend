# CATIA Bridge — setup, and the API the frontend codes against

Two audiences.

**Part 1** is for the engineer installing the daemon on a Windows workstation.
**Part 2** is the exact REST/SSE/TypeScript surface the frontend must implement.
It replaces the `localhost:9100` client entirely — see "What to delete" at the end.

The wire protocol between the daemon and the server is
[CATIA_BRIDGE_PROTOCOL.md](CATIA_BRIDGE_PROTOCOL.md); this document does not
repeat it.

---

# Part 1 — Installing the bridge on Windows

## What it is, and what it is not

A small Python process that runs beside CATIA and **dials out** to Kryova over a
WebSocket. It does not open a port, does not run a web server, and does not
listen on localhost. Nothing on the engineer's network needs to be opened, and
no web page — malicious or otherwise — can reach it.

Run it as the desktop user. **Never elevated.** It automates the CATIA session
that user already has open; running it as an administrator would hand a remote
agent more authority over the machine than the person sitting at it has.

## Requirements

- Windows 10/11, 64-bit
- CATIA V5-6R (R2019–R2024) — **already running**, with a licence. The bridge
  attaches to the running instance and never starts one, because launching CATIA
  from a background process spends a licence nobody asked to spend.
- Python 3.11 or newer, 64-bit
- Outbound HTTPS to your Kryova host. That is all — no inbound rules.

## Install

```powershell
# from a checkout of the backend repo, or a copy of scripts\catia_bridge\
cd scripts\catia_bridge
py -3 -m pip install -r requirements.txt
```

`requirements.txt` is two packages: `websockets` (pure Python) and `pywin32`
(Windows only). `pywin32` is not needed for `--mock`.

For convenience, put this in a `kryova-catia-bridge.cmd` on the PATH:

```bat
@echo off
py -3 -m catia_bridge %*
```

Run it from the directory that *contains* `catia_bridge\`, or add that directory
to `PYTHONPATH`.

## Pair

1. In Kryova: **Settings → CATIA bridge → Connect CATIA**. Give the workstation
   a name. Kryova shows an 8-character code, valid for 10 minutes, usable once.
2. On the workstation:

```powershell
kryova-catia-bridge pair --code ABCD1234 --server https://app.your-kryova-host
```

The daemon exchanges the code for a long-lived device token and writes it to
`%APPDATA%\Kryova\bridge.json`. Only the SHA-256 of that token is stored
server-side, so a database leak cannot produce a working connection.

The code alphabet excludes `I`, `L`, `O` and `U`, so a code read off a screen
cannot be mistyped into a different valid one.

## Run

```powershell
kryova-catia-bridge run
```

Leave it running. It reconnects by itself after a sleep, a VPN drop or a server
restart, with exponential backoff and jitter.

To run without CATIA — for a demo, or to test the plumbing on a machine with no
licence:

```powershell
kryova-catia-bridge run --mock
```

Mock mode is honest about itself: the `hello` frame carries `mock: true`, the
device row records `is_mock`, and `GET /catia/status` reports it, so the UI can
say so. The daemon **never** falls back to mock mode silently — if the real
backend cannot open, it exits. (An earlier bridge did fall back, which is the
worst possible behaviour: the agent builds a part, the user watches nothing
happen in CATIA, and every number they are shown is invented.)

To run it at logon, use Task Scheduler: *Run only when user is logged on*,
trigger *At log on*, action `py -3 -m catia_bridge run`, working directory set to
the folder containing `catia_bridge\`. Do **not** run it as a service under
`SYSTEM` — it needs the interactive desktop session that owns CATIA.

## Diagnose

```powershell
kryova-catia-bridge status
```

Reports pairing, the token's location, and whether CATIA is reachable —
**without contacting the server**, which is what separates the three failures
that otherwise all present as "it does not work":

| Symptom | Meaning |
|---|---|
| `paired    no` | Never paired, or `bridge.json` was deleted. Pair again. |
| `CATIA     unavailable -- CATIA is not running` | Start CATIA and open a part. |
| `CATIA     not responding` | Usually a modal dialog waiting for a click. |
| `status` is fine but Kryova shows offline | Network/TLS to the server, or a revoked device. Run `run -v`. |

Add `-v` to any command for debug logging.

## Security notes worth reading before rollout

- **No arbitrary code execution.** `SystemService.Evaluate` — CATIA's
  arbitrary-VBScript hatch — is not exposed and must never be added. It would
  turn one prompt injection into remote code execution on the workstation.
- **The daemon re-validates everything.** Tool name, tier and argument schema
  are checked against the daemon's own copy of the table, not the server's. It
  refuses what it does not recognise.
- **The model never supplies a path.** Every file the daemon touches is resolved
  under `%LOCALAPPDATA%\Kryova\catia\`.
- **CATIA-derived text is untrusted.** Part names and parameter comments were
  written by whoever made the file. They are stripped of control characters,
  length-capped, and fenced in a delimiter the system prompt declares carries no
  authority.
- **Revocation is immediate.** Deleting a device in Kryova closes its live socket
  as well as killing the token.
- **Late binding only.** `catia_com.py` never runs `makepy` against the CATIA V5
  Interfaces Object Library — that writes into pywin32's shared `gen_py` cache
  and changes how every *other* early-binding application on the workstation
  resolves CATIA, including macro suites the engineer already depends on.

---

# Part 2 — The API the frontend implements

Base: `${NEXT_PUBLIC_API_URL}` (default `http://localhost:8000/api/v1`).
Auth: the existing httpOnly cookies + `x-csrf-token` on mutations. Use
`lib/api-client.ts` — no new fetch path, and **no `NEXT_PUBLIC_CATIA_BRIDGE_URL`**.

Cross-user resources return **404, not 403**. Render "not found".

## REST

### `GET /catia/status?conversation_id=<id>`

The one call the CATIA panel polls. The backend is the only source of truth for
"is CATIA connected" — the browser cannot reach the daemon.

Disconnected:
```json
{ "connected": false, "enabled": true, "paired_devices": 1,
  "document": null, "detail": "No workstation is connected." }
```

Connected:
```json
{ "connected": true, "enabled": true, "paired_devices": 1,
  "device_id": "…", "device_name": "Office desktop", "hostname": "WS-ENG-04",
  "catia_version": "V5-6R2021", "bridge_version": "1.0.0", "mock": false,
  "capabilities": ["part","sketch","measure","export","capture"],
  "queue_depth": 0, "connected_since": "2026-08-29T09:12:44+00:00",
  "document": { "doc_name": "Bracket",
                "latest_checkpoint_id": "…", "bound_at": "2026-08-29T09:13:02+00:00" } }
```

`detail` is only present when `connected` is false, and distinguishes "nothing
paired yet" (show the pairing flow) from "paired but offline" (show "start the
bridge"). **`mock: true` must be visible in the UI** — a simulated part must
never be presented as a real one. `document` is null unless
`conversation_id` is supplied *and* that conversation has a bound document.

### `POST /catia/devices` → 201

Body `{ "name": "Office desktop" }`. Returns the pairing code **once**:
```json
{ "device": { …DeviceRead… }, "pairing_code": "H7K2M9QP",
  "pairing_expires_at": "2026-08-29T09:22:00+00:00",
  "command": "kryova-catia-bridge pair --code H7K2M9QP" }
```
Show `command` for copy-paste and count down to `pairing_expires_at`. There is
no endpoint to re-read the code; expiry means issuing a new device.

### `GET /catia/devices` → `DeviceRead[]`, newest first

```json
{ "id": "…", "name": "Office desktop", "hostname": "WS-ENG-04",
  "status": "active", "online": true, "catia_version": "V5-6R2021",
  "bridge_version": "1.0.0", "is_mock": false,
  "last_seen_at": "…", "created_at": "…" }
```
`status` is `pending | active | revoked`. `pending` means a code was issued and
never redeemed — offer to reissue.

### `DELETE /catia/devices/{id}` → 204

Revokes and hangs up on the live socket. The row is kept (the audit log points
at it), so the device reappears in the list with `status: "revoked"` — filter or
grey those out rather than expecting them to vanish.

### `GET /catia/tools`

`{ "tools": [{ "name", "tier", "mutating", "description" }] }`. Fetch this rather
than hard-coding tiers in the frontend: a tier the UI has wrong is a destructive
operation shown as routine.

### `GET /catia/conversations/{conversationId}/checkpoints` → `CheckpointRead[]`

Newest first, max 100. `{ "id", "label", "size_bytes", "stored_in_cloud", "created_at" }`.

**`stored_in_cloud: false` matters.** That checkpoint exists only on the
workstation (the document was over the 64 MiB inline transfer ceiling). It can
still restore on that machine, but it is not a backup. Label it differently —
"on workstation only" — do not show it as equivalent.

### `POST /catia/approvals`

The click that authorises a destructive operation.

Body `{ "tool": "catia_restore", "conversation_id": "…", "checkpoint_id": "…" }` →
`{ "approval_token": "1767225600.7Hn…", "expires_in_seconds": 300 }`.

Pass the token straight into the agent turn that calls `catia_restore`. The
signature binds user + tool + conversation + checkpoint, so it cannot be reused
for a different rollback, and it expires in five minutes. 422 if `tool` is not
destructive; 404 if the checkpoint is not the user's.

## SSE — `GET /catia/events`

Session-authenticated, per-user scoped, `text/event-stream`. Open one connection
for the signed-in user; do **not** open one per conversation.

```ts
const source = new EventSource(`${API_URL}/catia/events`, { withCredentials: true });
```

Every `data:` line is one JSON object:
```json
{ "event": "parameters_changed", "at": "2026-08-29T09:14:02+00:00",
  "data": { "device_id": "…", "changed": ["Length"] } }
```

Events: `stream_open` (first frame, always), `bridge_connected`,
`document_opened`, `document_saved`, `geometry_changed`, `parameters_changed`,
`checkpoint_created`, `export_completed`, `catia_lost`.

Lines beginning `:` are keepalive comments emitted every 15 s of silence —
`EventSource` swallows them; a hand-rolled reader must ignore them.

`bridge_connected` and `catia_lost` should re-fetch `GET /catia/status` rather
than being trusted to update the panel on their own: the status endpoint is the
authority, the events are the nudge.

`export_completed` is the cue to refresh the project's geometry list — a new
version has appeared without the browser having uploaded anything.

## `src/types/catia.ts`

Replace the file wholesale. The current one describes the dead `:9100` bridge —
its `CatiaEvent` shape (`document_name`, `file_path`, `file_format`) does not
exist anywhere in this API.

```ts
export type CatiaDeviceStatus = "pending" | "active" | "revoked";

export type CatiaTier = "read" | "write" | "destructive";

export interface CatiaDevice {
  id: string;
  name: string;
  hostname: string | null;
  status: CatiaDeviceStatus;
  online: boolean;
  catia_version: string | null;
  bridge_version: string | null;
  is_mock: boolean;
  last_seen_at: string | null;
  created_at: string;
}

export interface CatiaBoundDocument {
  doc_name: string;
  latest_checkpoint_id: string | null;
  bound_at: string;
}

/** GET /catia/status. `connected` discriminates the union. */
export type CatiaStatus =
  | {
      connected: false;
      enabled: boolean;
      paired_devices: number;
      document: CatiaBoundDocument | null;
      detail: string;
    }
  | {
      connected: true;
      enabled: boolean;
      paired_devices: number;
      device_id: string;
      device_name: string;
      hostname: string;
      catia_version: string;
      bridge_version: string;
      /** true when the daemon is simulating CATIA. Must be visible in the UI. */
      mock: boolean;
      capabilities: string[];
      queue_depth: number;
      connected_since: string;
      document: CatiaBoundDocument | null;
    };

export interface CatiaDeviceCreated {
  device: CatiaDevice;
  /** Shown once. There is no endpoint that returns it again. */
  pairing_code: string;
  pairing_expires_at: string;
  command: string;
}

export interface CatiaCheckpoint {
  id: string;
  label: string;
  size_bytes: number | null;
  /** false => the only copy is on the workstation. Not a backup. */
  stored_in_cloud: boolean;
  created_at: string;
}

export interface CatiaTool {
  name: string;
  tier: CatiaTier;
  mutating: boolean;
  description: string;
}

export type CatiaEventName =
  | "stream_open"
  | "bridge_connected"
  | "document_opened"
  | "document_saved"
  | "geometry_changed"
  | "parameters_changed"
  | "checkpoint_created"
  | "export_completed"
  | "catia_lost";

export interface CatiaEvent {
  event: CatiaEventName;
  at: string;
  data: Record<string, unknown> & { device_id?: string };
}

export interface CatiaApproval {
  approval_token: string;
  expires_in_seconds: number;
}
```

## UI states worth designing for

The panel has more than connected/disconnected, and collapsing them loses the
only useful information:

| State | Signal | What to say |
|---|---|---|
| Not paired | `connected:false`, `paired_devices:0` | Offer **Connect CATIA** |
| Paired, offline | `connected:false`, `paired_devices>0` | "Start the Kryova bridge on <name>" |
| Connected | `connected:true` | Version, hostname, bound document |
| Connected, mock | `mock:true` | A clear badge. Results are simulated. |
| Busy | `queue_depth>0` | "CATIA is working" — it runs one command at a time |
| Switched off | `enabled:false` | Disabled on this deployment, not broken |

## What to delete

These exist and are wired to a port nothing serves:

- `src/lib/catia-bridge.ts` — the whole `:9100` client
- `src/app/api/catia/events/route.ts` — proxies to `:9100/events`
- `src/hooks/use-catia-bridge.ts` — rewrite against `GET /catia/status` +
  `GET /catia/events`
- `src/types/catia.ts` — replace with the above
- `src/components/catia-bridge-panel.tsx` — rewrite against the states above
- the `NEXT_PUBLIC_CATIA_BRIDGE_URL` and `CATIA_BRIDGE_INTERNAL_URL` env vars

There is no Next.js route handler needed: `/catia/events` is same-origin through
the existing API client and is session-authenticated, so the browser can open it
directly.
