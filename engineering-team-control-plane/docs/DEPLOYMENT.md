# Exact-commit deployment

Only Ken may activate this control plane after Bob approves and Mus tests the exact commit.

1. Use a clean checkout of the approved SHA and verify `release/deployment-manifest.json` against the checkout with `verify-deployment`, using the checkout as both roots.
2. Create a bounded backup of the existing runtime source and record its manifest and hashes. Do not include runtime data in Git.
3. Copy the manifest-listed files to a staging runtime tree without changing relative paths.
4. Run `verify-deployment` with the approved checkout as `--source-root` and staging tree as `--runtime-root`. Any absent, extra-target, symlink, source drift, runtime drift, or hash mismatch blocks activation.
5. Run task/system audits read-only. Resolve historical receipt lifecycle and migration issues only under Ken's separately authorized runtime/data migration step.
6. Activate the verified staging tree through the normal bounded runtime switch and record the exact commit and manifest hash.

Rollback restores the manifest-verified pre-deploy runtime backup, verifies its hashes, and retains all receipts and memory. If an ID migration was applied, use its exact backup manifest as documented in `MIGRATION.md`; never delete data to make an audit green.
