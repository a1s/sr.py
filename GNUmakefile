PYTHON    ?= .venv/Scripts/python
VENV      ?= .venv

# A venv puts its interpreter in Scripts/ on Windows and bin/ everywhere
# else, so pick whichever is there rather than making one platform edit a
# file.  Before `make install` neither exists; the Windows spelling is the
# fallback because that is where this is developed.
VENV_PYTHON := $(if $(wildcard $(VENV)/bin/python),$(VENV)/bin/python,$(VENV)/Scripts/python.exe)
PYTHON    ?= $(VENV_PYTHON)

# The recipes below are POSIX: rm -rf, find, test.  On Windows that means
# running make from Git Bash, Cygwin or MSYS2 rather than from cmd.exe or
# PowerShell.

# Everything generated lands here: the tool caches, setuptools' metadata,
# and the compiled bytecode.  One ignored directory rather than five.
BUILD_DIR ?= build

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

.PHONY: all venv install test test-required lint fmt typecheck check \
        build-dir reference clean

all: check

# -------- environment --------

venv:
	test -d $(VENV) || python -m venv $(VENV)

build-dir:
	test -d $(BUILD_DIR) || mkdir -p $(BUILD_DIR)

# The engine's dependencies, the test and lint tools,
# and the two oracles the later milestones verify against.
# Editable, so `sr` imports from the working tree.
#
# build-dir first: setuptools writes its metadata into an egg_base
# that has to exist already, and setup.cfg points that at $(BUILD_DIR).
install: venv build-dir
	$(PYTHON) -m pip install -e ".[dev,oracles]"

# -------- dev hygiene --------

test:
	$(PYTHON) -m pytest

# For a machine that is supposed to have the oracle:
# an engine that cannot be built is a failure rather than a skip,
# so a suite that measured nothing does not pass.
test-required:
	$(PYTHON) -m pytest --differential-required

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

fmt:
	$(PYTHON) -m ruff check --fix .
	$(PYTHON) -m ruff format .

typecheck:
	$(PYTHON) -m mypy

check: lint typecheck test

# -------- the oracle --------

# Build the reference on its own, without running the suite.  Useful
# for writing a probe: build it with this binary and read the printout.
reference:
	$(PYTHON) -c "from tests.differential.reference import build_reference; print(build_reference())"

# $(BUILD_DIR) takes the caches and the metadata with it.  The two finds
# catch bytecode written by a command that ran without the prefix above.
clean:
	rm -rf $(BUILD_DIR) $(REFERENCE)
	find . -path ./$(VENV) -prune -o -name __pycache__ -type d -exec rm -rf {} +
	find . -path ./$(VENV) -prune -o -name '*.egg-info' -type d -exec rm -rf {} +
