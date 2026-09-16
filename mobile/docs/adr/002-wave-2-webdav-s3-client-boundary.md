# ADR-002：Wave 2 WebDAV/S3 客户端边界

- 状态：ACCEPTED
- 日期：2026-09-15
- 范围：OOHStory Flutter 云书库第一阶段

## 背景

WebDAV 与 S3 适配器已经具备协议级 list/stat/read/write/delete、ETag、重试、路径隔离和
安全日志测试，但客户端没有连接配置、云目录入口或“云端文件 → 本地解析”的产品路径。
适配器源码存在不能等同于用户可用；同时，云凭据不能进入普通偏好设置、源码或日志。

## 决策

1. 非秘密连接信息（名称、HTTPS endpoint、根目录、bucket、region、寻址方式和经用户
   确认的 Web CORS 状态）以版本化 JSON 存入 SharedPreferences。
2. 用户名、密码、access key、secret key、session token 只写入
   `FlutterSecureStorage`，通过稳定的连接 ID 分区；删除连接时同步清除该分区。
3. 新连接必须先通过真实根目录 list 探测，成功后才保存。探测失败会恢复原凭据和配置，
   不留下“看似已连接”的半成品状态。
4. 云目录第一阶段提供分页浏览、进入目录、刷新、打开受支持电子书、条件新增上传和带
   ETag 的显式删除。下载内容先受 64 MiB 限制，再交给既有本地安全解析器。
5. 覆盖默认不开放：上传使用 `If-None-Match: *`；删除必须有服务端 ETag。冲突对用户
   可见，不静默覆盖。
6. WebDAV 与 S3 分别受 `OOHSTORY_WEBDAV_ENABLED`、`OOHSTORY_S3_ENABLED` 控制。
   未提供真实服务证据的正式构建继续默认关闭。
7. S3 配置显式记录 path-style 或 virtual-hosted-style；自建兼容端点不得隐式改写为
   virtual host。大对象使用 multipart，任一分片失败时尽力 abort。
8. Web 构建只有在目标源站的 HTTPS/CORS 已由操作者明确验证后才能创建适配器；浏览器
   中不建议保存长期 S3 secret，正式 Web 应改用短期凭据或同源代理。

## 组件与数据流

```text
ProductCapabilityProfile
        └─> Profile / 存储与同步
                └─> CloudConnectionRepository
                      ├─> SharedPreferences（非秘密配置）
                      └─> FlutterSecureStorage（连接分区凭据）
                └─> CloudAdapterFactory
                      ├─> WebDavCloudAdapter
                      └─> S3CloudAdapter
                └─> CloudLibraryScreen
                      ├─> list / 分页 / ETag
                      ├─> 条件上传 / 显式删除
                      └─> bounded read ─> LocalContentService ─> 本地阅读器
```

云服务永远只看到配置根目录以下的相对路径；界面和持久化层不能绕过 `CloudRoot`。

## 备选方案

- 把凭据与 endpoint 一起存入 SharedPreferences：拒绝，普通偏好设置不是秘密存储。
- 先保存再异步探测：拒绝，失败配置会被误认为已连接并增加后续恢复复杂度。
- 允许无 ETag 删除或默认覆盖同名文件：拒绝，弱网重试和并发设备下可能误删、误覆盖。
- 让云书库另写一套解析器：拒绝，会绕开现有格式安全限制并造成行为分叉。
- 第一阶段直接做后台双向文件镜像：拒绝；离线队列仍需文件级持久化、可观察状态和真实
  弱网证据，不能只依赖当前内存队列。

## 影响与未完成项

- 用户可在启用能力的构建中配置并浏览 WebDAV/S3，打开云端支持格式，而无需第三方
  OAuth 审核。
- 仍需真实 WebDAV/S3 sandbox、移动/桌面安全存储、弱网、10k 条目和大文件 multipart
  证据后才能默认开启。
- 当前离线 mutation store 仍是协议测试实现；后台文件镜像与持久队列不在本纵切冒充
  完成，后续需采用文件负载 + 小型索引，而不是把大文件 Base64 塞入偏好设置。

## 回滚

1. 关闭对应编译期开关即隐藏入口，不删除现有连接或云端文件。
2. 删除单个连接只清理本机配置和安全存储，不删除远端根目录。
3. 回退 multipart/path-style 改动时保留单 PUT 和现有协议接口；未完成的 multipart upload
   由 abort 或桶生命周期规则回收。
