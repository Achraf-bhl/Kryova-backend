"""Prove a backup restores, by actually restoring it (P9 task 6).

**A backup nobody has restored is not a backup.** It is a file that is probably
a backup, and the difference is discovered at the worst possible moment. The
phase asks for "PITR verified by actually restoring; blob-store backup with
refcount integrity check; a written RTO/RPO and a quarterly drill that proves
it" — and the load-bearing word in all three is *actually*.

So this is not a script that checks a dump exists. It restores one into a
throwaway database, runs the migrations forward against it, counts what came
back, and cross-checks the blob store against the rows that point into it.
Every step can fail, and a failure is the point of running it.

**The refcount check is the half that surprises people.** A PostgreSQL dump and
a blob-store copy are taken at different instants by different tools, so they
disagree at the edges by construction: a row can reference a blob the store copy
predates, and the store can hold blobs no row references. The first is *data
loss* — a geometry version whose file is gone — and the second is merely waste.
Reporting them separately is what makes the first one visible; a single
"integrity: FAIL" would bury it.

**RTO and RPO are written down here rather than in a wiki**, because a target
nobody can find is a target nobody is measured against, and because this script
is what proves or disproves them. `--json` emits the measured restore time so a
drill's result is a number rather than a recollection.

Usage:

    python -m scripts.restore_drill --dump backup.sql --media-copy /mnt/backup/media
    python -m scripts.restore_drill --dump backup.sql --dry-run   # what it would do

It refuses to touch a database that is not obviously disposable — see
`_refuse_dangerous_target`. A restore drill that overwrote production would be
the incident it exists to prevent.

**What the first real run found (2026-09-16, local PostgreSQL 16.15).** The
status line said this drill "has never been run", and running it produced three
findings that reading it had not:

1. **`alembic upgrade head` ran against the wrong database — production.** The
   subprocess inherited the environment, so alembic resolved `settings.
   database_url` and migrated whatever `DATABASE_URL` pointed at, *not* the
   target just restored. Measured by pointing `DATABASE_URL` at a bystander
   database and watching all 38 tables appear in it while the drill reported
   "migrations: ok". In a real deployment `DATABASE_URL` is production, so a
   drill would have run migrations against it — the drill doing the thing it
   exists to prevent. `_alembic_env` now passes the target explicitly.
2. **A plain `pg_dump` by the application role cannot take a backup at all.**
   14 of 38 tables `FORCE ROW LEVEL SECURITY` and the role is `NOBYPASSRLS`
   (which is required — see CLAUDE.md *Database* item 3), so `pg_dump` exits
   non-zero with "query would be affected by row-level security policy". The
   drill took its dump as given and so could never have found this. The obvious
   fix, `--enable-row-security`, *succeeds* — and is only complete because every
   current tenant policy has an "unset means everything" branch. Write one
   without that branch and the same command produces a backup silently missing
   every row of those tables. `--check-dumpable` is the preflight for it.
3. **A blob check that could not run reported as a blob check that passed.**
   `_check_blobs` returned two empty lists when its query failed, which rendered
   as "0 referenced blob(s) missing" — a tick. It is a finding now.

A fourth thing is a fragility rather than a defect, and is worth knowing: the
row counts work because the database role and the schema are both called
`kryova`, so `search_path`'s `"$user"` happens to resolve. Set `DB_SCHEMA` to
anything else and every count silently came back empty with the drill still
reporting PASSED. The queries are schema-qualified now.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

#: **Recovery Time Objective**: how long a full restore may take before the
#: outage stops being a restore and starts being an incident. Four hours is
#: chosen against what this product actually is — an engineering tool people use
#: during a working day, not a payments system — and it is a *target this script
#: measures*, not an aspiration. A drill that comes in over it is a finding.
RTO_MINUTES = 240

#: **Recovery Point Objective**: how much work may be lost. Fifteen minutes,
#: which is a continuous-archiving figure rather than a nightly-dump one: a
#: nightly dump alone has an RPO of up to 24 hours and this number would be a
#: lie. Meeting it requires WAL archiving to be on, which `--check-archiving`
#: verifies rather than assumes.
RPO_MINUTES = 15

#: Databases this refuses to restore over. Substring match, deliberately blunt:
#: the cost of a false positive is renaming a scratch database, and the cost of
#: a false negative is restoring over production.
FORBIDDEN_TARGETS = ("prod", "production", "live", "main")

#: Tables whose row counts are reported after a restore. Not all of them — the
#: point is a handful a human can sanity-check against what they know the system
#: holds, not a wall of numbers nobody reads.
COUNTED_TABLES = (
    "users",
    "organisations",
    "projects",
    "geometry_versions",
    "simulation_jobs",
    "conversations",
    "design_documents",
    "attachments",
    "media",
)


@dataclass
class Finding:
    """One thing the drill established. `ok=False` is a finding, not an error."""

    name: str
    ok: bool
    detail: str


@dataclass
class DrillResult:
    target: str
    dry_run: bool
    findings: list[Finding] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    restore_seconds: float = 0.0
    #: Blobs referenced by a row and absent from the store copy. **Data loss.**
    missing_blobs: list[str] = field(default_factory=list)
    #: Blobs in the store copy that no row references. Waste, not loss.
    orphan_blobs: list[str] = field(default_factory=list)

    @property
    def within_rto(self) -> bool:
        return self.restore_seconds <= RTO_MINUTES * 60

    @property
    def redacted_target(self) -> str:
        """The target URL with its password removed, for anything a person reads.

        The first real run printed `postgresql://kryova:<the password>@localhost/...`
        as the report's first line. A drill report is the single most likely thing
        to be pasted into an incident channel or attached to a ticket, so the
        credential travelled with it. Redacted here rather than at the call sites
        because there are three of them and one that forgot would undo the other
        two — the same argument `standards.NOT_VALIDATED` makes for being one
        string.
        """
        return _redact(self.target)

    @property
    def passed(self) -> bool:
        """A drill passes only when nothing is missing and nothing failed.

        Orphans do **not** fail it: they are wasted disk, and failing a drill
        over waste teaches people to ignore drill failures — which is how the
        one that matters gets ignored too.
        """
        return all(finding.ok for finding in self.findings) and not self.missing_blobs

    def report(self) -> str:
        lines = [f"Restore drill against {self.redacted_target}", ""]
        for finding in self.findings:
            lines.append(f"  {'ok  ' if finding.ok else 'FAIL'} {finding.name}: {finding.detail}")
        if self.counts:
            lines.append("")
            lines.append("  Rows restored:")
            for table, count in sorted(self.counts.items()):
                lines.append(f"    {table}: {count:,}")
        lines.append("")
        lines.append(
            f"  Restore took {self.restore_seconds:.0f}s "
            f"({'within' if self.within_rto else 'OVER'} the {RTO_MINUTES}-minute RTO)"
        )
        if self.missing_blobs:
            lines.append(
                f"  DATA LOSS: {len(self.missing_blobs)} blob(s) are referenced by a row and "
                "are not in the store copy"
            )
        if self.orphan_blobs:
            lines.append(
                f"  {len(self.orphan_blobs)} orphan blob(s) in the store copy — wasted disk, "
                "not lost data"
            )
        lines.append("")
        lines.append(f"  {'PASSED' if self.passed else 'FAILED'}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """The result as JSON. `target` is redacted here too, and that is the point.

        `asdict` would carry the raw URL, password and all, into whatever a drill's
        `--json` output is piped into — a log aggregator, a CI artefact, a status
        page. Overwriting the key after the spread rather than redacting at one
        call site is deliberate: a field added to the dataclass later cannot
        reintroduce the leak through this path.
        """
        return {
            **asdict(self),
            "target": self.redacted_target,
            "rto_minutes": RTO_MINUTES,
            "rpo_minutes": RPO_MINUTES,
            "within_rto": self.within_rto,
            "passed": self.passed,
        }


_CREDENTIAL_RE = re.compile(r"(?P<scheme>[a-z+]+://)(?P<user>[^:/@\s]+):(?P<secret>[^@/\s]*)@")


def _redact(url: str) -> str:
    """A connection URL with its password replaced, leaving everything else legible.

    The user, host, port and database are the half a reader needs to tell which
    machine a drill ran against; the password is the half that must not reach a
    ticket. Only the `user:secret@` form is touched, so a URL with no credential
    passes through unchanged.
    """
    return _CREDENTIAL_RE.sub(r"\g<scheme>\g<user>:***@", url)


def _refuse_dangerous_target(url: str) -> None:
    """Refuse anything that looks like it might not be disposable.

    Blunt on purpose. A restore drill that overwrote production would be the
    incident it exists to prevent, and the cost of the false positive — renaming
    a scratch database — is nothing against that.
    """
    lowered = url.lower()
    for word in FORBIDDEN_TARGETS:
        if word in lowered:
            raise SystemExit(
                f"Refusing to restore into a target whose URL contains {word!r}. "
                "A drill restores into a throwaway database; point --target at one."
            )


def _run(
    command: list[str], *, dry_run: bool, env: dict[str, str] | None = None
) -> tuple[int, str]:
    if dry_run:
        return 0, "(dry run) " + " ".join(command)
    completed = subprocess.run(
        command, capture_output=True, text=True, check=False, env=env
    )
    return completed.returncode, (completed.stdout + completed.stderr).strip()


def _alembic_env(target: str) -> dict[str, str]:
    """The environment `alembic upgrade head` must run in to reach `target`.

    **The defect the first real run found.** `migrations/env.py` reads
    `settings.database_url`, which comes from the environment — so an alembic
    subprocess that simply inherits this process's environment migrates whatever
    `DATABASE_URL` names. On a developer's machine that is their working
    database; on a server running a drill it is **production**. Measured
    2026-09-16 by pointing `DATABASE_URL` at a bystander database: all 38 tables
    appeared there, none of them in the target, and the drill reported
    "migrations: ok".

    `TEST_DATABASE_URL` is cleared as well. It resolves ahead of `DATABASE_URL`
    in some configurations, and a drill that quietly migrated the test schema
    would collide with whatever suite is running — the failure mode CLAUDE.md
    records for two concurrent pytest runs.
    """
    env = dict(os.environ)
    env["DATABASE_URL"] = target
    env.pop("TEST_DATABASE_URL", None)
    return env


def _qualified(table: str, schema: str) -> str:
    """`schema.table`, because `search_path` is not something to rely on here.

    The counts appeared to work on the machine this was first run on, and only
    because the role and the schema are both named `kryova`, so `search_path`'s
    `"$user"` entry resolved to it. A deployment with `DB_SCHEMA` set to anything
    else got empty counts and a PASSED drill — `_count_rows` skips a table whose
    query fails, so "no such table" and "no rows" were the same answer.
    """
    return f'"{schema}"."{table}"'


def _schema() -> str:
    """The schema the application's tables live in, or `public` if it cannot be read.

    Imported lazily and defensively: this script is run during an incident, on a
    machine that may have a half-installed application, and failing to import
    settings must not be the reason a restore cannot be verified.
    """
    try:
        from app.core.config import settings

        return str(settings.db_schema or "public")
    except Exception:  # noqa: BLE001 - see above
        return os.environ.get("DB_SCHEMA", "public")


def drill(
    *,
    dump: Path,
    target: str,
    media_copy: Path | None,
    dry_run: bool = False,
    check_archiving: bool = False,
) -> DrillResult:
    """Restore `dump` into `target` and report what came back."""
    _refuse_dangerous_target(target)
    result = DrillResult(target=target, dry_run=dry_run)

    if not dry_run and not dump.is_file():
        result.findings.append(Finding("dump present", False, f"{dump} does not exist"))
        return result
    result.findings.append(Finding("dump present", True, str(dump)))

    started = time.monotonic()
    code, output = _run(["psql", target, "-v", "ON_ERROR_STOP=1", "-f", str(dump)], dry_run=dry_run)
    result.restore_seconds = time.monotonic() - started
    result.findings.append(
        Finding("restore", code == 0, output[-400:] if output else "restored")
    )
    if code != 0:
        return result

    # Forward to head. A dump restored from an older schema and never migrated
    # is a database the application refuses to start against, and finding that
    # out during a real outage is exactly what a drill is for.
    #
    # `env=` is load-bearing and is the first real run's finding: without it the
    # subprocess inherits DATABASE_URL and migrates production. See `_alembic_env`.
    code, output = _run(
        ["alembic", "upgrade", "head"], dry_run=dry_run, env=_alembic_env(target)
    )
    result.findings.append(Finding("migrations", code == 0, output[-400:] or "at head"))

    if check_archiving:
        code, output = _run(
            ["psql", target, "-tAc", "SHOW archive_mode"], dry_run=dry_run
        )
        on = output.strip() == "on"
        result.findings.append(
            Finding(
                "wal archiving",
                on or dry_run,
                (
                    f"archive_mode={output.strip()!r}; the {RPO_MINUTES}-minute RPO needs "
                    "continuous archiving, and a nightly dump alone has an RPO of a day"
                ),
            )
        )

    if not dry_run:
        schema = _schema()
        result.counts = _count_rows(target, schema)
        unreadable = [table for table in COUNTED_TABLES if table not in result.counts]
        if unreadable:
            # Not a zero. A table that could not be counted is a restore nobody
            # has checked, and before 2026-09-16 it was indistinguishable from an
            # empty one because `_count_rows` simply left it out.
            result.findings.append(
                Finding(
                    "row counts",
                    False,
                    f"{len(unreadable)} table(s) could not be counted in schema "
                    f"{schema!r}: {', '.join(unreadable)}",
                )
            )
        if media_copy is not None:
            result.missing_blobs, result.orphan_blobs, why = _check_blobs(
                target, media_copy, schema
            )
            if why is not None:
                result.findings.append(
                    Finding("blob refcounts", False, f"the check could not run: {why}")
                )
            else:
                result.findings.append(
                    Finding(
                        "blob refcounts",
                        not result.missing_blobs,
                        (
                            f"{len(result.missing_blobs)} referenced blob(s) missing, "
                            f"{len(result.orphan_blobs)} orphan(s)"
                        ),
                    )
                )
    return result


def _count_rows(target: str, schema: str | None = None) -> dict[str, int]:
    """Row counts per table, schema-qualified so an unreadable table is not a zero.

    A table missing from the mapping means its query failed, which the caller
    renders as `unreadable` rather than as nothing — "no such table" and "no
    rows" must not be the same answer on a page somebody reads during an outage.
    """
    schema = schema or _schema()
    counts: dict[str, int] = {}
    for table in COUNTED_TABLES:
        code, output = _run(
            ["psql", target, "-tAc", f"SELECT count(*) FROM {_qualified(table, schema)}"],
            dry_run=False,
        )
        if code == 0 and output.strip().isdigit():
            counts[table] = int(output.strip())
    return counts


def _check_blobs(
    target: str, media_copy: Path, schema: str | None = None
) -> tuple[list[str], list[str], str | None]:
    """Which referenced blobs are absent, which stored blobs are unreferenced, and why not.

    Reported as two lists rather than one verdict. They are different problems:
    the first is data loss and the second is wasted disk, and a single
    "integrity: FAIL" would bury the one that matters under the one that does
    not.

    The third return value is the reason the check could not run, and it exists
    because of what this returned before 2026-09-16: two empty lists, which the
    caller rendered as "0 referenced blob(s) missing" — **a tick**. A check that
    could not run reported as a check that passed, which is the one outcome worse
    than no check at all. `None` means it ran.
    """
    schema = schema or _schema()
    code, output = _run(
        ["psql", target, "-tAc", f"SELECT sha256 FROM {_qualified('media', schema)}"],
        dry_run=False,
    )
    if code != 0:
        return [], [], (output.strip()[-200:] or "the media table could not be read")
    if not media_copy.exists():
        return [], [], f"{media_copy} does not exist, so nothing could be compared"
    referenced = {line.strip() for line in output.splitlines() if line.strip()}
    stored = {path.name for path in media_copy.rglob("*") if path.is_file()}
    return sorted(referenced - stored), sorted(stored - referenced), None


#: The question `--check-dumpable` asks of the source database, and the reason it
#: exists. Kept as SQL rather than built from `pg_tables` so the check is one
#: round trip during an incident.
_FORCED_RLS_SQL = """
SELECT c.relname
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = %(schema)s AND c.relforcerowsecurity
ORDER BY c.relname
"""


def check_dumpable(source: str, schema: str | None = None) -> list[Finding]:
    """Can a complete backup of `source` actually be taken by this role?

    **The question the drill could not ask, because it took its dump as given.**
    Measured on 2026-09-16: 14 of this schema's 38 tables `FORCE ROW LEVEL
    SECURITY`, the application role is `NOBYPASSRLS` as the architecture requires,
    and a plain `pg_dump` therefore exits non-zero with *"query would be affected
    by row-level security policy"*. No backup at all — which the restore half of
    this script would never reach, because it starts from a file.

    The obvious response, `pg_dump --enable-row-security`, exits zero. Whether
    what it wrote is *complete* depends entirely on the policies: every current
    tenant policy permits everything when `kryova.organisation_ids` is unset, so
    today it is complete. A policy written without that branch would make the
    same command produce a backup missing every row of those tables, with nothing
    to see. So this reports the three facts separately and judges none of them by
    itself — it is the preflight a person reads before trusting a dump, not a
    verdict.
    """
    schema = schema or _schema()
    findings: list[Finding] = []

    code, output = _run(
        [
            "psql",
            source,
            "-tAc",
            "SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user",
        ],
        dry_run=False,
    )
    can_bypass = code == 0 and output.strip() == "t"
    findings.append(
        Finding(
            "role can read past RLS",
            can_bypass,
            (
                "this role bypasses row-level security, so a plain pg_dump is complete"
                if can_bypass
                else "this role is NOBYPASSRLS, which is what the architecture requires "
                "of the APPLICATION role (CLAUDE.md Database item 3) and what makes a "
                "plain pg_dump fail. Take the backup as a role that can bypass RLS, or "
                "read the next two findings before trusting --enable-row-security"
            ),
        )
    )

    code, output = _run(
        ["psql", source, "-tAc", _FORCED_RLS_SQL.replace("%(schema)s", f"'{schema}'")],
        dry_run=False,
    )
    forced = [line.strip() for line in output.splitlines() if line.strip()]
    findings.append(
        Finding(
            "tables forcing RLS",
            code == 0,
            f"{len(forced)} table(s) force row-level security"
            + (f": {', '.join(forced[:6])}" + ("..." if len(forced) > 6 else "") if forced else ""),
        )
    )

    if forced and not can_bypass:
        # Does every forced table's policy still let an unscoped session see
        # everything? That branch is the only reason --enable-row-security is
        # complete, and it is a property of the policy text, not of the backup.
        code, output = _run(
            [
                "psql",
                source,
                "-tAc",
                f"SELECT count(*) FROM pg_policies WHERE schemaname = '{schema}' "
                "AND qual IS NOT NULL AND qual NOT LIKE '%IS NULL%'",
            ],
            dry_run=False,
        )
        scoped_only = output.strip()
        findings.append(
            Finding(
                "every RLS policy has an unscoped branch",
                code == 0 and scoped_only == "0",
                (
                    "every policy permits an unscoped session, so pg_dump "
                    "--enable-row-security is complete today"
                    if scoped_only == "0"
                    else f"{scoped_only} policy(ies) have no 'setting is unset' branch, so "
                    "pg_dump --enable-row-security would exit 0 and write a backup "
                    "MISSING those rows. Take the backup as a role that bypasses RLS"
                ),
            )
        )
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dump", type=Path, required=True, help="the SQL dump to restore")
    parser.add_argument(
        "--target",
        default="postgresql:///kryova_restore_drill",
        help="a THROWAWAY database to restore into",
    )
    parser.add_argument("--media-copy", type=Path, default=None, help="the blob-store backup")
    parser.add_argument("--check-archiving", action="store_true")
    parser.add_argument(
        "--check-dumpable",
        metavar="SOURCE_URL",
        default=None,
        help=(
            "before restoring, ask whether a complete backup of SOURCE_URL can be taken "
            "by this role at all -- 14 of 38 tables force RLS and a plain pg_dump by the "
            "application role fails"
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result = drill(
        dump=args.dump,
        target=args.target,
        media_copy=args.media_copy,
        dry_run=args.dry_run,
        check_archiving=args.check_archiving,
    )
    if args.check_dumpable and not args.dry_run:
        # Prepended: "can a backup be taken" comes before "does this one restore",
        # and a reader scanning the report should meet them in that order.
        result.findings[:0] = check_dumpable(args.check_dumpable)
    print(json.dumps(result.to_dict(), indent=2) if args.json else result.report())
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
