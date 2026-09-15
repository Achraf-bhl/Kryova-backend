"""QIF's state as read, and the vendoring rule the reading settles (E21.6).

Offline. **Written on Linux on 2026-09-15 and not run there as pytest** (the user's rule). On that
date `git ls-files` listed no `.xsd` file, so the vendoring test passes vacuously today and exists
for the day a schema is added.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from app.manufacture import qif

ROOT = Path(__file__).resolve().parents[1]


def _tracked(suffix: str) -> list[str]:
    listed = subprocess.run(
        ["git", "ls-files", f"*{suffix}"], cwd=ROOT, capture_output=True, text=True, check=True
    )
    return [line for line in listed.stdout.splitlines() if line]


def test_a_schema_is_vendored_only_beside_its_licence() -> None:
    for path in _tracked(".xsd") + _tracked(".xsdOrig"):
        assert path.startswith(qif.VENDOR_DIRECTORY + "/"), (
            f"{path} is a schema outside {qif.VENDOR_DIRECTORY}/; QIF's schemas carry licence terms"
        )
        directory = (ROOT / path).parent
        licences = [
            parent / qif.VENDOR_LICENCE_FILE
            for parent in (directory, *directory.parents)
            if parent.is_relative_to(ROOT / qif.VENDOR_DIRECTORY)
        ]
        assert any(licence.is_file() for licence in licences), f"{path} has no {qif.VENDOR_LICENCE_FILE}"


def test_every_reading_names_a_source_and_a_date() -> None:
    assert qif.READINGS
    for reading in qif.READINGS:
        assert reading.source.startswith("https://")
        assert reading.read_on == "2026-09-15"
        assert reading.fact.endswith(".")


def test_the_version_facts_agree_with_each_other() -> None:
    assert qif.CURRENT_VERSION == "QIF 3.0"
    assert qif.NAMESPACE.endswith("/qif3")
    assert qif.SCHEMA_VERSION.startswith("3.0")
    assert any(qif.ISO_NUMBER in reading.fact for reading in qif.READINGS)


def test_nothing_in_the_product_claims_to_write_qif_yet() -> None:
    assert any("No QIF writer" in item for item in qif.OPEN)
    writers = [
        path
        for path in (ROOT / "app").rglob("*.py")
        if path.name != "qif.py" and "qifstandards.org/xsd" in path.read_text(encoding="utf-8")
    ]
    assert writers == [], "a module now uses the QIF namespace; close OPEN's first item with it"
