#!/usr/bin/env bash
# Build the Chrono image Kryova's multibody runs use (master plan E9.1).
#
# There is no published PyChrono container to pull, which is why this exists at all:
# PyChrono ships through conda and nowhere else. `pip install pychrono` installs an
# unrelated timing utility and succeeds, so it must never appear in any requirements
# file -- see `app/dynamics/engine.ChronoEngine`'s docstring for the full finding.
#
# The image is the version pin. It is built once, deliberately, and never during a run:
# a run that installs its own solver has a version nobody chose.
#
# Usage:  scripts/chrono_image.sh [tag]
# Default tag matches `settings.chrono_image`.
set -euo pipefail

TAG="${1:-kryova-chrono:9.0.1}"
# `mambaorg/micromamba:1.5.10` with Python 3.12 is where pychrono 9.0.1 was measured to
# install on 2026-09-14. Pinned rather than `:latest` for the reason the tag is pinned
# everywhere else here -- a base that moves makes the image id move for no reason anyone
# recorded.
BASE="mambaorg/micromamba:1.5.10"
CHRONO_VERSION="9.0.1"
PYTHON_VERSION="3.12"

echo "Building ${TAG} from ${BASE} (pychrono ${CHRONO_VERSION}, python ${PYTHON_VERSION})."
echo "This installs from conda and takes several minutes the first time."

workdir="$(mktemp -d)"
trap 'rm -rf "${workdir}"' EXIT

cat > "${workdir}/Dockerfile" <<DOCKERFILE
FROM ${BASE}

# -c projectchrono is the project's own channel; conda-forge supplies its dependencies.
# --freeze-installed is deliberately NOT used: the base's own pins would otherwise fight
# the solve and produce an environment that imports and then fails at the first solve.
RUN micromamba install -y -n base \\
        -c projectchrono -c conda-forge \\
        python=${PYTHON_VERSION} pychrono=${CHRONO_VERSION} && \\
    micromamba clean --all --yes

# **Required, and its absence does not look like a PATH problem.** In this base image the
# base environment is activated by the image's own entrypoint, so \`docker run IMAGE python\`
# works while a bare \`RUN python\` in this Dockerfile does not: RUN bypasses the entrypoint
# unless this ARG is set. Measured 2026-09-16 -- a probe run as
# \`docker run ... bash -lc "... && python probe.py"\` came back with
# \`bash: line 1: python: command not found\` after a fourteen-minute conda solve, which
# reads as a broken image and is nothing of the kind.
ARG MAMBA_DOCKERFILE_ACTIVATE=1

# Fail the BUILD rather than the first run if the wheel is the PyPI impostor or the
# environment did not solve. An image that builds and cannot simulate is the worst of
# both: it passes every availability probe and refuses every job.
RUN python -c "import pychrono as c; assert hasattr(c, 'ChSystemNSC'), 'not Project Chrono'; print('pychrono', getattr(c, '__version__', 'unknown'), 'ok')"

# The entry point is NOT baked in: it is written into the work directory per run, so the
# translation that executes is the one in this repository at this commit, which is what a
# result's provenance has to be able to say.
DOCKERFILE

docker build -t "${TAG}" "${workdir}"

echo
echo "Built ${TAG}:"
docker image inspect --format '  id      {{.Id}}' "${TAG}"
docker image inspect --format '  size    {{.Size}} bytes' "${TAG}"
echo
echo "Kryova binds a result to that id, not to the tag, because a tag can be rebuilt."
