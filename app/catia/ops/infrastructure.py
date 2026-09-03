"""Documents, materials, transfer in and out, and the safety net.

Two things here that no other module has.

**Import.** The gap review found this the hardest commercial blocker: CATIA
reads STEP, IGES, STL, DXF, Parasolid, ACIS, JT, 3dxml and V4, and Kryova could
only write STEP. No customer's existing data could enter the product at all.
`catia_import` is the one operation that changes.

**The safety net.** `catia_checkpoint` and `catia_restore` are what make every
other write operation recoverable, which is why `restore` is the only
destructive tier in the whole vocabulary and needs a signed approval token that
a human click produces. It is deliberately hard to reach.
"""

from __future__ import annotations

from app.catia.ops import limits
from app.catia.ops import vocabulary as vocab
from app.catia.ops.spec import (
    Operation,
    Tier,
    Workbench,
    bounded_number,
    flag,
    for_server,
    from_server,
    new_name,
    one_of,
    optional,
    raw,
    required,
    text,
)
from app.solve.materials import MATERIALS

_WB = Workbench.INFRASTRUCTURE

#: The material library the agent may choose from, named rather than described
#: by density: the model picks a material, the server looks up what it weighs.
#: Sourced from the solver's own library so the two can never disagree about
#: what "steel-1018" means.
MATERIAL_KEYS = tuple(MATERIALS)

#: Formats CATIA can read. Each needs the corresponding Data Exchange licence
#: on the seat; the result says plainly when one is missing rather than
#: reporting a generic failure.
IMPORT_FORMATS = (
    "step", "iges", "stl", "dxf", "dwg", "parasolid", "acis",
    "jt", "3dxml", "vrml", "vda", "catpart", "catproduct", "v4model", "cgr",
)

#: Formats CATIA can write.
EXPORT_FORMATS = (
    "step", "iges", "stl", "dxf", "dwg", "parasolid", "vrml", "cgr", "3dxml", "pdf3d",
)

OPERATIONS: tuple[Operation, ...] = (
    # -- documents -----------------------------------------------------------
    Operation(
        name="catia_new_part",
        summary=(
            "Create a new empty part document and make it the active one.\n"
            "Start here for anything modelled from scratch. Everything that follows "
            "acts on this document until another is opened."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(required("name", new_name("A name for the part.")),),
    ),
    Operation(
        name="catia_open_document",
        summary=(
            "Reopen the document this conversation is working on.\n"
            "Use it when CATIA has been closed and reopened, or when a tool reports "
            "that no document is active. It restores the conversation's own document, "
            "not an arbitrary file — the model never names a path."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(),
        server_fields=("doc_name", "remote_path", "fallback_checkpoint"),
    ),
    Operation(
        name="catia_import",
        summary=(
            "Import a CAD file the user has uploaded into the current document.\n"
            "This is how existing customer data enters the product: a STEP file from a "
            "supplier, an IGES surface, a legacy CATIA V4 model, an STL to reverse "
            "engineer. Imported solids can be modelled on like any other geometry; "
            "imported surfaces usually need catia_healing before they will close.\n"
            "Each format needs its Data Exchange licence on this seat. When one is "
            "missing the result says which, so the answer is 'ask for that licence' "
            "rather than 'it did not work'."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("file", vocab.element_reference("The uploaded file to import, by name.")),
            optional("format", one_of(IMPORT_FORMATS, "Format, when the extension does not say.")),
            optional(
                "import_as",
                one_of(
                    ("solid", "surface", "wireframe", "reference"),
                    "How to bring the geometry in. Default solid where the file allows it.",
                ),
            ),
            optional("heal", flag("Run healing on import to close small gaps. Default true for surfaces.")),
            optional("scale", bounded_number("Scale the geometry on import.", minimum=0.001, maximum=1000.0)),
        ),
        server_fields=("remote_path", "content_hash"),
        long_running=True,
    ),
    Operation(
        name="catia_export",
        summary=(
            "Export the current document to a neutral or native CAD format.\n"
            "STEP AP214 is the right default for handing a solid to another CAD "
            "system; STL for printing or meshing; IGES only when the receiving system "
            "asks for it, since it carries surfaces rather than solids and loses the "
            "topology."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            optional("format", one_of(EXPORT_FORMATS, "Which format. Default step.")),
            optional("note", text("A note to record with the export.")),
            optional(
                "step_schema",
                one_of(("ap203", "ap214", "ap242"), "STEP application protocol. Default ap214."),
            ),
            optional("tolerance_mm", bounded_number("Tessellation tolerance, for STL and VRML.", minimum=0.001, maximum=10.0, unit="mm")),
            optional("binary", flag("Write STL as binary rather than ASCII. Default true.")),
        ),
        server_fields=("max_inline_bytes",),
        long_running=True,
    ),
    Operation(
        name="catia_export_step",
        summary=(
            "Export the part as a STEP file and attach it to the conversation.\n"
            "The way to hand the finished geometry to the user or to the simulation "
            "side. Equivalent to catia_export with format 'step'; kept because it is "
            "the common case and reads more directly."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(optional("note", text("A note to record with the export.")),),
        server_fields=("max_inline_bytes",),
        long_running=True,
    ),
    # -- material ------------------------------------------------------------
    Operation(
        name="catia_set_material",
        summary=(
            "Apply a material from Kryova's library to the part.\n"
            "This is what every reported mass is computed from, and what the simulation "
            "uses for stiffness and yield. Set it before measuring mass or running a "
            "load case, or both will describe a part made of CATIA's default 1000 kg/m³ "
            "nothing."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("material", one_of(MATERIAL_KEYS, "Which material from the library.")),
            # Never model-supplied: the server looks the density up so a confused
            # agent cannot quietly change what the part weighs.
            from_server(
                "density_kg_m3",
                raw(
                    {
                        "type": "number",
                        "exclusiveMinimum": 0,
                        "maximum": limits.MAX_DENSITY_KG_M3,
                        "description": "Density from Kryova's material library, in kg/m³.",
                    }
                ),
            ),
        ),
    ),
    # -- looking at it -------------------------------------------------------
    Operation(
        name="catia_capture_view",
        summary=(
            "Take a screenshot of the part from a standard viewpoint.\n"
            "The only way to actually see what was built. Take one after a run of "
            "edits: a feature that succeeded but produced the wrong shape looks "
            "identical to one that worked, in every other result."
        ),
        tier=Tier.READ,
        workbench=_WB,
        params=(
            optional("view", one_of(vocab.VIEWPOINTS, "Which viewpoint. Default iso.")),
            optional("label", text("A caption to store with the image.", maximum=120)),
            optional("fit", flag("Zoom to fit the whole part. Default true.")),
            optional(
                "mode",
                one_of(
                    ("shaded", "shaded_with_edges", "wireframe", "hidden_line"),
                    "Display mode. Default shaded_with_edges.",
                ),
            ),
        ),
        server_fields=("max_inline_bytes",),
    ),
    # -- the safety net ------------------------------------------------------
    Operation(
        name="catia_checkpoint",
        summary=(
            "Save the document's current state so it can be restored later.\n"
            "Taken automatically before every mutating operation, so this is only "
            "needed to mark a point worth naming — 'before I try the draft angle'. "
            "Label it so the restore list is readable."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(required("label", text("What this checkpoint represents.", maximum=200)),),
        server_fields=("max_inline_bytes",),
    ),
    Operation(
        name="catia_status",
        summary=(
            "Report whether a CATIA workstation is connected, which document is bound "
            "to this conversation, and what the bridge can do.\n"
            "Answered by the server without touching CATIA, so it is instant and safe. "
            "Call it first when anything is unexpectedly failing: 'no device online' "
            "and 'the operation failed' need very different replies to the user."
        ),
        tier=Tier.READ,
        workbench=_WB,
        params=(),
        # Never leaves the server; there is no device method behind it.
        server_only=True,
    ),
    Operation(
        name="catia_restore",
        summary=(
            "Roll the document back to a checkpoint, discarding everything since.\n"
            "Irreversible: the work after the checkpoint is gone. Needs the user's "
            "explicit approval, which they give by clicking — you cannot approve it "
            "yourself. Say plainly what will be lost before asking."
        ),
        tier=Tier.DESTRUCTIVE,
        workbench=_WB,
        params=(
            for_server(
                "checkpoint_id",
                raw(
                    {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 36,
                        "description": "Id of the checkpoint to roll back to.",
                    }
                ),
            ),
            for_server(
                "approval_token",
                raw(
                    {
                        "type": "string",
                        "maxLength": 512,
                        "description": (
                            "Server-signed token proving the user approved this exact "
                            "destructive call. Supplied by the interface; never guessed."
                        ),
                    }
                ),
            ),
        ),
        # The model's own two arguments are consumed by the server, which
        # resolves them into the checkpoint payload the daemon actually needs.
        server_fields=("checkpoint",),
        # A checkpoint taken immediately before a restore would capture the
        # state the user is trying to leave, and offering it back as a recovery
        # point is a false reassurance.
        no_auto_checkpoint=True,
    ),
)
