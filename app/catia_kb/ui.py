"""Driving CATIA's own interface: which button, under which label, in which language.

The rest of `app/catia_kb` answers "where is this command and what does it do".
This module answers the harder operational question behind the interactive
bridge tools: **what string does this seat respond to, and what is the popup it
opens going to call its buttons?**

Three facts decided the shape of everything here, and all three are inconvenient.

**`Application.StartCommand` takes the command's displayed name, and displayed
names are translated.** `CATIA.StartCommand "Pad"` works on an English seat and
does nothing at all on a German one, where the command is `Block`. There is no
error -- it is a silent no-op, which is the worst possible failure mode for an
agent that then reports success. So a command is never sent as one string. It is
sent as an *ordered list of candidates*: the seat-language label first, English
second, and the daemon stops at the first one CATIA accepts.

**Internal command ids exist, are language-independent, and are undocumented.**
`OpenInNewWnd` and `SpecificationsLevelSelect` are real and work everywhere;
there is no published table, and Dassault has never committed to them as an
interface. `COMMAND_IDS` below holds only ids with a source. Guessing one is
worse than not having it: an id that does not exist fails the same silent way a
wrong translation does, and it would burn the candidate that was going to work.

**The live menu bar is the only complete, always-correct source.** CATIA V5's
menu bar on Windows is a native Win32 menu, so the daemon can read it and get
this seat's *actual* labels -- in Japanese, in Korean, in a language this package
has no vocabulary for at all. That is why `catia_list_commands` exists and why
it is the documented fallback: the tables here make the common case one round
trip instead of two, and the live read makes the uncommon case possible at all.

The honesty rule from `languages.py` applies unchanged. A command with no
recorded translation reports that it has none; it never gets a plausible-looking
invented one, because a label that does not exist cannot be recovered from by a
user reading their own menu.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from app.catia_kb import languages
from app.catia_kb.registry import _fold, registry
from app.catia_kb.types import WORKBENCH_NAMES, Entry, Kind

# ---------------------------------------------------------------------------
# Dialog buttons
# ---------------------------------------------------------------------------


class ButtonRole(StrEnum):
    """What a dialog button *does*, independent of what it says.

    The agent presses a role. It never presses a label, because the label is
    `OK`/`Annuler`/`Abbrechen`/`Annulla`/`Cancelar` depending on a choice made
    when the workstation was installed, and a bridge that needed to know which
    one would be a bridge that only worked in English.
    """

    OK = "ok"
    CANCEL = "cancel"
    APPLY = "apply"
    CLOSE = "close"
    YES = "yes"
    NO = "no"
    PREVIEW = "preview"
    MORE = "more"
    LESS = "less"
    HELP = "help"


#: Role -> language -> the labels that role wears in that language.
#:
#: Matching is done on the folded form (lowercase, accents stripped), so `Sí`
#: and `Sì` are one entry and `&OK` loses its accelerator marker before it gets
#: here. Several languages share a label -- `OK` is `OK` everywhere, `No` is
#: `No` in both Italian and Spanish -- and that is fine: the map is many-to-one
#: onto roles by design.
BUTTON_LABELS: Final[dict[ButtonRole, dict[str, tuple[str, ...]]]] = {
    ButtonRole.OK: {
        "en": ("OK", "Ok"),
        "fr": ("OK",),
        "de": ("OK",),
        "it": ("OK",),
        "es": ("Aceptar", "OK"),
    },
    ButtonRole.CANCEL: {
        "en": ("Cancel",),
        "fr": ("Annuler",),
        "de": ("Abbrechen",),
        "it": ("Annulla",),
        "es": ("Cancelar",),
    },
    ButtonRole.APPLY: {
        "en": ("Apply",),
        "fr": ("Appliquer",),
        # V5 dialogs use both; neither is wrong and which one appears varies by
        # dialog, so both are candidates rather than one being "the" German.
        "de": ("Anwenden", "Übernehmen"),
        "it": ("Applica",),
        "es": ("Aplicar",),
    },
    ButtonRole.CLOSE: {
        "en": ("Close",),
        "fr": ("Fermer",),
        "de": ("Schließen", "Schliessen"),
        "it": ("Chiudi",),
        "es": ("Cerrar",),
    },
    ButtonRole.YES: {
        "en": ("Yes",),
        "fr": ("Oui",),
        "de": ("Ja",),
        "it": ("Sì", "Si"),
        "es": ("Sí", "Si"),
    },
    ButtonRole.NO: {
        "en": ("No",),
        "fr": ("Non",),
        "de": ("Nein",),
        "it": ("No",),
        "es": ("No",),
    },
    ButtonRole.PREVIEW: {
        "en": ("Preview",),
        "fr": ("Aperçu",),
        "de": ("Vorschau",),
        "it": ("Anteprima",),
        "es": ("Vista preliminar", "Presentación preliminar"),
    },
    ButtonRole.MORE: {
        "en": ("More>>", "More >>", "More"),
        "fr": ("Plus>>", "Plus >>", "Plus"),
        "de": ("Mehr>>", "Mehr >>", "Mehr"),
        "it": ("Altro>>", "Altro >>", "Altro"),
        "es": ("Más>>", "Más >>", "Más"),
    },
    ButtonRole.LESS: {
        "en": ("Less<<", "Less <<", "Less"),
        "fr": ("Moins<<", "Moins <<", "Moins"),
        "de": ("Weniger<<", "Weniger <<", "Weniger"),
        "it": ("Meno<<", "Meno <<", "Meno"),
        "es": ("Menos<<", "Menos <<", "Menos"),
    },
    ButtonRole.HELP: {
        "en": ("Help",),
        "fr": ("Aide",),
        "de": ("Hilfe",),
        "it": ("Guida", "?"),
        "es": ("Ayuda",),
    },
}


#: The Win32 standard control ids. A dialog built on the common controls gives
#: its OK button id 1 and its Cancel button id 2 whatever the labels read, so
#: this is a language-proof second chance when label matching finds nothing.
#: It is a *fallback*: CATIA's own dialogs frequently number their buttons
#: themselves, in which case these ids mean nothing and matching by label is
#: the only thing that works. Neither mechanism is sufficient alone.
STANDARD_CONTROL_IDS: Final[dict[ButtonRole, int]] = {
    ButtonRole.OK: 1,
    ButtonRole.CANCEL: 2,
    ButtonRole.YES: 6,
    ButtonRole.NO: 7,
    ButtonRole.CLOSE: 8,
    ButtonRole.HELP: 9,
}


def _folded_button_index() -> dict[str, ButtonRole]:
    index: dict[str, ButtonRole] = {}
    for role, per_language in BUTTON_LABELS.items():
        for labels in per_language.values():
            for label in labels:
                folded = _fold(label)
                # First writer wins, and the enum's declaration order puts the
                # unambiguous roles first. Nothing currently collides; this is
                # here so that adding a language later cannot silently reassign
                # `OK` to something else.
                index.setdefault(folded, role)
    return index


_BUTTON_INDEX: Final[dict[str, ButtonRole]] = _folded_button_index()


def role_of(label: str) -> ButtonRole | None:
    """Which role a button label plays, or `None` for a label we do not know.

    `None` is a real answer and not a failure: CATIA dialogs carry plenty of
    buttons that are not one of these ten (`Reverse Direction`, `Mirrored
    extent`), and the agent reaches those by name.
    """
    if not label:
        return None
    folded = _fold(label.replace("&", ""))
    if folded in _BUTTON_INDEX:
        return _BUTTON_INDEX[folded]
    # `More>>` folds to `more` only after the chevrons become spaces, which
    # `_fold` does; but a dialog may pad it differently, so try the first word.
    head = folded.split(" ")[0] if folded else ""
    return _BUTTON_INDEX.get(head)


def button_labels(role: ButtonRole, language: str | None = None) -> tuple[str, ...]:
    """Every label `role` might wear, seat language first, then English.

    The ordering is what the daemon walks. Putting the seat's own language first
    matters for exactly one case and it is a real one: `OK` and `Ok` are both
    present in several languages, and a Spanish seat labels its accept button
    `Aceptar` -- looking for `OK` first would find nothing and fall through to
    the control-id fallback for a dialog that was perfectly readable.
    """
    code = languages.normalise_language(language) if language else None
    out: list[str] = []
    for candidate_language in (code, "en"):
        if candidate_language is None:
            continue
        for label in BUTTON_LABELS[role].get(candidate_language, ()):
            if label not in out:
                out.append(label)
    # Everything else, so a seat in a language with no table still gets tried
    # against the labels we do have rather than going straight to control ids.
    for per_language in (BUTTON_LABELS[role],):
        for labels in per_language.values():
            for label in labels:
                if label not in out:
                    out.append(label)
    return tuple(out)


# ---------------------------------------------------------------------------
# Menus
# ---------------------------------------------------------------------------

#: The menu-bar titles, for telling a user where to click and for matching a
#: menu path against a live menu bar the daemon has read.
#:
#: Only what is attested goes here. A gap is left as a gap: `catia_list_commands`
#: reads the real bar, so a missing translation costs one extra round trip,
#: while a wrong one sends the agent into the wrong menu and it reports the
#: command as absent.
MENU_TITLES: Final[dict[str, dict[str, str]]] = {
    "Start": {"fr": "Démarrer", "de": "Start", "es": "Inicio"},
    "File": {"fr": "Fichier", "de": "Datei", "it": "File", "es": "Archivo"},
    "Edit": {"fr": "Edition", "de": "Bearbeiten", "it": "Modifica", "es": "Edición"},
    "View": {"fr": "Affichage", "de": "Ansicht", "it": "Visualizza", "es": "Ver"},
    "Insert": {"fr": "Insertion", "de": "Einfügen", "it": "Inserisci", "es": "Insertar"},
    "Tools": {"fr": "Outils", "de": "Extras", "it": "Strumenti", "es": "Herramientas"},
    "Analyze": {"fr": "Analyse", "de": "Analyse", "it": "Analizza", "es": "Analizar"},
    "Window": {"fr": "Fenêtre", "de": "Fenster", "it": "Finestra", "es": "Ventana"},
    "Help": {"fr": "Aide", "de": "Hilfe", "it": "Guida", "es": "Ayuda"},
}

#: The menu bar's first item is the Start menu on every V5 seat, whatever it is
#: called. Position is the one property of a menu that survives translation, and
#: it is how `catia_switch_workbench` navigates when the title is not in the
#: table above.
START_MENU_INDEX: Final[int] = 0


def menu_title(english: str, language: str | None = None) -> str:
    """A menu-bar title in the seat's language, or the English one unchanged."""
    code = languages.normalise_language(language) if language else None
    if code is None or code == "en":
        return english
    return MENU_TITLES.get(english, {}).get(code, english)


# ---------------------------------------------------------------------------
# Language-independent identifiers
# ---------------------------------------------------------------------------

#: Entry key -> the internal command id `StartCommand` also accepts.
#:
#: These are language-independent and therefore tried first. The list is short
#: because it contains only ids with a published source; the rest of the
#: vocabulary resolves through translated labels and the live menu, which is
#: slower and always correct. Do not add one from memory -- see the module
#: docstring for why a wrong id is worse than a missing one.
COMMAND_IDS: Final[dict[str, str]] = {
    "infrastructure.open_in_new_window": "OpenInNewWnd",
    "infrastructure.expand_selection": "SpecificationsLevelSelect",
}

#: Entry key -> the id `Application.StartWorkbench` takes. Also language-
#: independent, and the reason `catia_switch_workbench` is a one-call operation
#: for the workbenches listed and a Start-menu walk for the others.
WORKBENCH_IDS: Final[dict[str, str]] = {
    "part_design": "PrtCfg",
    "generative_structural_analysis": "GPSCfg",
    "generative_shape_design": "CATShapeDesignWorkbench",
}


# ---------------------------------------------------------------------------
# Commands the bridge will not drive
# ---------------------------------------------------------------------------

#: Folded tokens that make a command off-limits, and the reason to report.
#:
#: This is the boundary that keeps a general "press any button" tool from being
#: a general "do anything to this workstation" tool. Each entry is refused
#: because a checkpoint cannot undo it, which is the thing every other mutating
#: tool relies on:
#:
#: * a macro is arbitrary code, and reaching it through the UI would reintroduce
#:   exactly the `SystemService.Evaluate` hole the tool vocabulary excludes;
#: * `Tools > Options` and `Customize` persist into every future session on this
#:   seat, including sessions Kryova has nothing to do with;
#: * `Save As` and `Save Management` write files the daemon did not choose,
#:   which is the "no filesystem paths from the model" rule with a dialog in
#:   front of it;
#: * exiting or closing throws away unsaved work and takes the bridge with it.
#:
#: Labels refused when the WHOLE command is one of them. Most dangerous CATIA
#: commands are a single menu word, and a whole-label rule is the one that
#: cannot over-refuse: `Options...` folds to exactly `options` and is caught,
#: while `Copy Options` and `Optional Rib` are not.
#:
#: `exit` is the reason this list is separate from the prefix one below.
#: Matching it as a leading word refused `Exit Sketcher Workbench`, which is an
#: ordinary Sketcher command an engineer uses constantly -- a refusal that
#: would have taught the agent the interface tools were unreliable, over a
#: command that closes nothing.
FORBIDDEN_EXACT: Final[dict[str, str]] = {
    # Code execution.
    "macro": "runs arbitrary code on this workstation",
    "macros": "runs arbitrary code on this workstation",
    "makro": "runs arbitrary code on this workstation",
    "makros": "runs arbitrary code on this workstation",
    "macros catia": "runs arbitrary code on this workstation",
    # Settings that outlive the session.
    "options": "changes settings for every future CATIA session on this seat",
    "optionen": "changes settings for every future CATIA session on this seat",
    "opciones": "changes settings for every future CATIA session on this seat",
    "opzioni": "changes settings for every future CATIA session on this seat",
    "customize": "changes this seat's toolbars and shortcuts permanently",
    "personnaliser": "changes this seat's toolbars and shortcuts permanently",
    "anpassen": "changes this seat's toolbars and shortcuts permanently",
    "personalizar": "changes this seat's toolbars and shortcuts permanently",
    "personalizza": "changes this seat's toolbars and shortcuts permanently",
    # Writing files the daemon did not choose.
    "save as": "writes a file outside the bridge's working directory",
    "save management": "writes files outside the bridge's working directory",
    "enregistrer sous": "writes a file outside the bridge's working directory",
    "gestion des enregistrements": "writes files outside the bridge's working directory",
    "speichern unter": "writes a file outside the bridge's working directory",
    "guardar como": "writes a file outside the bridge's working directory",
    "salva con nome": "writes a file outside the bridge's working directory",
    # Leaving, which takes the bridge with it.
    "exit": "closes CATIA and disconnects the bridge",
    "quitter": "closes CATIA and disconnects the bridge",
    "beenden": "closes CATIA and disconnects the bridge",
    "salir": "closes CATIA and disconnects the bridge",
    "esci": "closes CATIA and disconnects the bridge",
    # Licensing.
    "licence": "changes which licences this seat holds",
    "license": "changes which licences this seat holds",
    "licences": "changes which licences this seat holds",
    "licenses": "changes which licences this seat holds",
    "lizenz": "changes which licences this seat holds",
    "lizenzen": "changes which licences this seat holds",
}

#: Phrases refused when a command *begins* with them. Only for names where no
#: legitimate CATIA command shares the opening: `Visual Basic Editor` and
#: `Visual Basic support` are both the code editor, and nothing else in the
#: product starts with those two words.
FORBIDDEN_PREFIX: Final[dict[str, str]] = {
    "visual basic": "opens the code editor",
    "basic editor": "opens the code editor",
    "editeur visual basic": "opens the code editor",
    "macro instruction": "runs arbitrary code on this workstation",
    "license manager": "changes which licences this seat holds",
    "licence manager": "changes which licences this seat holds",
}

#: Both tables together. Kept for callers that want to see the whole policy.
FORBIDDEN_COMMAND_TOKENS: Final[dict[str, str]] = {**FORBIDDEN_EXACT, **FORBIDDEN_PREFIX}


def forbidden_reason(label: str) -> str | None:
    """Why the bridge refuses to drive `label`, or `None` if it will.

    Two rules, and the split matters: `FORBIDDEN_EXACT` matches the whole
    command name, `FORBIDDEN_PREFIX` matches its opening words. A single rule
    cannot serve both -- exact-only would wave through `Visual Basic Editor`,
    and prefix-only refuses `Exit Sketcher Workbench` because it begins with
    `Exit`.

    Substring matching is not used at all. `Copy Options` contains `options`
    and is a perfectly ordinary command.
    """
    folded = _fold(label.replace("&", ""))
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


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CommandTarget:
    """Everything the daemon needs to press one command on one seat."""

    #: The reference entry this resolved to, or `None` when the name is not in
    #: the reference at all. Not an error: the vocabulary is large and CATIA is
    #: larger, and the daemon can still search the live menu for the literal
    #: name.
    key: str | None
    #: The canonical English name, which is what the agent and the user talk in.
    name: str
    #: Ordered candidates for `StartCommand`. Internal id first when there is
    #: one, then the seat's language, then English.
    candidates: tuple[str, ...]
    workbench: str = ""
    menu: str = ""
    #: The seat-language label, when this package has one recorded.
    localised: str = ""
    language: str = ""
    #: Other entries the name could have meant. The tool reports these rather
    #: than picking one silently -- pressing the Sheet Metal `Flange` when the
    #: engineer meant the Aerospace Sheet Metal one builds the wrong part.
    alternatives: tuple[str, ...] = ()
    note: str = ""

    @property
    def translated(self) -> bool:
        return bool(self.localised)


def _command_entries(name: str) -> tuple[Entry, ...]:
    """Entries `name` could mean, commands preferred over everything else."""
    found = registry().lookup(name)
    if not found:
        return ()
    commands = tuple(item for item in found if item.kind is Kind.COMMAND)
    return commands or found


def resolve_command(name: str, *, language: str | None = None) -> CommandTarget:
    """What to send for `name` on a seat running in `language`.

    Never raises and never returns `None`. A name that is not in the reference
    comes back as a target carrying the name itself as its only candidate, with
    a note saying so -- the daemon will still try it against `StartCommand` and
    against the live menu, and an honest "CATIA did not recognise that command"
    is a better outcome than refusing to look.
    """
    code = languages.normalise_language(language) if language else None
    matches = _command_entries(name)

    if not matches:
        return CommandTarget(
            key=None,
            name=name.strip(),
            candidates=(name.strip(),) if name.strip() else (),
            note="not in the Kryova CATIA reference; sent to CATIA as written",
        )

    entry = matches[0]
    localised = languages.localised(entry.key, code) if code and code != "en" else None

    candidates: list[str] = []
    command_id = COMMAND_IDS.get(entry.key)
    if command_id:
        candidates.append(command_id)
    if localised:
        candidates.append(localised)
    if entry.name not in candidates:
        candidates.append(entry.name)

    note = ""
    if code and code not in {None, "en"} and not localised:
        # The honesty rule. English is still tried, because on a seat whose
        # language pack was never installed for this command it is what the
        # menu actually shows -- but the agent is told the label is a guess so
        # it can fall back to reading the live menu rather than reporting a
        # silent no-op as success.
        note = (
            f"no {code} label recorded for {entry.name}; the English name is being "
            "tried and may not match this interface"
        )

    return CommandTarget(
        key=entry.key,
        name=entry.name,
        candidates=tuple(candidates),
        # The workbench's own name, not `Entry.location()` -- that prefers the
        # menu path, and the caller wants to be able to say "switch to Part
        # Design" and to report the menu path separately.
        workbench=WORKBENCH_NAMES.get(entry.workbench, ""),
        menu=entry.menu,
        localised=localised or "",
        language=code or "",
        alternatives=tuple(item.key for item in matches[1:5]),
        note=note,
    )


@dataclass(frozen=True, slots=True)
class WorkbenchTarget:
    """How to reach one workbench: by id if we have one, by menu if not."""

    key: str | None
    name: str
    #: `StartWorkbench` id, empty when unknown.
    workbench_id: str = ""
    #: Start-menu path in the seat's language, for the walk-the-menu fallback.
    menu_path: tuple[str, ...] = ()
    localised: str = ""
    licence: str = ""
    alternatives: tuple[str, ...] = ()
    note: str = ""


def resolve_workbench(name: str, *, language: str | None = None) -> WorkbenchTarget:
    """Which workbench `name` means and how to switch to it.

    `menu_path` is the Start-menu walk, translated as far as this package can:
    the domain titles come from the entry's own recorded path and the workbench
    name from the translation table. Where a translation is missing the English
    segment is kept, and the daemon matches menu items by prefix, so a partly
    translated path still lands more often than it misses -- and when it misses,
    `catia_list_commands` reads the real menu.
    """
    code = languages.normalise_language(language) if language else None
    found = registry().lookup(name)
    benches = tuple(item for item in found if item.kind is Kind.WORKBENCH)
    if not benches:
        return WorkbenchTarget(
            key=None,
            name=name.strip(),
            note="not a workbench in the Kryova CATIA reference",
        )

    entry = benches[0]
    localised = languages.localised(entry.key, code) if code and code != "en" else None
    # `Start > Mechanical Design > Part Design` -> the segments after `Start`.
    segments = [part.strip() for part in entry.menu.split(">") if part.strip()]
    path = segments[1:] if segments and segments[0].lower() == "start" else segments
    if path:
        path[-1] = localised or path[-1]

    return WorkbenchTarget(
        key=entry.key,
        name=entry.name,
        workbench_id=WORKBENCH_IDS.get(entry.key, ""),
        menu_path=tuple(path),
        localised=localised or "",
        licence=entry.licence,
        alternatives=tuple(item.key for item in benches[1:5]),
        note=(
            ""
            if localised or not code or code == "en"
            else f"no {code} name recorded for {entry.name}"
        ),
    )
