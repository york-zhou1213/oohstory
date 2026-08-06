# OOH Story

OOH Story 是一个面向自托管电子书库的中文小说阅读站。项目包含响应式 Web 阅读器、账户与跨端进度、收藏/书架、分卷目录、段落评论、听书、投稿隔离审核以及拆书档案展示。

> 本仓库只提供程序代码与品牌静态资源，不包含小说正文、用户数据、数据库备份、签名 APK、私钥或生产凭据。部署者必须确保所有书籍和拆书资料具备合法的使用、翻译和发布权。

## 核心能力

- 书库检索、分类、排行榜、书籍/分卷/章节阅读
- 四种阅读布局、阅读断点、TTS 听书和移动端适配
- 邮箱/可选 Google 登录、云端阅读记录、收藏和书架
- 段落评论、点赞/感谢与内容过滤
- 小说/拆书投稿、格式验证、隔离目录和可选外部审核器
- SQLite 或 MySQL 目录后端，严格路径边界与安全响应头

## 快速开始

需要 Python 3.12+。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
set -a
. ./.env
set +a
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8091
```

健康检查：

```bash
curl http://127.0.0.1:8091/healthz
```

默认使用 SQLite 目录后端，书库根目录为 `data/library/`。项目不附带任何受版权保护的书籍或生产目录数据；请将自有/获授权的目录索引、正文和封面放入自己的数据目录，或通过 `.env` 切换至 MySQL。

## 配置

所有环境参数都列在 [`.env.example`](.env.example)。重要原则：

- 密码、SMTP 凭据与 OAuth 私密配置不得写入 Git；密码使用 root-owned 文件路径传入。
- `OOHSTORY_PUBLIC_ORIGIN` 与 `OOHSTORY_ALLOWED_HOSTS` 必须替换为自己的域名。
- Google 登录、SMTP 邮件和 AI 投稿审核都是可选功能，未配置时默认关闭。
- 不要把 `.env`、`var/`、`data/`、数据库、APK、ZIP、证书或私钥提交到仓库。

## 部署

`deploy/` 中的 systemd、Nginx 和 FRP 文件是脱敏示例。使用前至少需替换：

- `reader.example.com`
- `/srv/oohstory/library`
- `/etc/letsencrypt/live/reader.example.com/`
- MySQL 数据库/账号与密码文件
- 可选 FRP 远端端口和认证配置

公网 Nginx 模板保留了写操作精确白名单、限流、请求体上限和后台路由 404 边界。请不要为了方便而放宽这些规则。

## 测试

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
node --check static/app.js
```

## 开源与内容边界

程序代码使用 [MIT License](LICENSE)。MIT 许可不会自动授予任何小说、封面、用户投稿、翻译、品牌素材或第三方服务的权利。部署者对自己发布的内容与隐私合规负责。

贡献流程见 [`CONTRIBUTING.md`](CONTRIBUTING.md)，漏洞报告见 [`SECURITY.md`](SECURITY.md)。
