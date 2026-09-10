"""The delivery substrate: image, SBOM, restore drill, rollback notes (P9 3/6/7).

None of this can be *fully* proved on a laptop — an image nobody has built and a
restore nobody has run are not artefacts. What can be proved is that each piece
refuses the thing it exists to refuse: a health check that passes with no
solver, a licence scan that passes by finding nothing, a drill that would
happily restore over production, and a migration with no rollback note.
"""

from __future__ import annotations

import ast
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
