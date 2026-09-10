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
"""

from __future__ import annotations

import argparse
import json
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
    def passed(self) -> bool:
        """A drill passes only when nothing is missing and nothing failed.

        Orphans do **not** fail it: they are wasted disk, and failing a drill
        over waste teaches people to ignore drill failures — which is how the
        one that matters gets ignored too.
        """
        return all(finding.ok for finding in self.findings) and not self.missing_blobs

    def report(self) -> str:
        lines = [f"Restore drill against {self.target}", ""]
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
        return {
            **asdict(self),
            "rto_minutes": RTO_MINUTES,
            "rpo_minutes": RPO_MINUTES,
            "within_rto": self.within_rto,
            "passed": self.passed,
        }


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


def _run(command: list[str], *, dry_run: bool) -> tuple[int, str]:
    if dry_run:
        return 0, "(dry run) " + " ".join(command)
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    return completed.returncode, (completed.stdout + completed.stderr).strip()


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
    code, output = _run(["alembic", "upgrade", "head"], dry_run=dry_run)
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
        result.counts = _count_rows(target)
        if media_copy is not None:
            result.missing_blobs, result.orphan_blobs = _check_blobs(target, media_copy)
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


def _count_rows(target: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in COUNTED_TABLES:
        code, output = _run(["psql", target, "-tAc", f"SELECT count(*) FROM {table}"], dry_run=False)
        if code == 0 and output.strip().isdigit():
            counts[table] = int(output.strip())
    return counts


def _check_blobs(target: str, media_copy: Path) -> tuple[list[str], list[str]]:
    """Which referenced blobs are absent, and which stored blobs are unreferenced.

    Reported as two lists rather than one verdict. They are different problems:
    the first is data loss and the second is wasted disk, and a single
    "integrity: FAIL" would bury the one that matters under the one that does
    not.
    """
    code, output = _run(["psql", target, "-tAc", "SELECT sha256 FROM media"], dry_run=False)
    if code != 0:
        return [], []
    referenced = {line.strip() for line in output.splitlines() if line.strip()}
    stored = {path.name for path in media_copy.rglob("*") if path.is_file()}
    return sorted(referenced - stored), sorted(stored - referenced)


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
    print(json.dumps(result.to_dict(), indent=2) if args.json else result.report())
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
