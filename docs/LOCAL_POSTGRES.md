# Running Kryova against a local PostgreSQL

**Switched on this workstation 2026-09-07.** Neon stays in `.env` as a one-line
fallback; the local server is an override in `.env.local`.

## Why

Neon's pooled endpoint is in `eu-west-2` and every round trip crosses the
network. Measured on this machine:

| | Per round trip |
|---|---|
| Neon (`-pooler`, eu-west-2) | ~250 ms |
| Local PostgreSQL 18.6 | **0.135 ms** |

That is the whole of the argument. The DB test suite was ~4 minutes of almost
pure latency, and a CATIA turn is many statements — every one of which the user
waits through while a local model is already the slow part of the loop.

**It is not a move away from Postgres, and that distinction is the point.** The
rule this repo has always kept — test on the same engine you ship on, never
SQLite — is what catches JSONB, enum and cascade drift. The local server is
PostgreSQL **18.6**, the same major.minor Neon runs (`show server_version`), so
parity holds and nothing about the rule changes.

## What was installed

No installer, no Windows service, no administrator rights: the ZIP archive of
the official binaries, run as an ordinary user process.

| | Path |
|---|---|
| Binaries | `%USERPROFILE%\pg\pgsql` |
| Data directory | `%USERPROFILE%\pgdata` |
| Server log | `%USERPROFILE%\pgdata\server.log` |

**The data directory must not be under OneDrive.** OneDrive syncs open files;
a synced Postgres data directory is a corrupted one. `%USERPROFILE%\pgdata` is
outside the synced tree — `%USERPROFILE%\OneDrive\...` is not.

## Setting it up again from scratch

```powershell
# 1. Unpack (the zip is the "binaries" download, not the installer)
Expand-Archive postgresql-18.6-1-windows-x64-binaries.zip -DestinationPath $env:USERPROFILE\pg

# 2. Initialise. --locale=C keeps collation stable across machines.
$env:USERPROFILE\pg\pgsql\bin\initdb.exe -D $env:USERPROFILE\pgdata `
    -U postgres --pwfile=pw.txt -E UTF8 --locale=C

# 3. Start it. This is also how it is started after a reboot.
$env:USERPROFILE\pg\pgsql\bin\pg_ctl.exe -D $env:USERPROFILE\pgdata `
    -l $env:USERPROFILE\pgdata\server.log -w start

# 4. Role and database
psql -U postgres -h localhost -d postgres -c "CREATE ROLE kryova LOGIN PASSWORD '...' SUPERUSER;"
psql -U postgres -h localhost -d postgres -c "CREATE DATABASE kryova OWNER kryova ENCODING 'UTF8';"

# 5. Point the app at it -- in .env.local, which is read AFTER .env and wins
#    (Settings.model_config reads (".env", ".env.local")).
#    DATABASE_URL=postgresql://kryova:...@localhost:5432/kryova?sslmode=disable

# 6. Schema. migrations/env.py creates the `kryova` schema itself.
venv\Scripts\python.exe -m alembic upgrade head
venv\Scripts\python.exe -m alembic check     # expect "No new upgrade operations detected"
```

**`sslmode=disable` is required, not a shortcut.** A stock local Postgres has
`ssl = off` and refuses the handshake, so a connection asking for `require`
fails before any statement runs. `app/core/database.py::sslmode_for` reads the
mode out of the URL rather than hardcoding `require`, which is the change
(1188579) that makes the line above work at all. Neon is unaffected: its URL
already carries `?sslmode=require`, so the old hardcoded argument was only
restating what the URL said.

`SUPERUSER` on the `kryova` role is deliberate on a development machine: the
P2 tenancy migration installs Row-Level Security policies and the audit log's
append-only triggers, and both want an owner that can create them. It is not a
pattern to copy into a deployment, where the application role should be the
one RLS is enforced *against*.

## The account

There is deliberately no API that mints staff (`app/models/audit.py`), so the
admin account is provisioned out of band by `scripts/create_admin.py`:

```
venv\Scripts\python.exe scripts\create_admin.py     # admin@admin.com / admin
```

It is idempotent — re-running it resets the password rather than raising, which
is the way to recover a forgotten development password — and it refuses to run
against a non-local `DATABASE_URL` without `--i-know`, because its output is an
account with unrestricted platform standing.

`admin` is five characters and `UserCreate` requires eight, so this account
**cannot** be created through `POST /api/v1/auth/register`. The login route
takes an `OAuth2PasswordRequestForm`, which imposes no length, so it signs in
normally once it exists. That asymmetry is correct for production and is the
reason the script exists rather than a curl command.

## Two things to watch

- **It does not survive a reboot.** `pg_ctl` starts a plain user process, not a
  service. Either re-run the start command, or add a logon scheduled task (user
  context, no admin needed). A backend that starts before it will fail
  `/health`'s `database` check, which is the intended way to find out.
- **The tests follow the switch automatically.** `tests/conftest.py` takes its
  URL from `DATABASE_URL` and creates and drops the `kryova_test` schema per
  run, selected with `schema_translate_map` and never `SET search_path`. So
  pointing this file's URL at the local server is the whole of what makes the
  suite local too — there is nothing test-side to change.

## Going back to Neon

Comment out the `DATABASE_URL` line in `.env.local`. `.env` still holds the Neon
URL, and nothing else was changed.
