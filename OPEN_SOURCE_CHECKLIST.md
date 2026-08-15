# Open-source release checklist

Before the first public push:

- [ ] Choose the final GitHub owner/repository name and configure the remote.
- [ ] Review `LICENSE`, brand assets, contributor policy and content-rights notice.
- [ ] Confirm Reader, `admin/` Backend/Admin and `mobile/` are all present.
- [ ] Run `admin/scripts/electronic-library/render_mysql_init_sql.py --check`.
- [ ] Initialize a fresh MySQL 8.4 schema and verify 23 revisions, 29 tables and 7 triggers.
- [ ] Confirm a second `init.sql` run fails closed and the migration runner skips all applied revisions.
- [ ] Run Reader and Admin Python suites, JavaScript syntax check, Flutter analyze/test and native deployment checks.
- [ ] Optionally run the Compose sandbox smoke test; do not treat it as the production architecture.
- [ ] Stage intended files, then run `python scripts/check_repository_secrets.py .`.
- [ ] Inspect Git history for novel text, user data, databases, logs, tokens, production domains, host paths and signing artifacts.
- [ ] Replace `reader.example.com` and sample filesystem paths only in private deployment configuration, never in tracked examples.
- [ ] Create production credentials outside Git and use distinct least-privilege database accounts.
- [ ] Publish signed mobile packages through GitHub Releases, not the source tree.
- [ ] Enable branch protection and required CI checks before accepting changes.

Never add novel text, user data, database exports, OAuth/SMTP credentials, TLS
keys, APK signing material, production `.env` files, site-verification files or
server backups.
