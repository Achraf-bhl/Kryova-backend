"""Repository-level invariants that no other test would notice breaking.

`.env` was committed once, with live Neon credentials in it. The history is
public, so those have to be rotated -- but the code can at least make the same
mistake harder to repeat.
"""

import re
from pathlib import Path

import pytest

from app.core.config import BASE_DIR, Settings

GITIGNORE = (BASE_DIR / ".gitignore").read_text(encoding="utf-8")
ENV_EXAMPLE = BASE_DIR / ".env.example"


class TestSecretsHygiene:
    def test_env_is_ignored(self) -> None:
        assert any(line.strip() == ".env" for line in GITIGNORE.splitlines())

    def test_nothing_claims_env_is_deliberately_tracked(self) -> None:
        # The contradictory comment that made the last leak look intentional.
        assert "intentionally tracked" not in GITIGNORE

    def test_the_real_env_file_is_not_the_example(self) -> None:
        real = BASE_DIR / ".env"
        if real.exists():
            assert real.read_text(encoding="utf-8") != ENV_EXAMPLE.read_text(encoding="utf-8")

    def test_the_example_carries_no_real_credential(self) -> None:
        text = ENV_EXAMPLE.read_text(encoding="utf-8")
        assert "neon.tech/neondb" not in text
        assert not re.search(r"\bsk-[A-Za-z0-9_-]{16,}", text), "an API key is in .env.example"
        assert "SECRET_KEY=changeme" in text


class TestEnvExampleDocumentsEverySetting:
    """A setting nobody can discover is a setting nobody sets correctly."""

    # Derived, not configured: no environment variable feeds them.
    NOT_ENVIRONMENT_DRIVEN = {"project_name", "api_v1_prefix", "media_staging_dir"}
    # Read by the test suite rather than by Settings, so it has no field.
    EXTRA_DOCUMENTED = {"TEST_DATABASE_URL"}

    def test_every_settings_field_appears(self) -> None:
        text = ENV_EXAMPLE.read_text(encoding="utf-8")
        missing = [
            name
            for name in Settings.model_fields
            if name not in self.NOT_ENVIRONMENT_DRIVEN
            and not re.search(rf"^#?\s*{name.upper()}=", text, re.MULTILINE)
        ]
        assert not missing, f"undocumented in .env.example: {sorted(missing)}"

    def test_it_names_no_setting_that_no_longer_exists(self) -> None:
        known = {name.upper() for name in Settings.model_fields} | self.EXTRA_DOCUMENTED
        declared = set(
            re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", ENV_EXAMPLE.read_text(encoding="utf-8"), re.M)
        )
        assert not declared - known, f"stale keys in .env.example: {sorted(declared - known)}"


class TestContinuousIntegration:
    WORKFLOW = BASE_DIR / ".github" / "workflows" / "ci.yml"

    @pytest.fixture
    def workflow(self) -> str:
        return self.WORKFLOW.read_text(encoding="utf-8")

    def test_there_is_no_frontend_job(self, workflow: str) -> None:
        # There is no package.json in this repo; the job could only ever fail.
        assert "npm" not in workflow

    def test_all_three_gates_run(self, workflow: str) -> None:
        assert "ruff check ." in workflow
        assert "mypy app tests" in workflow
        assert "pytest -q" in workflow

    def test_ci_does_not_point_the_suite_at_a_real_database(self, workflow: str) -> None:
        # TEST_DATABASE_URL stays unset so conftest builds SQLite, and
        # DATABASE_URL is a placeholder nothing connects to.
        env_lines = [line for line in workflow.splitlines() if not line.lstrip().startswith("#")]
        assigned = "\n".join(env_lines)
        assert "TEST_DATABASE_URL:" not in assigned
        assert "secrets.DATABASE_URL" not in assigned


class TestMigrationChain:
    VERSIONS = BASE_DIR / "migrations" / "versions"

    @staticmethod
    def _chain() -> dict[str, str | None]:
        chain: dict[str, str | None] = {}
        for path in TestMigrationChain.VERSIONS.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            revision = re.search(r"^revision: str = ['\"]([^'\"]+)", text, re.M)
            down = re.search(r"^down_revision[^=]*=(.+)$", text, re.M)
            assert revision and down, f"{path.name} is not a migration"
            quoted = re.search(r"['\"]([^'\"]+)", down.group(1))
            chain[revision.group(1)] = quoted.group(1) if quoted else None
        return chain

    def test_there_is_exactly_one_head(self) -> None:
        """Two heads make `alembic upgrade head` fail outright, and two agents
        branching from the same revision is exactly how that happens."""
        chain = self._chain()
        parents = {parent for parent in chain.values() if parent}
        heads = sorted(set(chain) - parents)
        assert len(heads) == 1, f"multiple migration heads: {heads}"

    def test_every_parent_exists(self) -> None:
        chain = self._chain()
        dangling = {
            revision: parent for revision, parent in chain.items() if parent and parent not in chain
        }
        assert not dangling, f"migrations point at a missing parent: {dangling}"

    def test_migrations_live_only_in_the_active_directory(self) -> None:
        legacy = BASE_DIR / "alembic" / "versions"
        assert not legacy.exists() or not list(legacy.glob("*.py"))


def test_no_source_file_imports_celery() -> None:
    """The Celery backend was fiction: it dispatched nothing and ran the job
    inline instead. Nothing should reference it again without implementing it."""
    offenders = [
        path
        for path in (BASE_DIR / "app").rglob("*.py")
        if re.search(
            r"^\s*(import|from)\s+celery\b", path.read_text(encoding="utf-8"), re.MULTILINE
        )
    ]
    assert offenders == []


def test_there_is_no_async_engine() -> None:
    """`create_async_engine` needed asyncpg, which was never a dependency, so
    importing the app crashed outright. Nothing in this codebase is async-DB."""
    source = Path(BASE_DIR / "app" / "core" / "database.py").read_text(encoding="utf-8")
    assert "create_async_engine" not in source
    assert "asyncpg" not in source
