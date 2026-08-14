#!/bin/sh
set -eu

PYTHONPATH=. .venv/bin/python -m ruff check app tests --ignore-noqa
PYTHONPATH=. .venv/bin/python -m pytest -q \
  --cov=app --cov-report=term-missing --cov-fail-under=60
.venv/bin/python -m pip_audit -r requirements.txt --progress-spinner off
node --check static/app.js
node --check static/account-ui.js
