#!/bin/sh
# Use only a project venv, a CI interpreter, or a container interpreter.
set -eu
python_bin=${1:-python}
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
"$python_bin" -m ensurepip --upgrade
"$python_bin" -m pip --isolated --disable-pip-version-check install \
    --no-cache-dir --only-binary=:all: --no-deps --require-hashes \
    -r "$repo_root/requirements-bootstrap.txt"
