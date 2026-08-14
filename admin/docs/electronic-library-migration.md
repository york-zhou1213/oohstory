# Electronic-library source ownership migration

Migration date: 2026-08-03

Source workspace: `/opt/oohstory-admin`

Source base commit: `0c7340df3783dd332c2f6ecf55a608c0e0f80708`

The source workspace contained relevant uncommitted production fixes. This
repository therefore owns the copied working-tree snapshot, not only the base
commit. Runtime code must not import or execute Python from the source
workspace after this migration.

## Owned runtime package

The package lives under `src/oohstory_library/services/`. The recursive
`services.*` closure of these five entry points was copied and rewritten to
`oohstory_library.services.*`:

- `electronic_library`
- `library_task_runners`
- `ai_service`
- `library_download_queue`
- `local_source_upgrade`

The static closure contains 31 source modules. Two dynamically executed
companions, `library_task_worker` and `library_batch_worker`, are also owned by
this repository. `unit_names` is an OOHStory-specific compatibility module.

The complete copied source-module set is:

```text
ai_service
authorized_source_recovery
bilingual_service
browser_recovery
chapter_segments
codex_cli
cover_failure_policy
download_security
electronic_library
fanqie_downloader_bridge
genre_catalog
ixdzs_provider
library_batch_worker
library_catalog
library_catalog_mysql
library_covers
library_database
library_download_queue
library_identity_claims
library_object_store
library_runtime_mysql
library_task_runners
library_task_worker
linovelib_provider
local_source_upgrade
oh_story_contracts
project_prompt_store
projects_manager
shubaow_provider
tone_catalog
txt80_provider
xbiquge_provider
zlibrary_provider
```

## Owned operational assets

- `scripts/electronic-library/`: 38 Python tools, two browser helpers, and the
  operator README. Python paths resolve this repository's `src` directory.
- `deploy/mysql/`: 21 ordered MySQL migrations, OOHStory MySQL configuration,
  a non-secret infrastructure environment example, and operator notes.
- `deploy/systemd/`: 37 inactive OOHStory-named service/timer templates. They
  target `/opt/oohstory-admin`, `/etc/oohstory-admin/library.env`, and this
  repository's scripts/package; this migration does not install or enable
  them.
- `electronic-library`: stable repository-level symlink to the existing
  `/srv/oohstory/library` data tree. Data is not copied into Git.

The default library root is
`<oohstory-backend>/electronic-library`. `OOHSTORY_LIBRARY_*`
environment variables take precedence; legacy `WEBNOVEL_*` variables remain
accepted only for transition compatibility.

OOHStory unit names are the default. Set
`OOHSTORY_LIBRARY_LEGACY_UNIT_NAMES=1` only while an existing host still uses
the old `webnovel-*` systemd units. No unit files were installed or changed
by this source migration.

The existing `webnovel-shubaow-browser.service` and CDP endpoint on port 9222
remain an explicitly shared external browser. No second browser unit was
copied or defined; service templates that need Shubaow/Linovelib declare a
`Wants`/`After` relationship to that one shared unit.

## Excluded from the copy

- electronic-library data, generated indexes, generated covers, logs, caches,
  task state, backups, and object-store payloads
- virtual environments, `__pycache__`, bytecode, node modules, and CLI bundles
- environment files, passwords, API keys, cookies, browser profiles, and any
  other credentials
- source-repository Git metadata and unrelated API/UI code

## Deferred external content

Project-writing prompt templates previously expected under
`.claude/skills/webnovel-write/prompts` are not part of the electronic-library
engine and were not copied. Project prompt creation/reset operations remain
deferred until OOHStory-owned templates are placed under
`resources/prompts`. The electronic-library catalog, sync, cover, index,
download, MySQL/Redis, and deconstruction status code does not fall back to the
old repository for these files.

AI actions still require explicitly provisioned `AI_*` configuration and the
selected local CLI/runtime. No AI credential or login state was migrated.
