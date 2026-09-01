"""CATIA's interface language, which is a per-installation choice.

A user's CATIA is running in whatever language their site installed, and the
menus, dialog titles, toolbar tooltips, feature names in the specification tree
and material names are all translated. This matters for correctness in three
different ways, and they pull in different directions:

**Telling them where to click.** "Insert > Dress-Up Features > Edge Fillet" is
useless to someone whose menu reads "Einfügen > Ausformungselemente >
Kantenverrundung". Where this package knows the localised name, it says both.

**Understanding what they typed.** A user reports that "Tasche" failed, or asks
about "sformo", or types "congé". Those have to reach the Pocket, Draft Angle
and Edge Fillet entries or the assistant answers the wrong question. Every
translation recorded here is indexed by the recogniser exactly like an English
alias, so the language of the question never has to be established first.

**Driving CATIA.** This is the one that is *not* a problem, and saying so is
worth more than a translation table: **the COM automation API is not
localised.** `part.ShapeFactory.AddNewPad(...)` is `AddNewPad` on a Japanese
seat, a German seat and an English seat, and a `.CATScript` written against one
runs unchanged on all of them. What the automation layer *does* see localised is
data the user typed -- feature names in the tree, parameter names, material
names -- so a script that looks a feature up by the string "Pad.1" is the script
that breaks abroad, not one that walks `Bodies` and `Shapes` by index or type.
Kryova's CATIA bridge is built on that API, which is why it works on any
language install without a translation step.

**Honesty rule.** A missing translation is reported as missing. Inventing a
plausible German command name is worse than saying "in an English interface this
is Edge Fillet; I do not have the German name" -- the user can find a command
from the menu position and the icon, and cannot recover from being sent to a
menu item that does not exist.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Mapping

from app.catia_kb.types import Disambiguation, Entry, Kind, Section, entry


@dataclass(frozen=True, slots=True)
class Language:
    """One CATIA interface language."""

    code: str
    #: English name, then the endonym, because a user says "German" or "Deutsch".
    name: str
    endonym: str
    #: Whether this package carries a translated command vocabulary for it.
    vocabulary: bool = False
    note: str = ""

    def label(self) -> str:
        return f"{self.name} ({self.endonym})"


#: The languages CATIA V5 ships an interface in. Availability on a given seat is
#: an install-time choice: a site that installed only English and French cannot
#: switch to German without adding the language pack, which is why
#: `Tools > Customize > Options` may list fewer languages than this.
LANGUAGES: Final[tuple[Language, ...]] = (
    Language("en", "English", "English", vocabulary=True, note="Always installed; the fallback for every other language."),
    Language("fr", "French", "Français", vocabulary=True),
    Language("de", "German", "Deutsch", vocabulary=True),
    Language("it", "Italian", "Italiano", vocabulary=True),
    Language("es", "Spanish", "Español", vocabulary=True, note="Spanish sites very often run the English interface; Spanish-language training material commonly keeps the English command names."),
    Language("ja", "Japanese", "日本語"),
    Language("zh", "Chinese (Simplified)", "简体中文"),
    Language("ko", "Korean", "한국어"),
    Language("ru", "Russian", "Русский"),
    Language("pt", "Portuguese (Brazilian)", "Português"),
)

LANGUAGE_BY_CODE: Final[Mapping[str, Language]] = {lang.code: lang for lang in LANGUAGES}

#: Codes this package can translate a command name into.
TRANSLATED: Final[frozenset[str]] = frozenset(
    lang.code for lang in LANGUAGES if lang.vocabulary and lang.code != "en"
)

#: What a user might write instead of a two-letter code, folded and lowercased.
_LANGUAGE_ALIASES: Final[Mapping[str, str]] = {
    "english": "en", "anglais": "en", "englisch": "en", "inglese": "en", "ingles": "en", "en-us": "en", "en-gb": "en",
    "french": "fr", "francais": "fr", "franzosisch": "fr", "francese": "fr", "frances": "fr", "fr-fr": "fr",
    "german": "de", "deutsch": "de", "allemand": "de", "tedesco": "de", "aleman": "de", "de-de": "de",
    "italian": "it", "italiano": "it", "italien": "it", "italienisch": "it", "it-it": "it",
    "spanish": "es", "espanol": "es", "espagnol": "es", "spanisch": "es", "spagnolo": "es", "es-es": "es",
    "japanese": "ja", "japonais": "ja", "nihongo": "ja", "ja-jp": "ja",
    "chinese": "zh", "mandarin": "zh", "simplified chinese": "zh", "zh-cn": "zh",
    "korean": "ko", "ko-kr": "ko",
    "russian": "ru", "russe": "ru", "ru-ru": "ru",
    "portuguese": "pt", "brazilian portuguese": "pt", "pt-br": "pt",
}


def normalise_language(value: str | None) -> str | None:
    """Turn whatever the caller has into a CATIA language code, or None.

    Accepts a code (`fr`), a locale (`fr-FR`), an English name (`French`) or the
    endonym (`Deutsch`). Anything unrecognised returns None rather than a guess,
    because a wrong language is a wrong menu path.
    """
    if not value:
        return None
    text = value.strip().lower().replace("_", "-")
    if text in LANGUAGE_BY_CODE:
        return text
    if text in _LANGUAGE_ALIASES:
        return _LANGUAGE_ALIASES[text]
    head = text.split("-", 1)[0]
    if head in LANGUAGE_BY_CODE:
        return head
    return _LANGUAGE_ALIASES.get(head)


# ---------------------------------------------------------------------------
# The translated command vocabulary.
#
# Every name below was taken from vendor or university course material written
# in that language -- not translated by inference. The tables are deliberately
# short: they cover the commands a user actually names, and a command absent
# here is reported as untranslated rather than guessed at.
#
# Keyed by entry key, so a rename of the English display name cannot orphan a
# translation without the coverage test noticing.
# ---------------------------------------------------------------------------

#: `key -> {language code -> localised name}`.
NAMES: Final[dict[str, dict[str, str]]] = {
    # -- Sketcher ---------------------------------------------------------
    "sketcher": {"fr": "Esquisse", "de": "Skizzierer", "it": "Schizzo", "es": "Croquis"},
    "sketcher.profile": {"fr": "Profil", "de": "Profil", "it": "Profilo", "es": "Perfil"},
    "sketcher.line": {"fr": "Droite", "de": "Gerade", "it": "Retta", "es": "Recta"},
    "sketcher.circle": {"fr": "Cercle", "de": "Kreis", "it": "Cerchio", "es": "Círculo"},
    "sketcher.point": {"fr": "Point", "de": "Punkt", "it": "Punto", "es": "Punto"},
    "sketcher.rectangle": {"fr": "Rectangle", "de": "Rechteck", "it": "Rettangolo", "es": "Rectángulo"},
    "sketcher.spline": {"fr": "Spline", "de": "Spline", "it": "Spline", "es": "Spline"},
    "sketcher.constraint": {"fr": "Contrainte", "de": "Bedingung", "it": "Vincolo", "es": "Restricción"},
    "sketcher.corner": {"fr": "Congé", "de": "Ecke", "it": "Raccordo", "es": "Redondeo"},
    "sketcher.chamfer": {"fr": "Chanfrein", "de": "Fase", "it": "Smusso", "es": "Chaflán"},
    "sketcher.trim": {"fr": "Relimiter", "de": "Trimmen", "it": "Relimita", "es": "Recortar"},
    "sketcher.mirror": {"fr": "Symétrie", "de": "Spiegeln", "it": "Specchio", "es": "Simetría"},
    "sketcher.project_3d_elements": {"fr": "Projeter des éléments 3D", "de": "3D-Elemente projizieren"},
    # -- Part Design ------------------------------------------------------
    "part_design": {"fr": "Conception de pièces", "de": "Teilekonstruktion", "it": "Part Design", "es": "Diseño de piezas"},
    "part_design.pad": {"fr": "Extrusion", "de": "Block", "it": "Prisma", "es": "Extrusión"},
    "part_design.pocket": {"fr": "Poche", "de": "Tasche", "it": "Tasca", "es": "Bolsillo"},
    "part_design.shaft": {"fr": "Révolution", "de": "Welle", "it": "Alberello", "es": "Revolución"},
    "part_design.groove": {"fr": "Gorge", "de": "Nut", "it": "Scanalatura", "es": "Ranura"},
    "part_design.hole": {"fr": "Trou", "de": "Bohrung", "it": "Foro", "es": "Agujero"},
    "part_design.rib": {"fr": "Nervure", "de": "Rippe", "it": "Nervatura", "es": "Nervio"},
    "part_design.slot": {"fr": "Rainure", "de": "Nut", "it": "Scanalatura", "es": "Ranura"},
    "part_design.stiffener": {"fr": "Raidisseur", "de": "Versteifung", "it": "Irrigidimento", "es": "Refuerzo"},
    "part_design.edge_fillet": {"fr": "Congé d'arête", "de": "Kantenverrundung", "it": "Raccordo di uno spigolo", "es": "Redondeo de arista"},
    "part_design.chamfer": {"fr": "Chanfrein", "de": "Fase", "it": "Smusso", "es": "Chaflán"},
    "part_design.draft_angle": {"fr": "Dépouille", "de": "Formschräge", "it": "Sformo", "es": "Desmoldeo"},
    "part_design.shell": {"fr": "Coque", "de": "Schalenelement", "it": "Guscio", "es": "Vaciado"},
    "part_design.thickness": {"fr": "Surépaisseur", "de": "Aufmaß", "it": "Spessore", "es": "Espesor"},
    "part_design.thread_tap": {"fr": "Filetage/Taraudage", "de": "Gewinde", "it": "Filettatura", "es": "Rosca"},
    "part_design.mirror": {"fr": "Symétrie", "de": "Spiegeln", "it": "Simmetria", "es": "Simetría"},
    "part_design.rectangular_pattern": {"fr": "Répétition rectangulaire", "de": "Rechteckmuster", "it": "Ripetizione rettangolare", "es": "Patrón rectangular"},
    "part_design.circular_pattern": {"fr": "Répétition circulaire", "de": "Kreismuster", "it": "Ripetizione circolare", "es": "Patrón circular"},
    "part_design.user_pattern": {"fr": "Répétition utilisateur", "de": "Benutzermuster"},
    "part_design.translation": {"fr": "Translation", "de": "Verschieben", "it": "Traslazione", "es": "Traslación"},
    "part_design.rotation": {"fr": "Rotation", "de": "Drehen", "it": "Rotazione", "es": "Rotación"},
    "part_design.scaling": {"fr": "Facteur d'échelle", "de": "Maßstab", "it": "Scala", "es": "Escalar"},
    "part_design.plane": {"fr": "Plan", "de": "Ebene", "it": "Piano", "es": "Plano"},
    "part_design.point": {"fr": "Point", "de": "Punkt", "it": "Punto", "es": "Punto"},
    "part_design.line": {"fr": "Droite", "de": "Gerade", "it": "Retta", "es": "Recta"},
    "part_design.multi_sections_solid": {"fr": "Solide multi-sections", "de": "Multisektionsvolumen"},
    "part_design.close_surface": {"fr": "Remplissage de surface", "de": "Fläche schließen"},
    "part_design.thick_surface": {"fr": "Surface épaisse", "de": "Fläche mit Dicke"},
    "part_design.split": {"fr": "Découpage", "de": "Teilen", "it": "Taglia", "es": "Dividir"},
    # -- Surfaces ---------------------------------------------------------
    "gsd": {"fr": "Generative Shape Design", "de": "Generative Shape Design", "it": "Generative Shape Design"},
    "gsd.extrude": {"fr": "Extrusion", "de": "Extrudieren", "it": "Estrusione", "es": "Extrusión"},
    "gsd.revolve": {"fr": "Révolution", "de": "Rotationskörper", "it": "Rivoluzione", "es": "Revolución"},
    "gsd.offset": {"fr": "Décalage", "de": "Offset", "it": "Offset", "es": "Desfase"},
    "gsd.sweep": {"fr": "Balayage", "de": "Sweep", "it": "Sweep"},
    "gsd.fill": {"fr": "Remplissage", "de": "Füllen", "it": "Riempimento", "es": "Relleno"},
    "gsd.multi_sections_surface": {"fr": "Surface multi-sections", "de": "Multisektionsfläche", "it": "Superficie multisezioni"},
    "gsd.blend": {"fr": "Raccord", "de": "Übergang", "it": "Raccordo"},
    "gsd.join": {"fr": "Assembler", "de": "Verbinden", "it": "Assembla", "es": "Unir"},
    "gsd.split": {"fr": "Découper", "de": "Teilen", "it": "Taglia", "es": "Dividir"},
    "gsd.trim": {"fr": "Relimiter", "de": "Trimmen", "it": "Relimita", "es": "Recortar"},
    "gsd.boundary": {"fr": "Frontière", "de": "Randkurve", "it": "Contorno"},
    "gsd.extract": {"fr": "Extraire", "de": "Extrahieren", "it": "Estrai"},
    "gsd.extrapolate": {"fr": "Extrapoler", "de": "Extrapolieren", "it": "Estrapola"},
    "gsd.healing": {"fr": "Réparation", "de": "Heilung", "it": "Riparazione"},
    "gsd.intersection": {"fr": "Intersection", "de": "Schnittmenge", "it": "Intersezione"},
    "gsd.projection": {"fr": "Projection", "de": "Projektion", "it": "Proiezione"},
    "gsd.plane": {"fr": "Plan", "de": "Ebene", "it": "Piano", "es": "Plano"},
    "gsd.circle": {"fr": "Cercle", "de": "Kreis", "it": "Cerchio", "es": "Círculo"},
    "gsd.helix": {"fr": "Hélice", "de": "Helix", "it": "Elica"},
    "gsd.shape_fillet": {"fr": "Congé", "de": "Verrundung", "it": "Raccordo", "es": "Redondeo"},
    # -- Assembly ---------------------------------------------------------
    "assembly_design": {"fr": "Assemblage", "de": "Baugruppe", "it": "Assieme", "es": "Ensamblaje"},
    "assembly_design.coincidence_constraint": {"fr": "Coïncidence", "de": "Kongruenz", "it": "Coincidenza", "es": "Coincidencia"},
    "assembly_design.contact_constraint": {"fr": "Contact", "de": "Kontakt", "it": "Contatto", "es": "Contacto"},
    "assembly_design.offset_constraint": {"fr": "Décalage", "de": "Abstand", "it": "Offset", "es": "Desfase"},
    "assembly_design.angle_constraint": {"fr": "Angle", "de": "Winkel", "it": "Angolo", "es": "Ángulo"},
    "assembly_design.fix_component": {"fr": "Fixité", "de": "Fixieren", "it": "Fissa", "es": "Fijar"},
    "assembly_design.explode": {"fr": "Éclaté", "de": "Explosionsdarstellung", "it": "Esploso", "es": "Explosionar"},
    "assembly_design.bill_of_material": {"fr": "Nomenclature", "de": "Stückliste", "it": "Distinta base", "es": "Lista de materiales"},
    "assembly_design.update": {"fr": "Mise à jour", "de": "Aktualisieren", "it": "Aggiorna", "es": "Actualizar"},
    # -- Sheet metal ------------------------------------------------------
    "sheet_metal_design": {"fr": "Tôlerie", "de": "Blech", "it": "Lamiera", "es": "Chapa"},
    "sheet_metal_design.wall": {"fr": "Paroi", "de": "Wand", "it": "Parete", "es": "Pared"},
    "sheet_metal_design.flange": {"fr": "Bord tombé", "de": "Bördel", "it": "Bordo", "es": "Pestaña"},
    "sheet_metal_design.bend": {"fr": "Pli", "de": "Biegung", "it": "Piegatura", "es": "Plegado"},
    "sheet_metal_design.unfold": {"fr": "Déplier", "de": "Abwickeln", "it": "Sviluppa", "es": "Desplegar"},
    "sheet_metal_design.fold": {"fr": "Plier", "de": "Falten", "it": "Piega", "es": "Plegar"},
    "sheet_metal_design.cutout": {"fr": "Découpe", "de": "Ausschnitt", "it": "Ritaglio", "es": "Recorte"},
    "sheet_metal_design.hem": {"fr": "Ourlet", "de": "Saum", "it": "Orlo"},
    # -- Drafting ---------------------------------------------------------
    "drafting": {"fr": "Mise en plan", "de": "Zeichnung", "it": "Disegno", "es": "Dibujo"},
    "drafting.front_view": {"fr": "Vue de face", "de": "Vorderansicht", "it": "Vista frontale", "es": "Vista frontal"},
    "drafting.section_view": {"fr": "Vue en coupe", "de": "Schnittansicht", "it": "Vista in sezione", "es": "Vista de sección"},
    "drafting.detail_view": {"fr": "Vue de détail", "de": "Detailansicht", "it": "Vista di dettaglio", "es": "Vista de detalle"},
    "drafting.isometric_view": {"fr": "Vue isométrique", "de": "Isometrische Ansicht", "it": "Vista isometrica"},
    "drafting.dimensions": {"fr": "Cotation", "de": "Bemaßung", "it": "Quotatura", "es": "Acotación"},
    "drafting.balloon": {"fr": "Bulle", "de": "Positionsnummer", "it": "Pallinatura", "es": "Globo"},
    "drafting.area_fill": {"fr": "Hachurage", "de": "Schraffur", "it": "Tratteggio", "es": "Sombreado"},
    "drafting.text": {"fr": "Texte", "de": "Text", "it": "Testo", "es": "Texto"},
    # -- Measure / view / infrastructure ----------------------------------
    "ui.measure_between": {"fr": "Mesure entre", "de": "Messen zwischen", "it": "Misura tra", "es": "Medir entre"},
    "ui.specification_tree": {"fr": "Arbre de spécifications", "de": "Strukturbaum", "it": "Albero delle specifiche", "es": "Árbol de especificaciones"},
    "ui.compass": {"fr": "Boussole", "de": "Kompass", "it": "Bussola", "es": "Brújula"},
    "ui.hide_show": {"fr": "Cacher/Afficher", "de": "Ausblenden/Anzeigen", "it": "Nascondi/Mostra", "es": "Ocultar/Mostrar"},
    "ui.update": {"fr": "Mise à jour", "de": "Aktualisieren", "it": "Aggiorna", "es": "Actualizar"},
    "material_library": {"fr": "Bibliothèque de matériaux", "de": "Werkstoffbibliothek", "it": "Libreria materiali", "es": "Biblioteca de materiales"},
    "gps": {"fr": "Analyse structurale", "de": "Strukturanalyse", "it": "Analisi strutturale", "es": "Análisis estructural"},
}


def localised(key: str, language: str | None) -> str | None:
    """The command's name in `language`, or None when it is not recorded.

    None is a real answer here and callers must render it as "not recorded",
    never fall through to the English name presented as though it were the
    localised one. See the honesty rule in the module docstring.
    """
    code = normalise_language(language)
    if code is None or code == "en":
        return None
    return NAMES.get(key, {}).get(code)


def translations(key: str) -> dict[str, str]:
    """Every recorded translation for one entry, by language code."""
    return dict(NAMES.get(key, {}))


def alias_pairs() -> list[tuple[str, str]]:
    """`(localised name, entry key)` for every translation, for the recogniser.

    This is what lets a user type `Tasche`, `sformo` or `congé d'arête` and
    reach the right entry without anyone having established what language the
    conversation is in.
    """
    return [(name, key) for key, table in NAMES.items() for name in table.values()]


# ---------------------------------------------------------------------------
# Entries about the language setting itself, so the assistant can answer
# "how do I change CATIA to English" as well as work through it.
# ---------------------------------------------------------------------------

_ENTRIES = [
    entry(
        "setting.ui_language",
        "User interface language",
        Kind.SETTING,
        aliases=(
            "interface language",
            "change catia language",
            "catia in english",
            "langue de l interface",
            "sprache",
            "lingua interfaccia",
            "idioma",
            "ui language",
            "switch language",
        ),
        summary="Which language CATIA draws its menus, dialogs and tooltips in.",
        menu="Tools > Customize > Options tab > User interface language",
        fields=(
            "User interface language -- a drop-down listing only the language packs actually installed on this seat",
            "The change takes effect on the next CATIA start, not immediately",
        ),
        needs=(
            "The language pack must have been selected at install time. A seat installed English-only lists English only, and adding another needs the installer, not this dialog.",
        ),
        failures=(
            "The language you want is not in the list -- it was not installed, not a CATIA limitation",
            "Changing it does nothing until CATIA is restarted",
            "Feature names already typed into the specification tree do not translate; only the interface does",
        ),
        fixes=(
            "Re-run the CATIA installer and add the language pack, then set it here",
            "Restart CATIA after changing the setting",
        ),
        see_also=("api.localisation", "setting.catsettings"),
    ),
    entry(
        "api.localisation",
        "Automation is language-independent",
        Kind.API,
        aliases=(
            "macro language independent",
            "vba localisation",
            "script other language",
            "catscript language",
            "automation localisation",
            "does my macro work in german",
        ),
        summary="COM object and method names never translate; only user-typed data does.",
        failures=(
            "A macro that finds a feature by the literal string \"Pad.1\" breaks on a German seat, where the same feature is named \"Block.1\"",
            "A macro that matches a material by name breaks wherever the material catalogue is localised",
            "SendKeys-driven automation breaks everywhere, because it depends on menu text and keyboard layout",
        ),
        fixes=(
            "Walk collections by index or by type (`Bodies`, `Shapes`, `HybridBodies`) instead of by display name",
            "Set your own names on features you create, so later steps can rely on a name you chose rather than a generated localised one",
            "Never automate through SendKeys or menu text; use the object model",
        ),
        see_also=("api.automation_root", "setting.ui_language"),
    ),
]


_DISAMBIGUATIONS = [
    Disambiguation(
        term="language",
        aliases=("langue", "sprache", "lingua", "idioma"),
        options=(
            "The CATIA interface language -- Tools > Customize > Options > User interface language, install-time packs, restart required",
            "The language of the reference manuals being searched -- a retrieval preference only, both languages are always searched",
            "The language the automation API speaks -- it does not have one; COM names are always English",
        ),
        guidance="Ask which one is meant only if the answer differs. Usually it does not: tell them the English command name, the localised name where it is recorded, and the menu position, which is the same in every language.",
    ),
]


def language_entries() -> list[Entry]:
    return list(_ENTRIES)


SECTION = Section("languages", _ENTRIES, _DISAMBIGUATIONS)
