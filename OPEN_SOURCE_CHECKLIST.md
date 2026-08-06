# Open-source release checklist

This copy was prepared from `web-v20260806-v40` without carrying over the
private Git history or untracked workspace files.

Before the first public push:

- [ ] Choose the final GitHub owner/repository name.
- [ ] Review `LICENSE`, the brand assets, and contributor policy.
- [ ] Replace `reader.example.com` and sample filesystem paths in `deploy/`.
- [ ] Create production secrets outside Git and reference them by file path.
- [ ] Publish signed mobile packages through GitHub Releases, not the source tree.
- [ ] Run `python scripts/check_repository_secrets.py .`.
- [ ] Run the complete Python test suite and JavaScript syntax check.
- [ ] Enable branch protection and required CI checks before accepting changes.

Never add novel text, user data, databases, OAuth credentials, SMTP passwords,
TLS keys, APK signing material, production `.env` files, or server backups.
