# OOH Story

OOH Story 是一套可自托管的中文电子书平台。这个开源仓库包含 Web Reader、独立运维后台、书库 Backend/Worker、MySQL 数据库迁移，以及 Flutter 移动端源码。

> 仓库只提供程序代码与品牌静态资源，不包含小说正文、用户数据、数据库备份、签名安装包、私钥或生产凭据。部署者必须确保内容具备合法的使用和发布权。

## 仓库结构

- `app/`、`static/`：Reader、账户、阅读记录、评论、投稿与 Web UI。
- `admin/`：OOHStory Admin、书库引擎、任务脚本、001–022 MySQL 迁移和 systemd 模板。
- `mobile/`：Flutter Android/iOS 客户端源码。
- `scripts/`：本地空书库、Compose 凭据、数据库验收和敏感信息门禁。
- `compose.yaml`：MySQL 8.4、Redis 7、Reader、Admin 的本地完整栈。

## 一条命令从空环境启动

需要 Docker Engine 24+、Docker Compose 2.23.1+、Python 3.12+ 和 `curl`：

```bash
./scripts/compose_up.sh
```

首次运行会：

1. 在被 Git 忽略、权限为 0600 的 `.env.compose` 生成四个独立数据库密码；
2. 同一文件只保存管理员密码哈希，随机管理员明文密码只显示一次；
3. 创建不含任何书籍的 `data/library/` SQLite/目录骨架与默认封面；
4. 启动 MySQL 8.4 并真实执行 001–022 全部迁移；
5. 创建 writer、admin-reader、public-reader 三个最小权限账号；
6. 启动 Reader 与 Admin，核验 22 个 revision、27 张表、7 个触发器和两个健康接口。

入口：

- Reader：`http://127.0.0.1:8091`
- Admin：`http://127.0.0.1:8092/admin/`
- MySQL：`127.0.0.1:13306`（仅回环，可在 `.env.compose` 修改）

停止服务：

```bash
docker compose --env-file .env.compose down
```

删除本地容器数据库属于破坏性操作，只有确认不需要数据时才执行：

```bash
docker compose --env-file .env.compose down --volumes
```

Compose 适合本地体验和集成验证。Admin 中依赖宿主机 systemd/root helper 的任务控制在容器内会失败关闭；需要完整生产运维能力时，按 `deploy/` 与 `admin/deploy/` 的 systemd 模板安装到 Linux 主机。

## 不使用 Docker 的本地 Reader

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt
./scripts/init_local_library.py
cp .env.example .env
set -a
. ./.env
set +a
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8091
```

这条路径使用本地 SQLite 空书库。后续可导入自己有权使用的正文、目录和封面；程序不会下载或附带任何作品。

## 数据库初始化保证

- `admin/deploy/mysql/001_*.sql` 至 `022_*.sql` 是 MySQL schema 唯一真源。
- `admin/deploy/mysql/init.sql` 由生成器构建，专用于空 schema，检测到现有表会拒绝覆盖。
- `admin/deploy/mysql/runtime-users.sql` 用于宿主机安装，创建三个仅限 `127.0.0.1` 的随机密码账号。
- Compose 使用相同 `init.sql`，只把账号 host 调整为隔离容器网络。
- `scripts/verify_mysql_schema.py` 同时核验 migration 文件 SHA-256、台账、表和触发器数量。
- SQLite 账户库由 Reader 自动初始化；本地书库骨架由 `scripts/init_local_library.py` 幂等创建。

数据库细节见 [`admin/deploy/mysql/README.md`](admin/deploy/mysql/README.md)。

## 移动端

```bash
cd mobile
flutter pub get
flutter analyze
flutter test
flutter run --dart-define=OOHSTORY_API_BASE_URL=http://10.0.2.2:8091
```

Android 发布签名和 Google OAuth 配置必须由部署者在 Git 外提供。详见 [`mobile/README.md`](mobile/README.md)。

## 测试与开源门禁

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest -q

python3 -m venv admin/.venv
admin/.venv/bin/python -m pip install './admin[test]'
PYTHONPATH=admin/src admin/.venv/bin/python -m pytest -q admin/tests

node --check static/app.js
python scripts/check_repository_secrets.py .
```

GitHub Actions 另外会在真实 MySQL 8.4 服务中执行空库初始化、拒绝二次 `init.sql`、验证迁移器安全跳过已应用 revision，并对 Flutter 执行 analyze/test。

## 配置与安全

- `.env.example` 是 Reader 示例；`admin/deploy/admin.env.example` 是 Admin 示例。
- 密码、SMTP/OAuth 凭据必须放在权限受控文件或被 Git 忽略的本地环境中。
- 公网生产部署应由 Nginx/TLS 终止，Admin 保持回环访问，不得直接暴露。
- 不要提交 `.env*`（示例除外）、`.runtime/`、`data/`、数据库、日志、APK/AAB、证书、密钥、压缩包或站点验证文件。

## 开源与内容边界

程序代码使用 [MIT License](LICENSE)。MIT 许可不会自动授予任何小说、翻译、封面、用户投稿、品牌或第三方服务内容的权利。

贡献流程见 [`CONTRIBUTING.md`](CONTRIBUTING.md)，漏洞报告见 [`SECURITY.md`](SECURITY.md)，发布前核对 [`OPEN_SOURCE_CHECKLIST.md`](OPEN_SOURCE_CHECKLIST.md)。
