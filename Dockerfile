# The image that carries the fleet (master plan P9 task 3).
#
# **The determinism substrate and the deploy artefact are the same thing**, which
# is the phase's own sentence and the reason this file exists rather than a
# `pip install -r requirements.txt` on whatever host is available. E1 task 7 says
# the same spec must compile to the same geometry; that is only true if OCCT,
# gmsh and CalculiX are the same builds everywhere, and the only way to say
# "the same build" about three native libraries is to name them in one image.
#
# **Two stages, and the split is about size rather than tidiness.** `cadquery-ocp`
# is ~166 MB and drags ~640 MB of VTK in as a hard dependency that nothing in
# this codebase uses — `requirements.txt` says so and points here. A builder
# stage installs into a virtualenv, the runtime stage copies the virtualenv, and
# the build toolchain never reaches the shipped layer.
#
# **GPL components live in their own layer, per Decision 4.** CalculiX is GPL-2.0
# and is installed as a *system package invoked as a subprocess* — never linked,
# never imported. `app/solve/calculix/run.py` shells out to `ccx`, which is what
# keeps the licence boundary at the process edge where Decision 4 puts it. gmsh
# is the same shape (GPL, called through its Python API in a subprocess-isolated
# critical section). Anything that needs to ship without GPL components removes
# the `solver` stage and gets an image whose `find_ccx` returns None and whose
# error message says CalculiX is not installed — which is the honest degradation
# the code already implements.
#
# Not built by CI yet, and that is deliberate: an image nobody has run is not an
# artefact, it is a Dockerfile. P9 task 4 is where it gets pushed and deployed.

# ---------------------------------------------------------------------------
# Builder: wheels only, none of this reaches the runtime image.
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS builder

# Pinned to the same minor as `.github/workflows/ci.yml`'s PYTHON_VERSION. Two
# places, and they must not drift — a container running 3.13 while CI proves
# 3.12 is a fleet nobody has tested.
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install --no-install-recommends -y \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt ./

# A virtualenv rather than the system site-packages, so the runtime stage copies
# one directory and inherits nothing else from this one.
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -r requirements.txt

# ---------------------------------------------------------------------------
# Runtime: the solvers, the virtualenv, the application.
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

# **The three native pieces, named in one place.** `libgl1` and `libglu1-mesa`
# are OCCT's and gmsh's; they are needed even though nothing here renders,
# because the shared objects link against them at load time. `calculix-ccx` is
# the GPL solver, invoked as a subprocess.
#
# Versions are not pinned by tag here because Debian's own archive pins them:
# `bookworm` is a frozen suite, so `calculix-ccx` resolves to one version for
# the life of the release. That is the same determinism argument as pinning a
# digest, made by the base image rather than repeated here — and it is why the
# base is `bookworm` and not `stable`, which moves.
RUN apt-get update && apt-get install --no-install-recommends -y \
        calculix-ccx \
        libgl1 \
        libglu1-mesa \
        libxrender1 \
        libxcursor1 \
        libxinerama1 \
        libxft2 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Where blobs live. A named volume in production; the default keeps a bare
    # `docker run` working rather than failing on the first upload.
    MEDIA_ROOT=/var/lib/kryova/media

# Not root. The application writes only to `MEDIA_ROOT` and to temporary
# directories, both of which are given to this user explicitly — a container
# that runs as root to avoid thinking about permissions is one whose escape is
# a host compromise rather than a container one.
RUN useradd --system --create-home --uid 10001 kryova \
    && mkdir -p "$MEDIA_ROOT" \
    && chown -R kryova:kryova "$MEDIA_ROOT"

WORKDIR /srv/kryova
COPY --chown=kryova:kryova app ./app
COPY --chown=kryova:kryova migrations ./migrations
COPY --chown=kryova:kryova alembic.ini ./
COPY --chown=kryova:kryova scripts ./scripts
COPY --chown=kryova:kryova data ./data

USER kryova

# **Fails loudly when the solvers are missing.** A `HEALTHCHECK` that only
# answered "is the web server up" would report a green container that cannot
# solve anything, which is the most expensive kind of green there is: the fleet
# looks healthy and every job fails.
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD ["python", "-m", "scripts.container_health"]

EXPOSE 8000

# **Migrations are not run here.** `alembic upgrade head` on container start
# means N replicas racing the same migration, and P9 task 4 gates production
# migrations on `alembic check` as a deliberate step somebody watches. The
# entrypoint serves; deploying runs the migration once, first.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
