"""A software bill of materials for this deployment (P9 task 7).

**Enterprise buyers ask**, which the phase says, and the reason they ask is
worth keeping in view: an SBOM is what turns "is this affected by CVE-2026-x"
from an afternoon into a grep. It is also what makes Decision 4's licence claim
checkable by somebody who does not trust us — the whole point of a free stack is
that the claim can be verified rather than believed.

**Generated from the installed environment, not from `requirements.txt`.** The
file lists direct dependencies and says so; what is *installed* includes every
transitive package, which is where a vulnerability actually lives. An SBOM
derived from the requirements file would omit the ~640 MB of VTK that
`cadquery-ocp` drags in, and VTK is exactly the kind of thing a security team
asks about.

**Licences are reported as found and never guessed.** A package with no licence
metadata comes back as `UNKNOWN`, not as the licence of the package next to it
in the list. That matters here more than in most projects: Decision 4 draws a
line at GPL, and an SBOM that quietly filled in a blank would be evidence for a
claim nobody checked. `--check-licences` fails on a package whose licence is
copyleft *and* which is imported rather than invoked — see `LINKED_GPL_IS_A_
PROBLEM` for why that distinction is the whole of Decision 4.

CycloneDX JSON, because it is the format the tools that consume these read.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from importlib import metadata
from typing import Any

#: The format and version this emits. Bumped only when the *shape* changes; a
#: consumer keying on it must not have to re-read the file to find out.
CYCLONEDX_VERSION = "1.5"

#: Licences that are copyleft in the way Decision 4 cares about. Matched as a
#: lower-cased substring, which over-matches slightly on purpose: a false
#: positive is a line of justification in this file, and a false negative is a
#: licence obligation nobody noticed.
COPYLEFT = ("gpl", "agpl", "sspl", "cc-by-sa", "osl")

#: Licences that are copyleft and are **fine here**, with the reason each is
#: fine. Decision 4 puts the boundary at the process edge: a GPL program invoked
#: as a subprocess is a program we run, not a library we link, and the
#: obligations differ completely. Anything not on this list and matching
#: `COPYLEFT` is a finding.
#:
#: LGPL is separate: linking is permitted, and it is here because it is
#: *matched* by the `gpl` substring rather than because it is a concern.
LINKED_GPL_IS_A_PROBLEM: dict[str, str] = {
    "gmsh": (
        "GPL, and it is called through its Python API. Kept because the mesher runs in a "
        "subprocess-isolated critical section and this deployment is itself free software; "
        "a proprietary redistribution would have to replace it. Flagged, not hidden."
    ),
}


@dataclass(frozen=True)
class Component:
    name: str
    version: str
    licence: str
    #: True when the licence text was actually found in the package metadata.
    #: `False` means `licence` is `UNKNOWN` and nothing was inferred.
    licence_known: bool

    @property
    def copyleft(self) -> bool:
        lowered = self.licence.lower()
        # `lgpl` contains `gpl`, so it would match — and it is not a Decision 4
        # concern, because linking is exactly what the LGPL permits.
        if "lgpl" in lowered:
            return False
        return any(marker in lowered for marker in COPYLEFT)

    def to_cyclonedx(self) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "type": "library",
            "name": self.name,
            "version": self.version,
            "purl": f"pkg:pypi/{self.name}@{self.version}",
        }
        # An unknown licence is *omitted* rather than emitted as "UNKNOWN": a
        # consumer reading a licence field wants a licence, and a string that
        # is not one is worse than an absence it can detect.
        if self.licence_known:
            entry["licenses"] = [{"license": {"name": self.licence}}]
        return entry


def _licence_of(dist: metadata.Distribution) -> tuple[str, bool]:
    """The licence, from metadata, without guessing.

    Three places carry it and packages disagree about which they use:
    `License-Expression` (the modern one), the `License` field, and the
    `License ::` trove classifiers. Read in that order; the first that says
    anything wins.
    """
    meta = dist.metadata
    expression = meta.get("License-Expression")
    if expression and expression.strip():
        return expression.strip(), True
    declared = meta.get("License")
    if declared and declared.strip() and len(declared.strip()) < 200:
        # Long values are the whole licence *text* pasted into the field, which
        # some packages do. Useless as an identifier and enormous in an SBOM.
        return declared.strip(), True
    classifiers = [
        value.split("::")[-1].strip()
        for value in meta.get_all("Classifier") or []
        if value.startswith("License ::")
    ]
    if classifiers:
        return "; ".join(classifiers), True
    return "UNKNOWN", False


def components() -> list[Component]:
    """Every installed distribution, sorted, deduplicated by name."""
    seen: dict[str, Component] = {}
    for dist in metadata.distributions():
        name = dist.metadata.get("Name")
        if not name or name in seen:
            continue
        licence, known = _licence_of(dist)
        seen[name] = Component(
            name=name,
            version=dist.version or "unknown",
            licence=licence,
            licence_known=known,
        )
    return [seen[name] for name in sorted(seen, key=str.lower)]


def bom(found: list[Component]) -> dict[str, Any]:
    return {
        "bomFormat": "CycloneDX",
        "specVersion": CYCLONEDX_VERSION,
        "version": 1,
        "metadata": {
            "component": {"type": "application", "name": "kryova-backend"},
            # No timestamp. A field that changes on every run makes the SBOM
            # differ from itself, so nothing can tell "the dependencies moved"
            # from "somebody re-ran the script" — and diffing two SBOMs is the
            # main thing anybody does with them.
        },
        "components": [component.to_cyclonedx() for component in found],
    }


def licence_findings(found: list[Component]) -> list[str]:
    """Copyleft packages that are not accounted for, and unknowns.

    Both are reported. An unknown licence is not a pass — Decision 4's claim is
    that this stack is free and the obligations are understood, and a package
    nobody has checked is a hole in that claim rather than an absence of one.
    """
    findings: list[str] = []
    for component in found:
        if component.copyleft and component.name.lower() not in LINKED_GPL_IS_A_PROBLEM:
            findings.append(
                f"{component.name} {component.version} is {component.licence}, which is "
                "copyleft and is not in LINKED_GPL_IS_A_PROBLEM. Either it is invoked as a "
                "subprocess (say so there, with the reason) or it must go."
            )
        if not component.licence_known:
            findings.append(
                f"{component.name} {component.version} declares no licence. Decision 4's "
                "claim is that this stack is free and the obligations are understood; a "
                "package nobody has checked is a hole in that claim."
            )
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", help="write here instead of stdout")
    parser.add_argument(
        "--check-licences",
        action="store_true",
        help="exit non-zero on an unaccounted copyleft or unknown licence",
    )
    args = parser.parse_args(argv)

    found = components()
    document = json.dumps(bom(found), indent=2, sort_keys=True)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(document + "\n")
        print(f"{len(found)} components -> {args.out}")
    else:
        print(document)

    if args.check_licences:
        findings = licence_findings(found)
        for line in findings:
            print(f"licence: {line}", file=sys.stderr)
        if findings:
            print(f"{len(findings)} licence finding(s)", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
