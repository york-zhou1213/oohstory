# OOH Story 原生 Linux 部署

OOH Story 的正式部署不依赖 Docker。推荐在 Ubuntu 24.04 LTS 或 Debian 12 上使用 Python 3.12、systemd、Nginx、MySQL 8.4 和 Redis 7；Reader 与 Admin 只监听回环地址。

本文给出从空主机开始的部署顺序。示例域名、路径和账号必须在私有配置中替换，密码、证书和 `.env` 文件不得提交到 Git。

## 1. 准备系统账号与目录

```bash
sudo useradd --system --home /opt/oohstory-reader --shell /usr/sbin/nologin oohstory
sudo useradd --system --home /opt/oohstory-admin --shell /usr/sbin/nologin oohstory-admin

sudo install -d -o oohstory -g oohstory -m 0750 /opt/oohstory-reader
sudo install -d -o oohstory-admin -g oohstory-admin -m 0750 /opt/oohstory-admin
sudo install -d -o oohstory -g oohstory -m 0750 /srv/oohstory/library
sudo install -d -o root -g oohstory -m 0750 /etc/oohstory-reader
sudo install -d -o root -g oohstory-admin -m 0750 /etc/oohstory-admin
sudo install -d -o oohstory-admin -g oohstory-admin -m 0700 /var/lib/oohstory-admin
```

把仓库中的 Reader 文件安装到 `/opt/oohstory-reader`，把 `admin/` 安装到 `/opt/oohstory-admin`。不要把 `.git/`、本地数据、构建产物或开发虚拟环境复制到生产目录。

## 2. 初始化 Python 环境

```bash
sudo -u oohstory python3 -m venv /opt/oohstory-reader/.venv
sudo -u oohstory /opt/oohstory-reader/.venv/bin/python -m pip install \
  -r /opt/oohstory-reader/requirements.txt

sudo -u oohstory-admin python3 -m venv /opt/oohstory-admin/.venv
sudo -u oohstory-admin /opt/oohstory-admin/.venv/bin/python -m pip install \
  /opt/oohstory-admin
```

## 3. 初始化空书库与 MySQL

先创建不含任何作品的书库骨架：

```bash
cd /opt/oohstory-reader
sudo -u oohstory ./scripts/init_local_library.py \
  --library-root /srv/oohstory/library
```

在全新的 MySQL 8.4 实例中执行空库初始化。`init.sql` 发现已有表时会拒绝覆盖：

```bash
cd /opt/oohstory-admin
sudo mysql < deploy/mysql/init.sql

umask 077
sudo mysql --batch --raw < deploy/mysql/runtime-users.sql \
  > /var/lib/oohstory-admin/mysql-generated-passwords.txt
```

按 [`../admin/deploy/mysql/README.md`](../admin/deploy/mysql/README.md) 将三个随机密码分别写入受限密码文件，然后删除临时明文输出。Writer 密码保持 root-only；Reader 与 Admin 的只读密码文件分别使用 `root:oohstory` 和 `root:oohstory-admin`、mode 0640。三个运行链路必须使用不同的最小权限账号。

验证 schema：

```bash
cd /opt/oohstory-reader
.venv/bin/python scripts/verify_mysql_schema.py \
  --database oohstory_library \
  --user oohstory_library_reader \
  --password-file /etc/oohstory-admin/mysql-password
```

预期为 23 个 revision、29 张基础表和 7 个触发器。

## 4. 配置 Reader 与 Admin

```bash
sudo cp /opt/oohstory-reader/.env.example /etc/oohstory-reader/oohstory.env
sudo cp /opt/oohstory-admin/deploy/admin.env.example /etc/oohstory-admin/admin.env
sudo chown root:oohstory /etc/oohstory-reader/oohstory.env
sudo chown root:oohstory-admin /etc/oohstory-admin/admin.env
sudo chmod 0640 /etc/oohstory-reader/oohstory.env /etc/oohstory-admin/admin.env
```

至少修改以下内容：

- Reader 的公开域名、Allowed Hosts、书库路径和 MySQL 密码文件；
- Admin 的用户名、密码哈希、随机会话密钥和 MySQL/Redis 配置；
- OAuth、SMTP 等可选能力留空即禁用；
- Admin 必须保持 `127.0.0.1:8092`，不得直接暴露到公网。

使用 Admin 的 systemd 控制、脚本发布和书库写操作前，还必须按 [`../admin/README.md`](../admin/README.md#service-control-privilege) 安装 root-owned helper 与经过 `visudo -cf` 校验的 sudoers。未安装时应关闭 helper 模式，管理端只保留不需要特权的只读能力。

## 5. 安装 systemd 服务

```bash
sudo cp /opt/oohstory-reader/deploy/oohstory-reader.service /etc/systemd/system/
sudo cp /opt/oohstory-admin/deploy/oohstory-admin.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now oohstory-reader.service oohstory-admin.service
```

书库 Worker、缓存和周期任务模板位于 `admin/deploy/systemd/`。它们安装后不应全部自动启用；先完成数据路径、最小权限和单任务灰度验收，再逐项启用需要的 timer/worker。

检查本机健康接口：

```bash
curl --fail http://127.0.0.1:8091/healthz
curl --fail http://127.0.0.1:8092/healthz
```

## 6. 配置 Nginx 与 TLS

复制 `deploy/nginx-oohstory.conf`，把 `reader.example.com`、证书路径和受信代理网段替换为实际环境值。Reader 可通过 Nginx/TLS 公开；Admin 路径及管理 API 必须继续返回 404，管理端只通过回环或受控内网访问。

```bash
sudo nginx -t
sudo systemctl reload nginx
```

## 7. 上线前验收

- Reader 与 Admin 健康接口返回 200；
- MySQL 为 23 revisions / 29 tables / 7 triggers；
- Reader 账号不能执行 DDL，Admin 只读账号不能写书目；
- 公网 `/admin`、`/admin/*`、`/api/admin/*` 和 `/api/v1/admin/*` 返回 404；
- `.env`、数据库、日志、证书、密钥、签名包和作品数据均未进入 Git；
- 备份、恢复和书库数据权限已经在实际存储上验证。

Compose 仅用于可选的本地沙盒和 CI，不参与上述正式部署链路。
