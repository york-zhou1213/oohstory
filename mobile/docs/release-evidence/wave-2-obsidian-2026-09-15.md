# OOHStory Wave 2 Obsidian 单向导出验证证据（2026-09-15）

## 交付范围

- 新增统一 `DocumentIdentity`、批量 `AnnotationSink` 与 `ExportReceipt` 契约；回执记录
  provider、文档、规范化目标路径、内容 SHA-256、时间、处置结果及可选备份路径。
- 在“书签与批注”页增加受控的“导出到 Obsidian”入口。用户选择 Vault 与相对
  子目录后，按书生成带 YAML front matter 的 UTF-8 Markdown。
- 文件名由安全化书名与稳定文档 ID 哈希组成；批注按创建时间和 ID 确定性排序，正文不
  写入本次导出时间，因此相同输入产生相同字节。
- 上次导出的内容哈希保存在本机偏好设置中。目标文件不存在可信回执或回执后的内容被
  改动时，默认停止覆盖并显示确认；明确确认后先写入 `.oohstory-backups` 再原子替换。
- 相对目录拒绝绝对路径、`..`、反斜线和平台危险字符；目录链与目标文件拒绝符号链接
  越界。单书生成文件及冲突比对上限为 32 MB。

## 能力开关

`OOHSTORY_OBSIDIAN_EXPORT_ENABLED` 默认关闭。仅原生运行时显示入口；Web 即使编译时
传入开关也不会显示直接 Vault 写入功能。

本轮验证构建命令：

```text
/opt/flutter/bin/flutter build linux --release \
  --dart-define=OOHSTORY_OBSIDIAN_EXPORT_ENABLED=true
```

## 自动验证

- `/opt/flutter/bin/flutter analyze`：0 issues。
- `test/annotation_export` 与 `test/core/capabilities_test.dart`：13 项通过。
- `/opt/flutter/bin/flutter test --reporter json`：完成事件 `success: true`。
- Linux release bundle：构建成功；最终产物在
  `build/linux/x64/release/bundle/`。

覆盖的关键情形：

1. 安全、确定性的 Markdown 文件名和 front matter；多行摘录保持 blockquote 可读性。
2. 相同输入重复导出为 `unchanged`，不触碰目标文件修改时间。
3. 回执后的外部编辑会阻止静默覆盖。
4. 用户授权强制覆盖前，外部版本被复制到可恢复备份目录。
5. 已存在但无可信回执的同名文件按冲突处理。
6. 遍历/绝对子目录被拒绝；批量导出中的单书冲突不阻断其他书籍。
7. capability 默认隐藏，显式启用后才在原生笔记页显示入口。

## 未扩大声明

- 本轮只验证 Linux 原生构建和临时 Vault 的文件系统行为；Android SAF、iOS/iPadOS、
  macOS 与 Windows 仍需各平台的真实 Vault/权限验证后再单独开放。
- 当前批注数据没有附件字节来源，因此本轮未声称已导出附件；未来附件必须写到 Vault
  子目录并在 Markdown 中使用相对链接。
- 删除全部批注不会自动删除既有 Vault 文件，以免把用户在 Obsidian 中追加的内容当作
  OOHStory 所有数据删除。
- 本次 Obsidian 验证不覆盖 Notion、Readwise 或 Joplin；这些能力的后续状态以各自
  ADR、平台支持表和独立验证证据为准。

## 回滚

关闭 `OOHSTORY_OBSIDIAN_EXPORT_ENABLED` 即隐藏产品入口，不影响现有 ZIP 导出和本地
批注。已写入 Vault 的 Markdown 与 `.oohstory-backups` 归用户所有，回滚应用时不会
删除它们。
