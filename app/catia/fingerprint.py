"""What changed in CATIA that Kryova did not do (ROAD_TO_10 5.2).

The agent builds a part and then, a message later, the engineer nudges a parameter by hand in
CATIA. Nothing tells the agent: its picture of the part is the one in the transcript and in the
state block, both of which were true when they were written. So it edits a part it believes it
knows -- and a pad length it set to 40 is 55 now, the bracket it is about to fillet has a pocket
it never made, and every number it quotes afterwards is about a part that is gone.

A **fingerprint** is a deliberately small summary of the bound document: the feature names in
build order, the parameter values, and whether it is saved. The daemon computes one after each
operation it runs (that is the part Kryova *recorded*) and again before the next one (that is
the part CATIA *has*). When the two differ, somebody else moved something, and the state block
says so, by name.

**Why not the geometry.** A mass or a bounding box would catch a change a feature list misses,
and costs a rebuild to read. This is the cheap summary -- names and numbers, no recompute -- and
it is honest about being so: a hand edit that changes a sketch's geometry without moving a named
parameter or adding a feature is *not seen*, and nothing here claims it is.

**Pure.** No session, no socket. The state lives in `conversation.catia_state` (what Kryova
recorded and the notes taken from drift) and on the `DeviceConnection` (what the daemon last
reported), and `dispatch.py` joins them; this module only decides what a difference means.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: A part is dozens of features and parameters, not thousands; a fingerprint past this is a
#: daemon misbehaving, and the state block resends whatever is kept here on every step.
MAX_NAMES = 200

#: Notes about past drift that stay in the state block. The newest are the ones that matter.
MAX_NOTES = 3

#: How many differences a sentence names before it counts the rest.
_DESCRIBE_LIMIT = 6


def _scalar(value: Any) -> Any:
    """A parameter value as something comparable and bounded.

    Numbers are rounded to six decimals so a value that round-trips through COM as
    `40.00000000001` is not a manual edit; the daemon and this agree on that rounding or
    every operation would report drift.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return round(float(value), 6)
    if value is None:
        return None
    return str(value)[:120]


def normalise(raw: Any) -> dict[str, Any] | None:
    """A daemon's fingerprint as the three fields kept, or None when it is not one."""
    if not isinstance(raw, dict):
        return None
    features, parameters = raw.get("features"), raw.get("parameters")
    if not isinstance(features, list) or not isinstance(parameters, dict):
        return None
    saved = raw.get("saved")
    return {
        "features": [str(name)[:120] for name in features[:MAX_NAMES]],
        "parameters": {
            str(name)[:120]: _scalar(value) for name, value in list(parameters.items())[:MAX_NAMES]
        },
        "saved": saved if isinstance(saved, bool) else None,
    }


@dataclass(frozen=True)
class Drift:
    """The difference between what Kryova recorded and what CATIA now has."""

    parameters_moved: tuple[tuple[str, Any, Any], ...] = ()
    features_added: tuple[str, ...] = ()
    features_removed: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.parameters_moved or self.features_added or self.features_removed)

    def describe(self) -> str:
        """One sentence, names verbatim (the caller fences them like any CATIA text)."""
        parts: list[str] = []
        moved = [f"{name} {before!r} -> {after!r}" for name, before, after in self.parameters_moved]
        if moved:
            parts.append("parameters moved: " + _limited(moved))
        if self.features_added:
            parts.append("new: " + _limited(list(self.features_added)))
        if self.features_removed:
            parts.append("gone: " + _limited(list(self.features_removed)))
        return "; ".join(parts)


def _limited(items: list[str]) -> str:
    shown = ", ".join(items[:_DESCRIBE_LIMIT])
    if len(items) > _DESCRIBE_LIMIT:
        shown += f", and {len(items) - _DESCRIBE_LIMIT} more"
    return shown


def compare(recorded: dict[str, Any] | None, observed: dict[str, Any] | None) -> Drift | None:
    """What moved between two fingerprints of the same document, or None when nothing did.

    `saved` is not compared: a document saved or left unsaved is not something the agent's
    picture of the part is wrong about. A missing side is "cannot say", which is None and never
    a difference -- an unmeasured claim is not a pass, but it is not drift either.
    """
    if recorded is None or observed is None:
        return None
    before_parameters = recorded.get("parameters") or {}
    after_parameters = observed.get("parameters") or {}
    moved = tuple(
        (name, before_parameters.get(name), after_parameters.get(name))
        for name in sorted(set(before_parameters) | set(after_parameters))
        if before_parameters.get(name) != after_parameters.get(name)
    )
    before_features = list(recorded.get("features") or [])
    after_features = list(observed.get("features") or [])
    added = tuple(name for name in after_features if name not in before_features)
    removed = tuple(name for name in before_features if name not in after_features)
    drift = Drift(parameters_moved=moved, features_added=added, features_removed=removed)
    return drift if drift else None


# -- the two pieces of conversation state, as pure functions of the dict -------------------------


def document_key(remote_path: str | None, doc_name: str | None) -> str:
    """How a document is named across the daemon, the connection and the state: by path.

    A name is not unique (two unsaved parts can share one) and a path on Windows is not
    case-sensitive, so the key is the lower-cased path, falling back to the name.
    """
    return (remote_path or doc_name or "").strip().lower()


def recorded_of(state: dict[str, Any] | None) -> dict[str, Any] | None:
    recorded = (state or {}).get("fingerprint")
    return recorded if isinstance(recorded, dict) else None


def with_result(
    state: dict[str, Any] | None, fingerprint: dict[str, Any], *, step: int, document: str
) -> dict[str, Any]:
    """The state after a Kryova operation: this is what the part is, as far as Kryova knows."""
    return {**(state or {}), "fingerprint": {**fingerprint, "step": step, "document": document}}


def with_absorbed(
    state: dict[str, Any] | None, observed: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Fold a pending difference into the notes, so it outlives the next operation.

    Called once a Kryova operation has returned, *before* its own fingerprint is recorded.
    Without it that fingerprint would silently swallow the hand edit: after a pad the recorded
    part *includes* the engineer's change, the difference vanishes, and the agent is never
    told. Returns None when there is nothing to fold.
    """
    recorded = recorded_of(state)
    drift = compare(recorded, observed)
    if recorded is None or observed is None or drift is None:
        return None
    notes = list((state or {}).get("manual_changes") or [])[-(MAX_NOTES - 1) :]
    notes.append({"after_step": recorded.get("step"), "text": drift.describe()})
    return {
        **(state or {}),
        "manual_changes": notes,
        "fingerprint": {**observed, "step": recorded.get("step"), "document": recorded.get("document")},
    }


RESTORE = "restore"


def with_restore(
    state: dict[str, Any] | None,
    label: str,
    *,
    after_step: int,
    fingerprint: dict[str, Any] | None,
    document: str,
) -> dict[str, Any]:
    """The state after the user rolled the document back to a checkpoint (ROAD_TO_10 5.7).

    A restore is the one change to the part that is neither the agent's operation nor an edit
    the daemon can see happen: the transcript and the operation log still say every feature
    after the checkpoint exists, and nothing else contradicts them. So it is a note of its own
    kind. **The recorded fingerprint is replaced by what the restore reported, or dropped when
    the daemon reported none** -- keeping the pre-restore one would make the next comparison
    read the rollback itself as a hand edit.
    """
    notes = list((state or {}).get("manual_changes") or [])[-(MAX_NOTES - 1) :]
    notes.append({"after_step": after_step, "kind": RESTORE, "text": label})
    updated = {**(state or {}), "manual_changes": notes}
    if fingerprint is not None:
        updated["fingerprint"] = {**fingerprint, "step": after_step, "document": document}
    else:
        updated.pop("fingerprint", None)
    return updated


def notes_for(state: dict[str, Any] | None, observed: dict[str, Any] | None) -> list[str]:
    """The lines the state block carries: past hand edits first, then one not yet absorbed."""
    lines: list[str] = []
    for note in (state or {}).get("manual_changes") or []:
        if not isinstance(note, dict) or not note.get("text"):
            continue
        if note.get("kind") == RESTORE:
            lines.append(
                f"catia_manual_change: after step {note.get('after_step')} the user rolled the "
                f"document back to the checkpoint {note['text']!r}, so everything built after "
                "that checkpoint is gone from the part even where the conversation says it was "
                "built. Read the part again (catia_list_features) before you edit it."
            )
        else:
            lines.append(
                f"catia_manual_change: after step {note.get('after_step')} the document was "
                f"changed in CATIA by hand -- {note['text']}"
            )
    recorded = recorded_of(state)
    drift = compare(recorded, observed)
    if drift is not None and recorded is not None:
        lines.append(
            f"catia_manual_change: since step {recorded.get('step')} (your last operation) the "
            f"document was changed in CATIA by hand -- {drift.describe()}. The picture above "
            "predates that: read the part again (catia_list_features, catia_list_parameters) "
            "before you edit it, and say what moved if the user asks about it."
        )
    return lines
