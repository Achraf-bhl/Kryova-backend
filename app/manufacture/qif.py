"""QIF, read as it is on 2026-09-15 -- master plan E21.6, for E17.5's inspection plans.

`inspection.py` builds an inspection plan (features, probe points, steps). The metrology world
reads plans as QIF, and E21.6 found its state had been summarised rather than read: a download page
advertising a 2018 version, no licence on the page, and a pointer to a community GitHub. This
module is that reading, as facts with where each was read, plus the one rule the reading settles:
**what may be vendored and on what terms**. Nothing here writes QIF yet.

What was read, all on 2026-09-15:

* `https://qifstandards.org/download` -- "Latest version: ANSI QIF 3.0 released December 2018",
  free behind a form. The page states no licence or redistribution terms.
* `https://qifstandards.org` -- the same version, "ANSI QIF 3.0 | ISO 23952:2020". The ISO
  catalogue page itself could not be fetched, so ISO's current status of 23952 is not recorded.
* `https://github.com/QualityInformationFramework/qif-community` (last commit 2026-03-20), linked
  from DMSC's own "Get Started" page. `LICENSE.md` is the **Boost Software License 1.0**, with an
  exception for the CodeSynthesis C++ bindings, which fall under CodeSynthesis's own licence.
  The exception names paths (`...\\QIF_30\\QIF_30\\QIFApplications`) that do not match the tree as
  it stands (`bindings/CPP-CodeSynthesis/QIF_30.CodeSynthesis/...`).
* The QIF 3.0 schemas in that repository under `bindings/CPP-Kramer/schema/` are **modified**
  copies: each `.xsd` sits beside a `.xsdOrig`, and `QIFDocument.xsd` differs from its
  `.xsdOrig` in 1,272 diff lines, including key selectors (`.//*` became `.//t:*`). So they are a
  binding generator's schemas, and validating against them is not validating against QIF 3.0 as
  published. Each schema's header says it "is part of QIF 3.0, an open, industry-wide standard"
  and "shall not be used in any manner to claim any proprietary rights to such information".
* The repository is also where `pyxb`-based Python bindings live; PyXB is not a dependency here.

The rule that follows, enforced by `tests/test_manufacture_qif.py`: **a schema enters this
repository only under `data/qif/`, beside the licence it came with**. Nothing is vendored today.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class Reading:
    fact: str
    source: str
    read_on: str = "2026-09-15"


CURRENT_VERSION: Final = "QIF 3.0"
ISO_NUMBER: Final = "ISO 23952:2020"
#: The target namespace QIF 3.0's schemas declare (read from `Characteristics.xsd`'s header).
NAMESPACE: Final = "http://qifstandards.org/xsd/qif3"
SCHEMA_VERSION: Final = "3.0.0"

COMMUNITY_REPOSITORY: Final = "https://github.com/QualityInformationFramework/qif-community"
COMMUNITY_LICENCE: Final = "Boost Software License 1.0"

#: Where a vendored schema must sit, and the file that must sit beside it.
VENDOR_DIRECTORY: Final = "data/qif"
VENDOR_LICENCE_FILE: Final = "LICENSE.md"

READINGS: Final[tuple[Reading, ...]] = (
    Reading(
        "The latest version is ANSI QIF 3.0, released December 2018, a free download behind a form.",
        "https://qifstandards.org/download",
    ),
    Reading("The download page states no licence or redistribution terms.", "https://qifstandards.org/download"),
    Reading("QIF 3.0 is published by ISO as ISO 23952:2020.", "https://qifstandards.org"),
    Reading(
        "The community repository is licensed Boost 1.0, except the CodeSynthesis C++ bindings.",
        f"{COMMUNITY_REPOSITORY}/blob/HEAD/LICENSE.md",
    ),
    Reading(
        "The schemas under bindings/CPP-Kramer/schema are modified from the .xsdOrig beside them.",
        f"{COMMUNITY_REPOSITORY}/tree/HEAD/bindings/CPP-Kramer/schema",
    ),
)

#: What is still open, in the plan's words rather than a guess.
OPEN: Final[tuple[str, ...]] = (
    "No QIF writer: E17.5's plans are not emitted as QIF.",
    "No schema validator is installed (neither lxml nor xmlschema), so a writer could not be "
    "checked against a schema here; choosing one is a dependency decision.",
    "The unmodified QIF 3.0 schemas come from the form-gated DMSC download, whose terms are not "
    "stated; the community copies carry Boost 1.0 and are modified.",
    "ISO's current status of ISO 23952 was not read (iso.org refused the fetch).",
    "MBC and DMIS, the other two documents the plan names, were not read.",
)


__all__ = [
    "COMMUNITY_LICENCE",
    "COMMUNITY_REPOSITORY",
    "CURRENT_VERSION",
    "ISO_NUMBER",
    "NAMESPACE",
    "OPEN",
    "READINGS",
    "Reading",
    "SCHEMA_VERSION",
    "VENDOR_DIRECTORY",
    "VENDOR_LICENCE_FILE",
]
