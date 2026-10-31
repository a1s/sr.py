VENV      ?= .venv

# A venv puts its interpreter in Scripts/ on Windows and bin/ everywhere
# else, so pick whichever is there rather than making one platform edit a
# file.  Before `make install` neither exists; the Windows spelling is the
# fallback because that is where this is developed.
#
# Deferred rather than `:=`, so that it still sees a $(VENV) env.mk sets
# below, and so that the wildcard is tested when a recipe runs rather than
# once at startup -- `make install check` creates the venv in its first
# target and needs the interpreter in its second.
VENV_PYTHON = $(if $(wildcard $(VENV)/bin/python),$(VENV)/bin/python,$(VENV)/Scripts/python.exe)
PYTHON    ?= $(VENV_PYTHON)

# The recipes below are POSIX: rm -rf, find, test.  On Windows that means
# running make from Git Bash, Cygwin or MSYS2 rather than from cmd.exe or
# PowerShell.

# Everything generated lands here: the tool caches, setuptools' metadata,
# and the compiled bytecode.  One ignored directory rather than five.
BUILD_DIR ?= build

# build/ is ignored, but this one file in it is tracked, so that the
# directory setup.cfg's `egg_base` needs is there in a fresh clone
# and a plain `pip install` works without running anything here first.
# `clean` empties the directory and keeps the marker rather than deleting
# a tracked file and leaving the working tree dirty.
BUILD_MARKER := $(BUILD_DIR)/.gitkeep

# Where the differential harness builds the Go reference.
REFERENCE := tmp/reference

# Local settings
ifneq ($(wildcard env.mk),)
	include env.mk
endif

# pytest, ruff and mypy take their cache location from pyproject.toml.
# Bytecode has no configuration file, only this, so it is set for every
# command run through here.  A `pytest` typed straight into a shell still
# writes __pycache__ beside the sources unless the same variable is
# exported there.
export PYTHONPYCACHEPREFIX := $(BUILD_DIR)/pycache

.PHONY: all venv install test test-required test-linux test-linux-matrix \
        lint fmt typecheck check audit audit-packages audit-actions \
        build-dir reference clean

all: check

# -------- environment --------

venv:
	test -d $(VENV) || python -m venv $(VENV)

build-dir:
	mkdir -p $(BUILD_DIR)
	test -f $(BUILD_MARKER) || touch $(BUILD_MARKER)

# The engine's dependencies, the test and lint tools,
# the two oracles the later milestones verify against,
# and the tool `make audit` runs.
# Editable, so `sr` imports from the working tree.
#
# build-dir first: setuptools writes its metadata into an egg_base
# that has to exist already, and setup.cfg points that at $(BUILD_DIR).
install: venv build-dir
	$(PYTHON) -m pip install -e ".[dev,oracles,audit]"

# -------- dev hygiene --------

TEST_ARGS ?= -n auto

test:
	$(PYTHON) -m pytest $(TEST_ARGS)

# For a machine that is supposed to have the oracle:
# an engine that cannot be built is a failure rather than a skip,
# so a suite that measured nothing does not pass.
test-required:
	$(PYTHON) -m pytest $(TEST_ARGS) --differential-required

# Font enumeration is the one part of the engine whose answer
# depends on the machine, and doc/template.md names three platforms.
#  This runs the suite on the second of them, in a stock Debian container:
# see tests/linux/run.sh for what that exercises that a fixture cannot.
# Needs Docker, which is why it is not part of `check`.
test-linux:
	sh tests/linux/run.sh

# The interpreters pyproject.toml's `requires-python` promises,
# from its floor to the current release.  The engine's own tests
# do not vary with the interpreter, so this is checking the promise
# rather than the engine, and it is separate from `test-linux`
# because it is four container runs rather than one.
SUPPORTED_PYTHONS ?= 3.11 3.12 3.13 3.14

test-linux-matrix:
	@for one in $(SUPPORTED_PYTHONS); do \
	    echo "-- python $$one"; \
	    SR_LINUX_IMAGE=python:$$one-slim sh tests/linux/run.sh || exit 1; \
	done

# zizmor is a compiled program with no `python -m` form,
# so it is run from wherever its install recorded putting it.
# That is the scheme pip actually used, rather than the
# interpreter's default: pip falls back to the user's own directory
# when the interpreter's is not writable, as under Program Files.
# Deferred, like VENV_PYTHON, so that `make install lint` finds it.
ZIZMOR = $(shell $(PYTHON) -c "from importlib.metadata import files; \
    print(next(path.locate().resolve().as_posix() \
        for path in files('zizmor') \
        if path.parent.name in ('bin', 'Scripts')))")

# zizmor reads the workflows and the Dependabot config.
# Offline, because its online audits ask GitHub about advisories and commits,
# and their answers change while the commit under test does not.
# `make audit-actions` runs them.
lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .
	"$(ZIZMOR)" --offline .

fmt:
	$(PYTHON) -m ruff check --fix .
	$(PYTHON) -m ruff format .

# Font enumeration branches on sys.platform, and mypy checks only the
# branches of the platform it checks as: a call that exists on one
# platform alone passes there and fails everywhere else.  So each of
# the three platforms doc/template.md names gets a run of its own.
TYPECHECK_PLATFORMS ?= linux darwin win32

typecheck:
	@for one in $(TYPECHECK_PLATFORMS); do \
	    echo "-- mypy --platform $$one"; \
	    $(PYTHON) -m mypy --platform $$one || exit 1; \
	done

check: lint typecheck test

# Known vulnerabilities, in the packages and in the actions.
# Not part of `check`, and not run by the workflows:
# the advisories change from day to day,
# so their answer for one commit does too.
audit: audit-packages audit-actions

# Everything the venv holds:
# the engine's dependencies, the test and lint tools, and the oracles.
# The project itself is skipped, being installed from the working tree,
# not from PyPI.
audit-packages:
	$(PYTHON) -m pip_audit --skip-editable --cache-dir $(BUILD_DIR)/pip-audit

# The actions the workflows pin, through the zizmor audits `lint` leaves out:
# a pin to a commit that is not in the action's own repository,
# a version with an advisory, and a ref that is both a branch and a tag.
# They ask GitHub, which wants a token in GH_TOKEN,
# and without one zizmor quietly runs offline and passes,
# so the token is checked first.
audit-actions:
	@test -n "$$GH_TOKEN$$GITHUB_TOKEN$$ZIZMOR_GITHUB_TOKEN" \
	    || { echo "audit-actions: set GH_TOKEN to a GitHub token" >&2; exit 1; }
	"$(ZIZMOR)" --cache-dir $(BUILD_DIR)/zizmor .

# -------- the oracle --------

# Build the reference on its own, without running the suite.  Useful
# for writing a probe: build it with this binary and read the printout.
reference:
	$(PYTHON) -c "from tests.differential.reference import build_reference; print(build_reference())"

# Emptying $(BUILD_DIR) takes the caches and the metadata with it, while
# $(BUILD_MARKER) stays: it is tracked, and deleting it would leave the
# working tree dirty and `pip install` broken until it came back.  The two
# finds catch bytecode written by a command that ran without the prefix above.
clean: build-dir
	rm -rf $(REFERENCE)
	find $(BUILD_DIR) -mindepth 1 -maxdepth 1 -not -name .gitkeep -exec rm -rf {} +
	find . -path ./$(VENV) -prune -o -name __pycache__ -type d -exec rm -rf {} +
	find . -path ./$(VENV) -prune -o -name '*.egg-info' -type d -exec rm -rf {} +
