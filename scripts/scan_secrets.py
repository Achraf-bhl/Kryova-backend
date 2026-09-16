"""Scan the tracked tree for credentials that must never be committed (P9 task 7).

This repository has already committed a live `.env` once — the note at the top
of `.gitignore` records it, and says the credentials had to be *rotated* rather
than removed, because a secret that reached a commit is public from that moment
whatever the next commit does. The whole value of a scanner is that it says so
before the push rather than after the rotation.

**What it scans and what it does not.** It reads the files git currently
tracks. It does **not** read history, so it cannot tell you whether a secret
was committed and later deleted — that is a different tool and a different
remedy (rotate, then rewrite), and claiming otherwise here would be the most
dangerous thing this file could do. Every run prints that limit.

**Rules are named and stated, and there is no entropy threshold.** A
high-entropy string is not a secret, and a rule nobody agreed to is one people
route around: the same argument `app/core/status.py` makes for not inferring
"degraded" from a failure rate. Each rule below carries what it matches and why
a hit is worth a person's attention.

**An accepted match is pinned by digest, never by path.** `ALLOWED` keys on
(path, rule, sha256 of the matched text), so an example key living in a fixture
is accounted for by name — and a *different* secret appearing in that same file
is still a finding. Keying on the path alone would turn one justified exception
into a permanent blind spot over a whole file, which is how allowlists rot.
This is the same rule `app/core/gates.py` applies to what a signature covers.

**What it skips, it says.** Binary files and anything over `MAX_BYTES` are not
read — `data/bm25/` alone is ~450 MB of tracked PDFs. A scanner that skipped in
silence would report "clean" about a tree it had not looked at, so the skipped
count and the reason are part of the output.

Run it with `python -m scripts.scan_secrets`; `--json` for a machine, and the
exit code is 1 when anything is found.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

#: Files larger than this are not read. Chosen to be far above any source file
#: here and far below the tracked manuals; a source file this big is itself
#: worth a look.
MAX_BYTES = 2_000_000


@dataclass(frozen=True)
class Rule:
    """One thing worth stopping a commit for, and why it is worth stopping."""

    name: str
    pattern: re.Pattern[str]
    why: str
    #: When set, the named group holds a hostname and the match is only a
    #: finding if that host could be a machine somebody can reach. See
    #: `is_reachable_host`.
    host_group: str | None = None


#: Reserved by RFC 2606 §2 and §3 so documentation can name a host without
#: naming a real one. Matching on these is not a guess about intent: the
#: standard exists precisely to make this decidable.
RESERVED_SUFFIXES = (
    ".test",
    ".example",
    ".invalid",
    ".localhost",
    "example.com",
    "example.net",
    "example.org",
)

#: Hosts that resolve only on the machine reading them.
LOOPBACK = ("localhost", "127.0.0.1", "::1", "0.0.0.0")


def is_reachable_host(host: str) -> bool:
    """Could this name a machine somebody else can connect to?

    A credential is dangerous in proportion to what it opens, and a URL whose
    host is `localhost`, a bare service name from a compose file, or one of
    RFC 2606's reserved names opens nothing from outside the reader's machine.
    Without this the rule fires on every fixture and every setup example in the
    repository — twenty-five of them on the first run — and a rule that is
    wrong twenty-five times out of twenty-five is one people learn to skip.
    """
    name = host.strip().lower().rstrip(".")
    if not name or name in LOOPBACK:
        return False
    if name.startswith("[") or name.startswith("127.") or name.startswith("192.168."):
        return False
    if any(name == suffix.lstrip(".") or name.endswith(suffix) for suffix in RESERVED_SUFFIXES):
        return False
    # A single-label name (`db`, `host`, `postgres`) is a compose service or a
    # placeholder; it has no public resolution.
    return "." in name


#: Every pattern here matches a credential *format* that is issued by somebody
#: — so a hit is either a real credential or a documented example, and both are
#: worth a human deciding about. Deliberately absent: a generic
#: `password = "..."` rule, which fires on every test fixture in the suite and
#: teaches people to pass `--no-verify`.
RULES: tuple[Rule, ...] = (
    Rule(
        "private-key-block",
        re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"),
        "A private key in the tree is the key itself, not a reference to one.",
    ),
    Rule(
        "aws-access-key-id",
        re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
        "An AWS access key id; the secret half is usually on a neighbouring line.",
    ),
    Rule(
        "github-token",
        re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
        "A GitHub token, which can read or write every repository its owner can.",
    ),
    Rule(
        "slack-token",
        re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{12,}\b"),
        "A Slack token, which reads the workspace it was issued for.",
    ),
    Rule(
        "anthropic-key",
        re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{24,}\b"),
        "An Anthropic API key, billed to whoever issued it.",
    ),
    Rule(
        "openai-key",
        re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}\b"),
        "An OpenAI-format API key, billed to whoever issued it.",
    ),
    Rule(
        "url-with-password",
        re.compile(
            r"\b[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s/:@]+:[^\s/:@]+@(?P<host>[^\s/:@?\"']+)"
        ),
        "A connection URL carrying its own password — the shape a DATABASE_URL has.",
        host_group="host",
    ),
    Rule(
        # `[^']*[A-Za-z0-9][^']*` rather than a length: `PASSWORD '...'` and
        # `PASSWORD '***'` are elisions, and no password policy anywhere issues
        # a credential with no alphanumeric character in it. That is a fact
        # about what issuers produce, not a guess about what this text means.
        "sql-role-password",
        re.compile(r"(?i)\bPASSWORD\s+'(?P<secret>[^']*[A-Za-z0-9][^']*)'"),
        "A literal password in a role-creation statement. Whatever its value, "
        "every install that follows the instruction ends up sharing it — which "
        "is the default-credential failure P1 task 4 refuses at boot, arriving "
        "through the documentation instead.",
    ),
)

#: Paths that must never be tracked, whatever they contain. Matched against the
#: repository-relative path with `Path.match`, so `*.pem` catches one at any
#: depth. This is a separate check from the content rules on purpose: an empty
#: `.env` is still a mistake, because the next person to fill it in will not
#: notice it is committed.
FORBIDDEN_PATHS: tuple[tuple[str, str], ...] = (
    (".env", "holds real credentials; `.env.example` is the tracked one"),
    (".env.local", "the local override, read after `.env`, so it holds the live values"),
    ("*.env.local", "the local override, read after `.env`, so it holds the live values"),
    ("*.env*.bak", "a backup of a secrets file is still a secrets file"),
    ("*.pem", "a certificate or key file"),
    ("*.p12", "a PKCS#12 bundle, which carries the private key"),
    ("*.pfx", "a PKCS#12 bundle, which carries the private key"),
    ("id_rsa", "an SSH private key"),
    ("id_ed25519", "an SSH private key"),
    ("*.keystore", "a Java keystore, which carries the private key"),
)


@dataclass(frozen=True)
class Accepted:
    """A match somebody has looked at, pinned to the text they looked at."""

    path: str
    rule: str
    digest: str
    why: str


def digest_of(text: str) -> str:
    """The sha256 an `Accepted` pins. Public so the entry can be regenerated."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


#: Matches that have been read and accounted for. Adding one is a decision:
#: write what the value actually is and why it is not a credential. An entry
#: whose digest no longer matches anything in the tree is reported as stale
#: rather than ignored, so this list cannot quietly become permanent.
#: Every entry below is a URL whose *host* is Neon's hostname shape — which is
#: what the code under test parses, and why a fixture cannot use `localhost` —
#: carrying a two-letter or shouting placeholder where a password goes. Read on
#: 2026-09-16, all six.
ALLOWED: tuple[Accepted, ...] = (
    Accepted(
        ".env.example",
        "url-with-password",
        "db5270e772eb0ea20e681241aad3af648f60c87d2cec87652280b898ec69c4c3",
        "The documented example URL: `USER:PASSWORD@ep-xxxx-pooler.REGION…`, "
        "every field shouting that it is a field.",
    ),
    Accepted(
        ".github/workflows/ci.yml",
        "sql-role-password",
        "44bad66c0a954c335cbe5cc12b2349487b4332dbdb7b171f31ac74ada26e730f",
        "The application role in the CI PostgreSQL service container. The "
        "container is created and destroyed inside one job on the runner's own "
        "loopback and is never reachable; the role exists to be NOBYPASSRLS, "
        "which is the thing that job checks.",
    ),
    Accepted(
        "tests/test_config_and_jobs.py",
        "url-with-password",
        "0c9059767cac194cc43d041c279c86f68790f39a82703284211ac7c6214af00f",
        "`user:pw@example.neon.tech` — a settings fixture.",
    ),
    Accepted(
        "tests/test_database_isolation.py",
        "url-with-password",
        "c30b78e15ece7b78656ed96d7738b3c6020bd409785b05ad89b901fcece27a73",
        "`user:pw@ep-prod-pooler…` — the fixture for the pooled-endpoint "
        "refusal, which needs a host that reads as pooled.",
    ),
    Accepted(
        "tests/test_database_isolation.py",
        "url-with-password",
        "dfd94995d68893e0d6b6e9ea44a73640aa670c985910a0cc91e97705cd0b05e4",
        "`user:pw@ep-prod-pooler…` under `postgresql+psycopg`, the same "
        "fixture through the driver-qualified scheme.",
    ),
    Accepted(
        "tests/test_database_sslmode.py",
        "url-with-password",
        "36c77a5ff02607df085d7b090346bb655139999f455486351c0a55a76db0a59e",
        "`u:p@ep-x-pooler…?sslmode=require` — the fixture `sslmode_for` reads.",
    ),
)


@dataclass(frozen=True)
class Finding:
    """One match, with enough context for a person to decide and no more."""

    path: str
    line: int
    rule: str
    why: str
    matched: str
    digest: str

    def redacted(self) -> str:
        """The match with its middle removed.

        Printing the whole thing would put the credential into CI logs, which
        are a place secrets get copied to rather than a place they stop.
        """
        if len(self.matched) <= 12:
            return self.matched[:4] + "…"
        return f"{self.matched[:6]}…{self.matched[-4:]}"

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "line": self.line,
            "rule": self.rule,
            "why": self.why,
            "match": self.redacted(),
            "digest": self.digest,
        }


@dataclass(frozen=True)
class Skipped:
    """A tracked file that was not read, and the reason it was not."""

    path: str
    why: str


@dataclass(frozen=True)
class ScanResult:
    findings: tuple[Finding, ...]
    forbidden: tuple[tuple[str, str], ...]
    stale_allowances: tuple[Accepted, ...]
    scanned: int
    skipped: tuple[Skipped, ...]

    @property
    def clean(self) -> bool:
        """True when nothing needs a person.

        A stale allowance counts: it means this file describes a tree that no
        longer exists, and the next reader would trust it.
        """
        return not (self.findings or self.forbidden or self.stale_allowances)


def tracked_files(repo: Path) -> list[str]:
    """What git currently tracks, as repository-relative paths."""
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return [name for name in out.stdout.split("\0") if name]


def forbidden_findings(paths: list[str]) -> list[tuple[str, str]]:
    """Tracked paths that must not be tracked at all, with the reason."""
    found: list[tuple[str, str]] = []
    for name in paths:
        candidate = Path(name)
        for pattern, why in FORBIDDEN_PATHS:
            if candidate.match(pattern):
                found.append((name, why))
                break
    return found


def _readable(path: Path) -> str | None:
    """The file's text, or None when it is not text this should read."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\0" in raw[:8192]:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def scan_text(path: str, text: str) -> list[Finding]:
    """Every rule against one file's text."""
    findings: list[Finding] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for rule in RULES:
            for match in rule.pattern.finditer(line):
                if rule.host_group and not is_reachable_host(
                    match.group(rule.host_group) or ""
                ):
                    continue
                matched = match.group(0)
                findings.append(
                    Finding(
                        path=path,
                        line=number,
                        rule=rule.name,
                        why=rule.why,
                        matched=matched,
                        digest=digest_of(matched),
                    )
                )
    return findings


def _is_allowed(finding: Finding) -> bool:
    return any(
        entry.path == finding.path
        and entry.rule == finding.rule
        and entry.digest == finding.digest
        for entry in ALLOWED
    )


def scan(repo: Path = REPO) -> ScanResult:
    """Read every tracked text file and apply every rule."""
    paths = tracked_files(repo)
    findings: list[Finding] = []
    skipped: list[Skipped] = []
    scanned = 0
    seen_digests: set[tuple[str, str, str]] = set()

    for name in paths:
        full = repo / name
        try:
            size = full.stat().st_size
        except OSError:
            skipped.append(Skipped(name, "tracked but not on disk"))
            continue
        if size > MAX_BYTES:
            skipped.append(Skipped(name, f"{size} bytes, over the {MAX_BYTES} limit"))
            continue
        text = _readable(full)
        if text is None:
            skipped.append(Skipped(name, "not UTF-8 text"))
            continue
        scanned += 1
        for finding in scan_text(name, text):
            seen_digests.add((finding.path, finding.rule, finding.digest))
            if not _is_allowed(finding):
                findings.append(finding)

    stale = tuple(
        entry
        for entry in ALLOWED
        if (entry.path, entry.rule, entry.digest) not in seen_digests
    )
    return ScanResult(
        findings=tuple(findings),
        forbidden=tuple(forbidden_findings(paths)),
        stale_allowances=stale,
        scanned=scanned,
        skipped=tuple(skipped),
    )


LIMIT = (
    "This reads the tracked working tree, never git history: a credential that "
    "was committed and later deleted is still public and is not reported here."
)


def report(result: ScanResult) -> str:
    """What a person reads, limit included whether or not anything was found."""
    lines = [
        f"scanned {result.scanned} tracked text file(s); "
        f"skipped {len(result.skipped)} (binary or over {MAX_BYTES} bytes)",
        LIMIT,
    ]
    for path, why in result.forbidden:
        lines.append(f"FORBIDDEN PATH {path}: {why}")
    for finding in result.findings:
        lines.append(
            f"{finding.path}:{finding.line}: {finding.rule} "
            f"[{finding.redacted()}] — {finding.why}"
        )
        lines.append(f"    accept with digest {finding.digest}")
    for entry in result.stale_allowances:
        lines.append(
            f"STALE ALLOWANCE {entry.path} {entry.rule} {entry.digest[:12]}…: "
            "nothing in the tree matches it any more, so the reason it records "
            "describes a file that has moved on. Remove it or re-pin it."
        )
    if result.clean:
        lines.append("no findings")
    else:
        lines.append(
            f"{len(result.findings)} finding(s), "
            f"{len(result.forbidden)} forbidden path(s), "
            f"{len(result.stale_allowances)} stale allowance(s)"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--list-skipped",
        action="store_true",
        help="name every file that was not read, and why",
    )
    args = parser.parse_args(argv)

    result = scan()
    if args.json:
        print(
            json.dumps(
                {
                    "limit": LIMIT,
                    "scanned": result.scanned,
                    "skipped": [
                        {"path": s.path, "why": s.why} for s in result.skipped
                    ],
                    "forbidden": [
                        {"path": path, "why": why} for path, why in result.forbidden
                    ],
                    "findings": [f.as_dict() for f in result.findings],
                    "stale_allowances": [
                        {"path": e.path, "rule": e.rule, "digest": e.digest}
                        for e in result.stale_allowances
                    ],
                    "clean": result.clean,
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(report(result))
        if args.list_skipped:
            for entry in result.skipped:
                print(f"    skipped {entry.path}: {entry.why}")
    return 0 if result.clean else 1


if __name__ == "__main__":
    sys.exit(main())
