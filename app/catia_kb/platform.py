"""Startup, environment, the interface objects users describe, and Tools > Options.

Everything in this module is something a user names without knowing it has a
name. "My compass is stuck", "the tree disappeared", "the toolbar is gone",
"it says I have no licence", "the model looks faceted" -- each of those has one
specific answer, and the answer is usually a setting rather than a defect.

Two settings in here account for a disproportionate share of real problems:
**3D Accuracy (sag)**, which is why a cylinder looks like a polygon and why a
clash check can miss a real clash; and **Cache Management**, which is why a
large assembly either opens in twenty seconds or in twenty minutes.
"""

from __future__ import annotations

from app.catia_kb.types import Disambiguation, Kind, Section, bulk, entry

_SET = Kind.SETTING


_STARTUP = [
    entry(
        "setting.catia_environment",
        "CATIA environment file",
        _SET,
        aliases=(
            "environment file", "catenv", "env file", "catia environment", "-env",
            "-direnv", "environment editor", "setup wizard", "cat env",
        ),
        summary="The .txt file that defines every path CATIA uses for one installation, selected at launch with -env and -direnv.",
        menu="Start > Programs > CATIA > Tools > Environment Editor, or the CATIA shortcut's -env/-direnv arguments",
        fields=(
            "CATInstallPath -- the installation",
            "CATUserSettingPath -- where .CATSettings live, per user",
            "CATReferenceSettingPath -- site-locked settings, read before the user's",
            "CATStartupPath -- where document templates and standard start documents are found",
            "CATReffilesPath -- reference files (standards, feature dictionaries)",
            "CATCollectionStandard -- drafting and tolerancing standards",
            "CATDisciplinePath -- discipline resources (Piping, Electrical, Structure)",
            "CATDefaultCollectionStandard -- the standard used when none is named",
            "CATTemp -- scratch space; a full CATTemp fails saves and solves",
            "CATErrorLog -- where the error log is written",
            "CATDocView, CATFontPath, CATGraphicPath, CATMetaWordPath",
        ),
        failures=(
            "Two CATIA versions on one machine sharing a settings path corrupt each other's .CATSettings",
            "A CATTemp on a full or slow network drive makes CATIA appear to hang on save",
            "A CATReferenceSettingPath the user cannot read means locked settings silently do not apply",
        ),
        fixes=(
            "Give each installed release its own environment and its own CATUserSettingPath",
            "Point CATTemp at fast local disk with real free space",
        ),
        see_also=("setting.catsettings", "setting.command_line"),
    ),
    entry(
        "setting.catsettings",
        "CATSettings",
        _SET,
        aliases=(
            "catsettings", ".catsettings", "settings folder", "reset settings", "delete catsettings",
            "corrupt settings", "settings reset", "user settings", "admin mode settings",
        ),
        summary="The per-user binary files holding every Tools > Options choice, one file per settings page.",
        fields=(
            "Location -- the CATUserSettingPath in the environment file",
            "Admin mode (`CNEXT -admin`) writes into CATReferenceSettingPath and can lock a setting so users cannot change it",
            "A locked setting shows greyed in Tools > Options with a padlock",
        ),
        failures=(
            "CATIA behaves strangely -- toolbars in odd places, a workbench that will not open, a setting that will not stick -- and nothing in the model explains it",
            "Settings written by a newer release are not readable by an older one",
        ),
        fixes=(
            "Close CATIA, rename (do not delete) the CATSettings folder, and restart: CATIA rebuilds defaults. Renaming keeps the old one to copy specific files back from",
            "For a fleet, lock the settings that matter in admin mode rather than sending instructions",
        ),
        see_also=("setting.catia_environment", "diagnostic.strange_behaviour"),
    ),
    entry(
        "setting.command_line",
        "Command line switches",
        _SET,
        aliases=(
            "command line", "startup switches", "cnext", "catstart", "-batch", "-run",
            "-macro", "-object", "-nowindow", "-admin", "-direnv", "-env", "launch options",
        ),
        summary="How CATIA is started with a specific environment, in admin mode, or headlessly to run a macro.",
        fields=(
            "-env <name> and -direnv <path> -- which environment file to use",
            "-admin -- admin mode, for locking settings and editing standards",
            "-batch -- batch mode",
            "-run \"<script>\" -- run a macro at startup",
            "-macro <path> -- the macro to execute",
            "-object <document> -- open a document",
            "-nowindow -- no interface, for unattended runs",
        ),
        failures=(
            "-nowindow with a command that opens a dialog hangs forever with no output",
            "A macro run headlessly still needs a licence, and a licence server refusal looks like a silent failure",
        ),
        see_also=("api.modal_dialog", "batch_monitor"),
    ),
    entry(
        "setting.licensing",
        "Licensing (DSLS and LUM)",
        Kind.LICENCE,
        aliases=(
            "licensing", "licence", "license", "dsls", "lum", "nodelock", "catnodelockmgts", "licence server",
            "borrow a licence", "licence release timeout", "no licence available", "not licensed",
        ),
        summary="How CATIA acquires the right to run a product: node-locked, or from a DSLS/LUM network server.",
        menu="Tools > Options > General > Licensing",
        fields=(
            "Licence list -- every product trigram, and whether it is currently held",
            "Borrowing -- checking a licence out for offline use, with an expiry",
            "Release timeout -- how long an idle seat holds a licence before returning it to the pool",
            "CATNodelockMgtS -- the node-locked licence manager utility",
        ),
        failures=(
            "A workbench is missing from the Start menu -- the licence for it is not held, which looks identical to it not being installed",
            "Toolbar icons are greyed and nothing explains why -- again a licence, not a selection problem",
            "\"No licence available\" mid-session, because another user took the last one when this seat's timeout released it",
        ),
        fixes=(
            "Check Tools > Options > General > Licensing first for any \"the command is not there\" report; that is the fastest discriminator between missing licence and wrong workbench",
            "Raise the release timeout for seats that work in bursts, lower it for shared pools",
        ),
        see_also=("licence.tiers", "diagnostic.command_greyed_out"),
    ),
]


_UI = bulk(
    """
Specification Tree | specification tree, spec tree, tree, arbre de specifications, strukturbaum, albero delle specifiche, arbol, feature tree, model tree
Tree Overrun | tree overrun, graph tree, tree is greyed, tree not active, click on the tree branch, f3
Compass | compass, 3d compass, boussole, kompass, bussola, brujula, my compass is lost, privileged plane
Power Input | power input, power input line, command line in catia, type a command, c: prompt
Toolbar | toolbar, toolbars, barre d outils, werkzeugleiste, missing toolbar, restore position, chevron, double chevron
Customize | customize, tools customize, personnaliser, start menu favourites, user workbenches, keyboard accelerators
Named Views | named views, isometric, front back left right top bottom, vues nommees, standard views
Normal View | normal view, view normal to, vue normale
Fit All In | fit all in, zoom to fit, cadrer tout, zoom extents
Zoom Area | zoom area, window zoom, zoom sur une zone
Pan | pan, panoramique, middle mouse drag
Rotate | rotate the view, orbit, tourner la vue
Reframe On | reframe on, centre on, recentrer sur
Center Graph | center graph, centre the tree, centrer le graphe
Shading | shading, shaded, ombrage, solid display
Shading with Edges | shading with edges, shaded with edges, ombrage avec aretes
Shading with Material | shading with material, textured display
Wireframe | wireframe display, filaire, wire frame view
Dynamic Hidden Line Removal | dynamic hidden line removal, hlr, hidden line
Customize View Parameters | customize view parameters, view parameters, edges outlines transparency textures
Hide/Show | hide, show, hide show, cacher, afficher, ausblenden, nascondi, ocultar, swap visible space, no show
Graphic Properties | graphic properties, colour, color, transparency, line type, thickness, painter, proprietes graphiques
Layers | layer, layers, calque, layer filter, visualization filter
Selection Traps | rectangle selection, intersecting rectangle, polygon trap, free hand selection, outside rectangle, selection trap
Search | search, edit search, ctrl+f, rechercher, search by name, search by type, search by colour, search by parameter, favourites
Selection Sets | selection set, selection sets edition, jeu de selection
Edit Links | edit links, links, liens, broken links, pointed documents
Properties | properties, alt+enter, proprietes, product properties, mechanical properties, graphic properties tab
Measure Between | measure between, mesure entre, distance between two things, messen zwischen, misura tra, medir entre
Measure Item | measure item, measure a face, mesure d un element
Measure Inertia | measure inertia, mass properties, inertia, centre of gravity, cog
Update | update, mise a jour, aktualisieren, aggiorna, actualizar, red gear, update flag
Scenes | scenes, enhanced scenes, saved scene
New Window | new window, window tile, tile horizontally, tile vertically, multi viewport
Undo | undo, ctrl+z, annuler, undo stack size
Redo | redo, ctrl+y, retablir
What's This | what is this, shift+f1, contextual help
Help | help, f1, aide, documentation
Macros dialog shortcut | alt+f8, macro shortcut
""",
    kind=_SET,
    prefix="ui",
    toolbar="User interface",
)

_OPTIONS = bulk(
    """
Tools > Options | tools options, options, preferences, parametrage, einstellungen, where do i change the setting
General > Referenced Documents | referenced documents, search order, ordre de recherche, how catia finds a linked file
General > Undo stack | undo stack, stack size, number of undos
General > Data Save | data save, autosave, automatic backup, sauvegarde automatique
General > Memory Warning | memory warning, memory threshold
Display > Performance > 3D Accuracy | 3d accuracy, sag, fixed accuracy, proportional accuracy, curve accuracy ratio, why is my cylinder faceted, tessellation
Display > Performance > Level of Detail | level of detail, lod, static lod, dynamic lod, level of detail during moves
Display > Performance > Occlusion Culling | occlusion culling, pixel culling, culling
Display > Visualization | visualization, background colour, depth effect, anti-aliasing, ambient occlusion, highlight faces and edges, pre-selection navigator
Display > Navigation | navigation, mouse speed, examine mode, fly mode, animation, navigation speed
Display > Tree Appearance | tree appearance, tree manipulation, tree colour, tree type
Display > Linetype and Thickness | linetype, thickness, line thickness table, epaisseurs
Compatibility | compatibility, iges options, step options, stl options, dxf options, v4 v5 spaces, cgr options, 3d xml options
Parameters and Measure | parameters and measure, units, unites, decimal places, magnitudes, tolerances, real number display, change the units
Devices and Virtual Reality | devices and virtual reality, spacemouse, 3d device, vr
Infrastructure > Part Infrastructure | part infrastructure, hybrid design, default body, external references, keep link with selected object, display in specification tree
Infrastructure > Product Structure | product structure options, cache management, cgr generation, design mode, visualization mode, load referenced documents, work with the cache system
Infrastructure > Material Library | material library options, material catalogue path
Mechanical Design > Sketcher | sketcher options, grid, snap to point, create geometrical constraints, create dimensional constraints, solving mode, sketch plane orientation, allow direct manipulation, smartpick
Mechanical Design > Part Design | part design options, keep link with selected object, confirm when creating a geometrical feature, update behaviour
Mechanical Design > Assembly Design | assembly design options, update automatic manual, constraint creation defaults, paste behaviour
Mechanical Design > Drafting | drafting options, layout, generative view style, dimension generation, view axis, sheet auto-update, generate axis threads centrelines
Shape > Generative Shape Design | gsd options, keep link, create datum, shape options
Analysis & Simulation | analysis options, analysis and simulation settings, external storage
Digital Mockup | dmu options, clash options, section options, dmu cache
Equipment & Systems | equipment and systems options, discipline options
Machining | machining options, nc options, output options
Knowledgeware | knowledgeware options, load extended language libraries, all parameters in tree, all relations in tree, surface magnitude, volume magnitude, thickness magnitude
""",
    kind=_SET,
    prefix="setting",
    toolbar="Tools > Options",
)

_CACHE = [
    entry(
        "setting.cache_management",
        "Cache Management",
        _SET,
        aliases=(
            "cache", "cache management", "cgr cache", "visualization mode", "design mode",
            "gestion du cache", "large assembly", "assembly is slow to open", "lightweight",
        ),
        summary="Loads components as pre-tessellated .cgr pictures instead of full documents, which is what makes a large assembly openable.",
        menu="Tools > Options > Infrastructure > Product Structure > Cache Management",
        fields=(
            "Work with the cache system -- the master switch",
            "Local cache path -- put it on fast local disk, never a network share",
            "Maximum size -- when it fills, the least recently used CGRs are evicted and regenerated on demand",
            "Release path -- a shared, pre-generated cache the whole team reads",
            "Check timestamps -- whether a stale CGR is detected and regenerated",
        ),
        failures=(
            "Geometry looks coarse and measurements are approximate -- a CGR is a tessellation, and its accuracy is the sag it was generated at",
            "A clash check run in visualisation mode misses a shallow interference for the same reason",
            "The cache is on a network drive, and the assembly opens more slowly than without it",
            "Edits are impossible on a component in visualisation mode; it must be switched to design mode first",
        ),
        fixes=(
            "Review and navigate in visualisation mode; switch the components being worked on to design mode",
            "Run the final clash campaign in design mode, or regenerate CGRs at a finer sag",
            "Keep the local cache on local disk and let the release path be the shared one",
        ),
        see_also=("setting.3d_accuracy", "dmu_space_analysis.interference", "diagnostic.assembly_slow"),
    ),
    entry(
        "setting.3d_accuracy",
        "3D Accuracy (sag)",
        _SET,
        aliases=(
            "3d accuracy", "sag", "fleche", "tessellation accuracy", "faceted", "polygonal circle",
            "why does my cylinder look like a polygon", "curve accuracy ratio", "fixed accuracy", "proportional accuracy",
        ),
        summary="How far the displayed triangles are allowed to deviate from the true surface. Display only -- it changes nothing about the geometry itself.",
        menu="Tools > Options > General > Display > Performance",
        fields=(
            "Fixed accuracy -- one sag in millimetres for every object, regardless of size",
            "Proportional accuracy -- sag scales with the object's size, so small parts are not over-tessellated",
            "Curve accuracy ratio -- how curves are tessellated relative to surfaces",
            "3D accuracy applies to CGR generation too, which is why it propagates into the cache",
        ),
        failures=(
            "A cylinder displays as a visible polygon and users conclude the model is wrong -- it is not, the display sag is coarse",
            "Setting a very fine sag on a large assembly multiplies memory and makes navigation unusable",
            "CGRs generated at a coarse sag carry that coarseness into every downstream visualisation-mode measurement and clash check",
        ),
        fixes=(
            "Tighten it for a screenshot or a fine clash check, loosen it for navigation, and regenerate CGRs after changing it",
        ),
        see_also=("setting.cache_management", "format.cgr"),
    ),
]


_DISAMBIGUATIONS = [
    Disambiguation(
        term="update",
        aliases=("mise a jour", "refresh", "rebuild"),
        options=(
            "Update the model -- recompute out-of-date features (Edit > Update)",
            "Update a drawing -- regenerate views from the current 3D",
            "Update mode -- automatic or manual, in Tools > Options",
        ),
        guidance="A red gear on the tree means features are out of date. A drawing view that will not refresh is usually a broken link rather than an update-mode setting.",
    ),
    Disambiguation(
        term="accuracy",
        aliases=("sag", "tolerance", "precision"),
        options=(
            "3D Accuracy / sag -- display tessellation only, changes nothing in the geometry",
            "Model tolerance -- CATIA's internal resolution, 0.001 mm, not user-settable",
            "STL / CGR export sag -- the deviation of the exported triangles, which does change the exported file",
        ),
        guidance="Only the export sag changes data. The other two change what is on screen or how features are computed internally.",
    ),
]


ENTRIES = [*_STARTUP, *_UI, *_OPTIONS, *_CACHE]

SECTION = Section("platform", ENTRIES, _DISAMBIGUATIONS)

__all__ = ["ENTRIES", "SECTION"]
