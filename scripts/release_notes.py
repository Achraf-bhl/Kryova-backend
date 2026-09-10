"""Release notes, generated from the merge log (master plan P9 task 7).

**Written by `git log`, not by whoever cuts the release.** Notes typed at release
time describe what the author remembers doing, which is the part most likely to
be right and least likely to be complete; the log is complete by construction.
So this reads the first-parent history between two tags and groups it by the
prefixes this repository's commits actually carry — `feat(E6.3):`,
`fix(kernel,dispatch):`, `docs(ladder):`, `perf(E16.1),fix:` — rather than by a
convention borrowed from somewhere the history does not follow.

**First-parent, deliberately.** On `main` a merged branch appears once, as its
merge commit, and the branch's own work-in-progress commits stay out of the
notes. A plain `git log` would list both and count every merged pull request
twice.

**Nothing is dropped.** A commit with no recognised prefix is listed under a
named group, *Other changes*, and a merge that only synchronised a branch is
listed under *Merges*. Omitting what does not parse would make the notes look
tidier and be wrong exactly where a reader cannot see it: this history contains
"Implement code changes to enhance functionality and improve performance", and a
note that hid it would claim the release contains less than it does.

**Deterministic.** The same log produces byte-identical notes. There is no
timestamp unless the tag's date is passed in with `--date`, for the reason
`scripts/sbom.py` gives: a field that changes on every run makes a document
differ from itself, and "somebody re-ran the script" becomes indistinguishable
from "the release changed".

    python -m scripts.release_notes --tag v0.3.0
    python -m scripts.release_notes --tag v0.3.0 --previous v0.2.0 --date 2026-09-10 --out notes.md
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path

#: Groups in the order they are printed, keyed by the commit type that selects
#: them. Fixed here so the reading order never depends on which commit happened
#: to come first in the log. Every type this repository's history uses is
#: listed — counted over the whole history on 2026-09-10: feat, fix, docs,
#: test, perf, chore, refactor, ci, style, plan — so none of them falls through
#: to *Other changes* merely for being rare.
GROUPS: tuple[tuple[str, str], ...] = (
    ("feat", "Features"),
    ("fix", "Fixes"),
    ("perf", "Performance"),
    ("refactor", "Refactoring"),
    ("test", "Tests"),
    ("ci", "Continuous integration"),
    ("docs", "Documentation"),
    ("plan", "Plan"),
    ("chore", "Chores"),
    ("style", "Style"),
)

#: The named home of every commit whose subject carries no recognised prefix.
OTHER = "Other changes"

#: Merges that brought nothing of their own — `Merge remote-tracking branch
#: 'origin/main'` — kept apart from *Other changes*, which is real work.
MERGES = "Merges"

_TITLES = dict(GROUPS)
_ORDER = (*(title for _type, title in GROUPS), OTHER, MERGES)

#: `git log` field and record separators: the ASCII unit and record separators,
#: which a person cannot type into a commit message, unlike the newlines,
#: pipes and tabs that do turn up in bodies.
FIELD = "\x1f"
RECORD = "\x1e"
LOG_FORMAT = "%H%x1f%P%x1f%s%x1f%b%x1e"

#: Characters of the commit hash printed beside each line. Display only; the
#: full hash is in the log for anyone who needs to disambiguate.
SHORT_SHA = 7

#: `type(scope)`, optionally chained with commas (`perf(E16.1),fix`), an
#: optional `!`, a colon and a summary. The colon must follow the prefix
#: directly, so "fix the thing: now" is not read as a `fix`.
_PREFIX = re.compile(
    r"^(?P<types>[a-z]+(?:\([^()]*\))?(?:,[a-z]+(?:\([^()]*\))?)*)"
    r"(?P<breaking>!)?:\s+(?P<summary>\S.*)$"
)
_PART = re.compile(r"(?P<type>[a-z]+)(?:\((?P<scope>[^()]*)\))?")
_PULL_REQUEST = re.compile(r"^Merge pull request #(?P<number>\d+) from \S+")
_SYNC_MERGE = re.compile(r"^Merge (?:remote-tracking branch|branch|tag|commit)\b")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class Entry:
    """One line of the notes."""

    sha: str
    group: str
    summary: str
    scopes: tuple[str, ...] = ()
    #: The further types of a composite prefix: `perf(E16.1),fix:` is grouped
    #: under Performance and says it is also a fix, rather than being listed
    #: twice or losing the second word.
    also: tuple[str, ...] = ()
    breaking: bool = False
    pull_request: int | None = None

    def line(self) -> str:
        parts = ["- "]
        if self.scopes:
            parts.append(f"**{', '.join(self.scopes)}:** ")
        parts.append(self.summary)
        if self.also:
            parts.append(f" (also {', '.join(self.also)})")
        if self.breaking:
            parts.append(" — **breaking**")
        if self.pull_request is not None:
            parts.append(f" (#{self.pull_request})")
        parts.append(f" (`{self.sha[:SHORT_SHA]}`)")
        return "".join(parts)


def _from_subject(sha: str, subject: str) -> Entry:
    match = _PREFIX.match(subject)
    if match is None:
        return Entry(sha=sha, group=OTHER, summary=subject)
    parts = [(part.group("type"), part.group("scope")) for part in _PART.finditer(match["types"])]
    first = parts[0][0]
    if first not in _TITLES:
        # A lower-case word and a colon that is not one of this repository's
        # types ("wip: ...") is not a prefix anybody agreed on. The whole
        # subject is kept, so nothing the author wrote is cut off.
        return Entry(sha=sha, group=OTHER, summary=subject)
    scopes = tuple(
        dict.fromkeys(
            scope.strip()
            for _type, raw in parts
            if raw
            for scope in raw.split(",")
            if scope.strip()
        )
    )
    also = tuple(dict.fromkeys(kind for kind, _scope in parts[1:] if kind != first))
    return Entry(
        sha=sha,
        group=_TITLES[first],
        summary=match["summary"].strip(),
        scopes=scopes,
        also=also,
        breaking=bool(match["breaking"]),
    )


def classify(sha: str, subject: str, body: str = "", parents: tuple[str, ...] = ()) -> Entry:
    """Which group a first-parent commit belongs in, and how its line reads."""
    subject = subject.strip()
    pull = _PULL_REQUEST.match(subject)
    if pull is not None:
        # GitHub writes the pull request's title as the first line of the body
        # and a boilerplate subject; the title is what the change is called.
        number = int(pull["number"])
        title = next((line.strip() for line in body.splitlines() if line.strip()), "")
        if title:
            return replace(_from_subject(sha, title), pull_request=number)
        return Entry(sha=sha, group=MERGES, summary=subject, pull_request=number)
    if _PREFIX.match(subject) is None and (_SYNC_MERGE.match(subject) or len(parents) > 1):
        return Entry(sha=sha, group=MERGES, summary=subject)
    return _from_subject(sha, subject)


def parse_log(text: str) -> list[Entry]:
    """Entries from `git log --format=LOG_FORMAT` output, in log order."""
    entries: list[Entry] = []
    for record in text.split(RECORD):
        if not record.strip():
            continue
        fields = record.lstrip("\r\n").split(FIELD)
        if len(fields) < 3:
            raise ValueError(
                "A git log record has fewer than three fields. Run git log with "
                f"--format={LOG_FORMAT!r}; any other format cannot be parsed."
            )
        sha, parents, subject = fields[0].strip(), tuple(fields[1].split()), fields[2]
        body = fields[3] if len(fields) > 3 else ""
        entries.append(classify(sha, subject, body, parents))
    return entries


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def render(entries: list[Entry], tag: str, previous: str | None, date: str | None = None) -> str:
    """The notes as Markdown. Same arguments in, same bytes out."""
    if date is not None and not _DATE.match(date):
        raise ValueError(
            f"--date must be YYYY-MM-DD, got {date!r}. Pass the tag's own date "
            "(git for-each-ref --format='%(creatordate:short)' refs/tags/<tag>) so two "
            "runs over one tag print the same thing."
        )
    lines = [f"# Kryova backend {tag}", ""]
    if date is not None:
        lines.append(f"Released {date}.")
    if previous:
        lines.append(f"{_plural(len(entries), 'change')} on `main` since {previous}.")
    else:
        lines.append(
            f"First release: {_plural(len(entries), 'change')} on `main` up to {tag}, "
            "with no earlier release tag to start from."
        )
    if not entries:
        lines += ["", f"No commits between {previous or 'the start of history'} and {tag}."]
    for title in _ORDER:
        group = [entry for entry in entries if entry.group == title]
        if not group:
            continue
        lines += ["", f"## {title}", ""]
        lines += [entry.line() for entry in group]
    return "\n".join(lines) + "\n"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def _ref_exists(repo: Path, ref: str) -> bool:
    return _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").returncode == 0


def previous_tag(repo: Path, tag: str, pattern: str) -> str | None:
    """The nearest release tag reachable from `tag`'s parent, or None.

    `tag^` rather than `tag`, so the tag being released is never its own
    predecessor. A tag on a root commit has no parent and is a first release.
    """
    found = _git(repo, "describe", "--tags", "--abbrev=0", "--match", pattern, f"{tag}^")
    return found.stdout.strip() or None if found.returncode == 0 else None


def git_log(repo: Path, tag: str, previous: str | None) -> str:
    revision = f"{previous}..{tag}" if previous else tag
    completed = _git(repo, "log", "--first-parent", f"--format={LOG_FORMAT}", revision)
    if completed.returncode != 0:
        raise SystemExit(
            f"git log {revision} failed: {completed.stderr.strip()}. A shallow clone has no "
            "history to read; check out with fetch-depth: 0."
        )
    return completed.stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True, help="the release tag the notes are for")
    parser.add_argument("--previous", help="the tag to start from (default: the nearest earlier)")
    parser.add_argument(
        "--first-release",
        action="store_true",
        help="ignore earlier tags and describe the whole first-parent history",
    )
    parser.add_argument("--match", default="v[0-9]*", help="which tags count as releases")
    parser.add_argument("--date", help="the tag's date, YYYY-MM-DD; omitted means no date line")
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--out", type=Path, help="write here instead of stdout")
    args = parser.parse_args(argv)

    if args.date is not None and not _DATE.match(args.date):
        print(f"Refusing: --date must be YYYY-MM-DD, got {args.date!r}.", file=sys.stderr)
        return 2
    if not _ref_exists(args.repo, args.tag):
        print(
            f"Refusing: {args.tag!r} is not a tag or commit in {args.repo.resolve()}. "
            "Fetch tags (git fetch --tags) or check the name.",
            file=sys.stderr,
        )
        return 2

    previous: str | None
    if args.first_release:
        previous = None
    elif args.previous:
        if not _ref_exists(args.repo, args.previous):
            print(f"Refusing: --previous {args.previous!r} does not exist.", file=sys.stderr)
            return 2
        previous = args.previous
    else:
        previous = previous_tag(args.repo, args.tag, args.match)

    notes = render(parse_log(git_log(args.repo, args.tag, previous)), args.tag, previous, args.date)
    if args.out:
        # LF on every platform: a release note is attached to a public page, and
        # a CRLF file is a diff against the same notes generated on Linux.
        with open(args.out, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(notes)
    else:
        sys.stdout.write(notes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
