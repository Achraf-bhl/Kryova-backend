"""What the daemon will and will not press, decided on this side of the wire.

The server resolves a command name into labels to try and sends them down. It is
not trusted to decide whether the command is allowed, for the same reason it is
not trusted to decide a tier: the frame originated several layers up from a
language model's output, and a bridge that took the server's word for "this is
safe" would be a bridge with no opinion of its own about what runs on the
engineer's workstation.

So this table is a second, independent copy of `app/catia_kb/ui.py`'s refusal
list. It must stay in step, and if the two disagree the stricter one wins by
construction -- the daemon checks last.

**What is refused, and why it is refused rather than approval-gated.** Every
other mutating tool is safe because a checkpoint precedes it: whatever the agent
does to the part can be rolled back. These four classes escape that:

* a macro is arbitrary code, and reaching the macro editor through the menu
  would put back exactly the `SystemService.Evaluate` hole the tool vocabulary
  was built to exclude;
* `Tools > Options` and `Customize` write settings that persist into every
  future session on this seat, including work that has nothing to do with
  Kryova, and no checkpoint of a document restores them;
* `Save As` and `Save Management` write files the daemon did not name, which is
  the "no filesystem paths from the model" rule with a dialog in front of it;
* exiting closes CATIA, discards unsaved work, and takes the bridge with it.

Refusing outright rather than asking for approval is deliberate. An approval
dialog for "may I open the macro editor" is a question a user will say yes to
without reading, and the answer to "can the assistant change my CATIA settings"
should be no rather than a click away.
"""

from __future__ import annotations

import unicodedata

from .tool_table import ToolRefused

#: Labels refused when the WHOLE command is one of them. Mirrors
#: `FORBIDDEN_EXACT` in `app/catia_kb/ui.py`; a test asserts the two are equal.
FORBIDDEN_EXACT: dict[str, str] = {
    "macro": "runs arbitrary code on this workstation",
    "macros": "runs arbitrary code on this workstation",
    "makro": "runs arbitrary code on this workstation",
    "makros": "runs arbitrary code on this workstation",
    "macros catia": "runs arbitrary code on this workstation",
    "options": "changes settings for every future CATIA session on this seat",
    "optionen": "changes settings for every future CATIA session on this seat",
    "opciones": "changes settings for every future CATIA session on this seat",
    "opzioni": "changes settings for every future CATIA session on this seat",
    "customize": "changes this seat's toolbars and shortcuts permanently",
    "personnaliser": "changes this seat's toolbars and shortcuts permanently",
    "anpassen": "changes this seat's toolbars and shortcuts permanently",
    "personalizar": "changes this seat's toolbars and shortcuts permanently",
    "personalizza": "changes this seat's toolbars and shortcuts permanently",
    "save as": "writes a file outside the bridge's working directory",
    "save management": "writes files outside the bridge's working directory",
    "enregistrer sous": "writes a file outside the bridge's working directory",
    "gestion des enregistrements": "writes files outside the bridge's working directory",
    "speichern unter": "writes a file outside the bridge's working directory",
    "guardar como": "writes a file outside the bridge's working directory",
    "salva con nome": "writes a file outside the bridge's working directory",
    "exit": "closes CATIA and disconnects the bridge",
    "quitter": "closes CATIA and disconnects the bridge",
    "beenden": "closes CATIA and disconnects the bridge",
    "salir": "closes CATIA and disconnects the bridge",
    "esci": "closes CATIA and disconnects the bridge",
    "licence": "changes which licences this seat holds",
    "license": "changes which licences this seat holds",
    "licences": "changes which licences this seat holds",
    "licenses": "changes which licences this seat holds",
    "lizenz": "changes which licences this seat holds",
    "lizenzen": "changes which licences this seat holds",
}

#: Phrases refused when a command *begins* with them. Mirrors
#: `FORBIDDEN_PREFIX` on the server side.
#:
#: The split between this and the exact table is not cosmetic. Matching `exit`
#: as a leading word refused `Exit Sketcher Workbench` -- an ordinary Sketcher
#: command -- which is how a safety rule turns into a tool the agent learns to
#: distrust.
FORBIDDEN_PREFIX: dict[str, str] = {
    "visual basic": "opens the code editor",
    "basic editor": "opens the code editor",
    "editeur visual basic": "opens the code editor",
    "macro instruction": "runs arbitrary code on this workstation",
    "license manager": "changes which licences this seat holds",
    "licence manager": "changes which licences this seat holds",
}

#: Both, for callers that want the whole policy in one mapping.
FORBIDDEN: dict[str, str] = {**FORBIDDEN_EXACT, **FORBIDDEN_PREFIX}


def fold(text: str) -> str:
    """Lowercase, drop accents, drop the `&` accelerator, collapse punctuation.

    The same normalisation the server's reference package uses, reimplemented
    rather than imported because the daemon has no access to `app/` -- it ships
    to a workstation on its own. Divergence here would mean a label the server
    considered forbidden slipping past this check, so the rule is kept simple
    enough to be obviously the same on both sides.
    """
    decomposed = unicodedata.normalize("NFKD", text.replace("&", "").lower())
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    kept = "".join(ch if (ch.isalnum() or ch in "&+#") else " " for ch in stripped)
    return " ".join(kept.split())


def refusal(label: str) -> str | None:
    """Why this command may not be driven, or `None` if it may.

    The whole label against `FORBIDDEN_EXACT`, then its opening words against
    `FORBIDDEN_PREFIX`. Never a substring test: `Copy Options` contains
    `options` and is an ordinary command, and a rule that refused it would make
    the interface tools look broken rather than careful.
    """
    folded = fold(label)
    if not folded:
        return None
    if folded in FORBIDDEN_EXACT:
        return FORBIDDEN_EXACT[folded]
    words = folded.split(" ")
    for size in (3, 2):
        if len(words) > size:
            head = " ".join(words[:size])
            if head in FORBIDDEN_PREFIX:
                return FORBIDDEN_PREFIX[head]
    for phrase, reason in FORBIDDEN_PREFIX.items():
        if folded == phrase:
            return reason
    return None


def check(labels: list[str] | tuple[str, ...]) -> None:
    """Raise `Forbidden` if any candidate for a command is off-limits.

    Every candidate is checked, not just the one that ends up working. The
    server sends an ordered list -- an internal id, the seat's label, the
    English name -- and a caller that wanted the macro editor would only have to
    get one of them past this to succeed.
    """
    for label in labels:
        reason = refusal(label)
        if reason is not None:
            raise Forbidden(
                f"The bridge does not drive {label!r}: it {reason}. Nothing a "
                "checkpoint can undo, so it is yours to do by hand -- tell the user "
                "where the command is instead of running it."
            )


class Forbidden(ToolRefused):
    """A command this bridge will not press, with the reason for the user.

    A `ToolRefused`, not a plain error: this is the daemon declining to do
    something, which is the same category as an unknown tool or a missing
    approval token, and `session.py` already logs those as refusals and relays
    the message verbatim. Raising anything else would put a stack trace in the
    log and `Forbidden while running catia_run_command:` in front of a sentence
    written to be read by the user.
    """
