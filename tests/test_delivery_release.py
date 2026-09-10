"""Release notes from the merge log, and the release pipeline (P9 task 7).

The notes are tested on **synthetic log text**, never on this repository's own
history: a test that read the live log would pass or fail depending on what was
merged this week, which is a test of the week rather than of the code. One class
builds a throwaway git repository instead, because "first-parent" is a claim
about how git walks a merge and only a real merge can falsify it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.release_notes import (
    FIELD,
    GROUPS,
    MERGES,
    OTHER,
    RECORD,
    classify,
    main,
    parse_log,
    render,
)


def _sha(number: int) -> str:
    return f"{number:040x}"


def _log(*commits: tuple[int, str, str]) -> str:
    """git log output for (number, subject, body) commits, one parent each."""
    return "".join(
        f"{_sha(number)}{FIELD}{_sha(number + 1000)}{FIELD}{subject}{FIELD}{body}{RECORD}\n"
        for number, subject, body in commits
    )


def _groups(notes: str) -> list[str]:
    return [line[3:] for line in notes.splitlines() if line.startswith("## ")]


class TestEveryCommitIsListed:
    def test_an_unprefixed_commit_is_listed_under_other_changes_rather_than_dropped(self) -> None:
        """This history contains "Implement code changes to enhance functionality
        and improve performance". A note that hid it would claim the release
        contains less than it does."""
        subject = "Implement code changes to enhance functionality and improve performance"
        notes = render(parse_log(_log((1, subject, ""))), "v1.0.0", "v0.9.0")

        assert _groups(notes) == [OTHER]
        assert subject in notes

    def test_a_merge_that_only_synchronised_a_branch_is_listed_under_merges(self) -> None:
        entry = classify(_sha(1), "Merge remote-tracking branch 'origin/main'", "", (_sha(2), _sha(3)))

        assert entry.group == MERGES

    def test_a_merge_that_carries_a_prefix_is_read_as_the_change_it_names(self) -> None:
        entry = classify(_sha(1), "feat(E14): two authors on one product", "", (_sha(2), _sha(3)))

        assert entry.group == "Features"

    def test_an_unknown_lower_case_word_before_a_colon_is_not_a_prefix(self) -> None:
        """Nobody agreed on `wip:`. The whole subject is kept so nothing the author
        wrote is cut off."""
        entry = classify(_sha(1), "wip: half a thing")

        assert entry.group == OTHER
        assert entry.summary == "wip: half a thing"

    def test_a_colon_later_in_the_subject_does_not_make_a_prefix(self) -> None:
        assert classify(_sha(1), "fix the thing: properly").group == OTHER
        assert classify(_sha(2), "Add tests: rate limiting").group == OTHER


class TestThisRepositorysPrefixesAreRead:
    def test_every_type_the_history_uses_has_a_group_of_its_own(self) -> None:
        """Counted over the whole history on 2026-09-10. A rare type falling
        through to Other changes would read as an unprefixed commit."""
        declared = {kind for kind, _title in GROUPS}

        assert {"feat", "fix", "docs", "test", "perf", "chore", "refactor", "ci", "style", "plan"} <= declared

    def test_a_scoped_prefix_keeps_every_scope(self) -> None:
        entry = classify(_sha(1), "fix(kernel,dispatch): three defects the ladder found")

        assert entry.group == "Fixes"
        assert entry.scopes == ("kernel", "dispatch")
        assert entry.summary == "three defects the ladder found"

    def test_a_composite_prefix_is_grouped_by_its_first_type_and_names_the_rest(self) -> None:
        """`perf(E16.1),fix:` is a performance change that is also a fix — listed
        once, and without losing the second word."""
        entry = classify(_sha(1), "perf(E16.1),fix: the model is the latency")

        assert entry.group == "Performance"
        assert entry.also == ("fix",)
        assert entry.scopes == ("E16.1",)
        assert "(also fix)" in entry.line()

    def test_a_pull_request_merge_is_named_by_its_title_and_number(self) -> None:
        entry = classify(
            _sha(1),
            "Merge pull request #3 from Achraf-bhl/fix/catia-ownerdrawn-menus",
            "fix(bridge): owner-drawn menus are read\n\nlonger description",
            (_sha(2), _sha(3)),
        )

        assert entry.group == "Fixes"
        assert entry.pull_request == 3
        assert "(#3)" in entry.line()
        assert entry.summary == "owner-drawn menus are read"

    def test_a_breaking_change_is_marked(self) -> None:
        entry = classify(_sha(1), "feat(api)!: the login response is a union")

        assert entry.breaking
        assert "**breaking**" in entry.line()


class TestTheNotesAreDeterministic:
    LOG = _log(
        (1, "docs(ladder): Levels 2 and 3 pass", ""),
        (2, "fix(kernel): sketching on a face worked", ""),
        (3, "Add comprehensive tests for rate limiting", ""),
        (4, "feat(E6.3,E7): a shell can be meshed", ""),
    )

    def test_the_same_log_renders_the_same_bytes(self) -> None:
        first = render(parse_log(self.LOG), "v1.0.0", "v0.9.0")
        second = render(parse_log(self.LOG), "v1.0.0", "v0.9.0")

        assert first == second

    def test_there_is_no_date_unless_one_is_passed(self) -> None:
        """A field that changes on every run makes a document differ from itself
        — the reason `scripts/sbom.py` carries no timestamp either."""
        undated = render(parse_log(self.LOG), "v1.0.0", "v0.9.0")
        dated = render(parse_log(self.LOG), "v1.0.0", "v0.9.0", date="2026-09-10")

        assert "Released" not in undated
        assert "Released 2026-09-10." in dated

    def test_a_date_that_is_not_a_calendar_date_is_refused(self) -> None:
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            render(parse_log(self.LOG), "v1.0.0", "v0.9.0", date="10/09/2026")

    def test_the_group_order_does_not_depend_on_the_order_of_the_log(self) -> None:
        notes = render(parse_log(self.LOG), "v1.0.0", "v0.9.0")

        assert _groups(notes) == ["Features", "Fixes", "Documentation", OTHER]

    def test_a_first_release_says_there_was_no_earlier_tag(self) -> None:
        notes = render(parse_log(self.LOG), "v0.1.0", None)

        assert "First release" in notes

    def test_an_empty_range_says_there_was_nothing_rather_than_printing_a_blank_page(self) -> None:
        notes = render([], "v1.0.1", "v1.0.0")

        assert "No commits between v1.0.0 and v1.0.1." in notes

    def test_a_record_in_another_format_is_refused_with_the_format_to_use(self) -> None:
        with pytest.raises(ValueError, match="--format="):
            parse_log(f"only-a-sha{RECORD}")


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        [
            "git",
            "-c", "user.name=Release Notes Test",
            "-c", "user.email=release-notes@example.com",
            "-c", "commit.gpgsign=false",
            "-c", "tag.gpgsign=false",
            *args,
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return completed.stdout


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    """v0.1.0, then a branch merged with --no-ff whose inner commits are noise."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--quiet", "--initial-branch=main")
    _git(repo, "commit", "--allow-empty", "--quiet", "-m", "feat: the first thing")
    _git(repo, "tag", "v0.1.0")
    _git(repo, "commit", "--allow-empty", "--quiet", "-m", "fix(kernel): a real fix on main")
    _git(repo, "checkout", "--quiet", "-b", "topic")
    _git(repo, "commit", "--allow-empty", "--quiet", "-m", "wip typo inside the branch")
    _git(repo, "checkout", "--quiet", "main")
    _git(
        repo,
        "merge", "--no-ff", "--quiet", "topic",
        "-m", "Merge pull request #7 from someone/topic",
        "-m", "feat(E15): the branch's actual change",
    )
    _git(repo, "tag", "v0.2.0")
    return repo


class TestTheLogIsReadFirstParent:
    def test_a_merged_branchs_inner_commits_stay_out_and_its_title_goes_in(
        self, repository: Path, tmp_path: Path
    ) -> None:
        out = tmp_path / "notes.md"

        assert main(["--repo", str(repository), "--tag", "v0.2.0", "--out", str(out)]) == 0
        notes = out.read_text(encoding="utf-8")

        assert "since v0.1.0" in notes, "the previous tag was not found"
        assert "the branch's actual change (#7)" in notes
        assert "a real fix on main" in notes
        assert "wip typo inside the branch" not in notes
        assert "the first thing" not in notes, "the range started before the previous tag"

    def test_the_file_is_written_with_lf_line_endings(self, repository: Path, tmp_path: Path) -> None:
        out = tmp_path / "notes.md"

        main(["--repo", str(repository), "--tag", "v0.2.0", "--out", str(out)])

        assert b"\r\n" not in out.read_bytes()

    def test_a_tag_that_does_not_exist_is_refused_in_words(self, repository: Path) -> None:
        assert main(["--repo", str(repository), "--tag", "v9.9.9"]) == 2
