# OOHStory 电子书库 UI / 功能对照

本文件以 `webnovel-writer/frontend/src/views/LibraryView.vue`、
`LibraryCatalogView.vue`、`LibraryAssetView.vue` 和
`LibraryBookDetailModal.vue` 为只读参考，记录 OOHStory 自有实现的入口。
OOHStory 不在运行时调用或导入 `webnovel-writer`。

## 总览与导航

| 参考能力 | OOHStory 入口 | 状态 |
| --- | --- | --- |
| 书目总量、本地书库、番茄书库、正文可用 | `/admin/books/catalog?view=all/local/fanqie/readable` | 独立目录页，已接入真实 MySQL 数据 |
| 基调索引、剧情索引 | `/admin/books/catalog?view=tone/plot` | 独立目录视图，支持筛选和分页 |
| 全局拆书库 | `/admin/books/catalog?view=deconstruction` | 已接入 NAS manifest、状态和成果 |
| 电子书库总览 | `/admin/library` | 已接入指标、同步、封面、基础设施与任务 |
| 同步调度 | `/admin/library/sync` | 定时同步、按站全力同步和服务控制集中管理 |
| 作品详情 | `/admin/books/catalog/{catalog_id}` | 已接入书目、正文、索引与封面管理 |
| Reader 实际章节与指标 | `/admin/books/{public_id}` | 已接入 Reader 只读接口 |

## 全局书源搜索、归档与馆藏管理

| 参考能力 | OOHStory 入口 | 状态 |
| --- | --- | --- |
| 本地、授权站点、轻小说、番茄、Z-Library、公版搜索 | `/admin/books/search` | 9 类来源均使用固定 provider 白名单 |
| 远程下载归档及任务状态 | `/admin/books/import`、`/admin/books/jobs/{job_id}` | 后台任务，不阻塞页面 |
| 本地 / 番茄逻辑归库 | `/admin/books/catalog-action` | 支持单页多选；不改变来源真值 |
| 真实封面同步、AI 重绘 | `/admin/books/catalog-action` | 支持批量操作 |
| 安全上传替换封面 | `/admin/books/catalog/{id}` | 受限 MIME、大小、CSRF 和 root helper |
| 分类、正文状态、来源、标签筛选 | `/admin/books/catalog` | 支持组合筛选、页大小、页码和跳页 |
| 当前页全选 | `/admin/books/catalog` | 客户端选择，提交仍由服务端校验 |

## 同步与索引

| 参考能力 | OOHStory 入口 | 状态 |
| --- | --- | --- |
| 本地 / 番茄定时同步开关 | `/admin/books/sync-control` | 精确 systemd 白名单和审计 |
| txt80 / 新笔趣阁 / 爱下 / 书宝网 / 哔哩轻小说单站全力同步 | `/admin/books/site-full-sync` | 五个独立开关；固定每轮 100 本、轮次间隔至少 60 秒；授权站点共享安全执行槽 |
| 同步运行、上下次时间、封面进度 | `/admin/books`、`/admin/library` | 已展示真实状态 |
| 基调增量更新 / 完整重建 | `/admin/books/index`（`tone`） | 已接入 |
| 剧情增量更新 / 完整重建 | `/admin/books/index`（`plot`） | 已接入 |
| Redis 自动失效与回暖 | 独立 `127.0.0.1:6380` 缓存层 | MySQL/NAS 提交后失效并异步预热 |

## 全局拆书库

| 参考能力 | OOHStory 入口 | 状态 |
| --- | --- | --- |
| 全部、未开始、运行中、黄金三章、完整、异常筛选 | `/admin/books?view=deconstruction` | 已接入 |
| 单本黄金三章 / 完整拆书 | `/admin/books/deconstruction-action` | 已接入 |
| 本页批量和全部筛选结果 | `/admin/books/catalog-action` | 已接入 |
| 执行工具、模型、推理档位 | 拆书执行配置面板 | 固定可用 runner/profile 目录 |
| 断点续跑 | `/admin/books/deconstruction-action` | 已接入 |
| 任务步骤、进度与日志 | `/admin/books/tasks/{task_id}` | 已接入 |

## 剧情证据与改编

| 参考能力 | OOHStory 入口 | 状态 |
| --- | --- | --- |
| 单书证据分页 | `/admin/books/catalog/{id}/plot` | 已接入 |
| 剧情问答与候选证据 | `/admin/books/plot-workbench` | 已接入 |
| 改编计划与历史方案 | `/admin/books/plot-workbench` | 已接入 |
| 章节定位、差异预览 | `/admin/books/plot-workbench` | 已接入 |
| 绑定后续写作 / 确认写入正文 | `/admin/books/plot-workbench` | 原文哈希校验和可恢复备份 |

## 明确排除

“对标项目基调匹配”是用户明确排除的能力。OOHStory 不读取写作项目基线，
不把该功能伪造成按钮，其余电子书库管理闭环均有对应页面或 API。

## 全后台设计契约

- 天空蓝为默认背景与品牌色，暗色主题保留天空蓝层级，不使用外部资源。
- 桌面为固定信息导航；窄屏使用可关闭的抽屉导航。
- 320 / 390 / 430px 不产生页面级横向溢出；输入控件不低于 16px。
- 支持键盘焦点、跳至正文、Escape 关闭导航、减少动态效果与触控目标。
- 所有可见写操作必须经过现有 CSRF、固定白名单和审计；不增加假按钮。
