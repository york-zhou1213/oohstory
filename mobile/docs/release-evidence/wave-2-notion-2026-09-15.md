# OOHStory Wave 2 Notion 单向导出验证证据（2026-09-15）

## 交付范围

- 新增受 `OOHSTORY_NOTION_EXPORT_ENABLED` 控制的原生端 Notion 导出入口；默认关闭，
  Web 即使传入开关也不显示长期令牌配置。
- 用户只配置一个明确的 Notion 页面或 data source ID。客户端不会调用 Search API、
  不读取整个工作区，也不会自行发现其他页面。
- Internal connection token 只写入系统安全存储；父级类型、目标 ID、data source 标题
  字段、页面映射和内容哈希进入版本化普通设置，普通设置不含 token。
- 使用 Notion 官方 2026-03-11 契约：创建页面、读取页面 Markdown、替换页面 Markdown。
  首次创建后保存 page ID；后续导出读取并更新同一页面，不按标题重复创建。
- 写入前比较上次确认的远端 Markdown 哈希。检测到 Notion 外部修改时默认停止；用户
  明确确认后才替换正文，并保持 `allow_deleting_content=false`，避免静默删除子页面或
  数据库。
- 安全 GET/PATCH 对 429/5xx 退避并遵守 `Retry-After`；初次 POST 创建不自动重试，
  避免未知结果造成重复页面。已确认创建的 page ID 在验证读取前先落盘，读取失败后
  重试仍回到同一页面。
- 断开连接只清除本机 token、目标配置与该目标的页面映射，不删除远端 Notion 页面。

## 自动验证

- Notion 定向套件覆盖配置/凭据分离、固定 API 版本、page/data source 请求、无 Search
  调用、同页幂等、外部修改冲突、显式覆盖保护、`Retry-After`、创建不重试、创建后
  验证读取失败恢复，以及目标范围内断开清理。
- 注解 UI 套件覆盖 Notion 与 Obsidian 的独立 capability 门控。
- 云端回归套件同时验证公共 UTF-8 JSON fixture 修正没有破坏 WebDAV、S3、Dropbox、
  Google Drive 与共享 HTTP transport 测试。

```text
/opt/flutter/bin/flutter analyze
No issues found!

/opt/flutter/bin/flutter test test/annotation_export test/cloud \
  test/core/capabilities_test.dart
69 passed

/opt/flutter/bin/flutter test --reporter json
FULL_TEST_RESULT success=true

/opt/flutter/bin/flutter build linux --release \
  --dart-define=OOHSTORY_NOTION_EXPORT_ENABLED=true
Built build/linux/x64/release/bundle/oohstory

/opt/flutter/bin/flutter build web --release --base-href /app/ \
  --dart-define=OOHSTORY_NOTION_EXPORT_ENABLED=true
Built build/web
```

构建后确认 Linux 可执行文件存在；Web `main.dart.js` 不包含“Notion 导出”、连接表单或
token 字段文案，证明 `kIsWeb` 门控路径在发行构建中被裁剪。`git diff --check` 通过。

## 未形成的发布声明

- 没有项目所有者提供的 Notion 测试连接、页面/data source 和最小权限 token，因此本轮
  没有写入真实工作区，也没有声称真实服务验收通过。
- 当前是原生端 internal connection 纵切，不是面向所有用户的 OAuth + PKCE 流程；
  正式公开分发前仍需独立 Notion 应用、回调、隐私说明、撤销与真实限流验证。
- Notion API 对首次创建的网络未知结果没有通用幂等键。本实现不重试 POST；若连接在
  服务端确认创建但客户端未收到响应，必须由用户在目标中人工核对，不能自动猜测。
- 不实现双向删除。删除本地批注或断开连接不会删除远端页面。

## 回滚

关闭 `OOHSTORY_NOTION_EXPORT_ENABLED` 即隐藏入口，不影响 ZIP、Obsidian 或本地批注。
回退客户端不会删除已经创建的 Notion 页面；用户可通过“断开连接”清除本机授权材料。
