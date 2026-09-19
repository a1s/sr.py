#!/bin/sh
# Run the test suite inside a Debian container.
#
# Font enumeration is the one part of this engine whose answer depends on
# the machine, and doc/template.md names three platforms.  Everything
# about building the face table is tested against fixture directories and
# runs anywhere, but the per-platform code -- which directories, read how
# -- can only be checked by running it somewhere that has them.
#
# This is what does that for the specification's "Linux" row.  That row
# is really a row about fontconfig and about distributions: the kernel
# supplies none of it, and where a font lives and what the package that
# put it there is called differ between one distribution and the next.
# Debian is what is run here because `python:*-slim` is Debian and the
# run therefore costs one image.
#
# Alpine has been checked once by hand, to find out how much of what
# passes here is Debian's rather than the row's.  It all held on musl.
# The one difference is the one that matters: DejaVu is at
# `/usr/share/fonts/dejavu` there and `/usr/share/fonts/truetype/dejavu`
# here, which is why this row is walked recursively rather than as
# a list of directories.  It is not in the suite, deliberately -- one
# distribution in CI and a note about the other is the trade that was
# made.
#
# The image is deliberately a stock one rather than a tuned one: a
# machine nobody prepared is the machine whose font setup is worth
# finding out about.
#
# Run it from the repository root, or through `make test-linux`.
set -eu

# The interpreter is pinned to the floor `pyproject.toml` declares,
# for two reasons.  A run here is meant to say something about the platform,
# so holding everything else still is what makes a failure attributable;
# and `requires-python = ">=3.11"` is a promise that nothing else checks,
# since the development machine runs a later one and ruff and mypy only
# check the syntax and the types against 3.11 rather than the behaviour.
#
# Override it to test another: `SR_LINUX_IMAGE=python:3.14-slim`,
# or `make test-linux-matrix` for the whole supported range.
IMAGE="${SR_LINUX_IMAGE:-python:3.11-slim}"

# Debian package names.  fontconfig for the generic-family query the
# substitute step makes; dejavu and liberation because between them
# they carry the candidates the alias table reaches once Arial is absent;
# xfonts-base because its 480 `.pcf.gz` files are what the
# classify-and-skip rule is for.  Another distribution spells all four
# differently, which is the point being made above.
PACKAGES="fontconfig fonts-dejavu-core fonts-liberation2 xfonts-base"

# ckdl is a C extension and the rest are pure enough; the engine's
# own dependencies, minus the ones only the renderer needs.
WHEELS="ckdl fonttools cbor2 pillow qrcode tzdata pytest"

# Docker Desktop on Windows is a Windows program and the shell that
# runs this script there is not, so `pwd` answers something Docker
# cannot mount: `/cygdrive/c/src/sr.py` under Cygwin, `/c/src/sr.py`
# under Git Bash.  What Docker did with such a path was worse than
# refusing it.  The mount arrived as an empty directory, the copy
# below succeeded, and pytest reported that no tests ran.
# `cygpath -m` gives the Windows path with forward slashes, which
# Docker and the shell both accept, and the two variables stop Git
# Bash from rewriting the argument on its way to a native program.
SOURCE="$(pwd)"
if command -v cygpath >/dev/null 2>&1; then
    SOURCE="$(cygpath -m "$SOURCE")"
    MSYS_NO_PATHCONV=1
    MSYS2_ARG_CONV_EXCL="*"
    export MSYS_NO_PATHCONV MSYS2_ARG_CONV_EXCL
fi

# The arguments reach pytest as positional parameters rather than
# through a variable, so that `run.sh -k "one or another"` keeps
# its quoting.
exec docker run --rm \
    -v "$SOURCE:/src:ro" \
    -e "PACKAGES=$PACKAGES" \
    -e "WHEELS=$WHEELS" \
    "$IMAGE" sh -c '
set -eu
export DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1

# A mount that did not arrive is an empty directory rather than
# an error, and an empty directory is a suite of no tests,
# which passes.  Say so here, before spending a minute on packages.
test -f /src/pyproject.toml || {
    echo "the repository is not mounted at /src" >&2
    exit 1
}

apt-get -qq update >/dev/null 2>&1
apt-get -qq install -y $PACKAGES >/dev/null 2>&1
pip -q install $WHEELS >/dev/null 2>&1

# Copied rather than run in place: the mount is read only, and a build
# directory written into the working tree from a container would arrive
# owned by root.
cp -r /src /build
cd /build
rm -rf .venv build/pytest build/mypy build/ruff

echo "python $(python -V 2>&1 | cut -d" " -f2), $(fc-list | wc -l) fonts"
python -m pytest -q "$@"
' sh "$@"
