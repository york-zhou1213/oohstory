# Exact-commit deployment and compatibility routing

Only Ken may activate this control plane after Bob approves and Mus tests the exact commit. The v1 contract in `release/runtime-contract.json` freezes these production identities:

- canonical team root: `/root/.openclaw/workspaces/engineering-team`
- canonical runtime root: `/root/.openclaw/workspaces/engineering-team/runtime/learning-control-plane`
- canonical consumer: `/root/.openclaw/workspaces/engineering-team/scripts/learning_loop.py`
- versioned releases: `runtime/learning-control-plane/releases/RELEASE_ID`
- atomic selector: `runtime/learning-control-plane/active`

The compatibility adapter keeps `bootstrap`, `preflight`, `close`, `metrics`, and `stale` on the exact preserved legacy script. After a successful, fully identified legacy `close`, it invokes the active release's internal `upgrade-receipt` operation and returns the resulting schema-v2 receipt. It sends user-facing `audit-task` and `audit-system` directly to `active/scripts/learning_control_plane.py`. Unknown commands fail closed.

## Stage and activate

1. Use a clean checkout of the Bob-approved and Mus-tested SHA. Copy only manifest-listed files, preserving their relative paths, into a new real directory directly under the contracted `releases` directory. The release directory and activation receipt paths must not already exist.
2. Run `verify-deployment` against the clean checkout and release directory. Any absent target, symlink, source/runtime drift, hash mismatch, or unmanifested importable/executable file blocks activation.
3. Atomically activate the exact release and write its predecessor receipt:

   ```sh
   python3 scripts/learning_control_plane.py activate-release \
     --contract release/runtime-contract.json \
     --manifest release/deployment-manifest.json \
     --source-root APPROVED_CLEAN_CHECKOUT/engineering-team-control-plane \
     --release-root /root/.openclaw/workspaces/engineering-team/runtime/learning-control-plane/releases/RELEASE_ID \
     --source-revision APPROVED_40_CHARACTER_SHA \
     --receipt BOUNDED_BACKUP_DIR/release-activation.json
   ```

   Activation writes immutable release metadata (including the exact prior target) and a copy of the exact deployment manifest before replacing `active` with one atomic relative-symlink rename. Activation and rollback hold the same POSIX lock on the stable runtime parent from selector read through validation, publication, and post-verification, so another valid release transition is serialized instead of silently overwritten. The receipt records the same prior target plus the activated target, source SHA, manifest hash, and contract hash; rollback requires the receipt predecessor to equal the activated release's immutable metadata.
4. On the first activation only, atomically install the active release's reviewed adapter over the canonical consumer. This captures the previous consumer byte-for-byte at `compat/v1/learning_loop.py` before replacement:

   ```sh
   python3 scripts/learning_control_plane.py install-adapter \
     --contract release/runtime-contract.json \
     --adapter-source /root/.openclaw/workspaces/engineering-team/runtime/learning-control-plane/releases/RELEASE_ID/scripts/learning_loop_adapter.py \
     --receipt BOUNDED_BACKUP_DIR/adapter-install.json
   ```

5. Prove live resolution before declaring release. This checks every active manifest hash, runtime closure, canonical-consumer hash, preserved-legacy hash, source revision, and the adapter's own live inspection:

   ```sh
   python3 scripts/learning_control_plane.py verify-live-consumer \
     --contract release/runtime-contract.json \
     --manifest release/deployment-manifest.json \
     --source-root APPROVED_CLEAN_CHECKOUT/engineering-team-control-plane
   ```

6. In a disposable real team root, run the exact canonical sequence through the
   installed consumer: `preflight`, write the owner-day memory, `close`, then
   `audit-task` with the complete non-empty `learning-requirements` set from the
   authoritative task record. Require the emitted receipt to be schema v2 and the
   audit result to be `ok: true`. Also run `bootstrap --help`, a read-only legacy
   probe, and `audit-system` through the same consumer.
7. Before activation against an existing store, run the explicit historical
   receipt disposition dry run documented in `MIGRATION.md`. Any schema-v1 receipt
   without a retain/upgrade decision blocks the write. Apply only under Ken's
   separate migration authority with its new backup directory and verified
   rollback manifest.

Data writes, ID migration, receipt disposition, service activation, signing, and
production mutation remain separately authorized operations.

## Exact rollback

For a normal version upgrade, run `rollback-release --contract ... --receipt BOUNDED_BACKUP_DIR/release-activation.json`. The receipt is treated as untrusted input: its field set, schema, hashes, revision, target grammar, activated metadata, and predecessor metadata must all validate before mutation. `previous_target` must be either `null` or the canonical `releases/RELEASE_ID` form; absolute paths, `..`, noncanonical spellings, missing releases, symlink ancestors, and metadata drift fail closed. The command changes the selector only when the current target is the exact activated target, atomically restores the recorded predecessor, and then resolves and verifies the real target again.

For the first adapter installation, run `rollback-adapter --contract ... --receipt BOUNDED_BACKUP_DIR/adapter-install.json` first, then `rollback-release`. Install writes the exact-field receipt before any compatibility or consumer mutation. If install is interrupted after the receipt, legacy bytes, legacy metadata, runtime-contract copy, or consumer publication, repeat `install-adapter` with that same receipt to validate the durable prefix and resume it. Adapter rollback uses the same receipt as an immutable journal, validates every present boundary, requires the current active selector to equal the receipt's `active_target`, and restores the byte-exact consumer only when the current consumer equals either the reviewed adapter or already-restored hash. Cleanup runs in recoverable order (`runtime-contract.json`, metadata, legacy bytes); retry skips already removed leaves and completes without manual deletion. An older adapter receipt cannot restore legacy bytes while a later release is active. A second completed rollback is idempotent, and a later install may use a fresh adapter receipt. This makes interrupted install, interrupted rollback, and `activate → install → rollback-adapter → rollback-release → activate → install` supported recovery sequences.

Every adapter-controlled mutation opens each destination directory component with `O_NOFOLLOW`, holds that directory descriptor through validation and use, and performs temporary-file creation, replace, and unlink by relative descriptor operations. This applies to the release directory, deployed manifest, release metadata, active selector, canonical consumer, compatibility target and metadata, runtime-contract copy, and both receipt paths. A forbidden leaf or ancestor swap fails closed or remains bound to the already validated directory; post-mutation path checks prevent a raced namespace replacement from returning success.

Adapter/release rollback never mutates receipts, learning data, memory, or task
records. If an ID migration or receipt disposition was separately authorized and
applied, use its exact backup manifest as documented in `MIGRATION.md`; never
delete data to make an audit green.
