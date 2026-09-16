# OOHStory Wave 2 Joplin 桌面单向导出验证证据（2026-09-15）

## 本轮交付

- 新增受 `OOHSTORY_JOPLIN_EXPORT_ENABLED` 控制的 Joplin 桌面导出入口；默认关闭，
  仅 Linux、Windows、macOS 可见，Web 与移动端不开放。
- 用户只配置 Web Clipper 端口和一个明确的 notebook ID。客户端固定连接
  `127.0.0.1`，不接受任意主机或 URL；保存前验证 `/ping` 和目标 notebook。
- Data API token 只进入系统安全存储。普通偏好设置只保存端口、notebook ID、
  笔记映射和内容指纹。
- 每本书使用由 notebook + OOHStory document ID 派生的 32 位十六进制 note ID，
  重复导出更新同一笔记。首次创建即使在提交后断线，下一次也按确定性 ID 恢复，
  不重复创建。
- 为导出的笔记幂等维护 `oohstory` 与 `reading-notes` 两个确定性标签；只补建缺失
  标签和缺失关系。
- 写入前比较上次确认的 title、body、parent 和 `application_data` 归属标记指纹。
  检测到远端修改或删除时默认停止，用户明确确认后才替换或重建；归属标记不匹配
  时始终拒绝覆盖。
- 断开连接只清除本机 token、配置与该 notebook 的映射，不删除远端笔记或标签。

实现边界记录在 `docs/adr/005-joplin-desktop-annotation-export.md`。接口行为依据 Joplin
官方 Data API 文档：<https://joplinapp.org/help/api/references/rest_api/>。

## 自动化验证

### 静态分析

```text
/opt/flutter/bin/flutter analyze
Analyzing oohstory-app...
No issues found!
```

### Joplin、批注 UI 与 capability 定向套件

```text
/opt/flutter/bin/flutter test \
  test/annotation_export/joplin_export_test.dart \
  test/annotation_export/offline_notes_screen_test.dart \
  test/core/capabilities_test.dart --reporter expanded
00:02 +17: All tests passed!
```

定向场景覆盖：

- 配置/凭据分离、端口与 notebook ID 校验、固定 loopback endpoint；
- `/ping` 与 notebook 验证、令牌只按 Joplin 要求进入 query；
- 确定性笔记和标签 ID、重复导出不重复创建；
- 远端编辑与删除冲突、显式替换、归属碰撞拒绝覆盖；
- 首次 POST 已提交但连接断开后的无重复恢复；
- Joplin、Notion、Obsidian 三个产品入口独立门控。

### 全量 Flutter 回归

```text
/opt/flutter/bin/flutter test --reporter compact
00:33 +243: All tests passed!
```

### Linux release build

```text
/opt/flutter/bin/flutter build linux --release \
  --dart-define=OOHSTORY_JOPLIN_EXPORT_ENABLED=true
✓ Built build/linux/x64/release/bundle/oohstory
```

### Web 隔离与 `/app/` 路径

```text
/opt/flutter/bin/flutter build web --release --base-href /app/ \
  --dart-define=OOHSTORY_JOPLIN_EXPORT_ENABLED=true
✓ Built build/web
```

构建后确认 `build/web/index.html` 的 base href 为 `/app/`。即使编译参数误设为 true，
tree-shaken `main.dart.js` 也不包含“Joplin 导出”“连接 Joplin 桌面版”“Data API token”
或“目标笔记本 ID”等桌面 UI 文本。

## 仍未宣称完成

- 本轮当时没有可用的 Joplin 测试实例，因此没有真实写入；后续隔离 Joplin CLI
  3.7.1 Data API 验收见 `wave-2-joplin-resources-2026-09-16.md`。打包 Desktop UI 与
  系统安全存储验收前，能力仍默认关闭。
- 当前 OOHStory 批注模型没有二进制附件来源，因此未实现或宣称 Joplin resource
  上传、替换与删除；这需要独立的附件 identity/provenance 契约。
- Windows 与 macOS 本轮没有真实构建、系统安全存储和 Web Clipper 权限验收；Linux
  只有 release build 与 fixture-backed API 行为证据，尚不是签名安装包。
- 移动端和服务端 Joplin 形态未开放；本轮也不包含双向同步或远端删除传播。

## 回退

保持或恢复 `OOHSTORY_JOPLIN_EXPORT_ENABLED=false` 即可移除产品入口。回退代码或在
应用内断开连接都不会删除 Joplin 中已经创建的笔记与标签。
