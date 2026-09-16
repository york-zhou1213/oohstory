# OOHStory Wave 2 WebDAV/S3 验证证据（2026-09-15）

## 本轮交付

- “我的 → 内容工具 → 存储与同步”门控入口；不增加主导航目的地。
- WebDAV/S3 非秘密配置使用版本化本地设置，用户名、密码、access key、secret key、
  session token 只进入按连接分区的系统安全存储。
- 新连接先访问真实根目录验证，成功后保存；失败恢复此前凭据，不留下半连接状态。
- 云目录支持分页、目录进入/返回、刷新、受支持格式打开、create-only 上传，以及必须带
  当前 ETag 的确认删除。
- 云端 MOBI/AZW/AZW3/CBR/CBT/CB7 下载限制为 64 MiB，之后复用既有本地安全解析器；
  不建立绕开格式限制的第二套解析路径。
- WebDAV 207 member 错误映射、绝对 href 同源校验；重定向仍由底层 transport 禁止自动
  跟随。
- S3 显式 path-style / virtual-hosted-style、SigV4 和 multipart complete/abort；上传总量
  仍受现有 128 MiB 上限约束。
- ADR-002 记录凭据、覆盖、Web CORS、离线队列和回滚边界。

## 验证结果

```text
/opt/flutter/bin/flutter analyze
No issues found! (ran in 3.9s)

/opt/flutter/bin/flutter test
217 passed

/opt/flutter/bin/flutter test test/cloud
49 passed

/opt/flutter/bin/flutter build web --release --base-href /app/ \
  --dart-define=OOHSTORY_WEBDAV_ENABLED=true \
  --dart-define=OOHSTORY_S3_ENABLED=true
Built build/web

git diff --check
passed
```

云专项覆盖现有四类 provider 协议以及新增连接存储、失败回滚、凭据分离、云浏览、远端
本地解析、条件上传/删除、分页防循环、WebDAV 207/cross-origin、防 XML 实体、S3
virtual-host、multipart complete 与失败 abort。

## 尚未形成的发布声明

- `OOHSTORY_WEBDAV_ENABLED` 与 `OOHSTORY_S3_ENABLED` 仍默认关闭；本轮 Web 构建只证明
  启用路径可编译，不代表正式环境已经开启。
- 当前没有项目所有者提供的真实 WebDAV/S3 sandbox 与最小权限凭据，因此没有声称真实
  服务端、10k 目录、限流、配额、TLS/CORS 或两台设备端到端通过。
- multipart 已走真实协议分片，但客户端当前仍先在 128 MiB 上限内收集上传内容；更大
  文件的磁盘/流式 staging 尚未完成。
- 后台双向镜像和可观察的文件级持久离线队列尚未接线。现有内存 mutation store 只保留
  为协议测试，未冒充离线产品能力。
- Web 正式环境不应保存长期 S3 secret；需短期凭据或同源代理设计及生产授权。
- 本轮未连接 Dropbox/Google Drive，未写入任何真实凭据，也未执行生产部署或远端删除。

## 回滚

1. 关闭对应编译期开关即可隐藏入口，保留本机配置与远端数据。
2. 用户主动断开连接只删除本机设置和系统安全存储凭据，不触碰云端根目录。
3. S3 multipart 失败会尽力 abort；目标 bucket 仍应设置未完成 multipart 生命周期清理。
4. 回退本轮 UI/工厂不会改变现有账户状态、进度协议、本地阅读或其他 provider 适配器。
