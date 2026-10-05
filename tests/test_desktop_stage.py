"""What goes into the installer's backend, and what must not (ROAD_TO_10 4.2).

`scripts/stage_desktop_backend.py` builds `<bundle>/backend/` from an allow-list and a hashed
lock. Four things here would each ship quietly and each is the kind that costs a release:

* **A credential file in a signed installer.** `.env.local` holds a live model API key.
* **A lock that has drifted from `requirements.txt`**, so the installer carries a different
  numpy than the suite was tested with.
* **`torch` in the installer.** No local model, decided 2026-10-04; it is also ~2 GB.
* **A bundle that is not what the shell looks for** -- covered from the shell's side in
  `Kryova-frontend/src/lib/desktop-stage.test.ts`.

What this cannot show -- that the *wheels* run on Windows, that the tree installs on a clean
machine, how long the MSI takes to build -- is THE QUEUE G7. The staging below runs with
`wheels=False`, which says so in the manifest rather than leaving it to be assumed.

No test requests a database fixture.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from scripts import stage_desktop_backend as stage

BACKEND = Path(__file__).resolve().parents[1]


def _direct_pins() -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in (BACKEND / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        match = re.match(r"^([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?==([^\s;]+)", line)
        if match:
            pins[stage.normalise(match.group(1))] = match.group(2)
    return pins


class TestTheLockIsTheRequirementsMadeExact:
    lock = stage.lock_pins()

    def test_every_direct_pin_is_the_version_the_lock_carries(self) -> None:
        drifted = {
            name: (wanted, self.lock.get(name))
            for name, wanted in _direct_pins().items()
            if self.lock.get(name) != wanted
        }

        assert drifted == {}, (
            "requirements.txt and requirements-desktop.lock.txt disagree, so the installer "
            "would carry a different build than the suite ran against. Regenerate the lock "
            f"(command in its header): {drifted}"
        )

    def test_the_one_range_in_requirements_is_satisfied(self) -> None:
        # `redis>=5.0,<9.0` is the only non-pin; the lock must land inside it.
        major = int(self.lock["redis"].split(".")[0])

        assert 5 <= major < 9

    def test_no_local_model_gets_in_through_the_lock(self) -> None:
        # requirements-laya.txt: no local LLM, decided 2026-10-04, and torch alone is ~2 GB.
        laya = {
            stage.normalise(re.split(r"[=<>\[ ;]", line.strip())[0])
            for line in (BACKEND / "requirements-laya.txt").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith(("#", "-"))
        }

        assert laya >= {"torch"}
        assert laya & set(self.lock) == set()

    def test_every_pin_is_hashed_so_a_swapped_wheel_fails_to_install(self) -> None:
        text = (BACKEND / "requirements-desktop.lock.txt").read_text(encoding="utf-8")
        blocks = re.split(r"(?m)^(?=[A-Za-z0-9_.\-]+(?:\[[^\]]*\])?==)", text)[1:]

        assert len(blocks) == len(self.lock) > 50
        unhashed = [block.split("==")[0] for block in blocks if "--hash=sha256:" not in block]
        assert unhashed == []

    def test_it_was_resolved_for_the_interpreter_the_bundle_ships(self) -> None:
        header = (BACKEND / "requirements-desktop.lock.txt").read_text(encoding="utf-8")[:1200]

        assert f"--python-version {stage.PYTHON_VERSION}" in header
        assert "--python-platform windows" in header

    def test_the_windows_com_binding_is_in_it(self) -> None:
        # The marker keeps Linux installs working; the Windows installer is the one that needs it.
        assert "pywin32" in self.lock

    def test_name_normalisation_is_pep_503(self) -> None:
        assert stage.normalise("Typing_Extensions") == "typing-extensions"
        assert stage.normalise("zope.interface") == "zope-interface"
        assert stage.normalise("a--b__c") == "a-b-c"


class TestOnlyWhatIsNamedShips:
    def source(self, tmp_path: Path) -> Path:
        """A miniature checkout with the files that must not travel next to the ones that must."""
        root = tmp_path / "checkout"
        files = {
            "app/main.py": "",
            "app/desktop.py": "",
            "app/__pycache__/main.cpython-312.pyc": "",
            "app/core/stale.pyc": "",
            "migrations/env.py": "",
            "migrations/versions/aaaa_first.py": "",
            "alembic.ini": "",
            "scripts/catia_bridge/__main__.py": "",
            "scripts/create_admin.py": "",
            "data/verify/benchmark-outcomes.json": "{}",
            "data/bm25/manual.pdf": "x" * 100,
            "LICENSE": "",
            ".env": "AI_API_KEY=" + "sk-" + "not-a-real-key",
            ".env.local": "AI_API_KEY=" + "sk-" + "not-a-real-key",
            "tests/test_everything.py": "",
            "venv/pyvenv.cfg": "",
            "media_data/blob": "",
            "requirements-desktop.lock.txt": "pkg==1.0 \\\n    --hash=sha256:" + "a" * 64 + "\n",
        }
        for relative, body in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        return root

    def staged(self, tmp_path: Path) -> tuple[Path, dict[str, object]]:
        source = self.source(tmp_path)
        out = tmp_path / "bundle" / "backend"
        manifest = stage.stage(
            out,
            lock=source / "requirements-desktop.lock.txt",
            wheels=False,
            source=source,
            native=False,
        )
        return out, manifest

    def names(self, root: Path) -> set[str]:
        return {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}

    def test_the_credential_files_do_not_travel(self, tmp_path: Path) -> None:
        out, _ = self.staged(tmp_path)

        assert [name for name in self.names(out) if Path(name).name.startswith(".env")] == []

    def test_neither_do_the_tests_the_venv_the_media_or_the_manuals(self, tmp_path: Path) -> None:
        out, _ = self.staged(tmp_path)

        leaked = [
            name
            for name in self.names(out)
            if name.startswith(("tests/", "venv/", "media_data/", "data/bm25/"))
        ]
        assert leaked == []

    def test_bytecode_is_left_behind_and_the_sources_are_not(self, tmp_path: Path) -> None:
        out, _ = self.staged(tmp_path)
        names = self.names(out)

        assert "app/main.py" in names and "app/desktop.py" in names
        assert not any(name.endswith((".pyc", ".pyo")) or "__pycache__" in name for name in names)

    def test_the_bridge_daemon_and_the_migrations_and_the_trust_artefact_do_travel(
        self, tmp_path: Path
    ) -> None:
        out, _ = self.staged(tmp_path)
        names = self.names(out)

        assert "scripts/catia_bridge/__main__.py" in names
        assert "migrations/versions/aaaa_first.py" in names
        assert "alembic.ini" in names
        assert "data/verify/benchmark-outcomes.json" in names

    def test_the_admin_provisioning_script_does_not_travel(self, tmp_path: Path) -> None:
        # Only `scripts/catia_bridge` is named. `create_admin.py` mints a platform_admin.
        out, _ = self.staged(tmp_path)

        assert "scripts/create_admin.py" not in self.names(out)

    def test_the_manifest_says_what_was_and_was_not_done(self, tmp_path: Path) -> None:
        out, manifest = self.staged(tmp_path)
        lock = tmp_path / "checkout" / "requirements-desktop.lock.txt"

        assert manifest["bytecode"] == "absent"  # wheels were skipped, so nothing was compiled
        assert manifest["wheels"] == 0
        assert manifest["python"] == stage.PYTHON_VERSION
        assert manifest["lock_sha256"] == hashlib.sha256(lock.read_bytes()).hexdigest()
        assert json.loads((out / "bundle-manifest.json").read_text(encoding="utf-8")) == manifest

    def test_a_file_deleted_from_the_checkout_is_not_shipped_from_last_time(
        self, tmp_path: Path
    ) -> None:
        source = self.source(tmp_path)
        out = tmp_path / "bundle" / "backend"
        lock = source / "requirements-desktop.lock.txt"
        stage.stage(out, lock=lock, wheels=False, source=source, native=False)
        (source / "app" / "core" / "stale.pyc").unlink()
        (source / "app" / "main.py").write_text("")
        (source / "app" / "gone.py").write_text("")
        stage.stage(out, lock=lock, wheels=False, source=source, native=False)
        (source / "app" / "gone.py").unlink()

        stage.stage(out, lock=lock, wheels=False, source=source, native=False)

        assert "app/gone.py" not in self.names(out)

    def test_a_name_on_the_list_that_does_not_exist_is_a_failure_not_a_gap(
        self, tmp_path: Path
    ) -> None:
        source = self.source(tmp_path)

        with pytest.raises(SystemExit, match="does not exist"):
            stage.stage(
                tmp_path / "out",
                lock=source / "requirements-desktop.lock.txt",
                wheels=False,
                source=source,
                include=("app", "no-such-file.txt"),
                native=False,
            )

    def test_a_tree_without_its_entry_point_is_refused(self, tmp_path: Path) -> None:
        source = self.source(tmp_path)
        (source / "app" / "desktop.py").unlink()

        with pytest.raises(SystemExit, match="missing app/desktop.py"):
            stage.stage(
                tmp_path / "out",
                lock=source / "requirements-desktop.lock.txt",
                wheels=False,
                source=source,
                native=False,
            )


class TestTheFinalCheckCatchesWhatTheListMissed:
    """`bundle_problems` is the second wall: it reads the result, not the intent."""

    def tree(self, tmp_path: Path, *extra: str) -> Path:
        root = tmp_path / "backend"
        for name in (*stage.REQUIRED, *extra):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("")
        return root

    def test_a_sound_tree_has_no_problems(self, tmp_path: Path) -> None:
        assert stage.bundle_problems(self.tree(tmp_path)) == []

    @pytest.mark.parametrize(
        "leak",
        [
            ".env",
            ".env.local",
            ".env.production",
            "tests/test_x.py",
            "venv/pyvenv.cfg",
            "data/bm25/m.pdf",
        ],
    )
    def test_each_forbidden_thing_is_named_wherever_it_hides(
        self, tmp_path: Path, leak: str
    ) -> None:
        problems = stage.bundle_problems(self.tree(tmp_path, leak))

        assert any(leak.split("/")[0] in problem for problem in problems), problems

    def test_a_nested_env_file_is_found_too(self, tmp_path: Path) -> None:
        problems = stage.bundle_problems(self.tree(tmp_path, "app/deep/er/.env"))

        assert problems == ["must not ship: app/deep/er/.env"]

    def test_the_interpreters_own_tests_directories_are_not_mistaken_for_ours(
        self, tmp_path: Path
    ) -> None:
        # Every wheel ships a `tests/`. A scan that flagged them would fail every real
        # bundle and be turned off by the first person it blocked.
        root = self.tree(tmp_path, "python/Lib/site-packages/numpy/tests/test_x.py")

        assert stage.bundle_problems(root) == []

    def test_a_dotfile_inside_a_hashed_wheel_is_not_ours_to_refuse(self, tmp_path: Path) -> None:
        # Third-party content, pinned by hash. The credential rule is about what *this repository*
        # put in the tree; a wheel that ships an example `.env` would otherwise fail every build.
        root = self.tree(tmp_path, "python/Lib/site-packages/somepackage/.env")

        assert stage.bundle_problems(root) == []

    def test_a_missing_required_file_is_named(self, tmp_path: Path) -> None:
        root = self.tree(tmp_path)
        (root / "app" / "desktop.py").unlink()

        assert stage.bundle_problems(root) == ["missing app/desktop.py"]


class TestTheRealCheckout:
    def test_everything_on_the_include_list_exists(self) -> None:
        missing = [name for name in stage.INCLUDE if not (BACKEND / name).exists()]

        assert missing == []

    def test_the_real_tree_stages_clean_and_small(self, tmp_path: Path) -> None:
        out = tmp_path / "backend"

        manifest = stage.stage(out, wheels=False)

        assert stage.bundle_problems(out) == []
        assert manifest["app_files"] > 400
        total = sum(path.stat().st_size for path in out.rglob("*") if path.is_file())
        # Source and migrations only. The 450 MB of manuals are not here, and a tree that
        # grew past this has had something large added to the allow-list.
        assert total < 25 * 1024 * 1024, f"{total / 1e6:.1f} MB"
        assert not any(path.name.startswith(".env") for path in out.rglob("*"))
