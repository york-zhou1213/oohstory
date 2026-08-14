#!/bin/sh
set -eu

PYTHONPATH=src .venv/bin/python -m ruff check src tests --ignore-noqa
PYTHONPATH=src .venv/bin/python -m pytest -q \
  --cov=src/oohstory_admin --cov=src/oohstory_library \
  --cov-report=term-missing --cov-fail-under=27
.venv/bin/python -m pip_audit -r requirements.txt --progress-spinner off
PYTHONPATH=src .venv/bin/python -m compileall -q src tests
sh -n ops/oohstory-admin-systemctl
sh -n ops/oohstory-admin-library-action
