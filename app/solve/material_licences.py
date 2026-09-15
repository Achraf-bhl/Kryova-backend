"""Material data under licence: what was read, and how a customer's allowables get in (E21.4).

`materials.Source.right` says under what right each number is shown. This module holds the two
things that decide that right for data Kryova does not own:

1. **What the commercial sources' terms say**, as facts with where each was read. MatWeb's
   clauses live beside the source that cites it, in `app/solve/materials.py`. MMPDS's are here.
2. **The only way a customer's licensed allowables enter a deployment**: `customer_records`.
   It reads a file the customer keeps in their own deployment. Every property it produces is
   `Right.CUSTOMER_LICENCE` with the licensee named, and a customer file cannot claim any other
   right, because the loader sets it. Kryova's repository never holds such a file.

What was read on 2026-09-15:

* **MatWeb's License Agreement**, through the Wayback Machine's 2026-02-03 capture (matweb.com
  answered 403). It forbids redistribution and making the content available to anyone else,
  caps a personal database at 500 materials, and has the user agree not to rely on the content
  "in making structural or engineering decisions and calculations". It also forbids using the
  content for AI/ML training, evaluation or embeddings. **Two of Kryova's eight shipped
  materials, `steel-1018` and `stainless-304`, cite MatWeb.** That is recorded on the source
  rather than silently removed, because replacing the numbers needs a source whose terms allow
  it, and none has been read.
* **MMPDS-2026 Volume I** on the Accuris store (search snippet of the product page): $1,049.00,
  2,784 pages, published 07/01/2026. The page offers a "Secured PDF" with FileOpen DRM "by
  request of the Publisher", a multi-user PDF that is "a finite set of single user licenses",
  and lists restricted countries. No redistribution, site or API licence is offered there.

Not read: MatWeb's database-licensing terms (`/services/databaselicense.aspx`), MMPDS's own
licence text, Granta's terms, and the 2025 MMPDS price the plan's summary gives.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from app.solve.materials import (
    RECORDS,
    Condition,
    MaterialRecord,
    Right,
    Source,
    SourceKind,
    Status,
    transcribe,
)


@dataclass(frozen=True)
class Reading:
    fact: str
    source: str
    read_on: str = "2026-09-15"


MMPDS_2026_VOLUME_I: Final = "https://store.accuristech.com/standards/bmi-mmpds-2026-volume-i?product_id=3123680"

READINGS: Final[tuple[Reading, ...]] = (
    Reading(
        "MatWeb's licence forbids selling, redistributing or making its content available to "
        "anyone else, and relying on it for structural or engineering decisions and calculations.",
        "https://web.archive.org/web/20260203071848/https://www.matweb.com/reference/terms.aspx",
    ),
    Reading(
        "MatWeb's licence caps a personal database built from its content at 500 materials.",
        "https://web.archive.org/web/20260203071848/https://www.matweb.com/reference/terms.aspx",
    ),
    Reading(
        "MatWeb's licence forbids using its content to train, evaluate or benchmark an AI/ML "
        "system, or to generate embeddings.",
        "https://web.archive.org/web/20260203071848/https://www.matweb.com/reference/terms.aspx",
    ),
    Reading("MMPDS-2026 Volume I is $1,049.00, 2,784 pages, published 07/01/2026.", MMPDS_2026_VOLUME_I),
    Reading(
        "MMPDS-2026 Volume I is sold as a DRM-secured PDF, or as a finite set of single-user licences.",
        MMPDS_2026_VOLUME_I,
    ),
)


class CustomerDataError(ValueError):
    """A customer materials file that cannot be loaded, with what to change."""


def customer_records(document: Mapping[str, Any]) -> dict[str, MaterialRecord]:
    """The materials in a customer's licensed-data file, each shown under their licence.

    The file names the `licensee` and the `licence`, then lists materials. Every property is
    transcribed through `materials.transcribe`, so a value in ksi is converted once, here, as
    every other material is. The loader sets `Right.CUSTOMER_LICENCE` on every source; a file
    cannot choose a right. A slug that a shipped material already uses is refused, so a
    customer's number can never be mistaken for Kryova's.
    """
    licensee = str(document.get("licensee") or "").strip()
    licence = str(document.get("licence") or "").strip()
    if not licensee or not licence:
        raise CustomerDataError(
            "A licensed materials file names the `licensee` and the `licence` it is held under "
            "(e.g. 'MMPDS-2026 Volume I, single-user PDF'), so every number shows whose right it is."
        )
    materials = document.get("materials")
    if not isinstance(materials, list) or not materials:
        raise CustomerDataError("A licensed materials file lists at least one material under `materials`.")

    records: dict[str, MaterialRecord] = {}
    for entry in materials:
        slug = str(entry.get("slug") or "").strip()
        if not slug:
            raise CustomerDataError("Every material in a licensed materials file has a `slug`.")
        if slug in RECORDS:
            raise CustomerDataError(
                f"{slug!r} is a material Kryova ships. Give the licensed record its own slug "
                f"(e.g. {slug + '-' + licensee.lower().replace(' ', '-')!r}), so the two are never confused."
            )
        if slug in records:
            raise CustomerDataError(f"{slug!r} appears twice in the licensed materials file.")
        properties = []
        for prop in entry.get("properties") or []:
            citation = str(prop.get("citation") or "").strip()
            if not citation:
                raise CustomerDataError(
                    f"{slug}: every property cites where in the licensed document it was read "
                    "(e.g. 'MMPDS-2026 Table 3.2.3.0(b)')."
                )
            source = Source(
                citation=citation,
                kind=SourceKind(prop.get("kind", SourceKind.STANDARD)),
                right=Right.CUSTOMER_LICENCE,
                licensee=licensee,
                terms=licence,
            )
            properties.append(
                transcribe(
                    str(prop["name"]),
                    float(prop["value"]),
                    str(prop["unit"]),
                    status=Status(prop["status"]),
                    source=source,
                    note=str(prop.get("note") or ""),
                )
            )
        if not properties:
            raise CustomerDataError(f"{slug}: a licensed material carries at least one property.")
        records[slug] = MaterialRecord(
            slug=slug,
            display_name=str(entry.get("display_name") or slug),
            category=str(entry.get("category") or ""),
            condition=Condition(**(entry.get("condition") or {})),
            properties={p.name: p for p in properties},
        )
    return records


def load_customer_records(path: str | Path) -> dict[str, MaterialRecord]:
    """`customer_records` over a JSON file kept in the customer's own deployment."""
    source = Path(path)
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CustomerDataError(f"Could not read the licensed materials file {source}: {error}") from error
    if not isinstance(document, Mapping):
        raise CustomerDataError(f"{source} holds a JSON object with `licensee`, `licence` and `materials`.")
    return customer_records(document)


__all__ = [
    "MMPDS_2026_VOLUME_I",
    "READINGS",
    "CustomerDataError",
    "Reading",
    "customer_records",
    "load_customer_records",
]
