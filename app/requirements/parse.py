"""Reading a requirements document an engineer actually wrote, and writing one back.

Master plan **11.1**: an engineer arrives with a document, not a data structure.
This reads a small, explicit, line-oriented format — call it **`.kreq`** — and it
is explicit on purpose.

**It refuses; it never guesses.** A parser that silently misreads a requirement
is worse than one that refuses the line and says which, because the misreading
survives into a report that says the machine was verified. So every non-blank,
non-comment line either becomes a field or becomes a `ParseProblem` naming the
line number, the text, and what to write instead. Nothing is dropped. That
property is asserted by a test rather than trusted.

## The format

```
# Lines starting with # are comments. Blank lines separate nothing; they are free.

REQ-001: The press frame shall weigh no more than 850 kg.
    measure: mass_kg
    target: <= 850
    source: customer
    citation: Acme RFQ 2026-03 §2.1
    rationale: The shop crane at the customer's site lifts one tonne.
    status: agreed

REQ-002: The frame shall carry the ram load with a factor of safety of 2.
    measure: machine.fos.ram_stroke.factor_of_safety
    target: >= 2.0
    source: derived
    parent: REQ-001
    rationale: Flowed down from the frame requirement with the ram case named.

REQ-003: The frame shall survive ten years of two-shift operation.
    needs: no fatigue solver is federated yet (Phase 6).
    source: customer
```

* A **requirement starts at column 0**: `<id>: <statement>`, on one line.
* Its **attributes are indented**, one `key: value` per line.
* Keys: `measure`, `target`, `tolerance`, `source`, `citation`, `rationale`,
  `parent`, `needs`, `status`. An unknown key is refused, with the closest known
  spelling suggested — accepting two spellings for one field is how a document
  comes to mean different things to different readers.
* `target` carries the comparison and the number together, `<= 850`, because
  that is how the constraint is written on a datasheet. A formula over the
  design's parameters keeps the `=` marker the rest of the codebase uses:
  `target: <= =target_mass_kg`.
* `parent` takes a comma-separated list.
* A key given **twice** in one requirement is refused. Silently keeping the last
  one is precisely the quiet misreading this format exists to avoid.

## What it does NOT understand — read this before trusting it

This is a *format*, not natural-language understanding, and it will not pretend
otherwise:

* **No free-text engineering.** "The frame shall be light" imports as a
  requirement with no measurement and no `needs`, and is refused. It cannot infer
  `mass_kg <= something` from prose and does not try.
* **No units in values.** `target: <= 850 kg` is refused. The codebase is
  mm-N-MPa and **nothing converts**; the unit is a property of the measurement
  path, and a unit written beside the number is either redundant or a conversion
  nobody would perform.
* **No ranges or two-sided bounds.** `between 4 and 5` is two requirements. One
  comparison per requirement keeps the failure message actionable — "out by 0.3"
  means nothing about a band.
* **No conditional or nested requirements.** "If fitted with the heavy ram, then
  …" has no representation here. Write the condition into the statement and take
  the requirement as unconditional, or split the document.
* **No multi-line statements.** A wrapped statement's second line is refused,
  because an unindented continuation is indistinguishable from a malformed
  requirement header.
* **It does not read "shall" from "should".** An aspiration written in
  requirement form is imported as a requirement; only the author knows which it
  was.
* **It reads text, not documents.** PDF, Word and Excel extraction belongs to
  `app.media` and the attachment pipeline, and extracted text is quoted material
  (Decision 8) — it never becomes an instruction. Hand this the text.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Final

from app.design.assertions import COMPARISONS
from app.requirements.errors import RequirementError, RequirementParseError
from app.requirements.model import Requirement, RequirementSet, Source, Status

#: The conventional extension. Nothing here enforces it; it is what to call the
#: file so somebody opening a directory can tell what they are looking at.
SUFFIX: Final = ".kreq"

#: Every attribute key the format knows. A closed set: an unknown key is a
#: refusal with a suggestion, never a silent drop.
KEYS: Final[frozenset[str]] = frozenset(
    {
        "measure",
        "target",
        "tolerance",
        "source",
        "citation",
        "rationale",
        "parent",
        "needs",
        "status",
    }
)

_HEADER_RE: Final = re.compile(r"^(?P<id>[^\s:]+)\s*:\s*(?P<statement>\S.*)$")
_ATTRIBUTE_RE: Final = re.compile(r"^\s+(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*:\s*(?P<value>.*)$")
_TARGET_RE: Final = re.compile(r"^(?P<comparison>[<>=!]=|[<>])\s*(?P<value>.*)$")
_COMMENT_RE: Final = re.compile(r"^\s*#")


@dataclass(frozen=True)
class ParseProblem:
    """One line that could not be read, and what to do about it.

    Carries the line number and the line itself, because a requirements document
    is edited in a text editor and "line 47" is the whole of the fix.
    """

    line: int
    text: str
    problem: str

    def __str__(self) -> str:
        return f"line {self.line}: {self.problem}\n    {self.text.strip()}"


@dataclass(frozen=True)
class ParseResult:
    """What a document parsed into, and everything about it that could not be read.

    Falsey when there is any problem at all. A partial import is the failure this
    format exists to refuse — a requirement dropped for a typo reads exactly like
    a requirement nobody wrote — so the useful shape is `result.require()`, which
    either gives a validated set or raises with every problem listed at once.
    """

    name: str = "requirements"
    requirements: tuple[Requirement, ...] = ()
    problems: tuple[ParseProblem, ...] = ()
    origin: str = ""

    def __bool__(self) -> bool:
        return not self.problems

    def __len__(self) -> int:
        return len(self.requirements)

    def __iter__(self) -> Any:
        return iter(self.requirements)

    def require(self) -> RequirementSet:
        """The validated set, or a refusal listing every problem in the document.

        Building the set is where the *graph* is checked — duplicate ids, a
        parent that is not there, a cycle — so a document can parse line by line
        and still be refused here, which is correct: those are properties of the
        document as a whole and cannot be seen a line at a time.
        """
        if self.problems:
            listed = "\n".join(str(one) for one in self.problems)
            raise RequirementParseError(
                f"{self.origin or self.name}: {len(self.problems)} line(s) could not be "
                f"read, so the import was refused rather than performed partially — a "
                f"requirement dropped for a typo is indistinguishable from one nobody "
                f"wrote.\n{listed}"
            )
        return RequirementSet(
            name=self.name, requirements=self.requirements, origin=self.origin
        )

    def summary(self) -> str:
        head = f"{len(self.requirements)} requirement(s) read from {self.origin or self.name}."
        if not self.problems:
            return head
        return (
            head
            + f" {len(self.problems)} line(s) refused:\n"
            + "\n".join(str(one) for one in self.problems)
        )


def parse_requirements(
    text: str, *, name: str = "requirements", origin: str = ""
) -> ParseResult:
    """Read a `.kreq` document. Never raises; every failure is a `ParseProblem`.

    Not raising is the point: an engineer's document usually has several problems
    at once, and a parser that stops at the first turns one editing session into
    five. `ParseResult.require()` is where the refusal happens, with all of them.
    """
    problems: list[ParseProblem] = []
    requirements: list[Requirement] = []

    blocks, problems_found = _blocks(text)
    problems.extend(problems_found)

    for block in blocks:
        built = _build(block, problems)
        if built is not None:
            requirements.append(built)

    return ParseResult(
        name=name,
        requirements=tuple(requirements),
        problems=tuple(sorted(problems, key=lambda one: one.line)),
        origin=origin,
    )


@dataclass
class _Block:
    """One requirement's raw lines, before any of it is interpreted."""

    line: int
    id: str
    statement: str
    attributes: dict[str, tuple[int, str]]


def _blocks(text: str) -> tuple[list[_Block], list[ParseProblem]]:
    """Split the document into requirement blocks, accounting for every line.

    Every line is blank, a comment, a header, an attribute, or a problem. There
    is no fifth case and no line that is simply skipped, which is the property
    `tests/test_requirements_parse.py` pins by counting.
    """
    blocks: list[_Block] = []
    problems: list[ParseProblem] = []
    current: _Block | None = None

    for number, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip() or _COMMENT_RE.match(raw):
            continue

        attribute = _ATTRIBUTE_RE.match(raw)
        if attribute is not None:
            key = attribute.group("key")
            value = attribute.group("value").strip()
            if current is None:
                problems.append(
                    ParseProblem(
                        number,
                        raw,
                        f"{key!r} is indented as an attribute but no requirement has "
                        "started yet. A requirement begins at column 0 with "
                        "'<id>: <statement>'.",
                    )
                )
                continue
            if key not in KEYS:
                problems.append(ParseProblem(number, raw, _unknown_key(key)))
                continue
            if key in current.attributes:
                first = current.attributes[key][0]
                problems.append(
                    ParseProblem(
                        number,
                        raw,
                        f"{current.id} gives {key!r} twice (also on line {first}). "
                        "Keeping the last one silently is how a document comes to say "
                        "something nobody wrote — delete one.",
                    )
                )
                continue
            current.attributes[key] = (number, value)
            continue

        if raw[:1].isspace():
            problems.append(
                ParseProblem(
                    number,
                    raw,
                    "an indented line must be 'key: value'. A statement that wrapped "
                    "onto a second line is not supported — put it on one line, or move "
                    "the detail into 'rationale:'.",
                )
            )
            continue

        header = _HEADER_RE.match(raw)
        if header is None:
            problems.append(
                ParseProblem(
                    number,
                    raw,
                    "not a requirement and not an attribute. A requirement starts at "
                    "column 0 as '<id>: <statement>'; its attributes are indented.",
                )
            )
            continue

        found_id = header.group("id")
        if found_id in KEYS:
            # `measure: mass_kg` at column 0 is a real mistake and would otherwise
            # be read as a requirement whose id is "measure" — refused, but with a
            # message about id syntax that sends the author looking in the wrong
            # place. Indentation is the only thing that separates the two, so say so.
            problems.append(
                ParseProblem(
                    number,
                    raw,
                    f"{found_id!r} is an attribute of a requirement, not a requirement. "
                    "Indent it under the requirement it belongs to — indentation is what "
                    "separates the two.",
                )
            )
            continue

        current = _Block(
            line=number,
            id=found_id,
            statement=header.group("statement").strip(),
            attributes={},
        )
        blocks.append(current)

    return blocks, problems


def _build(block: _Block, problems: list[ParseProblem]) -> Requirement | None:
    """Turn one block into a `Requirement`, or add problems and return None."""
    before = len(problems)
    fields: dict[str, Any] = {"id": block.id, "statement": block.statement}

    comparison = ""
    target: float | str = 0.0
    if "target" in block.attributes:
        line, value = block.attributes["target"]
        parsed = _parse_target(value)
        if isinstance(parsed, str):
            problems.append(ParseProblem(line, value, parsed))
        else:
            comparison, target = parsed

    if "measure" in block.attributes:
        fields["measure"] = block.attributes["measure"][1]
        fields["comparison"] = comparison
        fields["target"] = target
        if "target" not in block.attributes:
            line = block.attributes["measure"][0]
            problems.append(
                ParseProblem(
                    line,
                    block.attributes["measure"][1],
                    f"{block.id} says what to measure but not what the answer must be. "
                    "Add a target, e.g. 'target: <= 850'.",
                )
            )
    elif "target" in block.attributes:
        line = block.attributes["target"][0]
        problems.append(
            ParseProblem(
                line,
                block.attributes["target"][1],
                f"{block.id} has a target but nothing to measure. Add 'measure: <path>' "
                "— a bound on nothing cannot be checked.",
            )
        )

    if "tolerance" in block.attributes:
        line, value = block.attributes["tolerance"]
        number = _parse_number(value)
        if number is None:
            problems.append(
                ParseProblem(
                    line, value, f"{value!r} is not a number. A tolerance is slack in the "
                    "unit of the measurement, e.g. 'tolerance: 0.05'."
                )
            )
        else:
            fields["tolerance"] = number

    for key, target_field in (
        ("citation", "citation"),
        ("rationale", "rationale"),
        ("needs", "needs"),
    ):
        if key in block.attributes:
            fields[target_field] = block.attributes[key][1]

    if "parent" in block.attributes:
        line, value = block.attributes["parent"]
        parents = tuple(part.strip() for part in value.split(",") if part.strip())
        if not parents:
            problems.append(
                ParseProblem(
                    line, value, "'parent:' was given with nothing after it. Name the "
                    "requirement this one flows down from, or delete the line."
                )
            )
        fields["parents"] = parents

    for key, kind in (("source", Source), ("status", Status)):
        if key not in block.attributes:
            continue
        line, value = block.attributes[key]
        try:
            fields[key] = kind(value.strip().lower())
        except ValueError:
            allowed = ", ".join(sorted(str(one) for one in kind))
            problems.append(
                ParseProblem(
                    line,
                    value,
                    f"{value!r} is not a {key} this build knows. Use one of: {allowed}.",
                )
            )

    if len(problems) != before:
        return None

    try:
        return Requirement(**fields)
    except RequirementError as exc:
        problems.append(ParseProblem(block.line, f"{block.id}: {block.statement}", str(exc)))
        return None


def _parse_target(value: str) -> tuple[str, float | str] | str:
    """`'<= 850'` -> `('<=', 850.0)`. Returns the problem text on failure."""
    match = _TARGET_RE.match(value.strip())
    if match is None:
        allowed = ", ".join(sorted(COMPARISONS))
        return (
            f"{value!r} is not a target. Write the comparison and the number together, "
            f"e.g. 'target: <= 850'. Comparisons: {allowed}."
        )
    comparison = match.group("comparison")
    rest = match.group("value").strip()
    if comparison not in COMPARISONS:
        allowed = ", ".join(sorted(COMPARISONS))
        return f"{comparison!r} is not a comparison. Use one of: {allowed}."
    if not rest:
        return f"'{comparison}' has nothing after it. Give the number the measurement is compared with."
    if len(rest.split()) > 1:
        return (
            f"{rest!r} has more than a number in it. If that is a unit, leave it out: "
            "the unit belongs to the measurement path and this codebase is mm-N-MPa "
            "with nothing converting anywhere, so a unit written here would either be "
            "redundant or a conversion nobody performs."
        )
    if rest.startswith("="):
        # A formula over the design's parameters — the same `=` marker the spec,
        # the parameters and `Assertion.bound` all already use. Left unevaluated
        # here; `check_assertions` resolves it against the resolved parameters.
        return comparison, rest
    number = _parse_number(rest)
    if number is None:
        return (
            f"{rest!r} is not a number. Write a number, or a formula over the design's "
            "parameters starting with '=', e.g. 'target: <= =target_mass_kg'."
        )
    return comparison, number


def _parse_number(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


def _unknown_key(key: str) -> str:
    close = difflib.get_close_matches(key, sorted(KEYS), n=1, cutoff=0.6)
    suggestion = f" Did you mean {close[0]!r}?" if close else ""
    return (
        f"{key!r} is not a key this format knows.{suggestion} Known keys: "
        f"{', '.join(sorted(KEYS))}. An unrecognised key is refused rather than "
        "ignored, because an ignored one is a requirement that says less than its "
        "author thinks."
    )


# -- writing ------------------------------------------------------------------


def format_requirements(requirements: RequirementSet | Iterable[Requirement]) -> str:
    """Write a requirement set back out in the same format, round-trippable.

    The writer exists so the format is the *storage* form as well as the import
    form: a set edited through the API can be handed back to the engineer as the
    document they gave, rather than as JSON they did not write. Round-tripping is
    asserted by a test — a writer that cannot be read back is a second format.
    """
    given: Sequence[Requirement] = (
        tuple(requirements)
        if not isinstance(requirements, RequirementSet)
        else requirements.requirements
    )
    lines: list[str] = []
    for one in given:
        lines.append(f"{one.id}: {one.statement}")
        if one.measure is not None:
            lines.append(f"    measure: {one.measure}")
            lines.append(f"    target: {one.comparison} {_number(one.target)}")
            if one.tolerance:
                lines.append(f"    tolerance: {_number(one.tolerance)}")
        else:
            lines.append(f"    needs: {one.needs}")
        lines.append(f"    source: {one.source}")
        if one.citation:
            lines.append(f"    citation: {one.citation}")
        if one.rationale:
            lines.append(f"    rationale: {one.rationale}")
        if one.parents:
            lines.append(f"    parent: {', '.join(one.parents)}")
        lines.append(f"    status: {one.status}")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def _number(value: float | str) -> str:
    if isinstance(value, str):
        return value
    return f"{value:g}"


__all__ = [
    "KEYS",
    "SUFFIX",
    "ParseProblem",
    "ParseResult",
    "format_requirements",
    "parse_requirements",
]
