# Contributing

## 开发流程

1. Fork 仓库并从 `main` 创建功能分支。
2. 不要提交书籍正文、用户数据、数据库、日志、APK、备份、密钥或生产域名配置。
3. 将新增配置写入 `.env.example`，只提供无敏感示例值。
4. 为行为变更添加测试，并在提交前运行：

```bash
python -m pytest -q
node --check static/app.js
```

5. Pull Request 说明需包含变更目的、安全边界、测试结果和部署影响。

## 内容贡献

代码贡献不得附带未授权小说、翻译、封面、数据抓取结果或用户投稿。测试内容应使用简短的自创样例。

## 安全问题

不要在公开 Issue 中提交可利用细节。请按 [`SECURITY.md`](SECURITY.md) 使用私密安全报告流程。
