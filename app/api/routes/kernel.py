"""Looking at the part the agent is building.

Step 2 of the integration gap found on 2026-09-05: `app/render/` renders eight
canonical views deterministically, cuts sections and diffs two of them, and until
this module existed **nothing outside a test ever called it**. Same for
`app/kernel/measurement`. The capability was green on the board and unreachable
from the product, which is a different thing from being finished.

Three endpoints, all about the part a *conversation* owns:

* `GET .../render` — a PNG of the current state, from any canonical view.
* `GET .../measure` — what the kernel measures on it, with provenance.
* `POST .../requirements` — a `.kreq` document checked against it, with coverage
  and per-requirement evidence (added 2026-09-09, the same gap one layer up:
  `app/requirements/` could verify a specification against a measurement payload
  and nothing outside a test had ever handed it one).
* `POST .../rules` — the design rules a manufacturing process owes this part,
  attached from the features it was built with and checked against the part's own
  measurements and scans (added 2026-09-15, E13.1: `app/rules/engine.py` had no
  consumer anywhere in `app/`).

**These serve the open-kernel backend only, and say so rather than guessing.** On
`GEOMETRY_BACKEND=catia` the part lives on the workstation, not in this process;
producing a picture of it means asking the seat for a screenshot, which is a
different mechanism (and a different fidelity) from HLR projection. Answering with
a plausible image built from something else would be worse than refusing, so this
refuses, names the backend it needs, and stays honest about which kernel drew what.

The render's own digest is the ETag. That is not a trick: 4.1 makes the bytes a
deterministic function of the geometry, so "the same part renders to the same
bytes" is exactly the guarantee an ETag needs, and a browser polling this while an
agent works gets a 304 until the part actually changes.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Annotated, Any, Final

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, DbSession
from app.geometry import backends
from app.models import Conversation, User
from app.render.views import ALL_VIEWS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/kernel", tags=["kernel"])

#: Canvas bounds for a requested render. The floor keeps a request from producing
#: an image too small to read; the ceiling keeps one from asking this process to
#: allocate a poster. Both are generous — the defaults sit well inside them.
MIN_PIXELS = 128
MAX_PIXELS = 4096


def _owned_conversation(db: Session, user: User, conversation_id: str) -> Conversation:
    """The caller's conversation, or 404.

    404 and never 403, like every other resource here, so ids cannot be
    enumerated across accounts.
    """
    conversation = db.get(Conversation, conversation_id)
    if conversation is None or conversation.owner_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
        )
    return conversation


def _live_runner(conversation_id: str) -> Any:
    """The runner holding this conversation's part, or an explained refusal.

    The *runner* rather than the document, because `.../measure/between` drives
    the agent's own operations through it — same handler, same argument
    refusals, same registry. `_live_document` narrows this to the document for
    the two callers that only want the shape.

    Three distinct 409s rather than one, because the remedies are different and
    the user has to be able to tell them apart: the wrong backend is a setting,
    an evicted document means starting the part again, and nothing built yet
    simply means asking the agent for something first.
    """
    if not backends.is_local():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This part is being built on a CATIA seat, so there is nothing in this "
                "process to draw. Rendering here serves the open kernel; set "
                "GEOMETRY_BACKEND=occt to build in-process, or take a screenshot on the "
                "workstation."
            ),
        )
    if backends.was_evicted(conversation_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "The part this conversation was building is no longer in memory — too "
                "many documents were open at once and this one was closed. Nothing was "
                "saved. Ask the agent to build it again."
            ),
        )

    runner = backends.peek_session(conversation_id)
    document = getattr(runner, "document", None) if runner is not None else None
    if document is None or getattr(document, "shape", None) is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Nothing has been built in this conversation yet. Ask for a part first — "
                "for example, a 60 by 40 by 20 plate."
            ),
        )
    return runner


#: Pull direction as this route's clients give it -> the origin plane
#: `catia_analysis_part` declares. **The tool takes a plane name, not a vector**:
#: its registry schema is `vocab.origin_plane`, because a CATIA user says "pulled
#: off the XY plane". This route's own 422 teaches a vector (`[0, 0, 1]`), which
#: is the right vocabulary for an API — a pull direction is a tool axis — so the
#: translation belongs here, in the adapter between the two.
#:
#: **It was not here until 2026-09-17, and the consequence was silent.** The
#: route sent the vector straight through, `_pull_direction` refused it as "not
#: a pull direction", the broad handler below turned that into "The draft scan
#: failed, so its rules are unmeasured", and **every draft and undercut rule on
#: every part came back `unmeasured`** with a note nobody had a reason to
#: disbelieve. `tests/test_kernel_routes.py` caught it the first time it ran.
#:
#: Spelled here rather than imported for `app/assembly/inertia.py`'s reason —
#: importing `app.kernel.occt.operations.inspection` pulls OCP into a route
#: module — and held equal to that module's `_PULL_NORMALS` by a test, so the
#: two cannot drift.
_PULL_PLANES: Final[dict[tuple[float, float, float], str]] = {
    (0.0, 0.0, 1.0): "XY",
    (1.0, 0.0, 0.0): "YZ",
    (0.0, 1.0, 0.0): "ZX",
}


def _pull_plane(direction: Sequence[float] | None) -> str:
    """The origin plane whose normal is `direction`, or an explained refusal.

    **Only the three positive axes, and the refusal says so rather than
    guessing.** `catia_analysis_part` analyses draft against an origin plane's
    normal, so a pull along −Z or along [1, 1, 0] is a question it cannot be
    asked — and answering the +Z question instead would report a plausible
    number for the wrong direction, which is the failure `_PULL_NORMALS`'
    own comment exists to prevent. A 400 naming what can be asked is the honest
    answer; widening the kernel to take a vector is a change to the operation
    schema the CATIA daemon also reads, and is recorded in THE QUEUE rather than
    made blind.
    """
    if direction is None:
        return "XY"
    key = tuple(float(v) for v in direction)
    plane = _PULL_PLANES.get(key)  # type: ignore[arg-type]
    if plane is None:
        allowed = ", ".join(
            f"{list(vector)} ({name})" for vector, name in _PULL_PLANES.items()
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Draft and undercut are analysed against an origin plane's normal, so "
                f"pull_direction must be one of: {allowed}. {list(key)} is not one of "
                "them — a pull along a negative axis or an arbitrary vector is not "
                "something this analysis can be asked yet."
            ),
        )
    return plane


def _live_document(conversation_id: str) -> Any:
    """The `PartDocument` this conversation has built, or an explained refusal.

    The document rather than the bare shape, because the two callers want
    different things from it — a render wants `.shape`, a measurement wants
    `.measure()`, and the document is what owns the cache behind the second.
    """
    return _live_runner(conversation_id).document


@router.get(
    "/conversations/{conversation_id}/render",
    responses={200: {"content": {"image/png": {}}}},
    response_class=Response,
)
def render_conversation_part(
    db: DbSession,
    current_user: CurrentUser,
    conversation_id: str,
    view: Annotated[str, Query(description=f"One of: {', '.join(ALL_VIEWS)}")] = "iso",
    width: Annotated[int, Query(ge=MIN_PIXELS, le=MAX_PIXELS)] = 1024,
    height: Annotated[int, Query(ge=MIN_PIXELS, le=MAX_PIXELS)] = 768,
    section: Annotated[
        str | None,
        Query(description="Cut through the middle of an axis before drawing: x, y or z."),
    ] = None,
) -> Response:
    """A PNG of the part this conversation is building.

    `section` cuts the part in half through the middle of the named axis and draws
    the cut face hatched, which is the only way to see an internal feature in a
    wireframe — a bore reads as two dashed lines and a pocket reads as nothing.
    """
    _owned_conversation(db, current_user, conversation_id)
    shape = _live_document(conversation_id).shape

    from app.render import render
    from app.render.section import SectionError, mid_section, render_section
    from app.render.views import view_named

    try:
        camera = view_named(view)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        if section:
            cut = mid_section(shape, section.strip().lower())  # type: ignore[arg-type]
            shot = render_section(shape, cut, camera, width=width, height=height)
        else:
            shot = render(shape, camera, width=width, height=height)
    except SectionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - a kernel fault must not 500 the viewer
        logger.exception("Rendering failed for conversation %s", conversation_id)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"The part could not be drawn: {exc}",
        ) from exc

    return Response(
        content=shot.png,
        media_type="image/png",
        headers={
            # Deterministic bytes make this a real content hash, so a client
            # polling during a build gets 304s until the geometry actually moves.
            "ETag": f'"{shot.digest}"',
            # Never cached by age: the part changes when the agent acts, not on a
            # timer, and a stale picture of a part is worse than a slow one.
            "Cache-Control": "no-cache",
            "X-Kryova-View": shot.view,
            "X-Kryova-Blank": "1" if shot.is_blank else "0",
        },
    )


@router.get("/conversations/{conversation_id}/measure")
def measure_conversation_part(
    db: DbSession,
    current_user: CurrentUser,
    conversation_id: str,
    detail: Annotated[
        str, Query(description="How much to measure: shape, bounds, full or inertia.")
    ] = "full",
) -> dict[str, Any]:
    """What the kernel measures on the part this conversation is building.

    Carries the provenance sidecar, so a caller can tell an integrated mass from a
    ray-cast wall thickness. `Detail` exists for latency rather than for taste —
    the full set integrates over the whole shape.
    """
    _owned_conversation(db, current_user, conversation_id)
    document = _live_document(conversation_id)

    from app.kernel.measurement import Detail

    try:
        level = Detail(detail.strip().lower())
    except ValueError as exc:
        allowed = ", ".join(one.value for one in Detail)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{detail!r} is not a detail level. Use one of: {allowed}.",
        ) from exc

    try:
        payload = document.measure(detail=level)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Measuring failed for conversation %s", conversation_id)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"The part could not be measured: {exc}",
        ) from exc

    return {
        "backend": backends.selected_backend(),
        "backend_version": backends.backend_version(),
        "detail": level.value,
        "measurements": payload,
    }


#: What `GET .../measure/between` will pass through to the operation. Declared
#: here as well as in `inspection` because the route's 400 must list what a
#: caller may ask for, and importing the private set to build an error message
#: would couple the route to a name the operations package does not export.
BETWEEN_KINDS: tuple[str, ...] = ("minimum_distance", "closest_points", "angle")


@router.get("/conversations/{conversation_id}/measure/between")
def measure_between_elements(
    db: DbSession,
    current_user: CurrentUser,
    conversation_id: str,
    first: Annotated[
        str, Query(min_length=1, max_length=200, description="The first element, by name.")
    ],
    second: Annotated[
        str, Query(min_length=1, max_length=200, description="The second element, by name.")
    ],
    kind: Annotated[
        str, Query(description=f"One of: {', '.join(BETWEEN_KINDS)}.")
    ] = "minimum_distance",
) -> dict[str, Any]:
    """Measure between two named elements of the part — **against the real B-rep**.

    P6.4's measure interaction. The viewer streams a *decimated* mesh (P6.2 picks
    the level from screen size), so a distance computed in the browser is a
    distance between triangles somebody chose for looking at, not between the
    faces the part has. It would be wrong by the chord error and it would change
    when the camera moved, which is the worst available shape for a number an
    engineer writes down. So the picked elements come back here and the answer is
    measured on the geometry.

    **This adds no measurer.** It calls the live runner with
    `catia_measure_between` — the operation the agent already has
    (`app/kernel/occt/operations/inspection.py`), behind
    `BRepExtrema_DistShapeShape` and a real boolean for the overlap. The route is
    a second *caller*, not a second implementation, which is the difference
    between this and the thing that would rot: two measurers agreeing today and
    disagreeing after a fix to one.

    A `GET` because it is a read. The operation is not in `RECORDED`, so it
    cannot journal a step into the part, and a viewer may poll it while the user
    drags a selection.

    Element names are the same vocabulary the agent uses — `bore`,
    `Pad.1#top`, a bare face word like `top`. What P6.4 still needs and this does
    not give it is the *other* direction: turning a click on a triangle into one
    of those names. That is E2 task 1's face predicate, and it is recorded in the
    status line rather than faked here.
    """
    _owned_conversation(db, current_user, conversation_id)
    runner = _live_runner(conversation_id)

    wanted = kind.strip().lower()
    if wanted not in BETWEEN_KINDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"{kind!r} is not a measurement this takes. "
                f"Use one of: {', '.join(BETWEEN_KINDS)}."
            ),
        )

    from app.kernel.errors import KernelError

    try:
        payload = runner(
            "catia_measure_between", {"elements": [first, second], "kind": wanted}
        )
    except KernelError as exc:
        # The kernel's own words, which name the element and say what to do: an
        # unresolvable name is the caller's mistake and is 400, and everything
        # else ran and produced nothing usable.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except Exception as exc:  # noqa: BLE001 - a kernel fault must not 500 the viewer
        logger.exception("Measuring between elements failed for %s", conversation_id)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Those two elements could not be measured: {exc}",
        ) from exc

    return {
        "backend": backends.selected_backend(),
        "backend_version": backends.backend_version(),
        "measurement": dict(payload),
    }


@router.get("/conversations/{conversation_id}/measure/element")
def measure_one_element(
    db: DbSession,
    current_user: CurrentUser,
    conversation_id: str,
    element: Annotated[
        str, Query(min_length=1, max_length=200, description="The element, by name.")
    ],
) -> dict[str, Any]:
    """Measure one named element — the length of an edge, the area of a face.

    The single-pick half of P6.4's measure interaction, and `catia_measure_item`
    is the operation, for the reason above. The payload says which *kind* of
    thing it found, which is what makes an unexpected answer traceable: asking
    for the diameter of something that turns out to be a planar face returns an
    area and the word `Plane`, not a silence and not a zero.
    """
    _owned_conversation(db, current_user, conversation_id)
    runner = _live_runner(conversation_id)

    from app.kernel.errors import KernelError

    try:
        payload = runner("catia_measure_item", {"element": element})
    except KernelError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Measuring an element failed for %s", conversation_id)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"That element could not be measured: {exc}",
        ) from exc

    return {
        "backend": backends.selected_backend(),
        "backend_version": backends.backend_version(),
        "measurement": dict(payload),
    }


@router.get("/conversations/{conversation_id}/selection/face")
def name_a_picked_face(
    db: DbSession,
    current_user: CurrentUser,
    conversation_id: str,
    face: Annotated[
        int | None,
        Query(ge=0, description="A face ordinal, from the display mesh's partition."),
    ] = None,
    triangle: Annotated[
        int | None,
        Query(ge=0, description="A triangle index, when the pick came from the mesh."),
    ] = None,
    level: Annotated[
        int, Query(ge=0, le=2, description="Which display level the triangle indexes.")
    ] = 0,
) -> dict[str, Any]:
    """What to call the face the user just clicked — master plan P6.6.

    The 3D → name direction. `GET .../measure/element` takes an element *name*
    and there was no way to get one from a pick; this is that way, and it is the
    reason P6.4's measure could not be reached from the viewer.

    **It offers, it does not decide.** Several predicates can name one face and
    the choice is the user's, so the payload is a ranked list. `best` is the one
    to preselect and is **null** when nothing describes the face by what it is —
    two identical bores being the everyday case. A client must show that rather
    than quietly using `positional`, which names a region of space and stops
    being true when the part is resized.

    Either `face` or `triangle` — a triangle is what a viewer actually has, and
    `level` says which mesh it indexed, because the partition differs per level.
    """
    _owned_conversation(db, current_user, conversation_id)
    document = _live_document(conversation_id)

    if (face is None) == (triangle is None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Give exactly one of 'face' (an ordinal) or 'triangle' (a pick from "
                "the display mesh). Passing both would let them disagree."
            ),
        )

    from app.kernel.errors import KernelError
    from app.kernel.occt.propose import propose_face
    from app.render import display

    shape = document.shape
    try:
        if triangle is not None:
            # Through `display.display_mesh`, which is the same call that built the GLB
            # the client is holding -- see its docstring for why a second tessellation
            # here would land the pick on the wrong face without erroring.
            mesh, _, _ = display.display_mesh(shape, display.level(level))
            ordinal = mesh.face_of(triangle)
        else:
            ordinal = int(face)  # type: ignore[arg-type]
        proposal = propose_face(shape, ordinal, document=document)
    except KernelError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Naming a picked face failed for %s", conversation_id)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"That face could not be named: {exc}",
        ) from exc

    def _render(item: Any) -> dict[str, Any]:
        return {
            "argument": item.as_argument(),
            "words": item.words,
            "matches": item.matches,
            "stable": item.stable,
        }

    return {
        "backend": backends.selected_backend(),
        "face": proposal.face_index,
        "best": _render(proposal.best) if proposal.best is not None else None,
        "positional": (
            _render(proposal.positional) if proposal.positional is not None else None
        ),
        "offered": [_render(item) for item in proposal.offered],
        "explanation": proposal.describe(),
    }


def _live_assembly(conversation_id: str) -> Any:
    """The assembly this conversation is composing, or an explained refusal.

    Deliberately not `_live_runner`: that one refuses when no *part* is open, and a
    conversation with a finished assembly has **no part open at all**. Taking a
    component hands the document to the assembly and leaves the context empty on purpose
    (`assembly_ops`'s docstring says why), so routing an assembly request through the
    part's guard would refuse every assembly that had been assembled.
    """
    if not backends.is_local():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This assembly is being built on a CATIA seat, so there is nothing in "
                "this process to draw. Set GEOMETRY_BACKEND=occt to build in-process."
            ),
        )
    if backends.was_evicted(conversation_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "The assembly this conversation was composing is no longer in memory — "
                "too many documents were open at once and this one was closed. Nothing "
                "was saved. Ask the agent to build it again."
            ),
        )
    runner = backends.peek_session(conversation_id)
    assembly = getattr(runner, "assembly", None) if runner is not None else None
    if assembly is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "No assembly has been started in this conversation. Ask the agent for "
                "one first — it records each part as a component and places instances "
                "of them."
            ),
        )
    return assembly


@router.get(
    "/conversations/{conversation_id}/assembly/scene",
    responses={200: {"content": {"model/gltf-binary": {}}}},
    response_class=Response,
)
def assembly_scene(
    db: DbSession,
    current_user: CurrentUser,
    conversation_id: str,
    level: Annotated[int, Query(ge=0, description="0 is the finest level.")] = 1,
) -> Response:
    """The whole assembly as one GLB scene, at one display level (P6.1).

    The half of P6.1 that `scene_for` could do and no route offered: a product structure
    drawn as one file, each leaf component tessellated once and each occurrence a node
    on it. **Forty bolts are one bolt's bytes** — that is the instancing the task asks
    for, and it falls out of the glTF node graph rather than being extracted afterwards.

    **The level means exactly what it means for a part, and that is a decision.** Each
    component is tessellated by `display.display_mesh` against *its own* bounding-box
    diagonal, so a bolt at level 1 is as smooth relative to itself as the frame is. The
    alternative — deflecting everything against the *assembly's* diagonal — would make a
    5 mm bolt in a 3 m machine coarser than the bolt, which is not a level of detail but
    a different part. The consequence is stated rather than hidden: **an assembly GLB at
    level N is the union of its distinct components at level N**, so its triangle count
    grows with how many *different* parts there are, and this route does not make a
    2,000-part machine cheap. Choosing a different level per component, by how much of
    the screen it covers, is P6.2's streaming question and lives in the client.
    `X-Assembly-Triangles` is here so a caller can see the number rather than infer it.

    **Nothing is cached, and that is also deliberate.** The part route keys its GLB on
    the stored file's sha256; an assembly held in memory has no stored bytes to key on,
    and a key computed from the meshes would cost the tessellation it was meant to save.
    A cache arrives with persistence, which `assembly_ops` records as needing a model and
    a migration.
    """
    from app.kernel.errors import KernelError
    from app.kernel.occt.operations import assembly_ops
    from app.render import display
    from app.render.gltf import GltfError, scene_for, write_glb

    _owned_conversation(db, current_user, conversation_id)
    state = _live_assembly(conversation_id)

    try:
        definition = display.level(level)
    except KernelError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    try:
        structure, shapes = assembly_ops.product_of(state, "the assembly scene")
    except Exception as exc:  # noqa: BLE001 -- GeometryError and StructureError alike
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc

    meshes: dict[str, Any] = {}
    triangles = 0
    try:
        for name, shape in shapes.items():
            mesh, _diagonal, _linear = display.display_mesh(shape, definition)
            meshes[name] = mesh
            triangles += mesh.triangle_count
        scene = scene_for(structure, meshes)
        data = write_glb(scene)
    except GltfError as exc:
        # The one that matters: a component with no geometry. `scene_for` names it, and
        # its message already says why a machine drawn without it is worse than none.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Building an assembly scene failed for %s", conversation_id)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"That assembly could not be drawn: {exc}",
        ) from exc

    return Response(
        content=data,
        media_type=display.GLB_CONTENT_TYPE,
        headers={
            "X-Display-Level": str(level),
            "X-Assembly-Name": structure.root,
            "X-Assembly-Components": str(len(meshes)),
            "X-Assembly-Occurrences": str(len(scene.placements)),
            "X-Assembly-Triangles": str(triangles),
        },
    )


class RequirementCheck(BaseModel):
    """A requirements document to check the conversation's part against.

    The document is `.kreq` text, the format `app.requirements.parse` reads — the
    thing an engineer writes, rather than a JSON shape a client would have to
    build. It arrives in the request rather than being stored because a
    requirement set belongs to a project and this endpoint answers a narrower
    question: *does the part on the screen right now meet these*.
    """

    document: str = Field(
        min_length=1,
        max_length=200_000,
        description="A .kreq requirements document, as written.",
        examples=[
            "REQ-001: The bracket shall weigh no more than 1.5 kg.\n"
            "  measure: mass_kg <= 1.5\n"
            "  source: customer\n"
        ],
    )
    name: str = Field(
        default="requirements",
        max_length=200,
        description="What to report the set under.",
    )
    detail: str = Field(
        default="full",
        description="How much to measure before checking: shape, bounds, full or inertia.",
    )


@router.post("/conversations/{conversation_id}/requirements")
def check_conversation_requirements(
    db: DbSession,
    current_user: CurrentUser,
    conversation_id: str,
    body: RequirementCheck,
) -> dict[str, Any]:
    """Check a requirements document against the part this conversation has built.

    The third step of the same integration gap `render` and `measure` closed:
    `app/requirements/` could read a document, compile every requirement into an
    assertion, verify a set against a payload and report coverage — and **nothing
    outside a test had ever called it**, so an engineer could not hand the product
    a specification and be told whether the part met it. This is that call.

    It measures the part here rather than taking numbers from the caller, for the
    reason the whole package exists: a requirement verified against a payload the
    client assembled is a claim about the client. The measurement carries its
    provenance sidecar, so the report can say per requirement whether the number
    behind it was integrated or sampled — which is half of what 11.4 means by
    *by what evidence*.

    Three refusals, and they are different problems with different fixes: a
    document that does not parse comes back 422 with **every** problem listed at
    once (an engineer's document usually has several, and a parser that stopped at
    the first would turn one editing session into five); a set that needs a scan
    nobody ran is reported per requirement as `UNMEASURED`, never as a pass, with
    `scans_needed` naming what to run; and a conversation with no part yet is the
    same 409 the other two endpoints give.
    """
    _owned_conversation(db, current_user, conversation_id)
    document = _live_document(conversation_id)

    from app.kernel.measurement import Detail
    from app.requirements import parse_requirements, verify_requirements
    from app.requirements.errors import RequirementError

    try:
        level = Detail(body.detail.strip().lower())
    except ValueError as exc:
        allowed = ", ".join(one.value for one in Detail)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{body.detail!r} is not a detail level. Use one of: {allowed}.",
        ) from exc

    parsed = parse_requirements(body.document, name=body.name, origin="conversation")
    try:
        requirements = parsed.require()
    except RequirementError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    try:
        measurements = document.measure(detail=level)
    except Exception as exc:  # noqa: BLE001 - a kernel fault must not 500 the check
        logger.exception("Measuring failed for conversation %s", conversation_id)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"The part could not be measured: {exc}",
        ) from exc

    report = verify_requirements(
        requirements,
        measurements,
        bound_to={
            "backend": backends.selected_backend(),
            "backend_version": backends.backend_version(),
            "conversation": conversation_id,
            "detail": level.value,
        },
    )
    return {
        "report": report.to_dict(),
        "summary": report.summary(),
        # What the caller would have to run for the still-unmeasured ones to be
        # answerable. Empty is the normal answer and does not mean everything was
        # measured — the per-requirement outcomes say that.
        "scans_needed": list(requirements.scans_needed()),
    }


class LimitIn(BaseModel):
    value: float
    source: str = Field(min_length=1, max_length=500)


class RuleCheck(BaseModel):
    """A process and the limits its rule set needs, each with the guide it came from.

    No limit has a default. A rule the process needs and the request does not give a
    limit for is reported as unset, and the answer is then not ok.
    """

    process: str = Field(description="cast, machined, printed, sheet, moulded or welded")
    limits: dict[str, LimitIn] = Field(default_factory=dict)
    pull_direction: tuple[float, float, float] | None = Field(
        default=None,
        description="The tool's pull or spindle direction; needed when a draft or undercut rule attaches.",
    )
    detail: str = Field(default="full")


@router.post("/conversations/{conversation_id}/rules")
def check_conversation_rules(
    db: DbSession,
    current_user: CurrentUser,
    conversation_id: str,
    body: RuleCheck,
) -> dict[str, Any]:
    """Check the part against the design rules its manufacturing process owes (E13.1).

    The rules are attached from the process and the feature tools the part was built
    with (`app.rules.processes.attach`), measured here, and the scans they need
    (wall thickness, draft, curvature) are run here too, so a wall rule is not left
    unmeasured because nobody knew to scan. A scan that fails leaves its rules
    `UNMEASURED` with the reason in `notes`, never a pass. An unknown process or a
    limit no rule reads is 422; a draft rule with no pull direction is 422 before
    anything is measured.
    """
    _owned_conversation(db, current_user, conversation_id)
    document = _live_document(conversation_id)

    from app.kernel.measurement import Detail
    from app.kernel.provenance import PROVENANCE_KEY
    from app.rules.errors import RuleError
    from app.rules.processes import Limit, attach, check

    try:
        level = Detail(body.detail.strip().lower())
    except ValueError as exc:
        allowed = ", ".join(one.value for one in Detail)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{body.detail!r} is not a detail level. Use one of: {allowed}.",
        ) from exc

    try:
        attachment = attach(
            body.process,
            [feature.tool for feature in document],
            {key: Limit(value=one.value, source=one.source) for key, one in body.limits.items()},
        )
    except RuleError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    scans = attachment.scans_needed()
    if "draft" in scans and body.pull_direction is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"The {attachment.process} rules include draft or undercut, which are "
                "measured against the direction the tool pulls. Give pull_direction, "
                "e.g. [0, 0, 1] for a tool that opens along +Z."
            ),
        )

    try:
        measurements = dict(document.measure(detail=level))
    except Exception as exc:  # noqa: BLE001 - a kernel fault must not 500 the check
        logger.exception("Measuring failed for conversation %s", conversation_id)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"The part could not be measured: {exc}",
        ) from exc

    notes: list[str] = []
    runner = backends.peek_session(conversation_id)
    for kind in scans:
        if runner is None:
            # `_live_document` above proved a document existed, but a session can
            # still be evicted between that call and this one — and calling None
            # would arrive in the handler below as "'NoneType' object is not
            # callable", a note that tells the reader nothing about what to do.
            # Both sibling call sites in this module guard and this one did not;
            # found by running mypy on Windows for the first time, 2026-09-17.
            notes.append(
                f"The {kind} scan could not run: the part left memory between being "
                "measured and being scanned, so its rules are unmeasured. Ask for the "
                "part again."
            )
            continue
        arguments: dict[str, Any] = {"kind": kind}
        if kind == "draft":
            arguments["direction"] = _pull_plane(body.pull_direction)
        try:
            scanned = dict(runner("catia_analysis_part", arguments))
        except Exception as exc:  # noqa: BLE001 - a failed scan leaves its rules unmeasured
            notes.append(f"The {kind} scan failed, so its rules are unmeasured: {exc}")
            continue
        sidecar = scanned.pop(PROVENANCE_KEY, None)
        measurements.update(scanned)
        if isinstance(sidecar, dict):
            # A new dict, not an update in place: the base payload's sidecar may be
            # the document's cached one.
            measurements[PROVENANCE_KEY] = {**measurements.get(PROVENANCE_KEY, {}), **sidecar}

    answer = check(attachment, measurements).to_dict()
    answer["notes"] = notes
    return answer


__all__ = ["router"]
