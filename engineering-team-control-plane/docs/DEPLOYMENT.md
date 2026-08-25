# Exact-commit deployment

Only Ken may activate this control plane after Bob approves and Mus tests the exact commit.

1. Use a clean checkout of the approved SHA, copy only manifest-listed files into a disposable staging tree, and run `verify-deployment` with the checkout as `--source-root` and that tree as `--runtime-root`.
2. Create a bounded backup of the existing runtime source and record its manifest and hashes. Do not include runtime data in Git.
3. Copy the manifest-listed files to a staging runtime tree without changing relative paths.
4. Run `verify-deployment` with the approved checkout as `--source-root` and staging tree as `--runtime-root`. Any absent target, symlink, source/runtime drift, hash mismatch, or unmanifested importable/executable file anywhere in the runtime tree blocks activation, including files under plugin and interpreter-cache directories.
5. Run task/system audits read-only. Resolve historical receipt lifecycle and migration issues only under Ken's separately authorized runtime/data migration step.
6. Activate the verified staging tree through the normal bounded runtime switch and record the exact commit and manifest hash.

Rollback restores the manifest-verified pre-deploy runtime backup, verifies its hashes, and retains all receipts and memory. If an ID migration was applied, use its exact backup manifest as documented in `MIGRATION.md`; never delete data to make an audit green.
