# OOHStory 增量产品 Intake（2026-09-15）

## 结论

本轮需求作为一个跨客户端、服务端、第三方平台和发行基础设施的发布计划接收，
不应按“已有源码文件”直接宣称功能完成。当前仓库已经具备一部分安全适配器、
同步契约和多平台构建骨架，但尚未形成全部可发现、可配置、可恢复、经过真实平台
验证的正式产品能力。

- 总体风险：**CRITICAL**。原因是阅读进度属于用户数据，云端与生态集成涉及长期
  凭据，Windows/macOS/iOS 发布涉及签名身份与外部商店，Web 切换涉及生产流量。
- 推荐交付方式：按本文的 5 个工作流、4 个发布波次推进；每个波次可独立关闭或
  回滚，不以“大版本一次性全开”为发布条件。
- 产品边界：只支持用户有权使用的无 DRM 内容；不实现或协助绕过 Kindle DRM，
  不复制 AGPL 项目源码、资源或私有数据结构。
- 当前外部动作授权：**无**。本文不授权创建第三方应用、接受商店协议、使用个人
  身份签名、上传商店、修改 DNS/Nginx、部署生产或迁移生产用户数据。

## 代码与部署边界

| 边界 | 规范源 | 本轮职责 |
| --- | --- | --- |
| Flutter 客户端 | `<workspace>/oohstory-app` | 本地格式、词典/OCR、云书库、同步客户端、桌面/iOS/Web 构建 |
| Reader 服务 | `<workspace>/oohstory-reader` | 账户态进度同步、KOReader 兼容服务、Web 同源发布工具 |
| Operations Admin | `<workspace>/oohstory-backend` | 不承载个人阅读同步；仅在发布需要运维可观测性时添加只读状态 |

基线版本：Flutter 工作区 `de2ed752cbbeb2464b47f192959991a600ca4778`，Reader
仓库 `f6f3aaa28ffc53d3558e7513a4eaf02102e85303`，Operations Admin 仓库
`9ae093d4c1aeafb7f2c0219967d8912944eb0ae3`。这些 SHA 只记录 intake 时的已提交
基线；三个工作区均存在未提交工作，实施时必须保留无关改动并重新确认实际基线。

## 现状核对

状态定义：`骨架` 表示已有接口或测试实现；`已接线` 表示用户能从产品入口使用；
`可发布` 表示真实平台、真实服务或签名产物已有验收证据。

| 能力 | 当前证据 | 状态 | 完整交付缺口 |
| --- | --- | --- | --- |
| MOBI/AZW/AZW3 | `KindleFormatDecoder`、格式测试 | 骨架 | 接入书架/阅读器；补 KF8、HUFF/CDIC 和真实书库语料；保持 DRM 拒绝 |
| CBR/CBT/CB7 | 安全归档解析、页面排序和测试 | 骨架 | 常见压缩 RAR/7z 兼容、分页缓存、方向/缩放、真实漫画语料 |
| MDX | 正式阅读器选词/手输查询；MDX v2 stored/zlib/LZO；MDD 图片/音频；持久词典管理；沙箱样式与词条跳转；真实第三方 v2/MDD 样本验证 | 已接线 | 继续扩充不同制作者的大型合法词典兼容语料；加密词典仍明确拒绝 |
| OCR | 本地接口、取消和资源上限 | 演示级 | 当前可移植引擎仅识别高对比度英文 PNG；需接原生中英 OCR 和相册/扫描入口 |
| WebDAV/S3/Dropbox/Drive | 四套云适配器及模拟 HTTP 测试 | 骨架 | OAuth/凭据 UI、持久离线队列、真实服务契约测试、冲突与恢复体验 |
| 跨设备进度 | 冻结 API 契约、MySQL 仓储、独立 staging unit | 服务端骨架 | Flutter 传输/本地队列接线、账户隔离、冲突 UI、生产灰度与迁移证据 |
| KOReader | 独立兼容服务、迁移和 staging 配置 | 服务端骨架 | 文档指纹映射、端到端设备测试、用户配置说明、正式域名与发布 |
| Readwise/Notion/Obsidian/Joplin | Obsidian、Notion、Readwise 原生端单向导出与 Joplin 桌面笔记/标签/资源导出已接线 | 部分已接线 | Readwise/Notion 与打包版 Joplin 真实账号/安全存储验收；OAuth、撤销/删除语义 |
| Windows/Linux/macOS | CI 可构建目标；Windows 安装器、Linux deb/rpm/AppImage、macOS ZIP/DMG 打包已接线 | 构建与打包 | 签名/公证、更新通道、干净机安装与升级/降级证据 |
| iOS | CI `--no-codesign` 构建 | 构建骨架 | Bundle/entitlements、签名、TestFlight、商店元数据与审核流程 |
| Web 同源部署 | `/app/` 原子发布和回滚工具 | staging 骨架 | 与现有 Reader Web 的路由归属决策、生产构建、CSP/缓存/E2E、灰度切换 |

因此，`docs/platform-support.md` 中“格式、云、签名发行仍为缺口”的说法仍然成立；
现有适配器不能单独作为正式支持声明。

## 已确认的产品约束

1. 云端文件与 OOHStory 账户进度是两个独立同步域。连接 WebDAV/S3/Dropbox/Drive
   不得自动上传账户数据；用户分别开启并可分别断开。
2. 阅读进度使用稳定 `book_id + document_version`，位置由格式适配器保存为不透明值，
   同时保留 0–1 百分比作为跨引擎降级位置。
3. 同步写入使用服务端 revision 和条件请求；409 不静默覆盖。UI 必须让用户选择
   本机、云端或“取更远阅读位置”，选择结果作为新 revision 上传。
4. OAuth 使用授权码 + PKCE；刷新令牌只存系统安全存储。WebDAV/S3 凭据同样不得
   进入普通偏好设置、日志、崩溃报告或仓库。注销默认清除本机凭据，但不删除远端文件。
5. 所有远端对象写入都限定在用户选择的 OOHStory 根目录，支持 ETag/版本条件；
   离线队列必须落盘、按账号隔离、加密且可检查/取消，不能继续使用纯内存队列作为正式实现。
6. 导出记录携带稳定来源 ID 和内容哈希，实现幂等重试。删除本地高亮不会默认删除
   第三方内容，除非用户显式开启双向删除并再次确认。
7. 自动更新只接受签名清单与 HTTPS 产物；校验失败保持旧版本，不执行任意脚本。
8. 正式发布必须区分“可编译”“可安装”“已签名”“已商店/生产发布”四种状态。

## 工作流与验收标准

### A. 格式、MDX 与 OCR

交付范围：把现有 `adapters/formats`、`adapters/dictionary`、`adapters/ocr` 和
`features/local_content` 接入正式书架与阅读工作区，而不是另设不可发现的演示页。

验收：

1. 可从文件选择器或系统“打开方式”导入无 DRM 的 MOBI、AZW、AZW3、CBR、CBT、CB7；
   格式按魔数探测，不只信任扩展名。
2. Kindle 验收集覆盖 MOBI6/7、KF8/AZW3、PalmDOC、HUFF/CDIC、UTF-8/CP1252；
   DRM、加密、损坏和解压炸弹样本稳定拒绝并给出可理解错误。
3. 漫画验收集覆盖常见 RAR4/RAR5、tar、7z 压缩组合及自然页序；方向、缩放、
   预读和缓存不会一次解压整本导致内存失控。
4. 阅读位置在关闭/重启后恢复；替换内容后通过 `document_version` 避免跳到错误位置。
5. MDX 至少支持常用 v2 词典、zlib/LZO 块、MDD 图片/音频资源、词条跳转和本地样式
   沙箱；词典可启停、排序和移除，不把查询词上传。
6. OCR 在 Android/iOS/macOS/Windows 使用受支持的本地系统或打包引擎，至少支持
   简体中文和英文；Linux 给出明确支持矩阵；Web 若无本地引擎则隐藏入口而非远程上传。
7. 真实设备完成大文件、低内存、取消、后台/前台切换和恶意归档回归测试。

### B. 云端书库与跨设备进度

交付范围：增加统一“存储与同步”设置、云书架入口、可观察队列和账户进度传输。

验收：

1. WebDAV 完成 PROPFIND/list/stat/read/write/delete、分页、ETag 和 207 错误映射；
   默认要求 HTTPS，重定向不得逃出配置源站。
2. S3 支持 AWS S3 和明确配置的 S3-compatible endpoint、SigV4、分页、条件写入及
   大文件 multipart；path-style 仅按端点配置开启。
3. Dropbox 与 Google Drive 使用独立应用和 PKCE，默认仅申请应用文件夹/最小权限；
   token 刷新、撤销、限流、分页和账号切换通过真实 sandbox 验收。
4. 云端新增、更新、删除及离线重放具备幂等性；弱网、超时、429、5xx、配额不足和
   ETag 冲突均不会丢失或误覆盖文件。
5. Flutter 客户端接入 `/api/v1/sync/progress`：登录后可手动同步和按用户选择自动同步；
   未登录继续纯本地工作。
6. 两台真实设备对同一本、不同本、离线并发和内容版本变化进行端到端测试；冲突可见、
   可恢复，退出账号后不显示前一个账号的队列或进度。
7. 服务端按用户隔离、限流、审计（不记录位置正文/凭据），并有迁移前备份、向下迁移
   或禁用路由后保留数据的回滚路径。

### C. 生态联动

统一中间模型为 `DocumentIdentity`、`Annotation`、`ProgressRecord` 和 `ExportReceipt`；
第三方特有字段保存在 namespaced metadata，不污染核心阅读模型。

验收：

1. KOReader：使用现有隔离服务完成注册/鉴权/进度读写；通过内容指纹建立 KOReader
   document 与 OOHStory `book_id` 的可解释映射，真实 KOReader 设备双向续读通过。
2. Readwise：高亮、笔记、书目信息按游标增量同步；重试不产生重复记录，冲突和远端
   删除策略可配置，API 限流有退避。
3. Notion：作为单向导出，用户选择目标 database/page；重复导出更新同一条记录，
   不默认读取整个工作区。
4. Obsidian：以可读 Markdown + front matter 写入用户指定 vault 子目录，附件使用相对
   路径，文件名安全且稳定；检测外部修改并在覆盖前提示。
5. Joplin：第一阶段采用用户显式授权的 Joplin Data API（桌面）完成笔记/标签/资源
   幂等同步；移动端支持需在确认可用的 Joplin 服务接口后另行开放，不伪装为全平台能力。
6. 每个集成都能单独断开、导出诊断信息（脱敏）和清除本地 token；某个平台故障不阻塞
   其他同步域或本地阅读。

### D. 桌面正式发布

验收：

1. Windows 产出签名 MSIX 或安装器，并提供签名 App Installer/更新清单；干净 Windows
   10/11 完成安装、覆盖升级、自动更新、卸载与用户数据保留测试。
2. macOS 产出 universal 或分别标记架构的 DMG/PKG，使用 Developer ID 签名、启用
   Hardened Runtime，完成 notarization 与 stapling；Gatekeeper 离线校验通过。
3. macOS 若启用应用内更新，使用签名更新源并验证升级/失败回退；未完成前不显示自动
   更新入口。
4. Linux 产出 AppImage、deb、rpm，明确支持的发行版/架构；文件关联、桌面图标、
   sandbox/secret-service 依赖、安装/升级/卸载通过干净 VM 验收。
5. 所有产物由 tag 驱动的可复现 CI 生成，附 SHA-256、SBOM、版本号和 changelog；
   不把签名密钥写入仓库或普通 CI 日志。

### E. iOS 与 Web 正式发布

验收：

1. iOS 固定 Bundle ID、版本策略、entitlements、隐私清单和最小权限；签名材料使用
   受保护的 SecretRef/CI secret，并有轮换说明。
2. TestFlight 由 tag/批准门触发，上传成功后至少完成一台 iPhone 和一台 iPad 的
   登录、导入、阅读、同步、后台恢复与崩溃检查。
3. App Store 材料包含隐私标签、数据删除说明、审核账号/说明、截图、年龄分级和出口
   合规；提交及协议接受必须由获得明确授权的人执行。
4. Flutter Web 以同源 `/app/` 发布，API、OAuth 回调、深链和静态资源路径不依赖
   跨域放行；刷新任意客户端路由不会 404。
5. Web 发布使用内容寻址目录与原子 current 链接；CSP、service worker 缓存、版本漂移、
   Safari/Chrome/Edge 响应式 E2E 和回滚演练通过后，才能从现有 Reader Web 切换归属。

## 测试矩阵

| 层级 | 必须覆盖 |
| --- | --- |
| 单元 | 每个格式/协议的正常、边界、恶意和取消路径；ID/版本/冲突模型；导出幂等 |
| 契约 | WebDAV/S3/Dropbox/Drive sandbox；Reader sync API；KOReader；第三方 API 录制的脱敏契约 |
| 集成 | 安全存储、离线持久队列、账号切换、token 刷新、网络中断、迁移与回滚 |
| E2E | 手机/平板/桌面/Web 的导入→阅读→同步→另一设备续读；高亮→第三方→重试 |
| 兼容 | 真实、合法、去标识化的电子书/漫画/MDX 语料；Windows 10/11、受支持 macOS/Linux/iOS |
| 安全 | 归档穿越/炸弹、XML 实体、SSRF/重定向、凭据泄漏、OAuth state/PKCE、签名与更新劫持 |
| 性能 | 首开时间、翻页、峰值内存、流式下载、10k 书目分页、弱网/离线队列和电量影响 |
| 可访问性 | 键盘/读屏、动态字体、触控目标、对比度、缩放和减少动态效果 |

## 交付顺序

### 波次 0：契约与可发布基线

- 冻结稳定文档 ID、位置、批注和导出回执模型。
- 将已有适配器接入 capability registry，但默认关闭未通过真实服务/平台验证的能力。
- 建立真实但去标识化的格式语料、第三方 sandbox 和 release evidence 清单。

### 波次 1：本地阅读 + OOHStory/KOReader 进度

- 完成本地格式、MDX、原生 OCR 的产品接线。
- 完成 OOHStory 账户进度客户端和现有 KOReader 服务的端到端验证。
- 这是最小可用竖切：不依赖 Dropbox/Google/商店账号即可交付。

### 波次 2：云书库 + 生态导出

- 先 WebDAV/S3，再 Dropbox/Google Drive；前两者不依赖第三方 OAuth 审核。
- 先 Obsidian/Notion 单向导出，再 Readwise/Joplin 增量同步，降低双向删除和冲突风险。

### 波次 3：桌面发行

- Linux 非签名生态包先验证打包结构；Windows 签名更新和 macOS 签名/公证分别灰度。
- 每个平台使用独立 capability flag，不能因一个平台签名阻塞其他平台构建。

### 波次 4：TestFlight、App Store 与 Web 切换

- TestFlight 先验证真实 Apple 权限、OAuth 回调和后台行为，再准备 App Store。
- Web 在 `/app/` 灰度；确认旧 Reader Web 路由、SEO 和缓存责任完成迁移后再切正式入口。

## 依赖与需用户授权的阻塞项

实施可以先从波次 0/1 开始，但以下动作到达发布阶段前必须由项目所有者提供或明确授权：

- Apple Developer Team、Bundle ID、签名证书/App Store Connect API Key，以及协议/税务状态；
- Windows 代码签名证书与发布域名；macOS Developer ID 与 notarization 权限；
- Dropbox 和 Google Cloud OAuth 应用、回调域名、隐私政策及所需审核；
- Readwise/Notion 的应用或集成凭据，以及 Joplin 桌面 Data API 的真实测试实例、目标
  notebook 与 token；
- S3 兼容目标清单、测试桶和最小权限测试凭据；
- Web `/app/` 的正式域名/路由归属、灰度窗口及生产部署授权。

任何凭据只进入批准的 SecretRef、系统安全存储或受保护 CI secret，不进入本 intake、
源码、测试夹具、日志或聊天。

## 回滚原则

1. 客户端能力全部受 capability flag 控制；远端故障时退回本地阅读和本地进度。
2. 同步 schema 使用可逆迁移；禁用路由/服务不删除用户数据，物理删除需单独明确授权。
3. 云写入以新对象或条件覆盖为主；覆盖前保留版本/ETag，批量删除默认不开放。
4. 桌面和 Web 保留上一个签名/内容寻址版本；更新或健康检查失败原子切回。
5. iOS 通过停止 TestFlight/App Store 分阶段发布回滚，不依赖远程强制降级；服务端兼容
   至少当前与前一个已发布客户端契约。

## Intake 完成定义

本 intake 在以下条件下视为完整：需求已逐项映射到代码边界、现状、约束、可测试验收、
依赖、外部授权和回滚；没有把适配器骨架或 unsigned build 误报为正式发布。后续实施
应从波次 0/1 建立纵向可用版本，并在获得相应外部授权后再执行签名、商店和生产动作。
