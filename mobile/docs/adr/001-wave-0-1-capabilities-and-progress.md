# ADR-001：波次 0/1 的能力开关与阅读进度协议

- 状态：ACCEPTED
- 日期：2026-09-15
- 范围：OOHStory Flutter 客户端与 Reader 同步服务

## 背景

Flutter 客户端已经包含本地 Kindle/漫画解析、MDX、OCR、云存储适配器和跨平台
进度模型；Reader 仓库包含独立的 `/api/v1/sync/progress` 服务。现有正式客户端还
通过 `/api/v1/me/state` 同步历史和书架。源码存在不能等同于正式可用，未经过真实
平台或生产验证的能力必须保持关闭。

## 决策

1. `/api/v1/me/state` 继续承载现有历史、收藏和书架兼容，不在本波次移除或迁移。
2. `/api/v1/sync/progress` 作为增量的专用进度协议，客户端实现冻结的 v1 transport，
   并由 `OOHSTORY_PROGRESS_SYNC_ENABLED` 编译期开关控制。服务端正式路由未发布前
   默认关闭。
3. 本地进度始终先写设备存储；网络同步失败不得阻断翻页、退出阅读器或离线使用。
4. 服务端 revision 与 `If-Match` 是唯一覆盖前提。409 返回当前远端记录，客户端不得
   静默重试覆盖。
5. 高级本地阅读入口放在“我的 → 内容工具”。它不增加第五个主导航目的地，也不改动
   正在演进的主导航结构。
6. capability registry 是对外呈现能力的唯一判断源。解析器支持声明与产品启用状态
   分离；云端、专用进度同步和演示级 OCR 默认关闭。
7. Reader 现有 KOReader 服务保持数据库、身份和路由隔离；本波次只验证其冻结契约，
   不把 KOReader 密码或进度写入 OOHStory 账户表。

## 客户端/服务端契约

沿用 Reader 仓库 `docs/contracts/CONTRACT-20260823-001.yaml`：

- `GET /api/v1/sync/capabilities`
- `GET /api/v1/sync/progress?cursor=&limit=`
- `PUT /api/v1/sync/progress/{book_id}`，更新时带 `If-Match`
- `DELETE /api/v1/sync/progress/{book_id}`，必须带 `If-Match`
- Bearer session、JSON、RFC3339 UTC、revision、tombstone 和标准错误 envelope

客户端忽略响应中的未知字段，拒绝缺少必填字段、非 UTC 时间、越界百分比、过大响应、
非 JSON 响应和不安全的非 HTTPS 正式端点。

## 依赖图

```text
CapabilityProfile ──> Profile 内容工具 ──> LocalContentHub
        │                                      ├─> Kindle/Comic decoders
        │                                      ├─> MDX dictionary
        │                                      └─> local OCR adapter
        │
        └─> progress-sync flag ──> OohStoryProgressTransport
                                      ├─> AccountService.authHeaders
                                      ├─> /api/v1/sync/progress
                                      └─> ProgressRecord / CoreException

KOReader client ──> isolated KOReader HTTPS service ──> isolated SQLite
```

关键路径是 capability/profile 接线与 progress transport 契约测试。第三方 OAuth、
签名发行和生产部署不在这条关键路径上。

## 备选方案

- 直接用新协议替换 `/api/v1/me/state`：拒绝。会同时改变书架、历史和阅读位置，回滚
  面过大。
- 发现适配器类就显示功能：拒绝。会把 mock/演示能力误报为正式能力。
- 复用 KOReader 身份存储作为 OOHStory 账户同步：拒绝。破坏隔离边界并扩大凭据风险。
- 在主导航增加第五个入口：拒绝。四目的地结构已经是跨屏设计基线。

## 影响

- 好处：现有行为保持兼容；本地阅读可发现；新同步协议可独立测试和灰度；未验证能力
  不会误开放。
- 代价：过渡期存在两种同步协议；产品状态页和发布证据必须明确区分两者。
- 风险：如果服务端只部署独立 staging 而没有同源代理，新 transport 会得到 404；
  因此正式开关必须与服务端路由发布绑定。

## 回滚

1. 关闭 `OOHSTORY_PROGRESS_SYNC_ENABLED`，客户端继续使用本地进度与现有书架同步。
2. 从“内容工具”隐藏高级本地阅读入口不会删除用户本地文件。
3. 停用独立同步路由/服务但保留数据库；数据物理删除需要单独明确授权。
4. KOReader 服务可独立停用，既不修改 OOHStory 账户，也不影响主 Reader 服务。
