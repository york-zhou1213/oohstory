#!/bin/sh
set -eu

PYTHONPATH=. .venv/bin/python -m py_compile \
  app/audiobook_policy.py app/audiobook_snapshot.py app/audiobook.py \
  app/audiobook_cast.py app/audiobook_cast_review_worker.py app/main.py
node --check static/audiobook-lifecycle.js
node --check static/audiobook-cache.js
node --check static/audiobook-fallback.js
node --check static/app.js
OOHSTORY_RUN_MYSQL_INTEGRATION=1 PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_architecture_limits.py \
  tests/test_audiobook_contract_v14.py \
  tests/test_audiobook.py \
  tests/test_audiobook_cast_prewarm.py \
  tests/test_audiobook_mysql_integration.py \
  tests/test_tts_dialogue_contract.py \
  tests/test_tts_emotions.py \
  tests/test_frontend_contract.py
