# OOHStory Wave 2 Joplin resource 验证证据（2026-09-16）

## 本轮交付

- 为批注导出增加可选附件契约：稳定附件 ID、所属书籍/批注、文件名、媒体类型、
  不可变字节，以及明确的 `(source, sourceId)` 来源标记。
- 通过 Joplin Data API 的 multipart `data` + `props` 上传和更新 resource；单个附件
  限制 16 MiB，单书最多 64 个、合计 64 MiB。
- 由 target/document/attachment 身份派生稳定 resource ID；multipart 写入字节后，
  再以幂等 JSON PUT 固化 MIME、filename 与 `user_data`，并保存 local/remote
  fingerprint。旧 note-only 和旧生成式 resource 映射仍可读取。
- 把 target、document、annotation、attachment 与 provenance 写入 resource
  `user_data`。状态丢失或首次 POST 已提交但连接断开时，有界扫描最多 2,000 条
  resource 元数据，按完全相同的来源标记恢复，不重复创建。
- resource POST 不自动重试；GET 和幂等 PUT 复用有界重试。每次协调读取元数据和
  远端字节哈希，外部修改或删除默认阻止，只有用户明确确认才替换；归属标记变化
  永远拒绝覆盖。
- 确认后的 resource ID 以 Joplin `:/<id>` Markdown 形式写入书籍批注笔记。
  本地移除附件只删除笔记引用和本地映射，不自动删除 Joplin resource。

架构边界记录在
`docs/adr/006-joplin-resource-provenance-and-idempotency.md`。除官方文档与源码核对外，
协议现已通过隔离的 Joplin CLI 3.7.1 Data API 实例真实写入验证。该版本的 multipart
resource 路径只可靠应用 ID/title，note POST 也不应用 `application_data`，因此两者均在
创建后执行幂等元数据 PUT。

## 自动化验证

### 静态分析

```text
/opt/flutter/bin/flutter analyze
Analyzing oohstory-app...
No issues found!
```

### Joplin 与批注 UI 定向套件

```text
/opt/flutter/bin/flutter test \
  test/annotation_export/annotation_attachment_storage_test.dart \
  test/annotation_export/joplin_export_test.dart \
  test/annotation_export/offline_notes_screen_test.dart \
  test/core/capabilities_test.dart \
  test/reading_bookshelf_contract_test.dart \
  --reporter expanded
00:05 +43: All tests passed!
```

定向场景覆盖：

- 原有连接凭据隔离、loopback 边界、note/tag 幂等与 UI capability 门控；
- 稳定 resource ID、两阶段元数据固化、旧来源映射复用与 Markdown 引用；
- resource 外部修改阻断、显式替换、归属标记不可覆盖；
- 多附件先完成整批只读冲突预检，再执行首个写入；来源恢复只扫描一轮；
- resource create 与 note create 两种“已提交后断线”恢复，均不重复创建；
- 旧 note-only 状态兼容；移除本地引用时保留远端 resource。
- 本地附件复制、内容幂等、篡改检测、级联删除、16 MiB 限制，以及 schema 3
  离线备份/恢复；
- 批注页显示附件数量，并提供添加、管理和删除入口。
- 普通批注 ZIP 的附件索引、相对链接和二进制内容；阅读器批注抽屉复用附件入口。
- Data API token/端口验证、分页 notebook 发现、层级路径选择、异常层级拒绝与
  10 秒请求超时；保存前再次验证目标 notebook；官方 41184..41194 端口范围使用
  无 token `/ping` 有界发现，避免错误端口接收令牌。

### 真实 Joplin Data API 验收

使用隔离的一次性 Joplin CLI 3.7.1 profile 启动官方 Data API（端口 41190），临时
token 仅通过进程环境传入且未写入仓库、日志或证据：

```text
OOHSTORY_JOPLIN_E2E_PORT=<port> \
OOHSTORY_JOPLIN_E2E_TOKEN=<redacted> \
OOHSTORY_JOPLIN_E2E_NOTEBOOK_ID=<id> \
  flutter test test/annotation_export/joplin_live_acceptance_test.dart
00:00 +1: All tests passed!
```

真实流程覆盖无 token `/ping`、分页 notebook 读取和目标验证，以及带 token 的 note、
tag、resource 创建与读回。首次导出创建 1 条笔记、2 个已关联标签和 1 个附件；第二次
导出为 unchanged；外部修改笔记后默认产生冲突，显式覆盖后恢复。服务端独立读回再次
确认 `LIVE_NOTES=1`、`LIVE_ATTACHED_TAGS=2`、`LIVE_RESOURCES=1`，附件 MIME、文件名、
字节数、ownership marker 与笔记正文引用均一致。

该 live 测试在未提供三个显式环境变量时默认跳过，不会访问开发者或 CI 机器上的
任意 Joplin 实例。

### 全量 Flutter 回归

```text
/opt/flutter/bin/flutter test --reporter compact
00:41 +267 ~1: All tests passed!
```

`~1` 是未提供显式 live 环境变量时按设计跳过的 Joplin 真实验收；上述独立 live
命令已在隔离实例上通过。

### Linux release build

```text
/opt/flutter/bin/flutter build linux --release \
  --dart-define=OOHSTORY_JOPLIN_EXPORT_ENABLED=true
✓ Built build/linux/x64/release/bundle/oohstory
```

构建前确认 `CMAKE_INSTALL_PREFIX` 指向项目内
`build/linux/x64/release/bundle`，并在构建后确认可执行文件存在。

### Web 隔离与 `/app/` 路径

```text
/opt/flutter/bin/flutter build web --release --base-href /app/ \
  --dart-define=OOHSTORY_JOPLIN_EXPORT_ENABLED=true
✓ Built build/web
```

构建后确认 `build/web/index.html` 的 base href 为 `/app/`。即使误开 Joplin flag，
tree-shaken `main.dart.js` 也不包含“Joplin 导出”“连接 Joplin 桌面版”
“Data API token”“自动查找本机 Joplin”“验证并读取笔记本”“目标笔记本”
“保存并导出”或附件管理等桌面 UI 文本。

## 仍未宣称完成

- 已完成官方 Joplin CLI Data API 的隔离真实写入，但尚未在打包后的 Joplin Desktop
  GUI 与系统安全存储上完成 Linux/Windows/macOS 人工验收，因此能力继续默认关闭。
- 不做远端 resource 自动清理；Windows/macOS 真实构建、安全存储与 Web Clipper 权限
  验收也仍待完成。

## 本地附件闭环补充

同日后续切片已补齐产品入口与本地持久化：原生批注页可以添加、查看和移除附件；
文件复制到应用自有目录，元数据与 SHA-256 单独持久化。Joplin 导出只在需要时异步
读取并校验附件，Obsidian/Notion 文字导出不受附件损坏连带影响。单文件 16 MiB、
单书 64 个/64 MiB，与远端预检保持一致。

离线备份升级为 schema 3，包含附件元数据与校验后的二进制；恢复兼容 schema 1/2，
schema 3 会在替换附件目录前验证 ID、批注归属、大小和哈希。架构决策见
`docs/adr/007-local-annotation-attachment-storage.md`。

后续完善把同一套附件选择与管理组件接入本地阅读器的批注抽屉，用户无需离开阅读
上下文即可操作附件。普通“导出批注”ZIP 保留原有 CSV、Markdown、HTML、TXT 和
JSON，并新增 `attachments.json` 与校验后的附件文件；Markdown/HTML/TXT 使用相对
路径引用附件，归档路径只使用应用生成 ID 和安全扩展名，总附件上限 256 MiB。

Joplin 连接配置也已从手填 notebook ID 升级为 Data API 两步验证：先校验 loopback
端口与 token，再分页读取最多 2,000 个 notebook 的 `id,parent_id,title`，生成层级
路径供用户明确选择。重复 ID、缺失父级、循环层级、超过 100 层或超出读取上限都会
失败且不写入。每个请求与响应流均有 10 秒超时，超时会取消底层请求；保存前再次
验证所选 notebook。配置阶段不搜索笔记、不读取正文，也不创建 notebook。详见
`docs/adr/008-joplin-notebook-discovery.md`。

端口可以继续手填，也可以按 Joplin 官方建议在 41184..41194 范围内顺序自动查找。
探测请求为无 token 的 `GET /ping`，每端口最多等待 750 ms，识别到首个严格匹配的
`JoplinClipperServer` 响应后立即停止；只有后续受保护的 Data API 请求才附带 token。

## 回退

保持或恢复 `OOHSTORY_JOPLIN_EXPORT_ENABLED=false` 即可移除产品入口。回退适配器或
断开连接只清理本地状态，不删除已写入 Joplin 的笔记、标签或 resource。
