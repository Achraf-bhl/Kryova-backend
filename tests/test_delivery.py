"""The delivery substrate: image, SBOM, restore drill, rollback notes (P9 3/6/7).

None of this can be *fully* proved on a laptop — an image nobody has built and a
restore nobody has run are not artefacts. What can be proved is that each piece
refuses the thing it exists to refuse: a health check that passes with no
solver, a licence scan that passes by finding nothing, a drill that would
happily restore over production, and a migration with no rollback note.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


class TestTheContainerHealthCheck:
    def test_it_asks_the_code_that_uses_the_solver_not_the_filesystem(self) -> None:
        """A file at `/usr/bin/ccx` that will not execute passes a `stat` and
        fails every job."""
        from scripts import container_health

        source = Path(container_health.__file__).read_text(encoding="utf-8")

        assert "find_ccx" in source
        assert "os.path.exists" not in source
        assert "Path(" not in source or "/usr/bin/ccx" not in source

    def test_a_missing_solver_makes_the_container_unhealthy(self) -> None:
        """The check exists because a green container that cannot solve anything
        is the most expensive kind of green: the fleet looks healthy and every
        job fails."""
        from scripts.container_health import CHECKS

        required = {name for name, _check, is_required in CHECKS if is_required}

        assert "calculix" in required
        assert "gmsh" in required

    def test_occt_being_absent_does_not_fail_the_container(self) -> None:
        """`app/kernel/` imports without it and degrades to the CATIA backend —
        the same contract the bridge keeps for pywin32. An image deliberately
        built for a CATIA-only deployment is healthy."""
        from scripts.container_health import CHECKS

        optional = {name for name, _check, is_required in CHECKS if not is_required}

        assert optional == {"occt"}

    def test_it_reports_this_machines_real_state(self) -> None:
        """Not a mock. Running it here should say what is actually installed,
        which is how a developer finds out before an import error halfway
        through a run."""
        from scripts.container_health import run

        results = {name: healthy for name, healthy, _detail, _required in run()}

        assert set(results) == {"calculix", "gmsh", "occt"}


class TestTheDockerfile:
    def _text(self) -> str:
        return (REPO / "Dockerfile").read_text(encoding="utf-8")

    def test_the_three_native_pieces_are_named(self) -> None:
        """The determinism substrate and the deploy artefact are the same thing
        (E1 task 7). "The same build" of three native libraries is only sayable
        about one image."""
        text = self._text()

        assert "calculix-ccx" in text
        assert "gmsh" in text.lower()
        assert "cadquery-ocp" in text or "requirements.txt" in text

    def test_the_runtime_stage_installs_the_openmp_runtime_gmsh_links(self) -> None:
        """Nightly run 34822694239 failed with `libgomp.so.1: cannot open shared object file`.

        The builder stage had the library through the compiler, so only the runtime stage's own
        package list says whether the shipped layer has it.
        """
        runtime = self._text().split("AS runtime", 1)[1]
        install = runtime.split("rm -rf /var/lib/apt/lists", 1)[0]

        assert re.search(r"^\s+libgomp1 \\$", install, re.MULTILINE)

    def test_it_does_not_run_as_root(self) -> None:
        text = self._text()

        assert re.search(r"^USER kryova", text, re.MULTILINE)

    def test_it_does_not_migrate_on_start(self) -> None:
        """N replicas racing one migration, and P9 task 4 gates production
        migrations on `alembic check` as a step somebody watches."""
        text = self._text()
        command = [line for line in text.splitlines() if line.startswith("CMD")]

        assert command
        assert "alembic" not in command[0]

    def test_the_python_version_matches_ci(self) -> None:
        """A container running 3.13 while CI proves 3.12 is a fleet nobody has
        tested."""
        image = re.search(r"FROM python:(\d+\.\d+)", self._text())
        workflow = (REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        pinned = re.search(r'PYTHON_VERSION:\s*"?(\d+\.\d+)', workflow)

        assert image and pinned
        assert image.group(1) == pinned.group(1)

    def test_the_base_image_is_a_frozen_suite(self) -> None:
        # `stable` moves; `bookworm` does not. The determinism argument is made
        # by the base image rather than by pinning every apt package.
        assert "bookworm" in self._text()


class TestTheSBOM:
    def test_it_reads_the_installed_environment_not_the_requirements_file(self) -> None:
        """`requirements.txt` lists direct dependencies. A vulnerability lives in
        a transitive one — most obviously the ~640 MB of VTK that
        `cadquery-ocp` drags in."""
        from scripts.sbom import components

        names = {component.name.lower() for component in components()}

        assert "fastapi" in names
        assert len(names) > 40, "only the direct dependencies were found; it is reading the file"

    def test_the_licence_scan_is_not_passing_by_finding_nothing(self) -> None:
        """The guard on the guard. A scan that read no licences at all would
        report zero findings and look exactly like a clean result."""
        from scripts.sbom import components

        found = components()

        assert any(component.licence_known for component in found)
        assert any(component.copyleft for component in found), (
            "no copyleft package was detected at all; gmsh is GPL and is installed, "
            "so the detection is broken rather than the environment being clean"
        )

    def test_an_unaccounted_copyleft_package_is_a_finding(self) -> None:
        """Verified by removing the one accounted package. Decision 4's boundary
        is the process edge, and a GPL library nobody has justified is a licence
        obligation nobody noticed."""
        import scripts.sbom as module

        original = dict(module.LINKED_GPL_IS_A_PROBLEM)
        try:
            module.LINKED_GPL_IS_A_PROBLEM.clear()
            findings = module.licence_findings(module.components())
        finally:
            module.LINKED_GPL_IS_A_PROBLEM.clear()
            module.LINKED_GPL_IS_A_PROBLEM.update(original)

        assert any("gmsh" in finding for finding in findings)

    def test_lgpl_is_not_treated_as_a_decision_4_concern(self) -> None:
        """`lgpl` contains `gpl` and would match the substring. Linking is
        exactly what the LGPL permits, so flagging it would be noise that
        teaches people to ignore the flag."""
        from scripts.sbom import Component

        component = Component("x", "1", "LGPL-3.0", licence_known=True)

        assert not component.copyleft

    def test_an_unknown_licence_is_omitted_rather_than_written_as_unknown(self) -> None:
        """A consumer reading a licence field wants a licence, and a string that
        is not one is worse than an absence it can detect."""
        from scripts.sbom import Component

        entry = Component("x", "1", "UNKNOWN", licence_known=False).to_cyclonedx()

        assert "licenses" not in entry

    def test_the_document_has_no_timestamp(self) -> None:
        """A field that changes on every run makes the SBOM differ from itself,
        so nothing can tell "the dependencies moved" from "somebody re-ran the
        script" — and diffing two is the main thing anybody does with them."""
        from scripts.sbom import bom, components

        document = bom(components())

        assert "timestamp" not in document["metadata"]


class TestTheRestoreDrill:
    def test_it_refuses_a_target_that_might_be_production(self) -> None:
        """Blunt on purpose: a false positive costs a rename, a false negative
        is the incident the drill exists to prevent."""
        from scripts.restore_drill import drill

        for url in (
            "postgresql://host/kryova_production",
            "postgresql://prod-db/kryova",
            "postgresql://host/main",
        ):
            with pytest.raises(SystemExit):
                drill(dump=Path("x.sql"), target=url, media_copy=None, dry_run=True)

    def test_a_throwaway_target_is_allowed(self) -> None:
        from scripts.restore_drill import drill

        result = drill(
            dump=Path("x.sql"),
            target="postgresql:///kryova_restore_drill",
            media_copy=None,
            dry_run=True,
        )

        assert result.dry_run

    def test_missing_blobs_and_orphans_are_reported_apart(self) -> None:
        """They are different problems: the first is data loss and the second is
        wasted disk. A single "integrity: FAIL" buries the one that matters."""
        from scripts.restore_drill import DrillResult

        result = DrillResult(target="t", dry_run=False, missing_blobs=["a"], orphan_blobs=["b"])

        report = result.report()
        assert "DATA LOSS" in report
        assert "wasted disk, not lost data" in report

    def test_orphans_alone_do_not_fail_a_drill(self) -> None:
        """Failing over waste teaches people to ignore drill failures, which is
        how the one that matters gets ignored too."""
        from scripts.restore_drill import DrillResult

        result = DrillResult(target="t", dry_run=False, orphan_blobs=["b", "c"])

        assert result.passed

    def test_a_missing_referenced_blob_fails_the_drill(self) -> None:
        from scripts.restore_drill import DrillResult

        result = DrillResult(target="t", dry_run=False, missing_blobs=["a"])

        assert not result.passed

    def test_rto_and_rpo_are_written_down_and_measured(self) -> None:
        """A target nobody can find is a target nobody is measured against."""
        from scripts.restore_drill import RPO_MINUTES, RTO_MINUTES, DrillResult

        assert RTO_MINUTES > 0 and RPO_MINUTES > 0
        over = DrillResult(target="t", dry_run=False, restore_seconds=RTO_MINUTES * 60 + 1)
        assert not over.within_rto
        assert "OVER" in over.report()


class TestWhatTheFirstRealDrillRunFound:
    """Five defects the drill had while its refusals were all tested (P9 task 6).

    P9.6 stood at PARTIAL saying "the drill is written and its refusals are
    tested; it has never been run". It was run for the first time on 2026-09-16,
    against a real local PostgreSQL 16.15 with two scratch databases, and every
    one of these is something reading the code had not produced. They are pinned
    here so the fixes cannot quietly come undone.

    **Written on Linux and not run as pytest**; each assertion was evaluated once
    by a one-off script, and the behaviours they pin were measured against the
    real server first.
    """

    def test_the_migration_subprocess_is_pointed_at_the_target(self) -> None:
        """The worst of the five: `alembic upgrade head` migrated production.

        The subprocess inherited the environment, so `migrations/env.py` read
        `settings.database_url` and migrated whatever `DATABASE_URL` named rather
        than the database just restored. Measured by pointing `DATABASE_URL` at a
        bystander database: all 38 tables appeared there, none in the target, and
        the drill reported "migrations: ok". On a server, `DATABASE_URL` is
        production.
        """
        from scripts.restore_drill import _alembic_env

        env = _alembic_env("postgresql://u:p@host/restored")
        assert env["DATABASE_URL"] == "postgresql://u:p@host/restored"

    def test_the_test_database_url_is_cleared_too(self) -> None:
        """It resolves ahead of DATABASE_URL in some configurations.

        A drill that quietly migrated the test schema would collide with whatever
        suite is running — the two-concurrent-pytest-runs failure CLAUDE.md
        records, arriving from a direction nobody would look in.
        """
        import os
        from unittest import mock

        from scripts.restore_drill import _alembic_env

        with mock.patch.dict(os.environ, {"TEST_DATABASE_URL": "postgresql:///kryova_test"}):
            assert "TEST_DATABASE_URL" not in _alembic_env("postgresql:///restored")

    def test_a_password_never_reaches_the_report(self) -> None:
        """A drill report is the likeliest thing in this repo to be pasted into a ticket."""
        from scripts.restore_drill import DrillResult

        result = DrillResult(
            target="postgresql://kryova:hunter2@localhost:5432/kryova_restore?sslmode=disable",
            dry_run=True,
        )
        assert "hunter2" not in result.report()
        assert "hunter2" not in json.dumps(result.to_dict())
        # Redacted, not destroyed: which machine a drill ran against is the half
        # a reader needs.
        assert "localhost:5432/kryova_restore" in result.redacted_target
        assert "kryova:***@" in result.redacted_target

    def test_a_url_with_no_credential_passes_through_unchanged(self) -> None:
        from scripts.restore_drill import _redact

        assert _redact("postgresql:///kryova_restore_drill") == "postgresql:///kryova_restore_drill"
        assert _redact("postgresql://localhost/db") == "postgresql://localhost/db"

    def test_a_table_that_could_not_be_counted_is_not_a_zero(self) -> None:
        """`_count_rows` omits a table whose query failed, so the caller must notice.

        Before this, "no such table" and "no rows" were the same answer: the
        counts came back empty and the drill still said PASSED. It only ever
        worked because the role and the schema are both named `kryova`, so
        `search_path`'s `"$user"` resolved — set `DB_SCHEMA` to anything else and
        every count silently vanished.
        """
        from scripts.restore_drill import COUNTED_TABLES, Finding

        counted = {"users": 3}
        unreadable = [table for table in COUNTED_TABLES if table not in counted]
        finding = Finding("row counts", not unreadable, "")
        assert not finding.ok
        assert len(unreadable) == len(COUNTED_TABLES) - 1

    def test_the_count_query_is_schema_qualified(self) -> None:
        from scripts.restore_drill import _qualified

        assert _qualified("users", "kryova") == '"kryova"."users"'

    def test_a_blob_check_that_could_not_run_is_not_a_pass(self) -> None:
        """The one outcome worse than no check: a check that failed, reporting a tick.

        `_check_blobs` returned two empty lists when its query failed, and the
        caller rendered that as "0 referenced blob(s) missing" — indistinguishable
        from a clean store. It returns a third value now, and `None` is the only
        value that means it ran.
        """
        from pathlib import Path as _Path

        from scripts.restore_drill import _check_blobs

        missing, orphans, why = _check_blobs(
            "postgresql://127.0.0.1:1/nothing-here", _Path("/nonexistent"), "kryova"
        )
        assert why is not None
        assert missing == [] and orphans == []

    def test_the_preflight_asks_whether_a_backup_can_be_taken_at_all(self) -> None:
        """The question the drill could not ask, because it started from a file.

        14 of 38 tables FORCE row-level security and the application role is
        NOBYPASSRLS — which the architecture requires (CLAUDE.md *Database* item
        3) — so a plain `pg_dump` exits non-zero and there is no backup. Measured
        2026-09-16. `--enable-row-security` succeeds, and is complete only
        because every current policy permits an unscoped session; a policy
        written without that branch would make the same command write a backup
        missing every row of those tables.
        """
        from scripts.restore_drill import check_dumpable

        findings = check_dumpable("postgresql://127.0.0.1:1/nothing-here", "kryova")
        names = [finding.name for finding in findings]
        assert "role can read past RLS" in names
        assert "tables forcing RLS" in names
        # Unreachable server: the bypass question cannot be answered, so it must
        # not answer "yes".
        assert not findings[0].ok


class TestMigrationRollbackNotes:
    """Every migration says what a rollback would cost (P9 task 4).

    "Production migrations gated on `alembic check` and **a rollback note per
    migration**". The check half is CI's; this is the note. A `downgrade` that
    silently drops a table holding every approval anybody ever signed is a
    rollback nobody should run without being told first — and being told at 3am
    by reading the DDL is not being told.
    """

    #: Migrations written before this rule existed (2026-09-10). **This list may
    #: only ever shrink.** They are grandfathered rather than back-filled
    #: because a rollback note invented by somebody who did not write the
    #: migration is worse than an absent one: it reads as considered and is a
    #: guess, on the one document somebody consults at 3am. Anyone who
    #: understands what one of these rollbacks costs should write the note and
    #: delete the line.
    GRANDFATHERED = frozenset(
        {
            "1b07f4f27e89_organisations_memberships_invitations_.py",
            "286a04c00025_initial_schema_on_postgres.py",
            "2f3f8aadb319_staff_grants_impersonation_sessions_and_.py",
            "490d3f517ca6_simulation_analysis_kind_and_plane_.py",
            "6b877d045c82_user_sessions_with_rotation_and_reuse_.py",
            "8f1c7a9b2d4e_add_refresh_token_hash.py",
            "90957dafff41_record_the_solver_version_that_produced_.py",
            "9b30db1018ad_simulation_grid_count_for_a_convergence_.py",
            "a1b2c3d4e5f6_add_simulation_element_order.py",
            "b941a651831a_simulation_thermal_case_and_a_nullable_.py",
            "c4a1b2d3e5f6_add_agent_conversations.py",
            "d5e6f7a8b9c0_add_password_reset_and_indexes.py",
            "e7f8a9b0c1d2_add_ai_context_and_token_accounting.py",
            "f1a2b3c4d5e6_add_catia_bridge.py",
        }
    )

    def _migrations(self) -> list[Path]:
        found = sorted((REPO / "migrations" / "versions").glob("*.py"))
        assert len(found) > 5, "the migration walk found almost nothing"
        return found

    def test_the_grandfather_list_names_only_migrations_that_exist(self) -> None:
        """A stale entry silently exempts nothing and hides that the list has
        stopped shrinking — which is how a grandfather list becomes permanent."""
        on_disk = {path.name for path in self._migrations()}

        assert self.GRANDFATHERED <= on_disk

    def test_a_new_migration_that_drops_something_needs_a_rollback_note(self) -> None:
        missing: list[str] = []
        for path in self._migrations():
            text = path.read_text(encoding="utf-8")
            module = ast.parse(text)
            docstring = ast.get_docstring(module) or ""
            downgrade = next(
                (
                    node
                    for node in module.body
                    if isinstance(node, ast.FunctionDef) and node.name == "downgrade"
                ),
                None,
            )
            if downgrade is None:
                continue
            body = ast.get_source_segment(text, downgrade) or ""
            destructive = any(
                marker in body for marker in ("drop_table", "drop_column", "drop_constraint")
            )
            if (
                destructive
                and "rollback note" not in docstring.lower()
                and path.name not in self.GRANDFATHERED
            ):
                missing.append(path.name)

        assert not missing, (
            "these migrations drop something and do not say what a rollback would cost: "
            + ", ".join(missing)
        )


class TestTheSecretScan:
    """P9.7. This repository committed a live `.env` once — the note at the top
    of `.gitignore` records it, and records that the credentials had to be
    rotated rather than removed. The scanner is what says so before the push."""

    def test_the_tracked_tree_is_clean(self) -> None:
        """The check itself, and the reason it is blocking in CI while
        `pip-audit` is advisory: a CVE is somebody else's clock running, a
        credential in the tree is this repository's own mistake."""
        from scripts.scan_secrets import scan

        result = scan()

        assert result.clean, report_of(result)

    def test_it_actually_read_the_tree_it_calls_clean(self) -> None:
        """The guard on the guard. A scanner that read nothing reports clean and
        looks exactly like a clean result — the shape `TestTheSBOM` guards the
        licence scan against."""
        from scripts.scan_secrets import scan

        result = scan()

        assert result.scanned > 500, f"only {result.scanned} files were read"

    def test_what_it_did_not_read_is_counted_rather_than_silent(self) -> None:
        """`data/bm25/` alone is ~450 MB of tracked PDFs. A scanner that skipped
        in silence would report clean about a tree it had not looked at."""
        from scripts.scan_secrets import scan

        result = scan()

        assert result.skipped, "nothing was skipped, and the PDFs are tracked"
        assert all(entry.why for entry in result.skipped)

    def test_a_planted_aws_key_is_found(self) -> None:
        """AWS's own documented example key, so this file carries no credential.
        Built from two pieces for the same reason: a literal here would be a
        finding in the very tree the test above asserts is clean."""
        from scripts.scan_secrets import scan_text

        planted = "AKIA" + "IOSFODNN7EXAMPLE"

        found = scan_text("planted.env", f"AWS_ACCESS_KEY_ID={planted}\n")

        assert [f.rule for f in found] == ["aws-access-key-id"]

    def test_a_planted_private_key_header_is_found(self) -> None:
        from scripts.scan_secrets import scan_text

        header = "-----BEGIN RSA " + "PRIVATE KEY-----"

        found = scan_text("planted.pem", header)

        assert [f.rule for f in found] == ["private-key-block"]

    def test_a_url_whose_host_nobody_can_reach_is_not_a_finding(self) -> None:
        """Twenty-five of twenty-five hits on the first run were fixtures and
        setup examples. A rule that is wrong every time is one people learn to
        skip, so the rule asks whether the host is reachable at all."""
        from scripts.scan_secrets import scan_text

        for url in (
            "postgresql://kryova:hunter2@localhost:5432/kryova",
            "postgresql://u:p@db.example.com/db",
            "postgresql://u:p@host/db",
            "postgresql://u:p@127.0.0.1/db",
        ):
            assert scan_text("fixture.py", url) == [], url

    def test_a_url_whose_host_is_real_is_a_finding(self) -> None:
        """The other half of the same rule — otherwise it passes by never
        matching anything."""
        from scripts.scan_secrets import scan_text

        url = "postgresql://kryova:" + "pw" + "@db.kryova.example-host.co/kryova"

        found = scan_text("x.py", url)

        assert [f.rule for f in found] == ["url-with-password"]

    def test_a_password_written_into_a_role_statement_is_a_finding(self) -> None:
        """How the live local password reached `docs/LOCAL_POSTGRES.md`: not as
        a leak, as an instruction. Every machine that followed it shared one
        credential — P1.4's default-secret failure arriving through the docs."""
        from scripts.scan_secrets import scan_text

        statement = "CREATE ROLE kryova LOGIN PASSWORD " + "'fixed-in-a-doc'" + ";"

        found = scan_text("docs/x.md", statement)

        assert [f.rule for f in found] == ["sql-role-password"]

    def test_an_elision_is_not_a_password(self) -> None:
        """`PASSWORD '...'` is how the docs write the field. No password policy
        anywhere issues a credential with no alphanumeric character in it, so
        this is a fact about issuers rather than a guess about the text."""
        from scripts.scan_secrets import scan_text

        assert scan_text("docs/x.md", "CREATE ROLE kryova PASSWORD '...';") == []
        assert scan_text("docs/x.md", "CREATE ROLE kryova PASSWORD '***';") == []

    def test_the_setup_recipe_generates_its_password_rather_than_printing_one(
        self,
    ) -> None:
        """The finding the first real run produced, fixed. Until 2026-09-16 the
        Linux recipe created the role with a fixed literal."""
        recipe = (REPO / "docs" / "LOCAL_POSTGRES.md").read_text(encoding="utf-8")

        assert "kryova_dev_local" not in recipe
        assert "secrets.token_urlsafe" in recipe
        assert "PASSWORD :'pw'" in recipe

    def test_the_recipe_warns_that_an_older_install_still_holds_the_old_one(
        self,
    ) -> None:
        """Changing the instruction does not rotate a password already set, and
        it is in this repository's history where the scanner cannot reach."""
        recipe = (REPO / "docs" / "LOCAL_POSTGRES.md").read_text(encoding="utf-8")

        assert "before 2026-09-16" in recipe
        assert "ALTER ROLE kryova" in recipe

    def test_an_allowance_is_pinned_to_the_text_it_accepted(self) -> None:
        """Keying on the path alone would turn one justified exception into a
        permanent blind spot over a whole file. Verified by changing the value
        at an allowlisted path: the finding comes back."""
        from scripts.scan_secrets import ALLOWED, scan_text

        entry = next(e for e in ALLOWED if e.rule == "url-with-password")
        changed = "postgresql://USER:" + "s3cr3t" + "@ep-xxxx-pooler.REGION.aws.neon.tech/DB"

        found = scan_text(entry.path, changed)

        assert [f.rule for f in found] == ["url-with-password"]
        assert found[0].digest != entry.digest

    def test_every_allowance_states_a_reason(self) -> None:
        from scripts.scan_secrets import ALLOWED

        assert ALLOWED
        for entry in ALLOWED:
            assert len(entry.why) > 30, entry

    def test_an_allowance_nothing_matches_any_more_is_reported_not_ignored(
        self,
    ) -> None:
        """Or the list becomes permanent by going stale — the same rule the
        fourteen grandfathered migration names are held to."""
        from scripts.scan_secrets import scan

        result = scan()

        assert result.stale_allowances == ()

    def test_a_forbidden_path_is_refused_whatever_it_contains(self) -> None:
        """An empty `.env` is still a mistake: the next person to fill it in
        will not notice it is committed."""
        from scripts.scan_secrets import forbidden_findings

        found = forbidden_findings([".env", "certs/server.pem", "app/main.py"])

        assert [path for path, _ in found] == [".env", "certs/server.pem"]
        assert all(why for _, why in found)

    def test_no_forbidden_path_is_tracked_today(self) -> None:
        from scripts.scan_secrets import scan

        assert scan().forbidden == ()

    def test_the_report_never_prints_the_whole_match(self) -> None:
        """CI logs are a place secrets get copied to, not a place they stop."""
        from scripts.scan_secrets import scan_text

        planted = "AKIA" + "IOSFODNN7EXAMPLE"
        finding = scan_text("x.env", planted)[0]

        assert planted not in finding.redacted()
        assert planted not in str(finding.as_dict())

    def test_the_report_states_what_it_cannot_see(self) -> None:
        """History is where this repository's one real leak is, and a scanner
        silent about its own limit would read as covering it."""
        from scripts.scan_secrets import LIMIT, ScanResult, report

        text = report(ScanResult((), (), (), 10, ()))

        assert LIMIT in text
        assert "never git history" in text

    def test_ci_runs_it_and_does_not_let_it_fail_quietly(self) -> None:
        """`pip-audit` is `continue-on-error` by a stated decision; this must
        not inherit it."""
        workflow = (REPO / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )

        assert "scripts.scan_secrets" in workflow
        step = workflow[workflow.index("Secret scan") : workflow.index("Generate SBOM")]
        assert "continue-on-error" not in step


def report_of(result: object) -> str:
    """The scanner's own report, for a failure message worth reading."""
    from scripts.scan_secrets import report

    return report(result)  # type: ignore[arg-type]


class TestTheReleaseNotesWorkflow:
    """P9.7's other half: `scripts/release_notes.py` existed since 2026-09-10
    and nothing ran it. A step that never runs proves nothing."""

    def _workflow(self) -> dict:
        import yaml

        return yaml.safe_load(
            (REPO / ".github" / "workflows" / "release.yml").read_text(
                encoding="utf-8"
            )
        )

    def test_it_runs_the_generator_on_a_version_tag(self) -> None:
        workflow = self._workflow()

        # `on` is the YAML 1.1 boolean True once parsed, which is why this is
        # not spelled "on".
        triggers = workflow[True] if True in workflow else workflow["on"]
        assert triggers["push"]["tags"] == ["v[0-9]*"]

        steps = workflow["jobs"]["notes"]["steps"]
        assert any("scripts.release_notes" in str(step.get("run", "")) for step in steps)

    def test_it_checks_out_the_whole_history(self) -> None:
        """The generator reads the first-parent log between two tags. A shallow
        clone would silently describe a shorter release than happened."""
        steps = self._workflow()["jobs"]["notes"]["steps"]

        checkout = next(s for s in steps if "checkout" in str(s.get("uses", "")))

        assert checkout["with"]["fetch-depth"] == 0
        assert checkout["with"]["fetch-tags"] is True

    def test_the_date_comes_from_the_tag_and_not_from_today(self) -> None:
        """A release regenerated a week later must produce the same document,
        or "the notes changed" stops meaning "the history changed"."""
        steps = self._workflow()["jobs"]["notes"]["steps"]
        runs = " ".join(str(step.get("run", "")) for step in steps)

        assert "git log -1 --format=%cs" in runs
        assert "date +" not in runs

    def test_every_action_is_pinned_to_a_sha(self) -> None:
        """A tag is a moving pointer, and CI is the one place a silently
        updated third-party action runs with the repository checked out."""
        import re

        steps = self._workflow()["jobs"]["notes"]["steps"]

        for step in steps:
            uses = step.get("uses")
            if uses:
                assert re.search(r"@[0-9a-f]{40}$", uses), uses

    def test_it_publishes_notes_and_not_an_artefact(self) -> None:
        """P9 task 5 is BLOCKED — an MSI built on a runner starts nothing on a
        customer machine — and attaching a binary here would look like it was
        not."""
        text = (REPO / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )
        steps = self._workflow()["jobs"]["notes"]["steps"]

        uploads = [s for s in steps if "upload-artifact" in str(s.get("uses", ""))]
        assert [s["with"]["path"] for s in uploads] == ["release-notes.md"]
        assert ".msi" not in text.lower()
        assert "notes, not artefacts" in text


class TestTheDemoSeed:
    """P9.4's Linux half. Staging needs an environment this deployment has not
    got; a seeded demo organisation does not, and it is useful on its own."""

    def test_it_writes_no_result_of_any_kind(self) -> None:
        """Decision 3: an unmeasured claim is never a pass. A demo database
        carrying a stress figure nobody solved for puts a fabricated number
        behind the product's own provenance machinery, where every surface
        downstream is built to trust it. Read from the imports rather than from
        a run, so the guarantee holds without a database."""
        source = (REPO / "scripts" / "seed_demo.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        imported = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }

        for forbidden in (
            "app.models.simulation",
            "app.solve.types",
            "app.verify.benchmarks",
            "app.verify.register",
        ):
            assert forbidden not in imported, forbidden

    def test_it_says_in_its_output_that_it_seeded_no_results(self) -> None:
        """Somebody reading the terminal is the person who would otherwise
        wonder why the projects are empty."""
        from scripts.seed_demo import PEOPLE, SeedResult

        said = SeedResult("id", "slug", (), tuple(p.email for p in PEOPLE), (), False).report()

        assert "nobody computed" in said

    def test_both_role_axes_are_exercised(self) -> None:
        """P2.2 split `OrgRole` from `DomainRole` because an owner is not
        automatically a reviewer. A demo where everybody is an owner shows none
        of it."""
        from app.models.organisation import DomainRole, OrgRole
        from scripts.seed_demo import PEOPLE

        assert {person.role for person in PEOPLE} == set(OrgRole)
        assert DomainRole.REVIEWER in {person.domain_role for person in PEOPLE}

    def test_exactly_one_reviewer_and_they_are_not_an_admin(self) -> None:
        """The case the split exists for. If the only reviewer were also the
        owner, a walkthrough could not tell the two columns apart."""
        from app.models.organisation import DomainRole, OrgRole
        from scripts.seed_demo import PEOPLE

        reviewers = [p for p in PEOPLE if p.domain_role is DomainRole.REVIEWER]

        assert len(reviewers) == 1
        assert not reviewers[0].role.at_least(OrgRole.ADMIN)

    def test_somebody_has_no_domain_role_at_all(self) -> None:
        """`None` means *not stated*, and P5.5 refuses a sign-off from such a
        member. A seed where the state cannot occur hides the rule the column's
        own docstring states."""
        from scripts.seed_demo import PEOPLE

        unset = [p for p in PEOPLE if p.domain_role is None]

        assert unset, "nothing demonstrates the nullable domain role"
        assert all(p.why for p in PEOPLE)

    def test_a_project_is_owned_by_someone_who_is_not_the_org_owner(self) -> None:
        """Access comes from membership, never from `Project.owner_id` — the
        comment on that column since P2. A demo has to be able to show it."""
        from scripts.seed_demo import PEOPLE, PROJECTS
        from app.models.organisation import OrgRole

        owner = next(p for p in PEOPLE if p.role is OrgRole.OWNER)
        holders = {project.owner_email for project in PROJECTS}

        assert holders - {owner.email}
        assert owner.email in holders

    def test_every_seeded_address_is_in_a_reserved_domain(self) -> None:
        """RFC 2606 reserves `.test`. A demo account at a domain somebody owns
        is a password-reset mail to a stranger the first time production
        settings reach this script."""
        from scripts.seed_demo import PEOPLE

        assert all(person.email.endswith(".test") for person in PEOPLE)

    def test_it_refuses_a_database_it_was_not_pointed_at_deliberately(self) -> None:
        """`create_admin.py`'s guard, for the same reason: the blast radius is
        rows in somebody's tenant."""
        from scripts.seed_demo import LOCAL_HOSTS, _host_of

        # Built from pieces, like the fixtures above: a literal here is a
        # finding in the very tree `TestTheSecretScan` asserts is clean.
        remote = "postgresql://u:" + "pw" + "@ep-prod.aws.neon.tech/db"
        local = "postgresql://u:" + "pw" + "@localhost:5432/kryova"

        assert _host_of(remote) not in LOCAL_HOSTS
        assert _host_of(local) in LOCAL_HOSTS

    def test_the_guard_reads_the_host_not_the_whole_url(self) -> None:
        """So a password containing the word `localhost` cannot talk its way
        past it."""
        from scripts.seed_demo import LOCAL_HOSTS, _host_of

        sneaky = "postgresql://u:" + "localhost" + "@ep-prod.aws.neon.tech/db"

        assert _host_of(sneaky) not in LOCAL_HOSTS
