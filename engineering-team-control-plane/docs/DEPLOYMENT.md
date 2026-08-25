# Exact-commit deployment and compatibility routing

Only Ken may activate this control plane after Bob approves and Mus tests the exact commit. The v1 contract in `release/runtime-contract.json` freezes these production identities:

- canonical team root: `/root/.openclaw/workspaces/engineering-team`
- canonical runtime root: `/root/.openclaw/workspaces/engineering-team/runtime/learning-control-plane`
- canonical consumer: `/root/.openclaw/workspaces/engineering-team/scripts/learning_loop.py`
- versioned releases: `runtime/learning-control-plane/releases/RELEASE_ID`
- atomic selector: `runtime/learning-control-plane/active`

The compatibility adapter keeps `bootstrap`, `preflight`, `close`, `metrics`, and `stale` on the exact preserved legacy script. It sends only `audit-task` and `audit-system` to `active/scripts/learning_control_plane.py`. Unknown commands fail closed.

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

   Activation writes immutable release metadata and a copy of the exact deployment manifest before replacing `active` with one atomic relative-symlink rename. The receipt records the exact prior target, activated target, source SHA, manifest hash, and contract hash.
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

6. Run `bootstrap --help` and a read-only legacy probe through the canonical consumer, then run `audit-task` and `audit-system` through the same consumer. Data writes, ID migration, service activation, signing, and production mutation remain separately authorized operations.

## Exact rollback

For a normal version upgrade, run `rollback-release --contract ... --receipt BOUNDED_BACKUP_DIR/release-activation.json`. It changes the selector only when the current target is the exact activated target and atomically restores the recorded predecessor. Drift refuses rollback.

For the first adapter installation, run `rollback-adapter --contract ... --receipt BOUNDED_BACKUP_DIR/adapter-install.json` first, then `rollback-release`. Adapter rollback restores the byte-exact legacy consumer only when the current consumer still equals the recorded reviewed adapter. A first-release rollback removes the new selector after restoring the legacy consumer; later release rollbacks are atomic selector replacements.

Rollback never mutates receipts, learning data, memory, or task records. If an ID migration was separately authorized and applied, use its exact backup manifest as documented in `MIGRATION.md`; never delete data to make an audit green.
