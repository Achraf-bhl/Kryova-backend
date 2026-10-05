# Releasing the desktop installer

Written 2026-10-05 with ROAD_TO_10 Phase 4. For whoever cuts a release of `Kryova-frontend`'s
Tauri app. It says what the installer is, what you must provide that no one else can, how a
release is made, and — in its own section — what has never been run.

## What an installer contains

Everything an installed Kryova runs is beside its executable, and nothing is read from a
checkout. `node scripts/stage-desktop.mjs` (in `Kryova-frontend`) assembles it under
`src-tauri/bundle/`, and the MSI carries that tree as its resources:

```
frontend/         the Next standalone server
runtime/node/     a pinned Node
backend/python/   a pinned relocatable CPython with this repo's wheels (requirements-desktop.lock.txt)
backend/app, migrations, scripts/catia_bridge, data/verify/benchmark-outcomes.json   the backend, by allow-list
postgres/         PostgreSQL's bin, lib and share
```

Every download is pinned by version, URL and SHA-256 in `scripts/desktop-runtime.json`, and
refused on a mismatch. **PostgreSQL's hash is trust-on-first-use:** EDB publishes no checksum,
so the pin is what was measured on 2026-10-05, and it stops a later substitution without being
able to say the first file was what EDB intended.

What the person who installs it gets, and where it lives (`%LOCALAPPDATA%\Kryova`, the same
rule as `app/desktop.py::resolve_home` and the shell's `layout.rs`):

| Path | What |
|---|---|
| `pgdata/`, `pgdata.log` | the database cluster, and its log beside it (CLAUDE.md, *Database* 4a) |
| `secrets.json` | generated once, never regenerated; losing it with the database present is refused with a sentence |
| `media/` | every uploaded and derived file |
| `backups/` | a `pg_dump` before any upgrade that migrates; the newest 3 are kept |
| `logs/` | `backend.log` and `frontend.log`, rotated on every launch (5 kept) |
| `config.env` | the user's own settings — a model key, SMTP. It may not set the database, the signing key, the media folder or the origins; those lines are ignored with a warning |

**An uninstall removes the program and keeps all of the above.** The database is the user's, and
the next install or the next version finds it where it was. To remove it, delete the folder.
The release pipeline asserts this on every build.

## What only you can provide

Nothing below has a default, because each would be a decision made for somebody else. The
build refuses to proceed on half of any of them.

### 1. The updater key — offline, and the one thing that cannot be recovered

`npx tauri signer generate -w <file outside any repository>`. The **private key** signs each
release's MSI, by hand, and never reaches CI or a repository. An installed app trusts exactly
one public key and has no way to learn another, so **losing the private key ends updates for
every installed copy for ever**; keep it on a hardware token or two offline copies.

The **public key** (the `.pub` file's contents) goes in the repository variable
`KRYOVA_UPDATER_PUBKEY`. The app is built with `requireSignedVersion`, so a response pairing a
new version number with an older release's valid signature is refused by the client.

### 2. An update host

An `https` origin you control, serving `<base>/<channel>/latest.json` (`stable` and `beta`) and
the MSIs it points at. A build belongs to one channel; moving between channels is installing the
other channel's build. Repository variable `KRYOVA_UPDATE_BASE_URL` (no trailing slash).
With neither variable set the build has no updater, which is the honest shape of an installer
that nobody can update yet.

### 3. A code-signing certificate

Without one the MSI builds, installs and passes the install test, **and the pipeline refuses to
publish it.** Unsigned, it trips SmartScreen, and Microsoft Defender has quarantined the
unsigned 0.2.0 `kryova.exe` as `Trojan:Script/Wacatac.H!ml` (CLAUDE.md, *The installed desktop
app and Microsoft Defender*). Signing is the real fix; a rebuild that happens to scan clean is
luck. OV signs now and builds SmartScreen reputation over time; EV is trusted at once.

**Where the certificate lives decides how CI can use it.** Since mid-2023 a newly issued
OV or EV certificate's private key must be on a hardware module or in a vendor's cloud, so there
is no `.pfx` to hand a runner. Two routes are wired, and which applies is a consequence of what
you buy:

* **A command** — `KRYOVA_SIGN_COMMAND`, containing `%1` where Tauri puts each file's path
  (`bundle.windows.signCommand`). Whatever your provider's CLI is. Repository *variable*.
  Tauri splits the string on single spaces, so no argument may hold a space: a signer under
  `Program Files` needs a wrapper script on a path without one.
* **A certificate in the runner's store**, named by thumbprint — what the pipeline does when the
  secrets `WINDOWS_CERTIFICATE_PFX_BASE64` and `WINDOWS_CERTIFICATE_PASSWORD` exist, plus the
  variable `KRYOVA_SIGN_TIMESTAMP_URL` (a signature with no timestamp stops being valid the day
  the certificate expires, so the build refuses to sign without one). Fits an older `.pfx`, or a
  self-hosted runner with the token plugged in.

### 4. Access to the backend repository

The pipeline checks out `Kryova-backend` beside the frontend. A private sibling cannot be read
with the frontend repository's own token; add a read-only token as the secret
`KRYOVA_BACKEND_TOKEN`.

## Making a release

1. Bump `version` in `src-tauri/tauri.conf.json` (and `Cargo.toml`, `package.json`).
2. Actions → **Desktop** → *Run workflow*: pick the channel and the backend ref, tick *publish*.
   It builds the MSI, then installs it on a **second, clean runner**, launches it, waits for
   `http://127.0.0.1:8000/health` and the frontend, checks the database is under the user's data
   folder and nowhere in the install, closes the app and checks it took its servers with it,
   uninstalls, and checks no uninstall entry remains in any of the three lists Windows keeps and
   the user's database survived. **Only then**, and only if signed and *publish* was ticked,
   a job makes a **draft** GitHub release holding the MSI and its SHA-256.
3. Sign the MSI with the updater key: `npx tauri signer sign <msi>`.
4. `node scripts/make-latest-json.mjs --msi <msi> --version <v> --channel <c> --url <https url>`.
   It refuses a signature recorded for a different version — the check each customer's app would
   make, made before it is too late.
5. Upload the MSI and `latest.json` to the host, then publish the draft.

A local build, for trying the installer yourself (Windows, in the frontend repository):
`npm run desktop:release` — stages the bundle and builds the MSI. Set the variables above in the
environment to build with an updater or a signature.

## Verified, and not verified

**Verified on Linux, by tests that were broken to watch them fail:** the layout the shell looks
for, the stager's allow-list and its refusal to ship a `.env` or a `.git`, the lock file's pins,
the shell/backend environment contract, the first-run database (a real `initdb`, 38 migrations,
the application role `NOSUPERUSER NOBYPASSRLS` so row-level security still enforces, a backup
that restores), the overlay for the updater and for signing, `latest.json`'s refusals, deep-link
validation, and the workflow's own promises (`src/lib/desktop-workflow.test.ts`). The Rust half
compiles for the Windows target; it has not been linked or run.

**Not verified anywhere, and recorded in `docs/WINDOWS_VERIFICATION.md` (THE QUEUE G5, G7):**

* **Nothing has been installed on Windows.** Not the MSI's size or file count, not the pinned
  CPython running with its wheels (OCP, gmsh, scipy), not `initdb` under Windows antivirus, not a
  launch from `Program Files`. `.github/workflows/desktop.yml` is written to find out, has not
  run, and is expected to find something on its first run.
* **The page's access to Tauri's commands** from a remote loopback origin (`withGlobalTauri` plus
  the capability's `remote.urls`) is configured and unconfirmed in a webview. Because the page is
  the app's own origin, **the CSP in the frontend's `proxy.ts` is the only wall** between a
  script injected into the app and those commands.
* **The ports are fixed** (backend 8000, frontend 3000). Another program on either stops that
  half from starting, and the setup page says so; the database's port, by contrast, is chosen
  and moved if taken.
* **File associations for `.CATPart`, `.CATProduct`, `.stp` and `.step` are not registered.**
  `kryova://` is. Registering an extension takes it from whatever opens it today.
* **The tray shows bridge status; it does not show running jobs**, and nothing notifies a person
  when a background run finishes — both need an endpoint that lists a user's active runs, which
  is a new task (master plan 7.9).
* **The CATIA bridge daemon the backend spawns can outlive a hard kill of the app** on Windows
  (`child.kill()` ends the backend, not its children). Whether closing the window takes the
  daemon with it is unmeasured, and a hard-killed app certainly may not. The install test closes
  the window, then fails and lists the processes if any of the install's programs stay running.
* **Auto-update has never updated anything.** No key exists, no host exists, no certificate is
  bought. The client-side refusals are tested; the round trip is not.

## When it fails

Read, in this order: the run's summary (what was built, the backend commit, whether it was
signed), `install.log`, then the uploaded `kryova-install-logs` artifact — `backend.log`,
`frontend.log` and `pgdata.log` are the first run's own account. On a customer's machine the
same files are under `%LOCALAPPDATA%\Kryova` and the setup page's *Copy diagnostics* button
collects them.
