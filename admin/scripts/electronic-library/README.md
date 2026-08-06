# Electronic Library

书库逻辑根目录固定为 `electronic-library/`，该路径是指向
`/srv/oohstory/library` 的软链接。正文、封面和全局拆书成果均通过这个稳定
路径访问。

脚本源码固定保存在项目内的 `scripts/electronic-library/`，systemd 也只从这里
启动脚本。部署时会把同版本副本同步到书库的 `_tools/`，方便在书库目录内手动
调用，但 `_tools/` 不是源码真值。

## 本地馆藏

- 爬虫：`scripts/electronic-library/txt80_crawler.py`
- 持久目录、任务与阅读指标：MySQL 8
- 短时调度与下载流：Redis 7
- 目录清单：`txt80/catalog.csv`
- 小说文件：`txt80/书籍/<分类>/<书名>__<作者>__<来源ID>.txt`
- 唯一全局拆书库：`txt80/全局拆书库/<书名>__<来源ID>/`
- 全局基调、剧情索引与精确字数/章节指标：MySQL 8
- 日志：`txt80/logs/`

任务具备分页与单书双层断点续传、失败重试、文件去重、SHA-256
校验和磁盘安全线。重复执行同一命令只处理未完成项目。

```bash
python3 scripts/electronic-library/txt80_crawler.py --pages 3200 --workers 4 --delay 0.35
python3 scripts/electronic-library/txt80_crawler.py --stats
```

## 基调自动更新与剧情手动更新

新书下载完成后只把明确的 `catalog_id` 原子写入独立入库队列，由
`oohstory-library-ingestion-index.service` 顺序执行。该 worker 每本最多读取约
80 KiB 分层样本，只补齐列表可见性、题材、基调和检索元数据；不会扫描整库，
不会为自动任务读取两遍全文建立阅读目录，也没有剧情索引执行分支。授权站点一轮
正文全部结束并释放下载缓冲后才启动这条队列，索引与爬取不并行争抢磁盘和内存。

后台人工点击书籍基调索引或剧情索引时，才会使用
`oohstory-library-derived-index.service`。剧情增量和完整重建只能由剧情区域的
手动按钮创建请求；目录同步、正文下载、封面与后处理均不能写入该手动请求文件。
`oohstory-library-derived-index-probe.timer` 在生产环境保持禁用。

```bash
systemctl status oohstory-library-ingestion-index.service
systemctl status oohstory-library-derived-index.service
```

Web 前端运行期间只轮询文件型状态探针，不会为状态刷新重复执行书目全表查询。

## 精确字数与章节索引

书架字数、章节数量、章节名称和阅读边界统一由正文文件机械扫描得出，不再使用
文件抽样比例估算。全库首次修复或解析规则升级后运行：

```bash
# 先查看需要重建多少本（不写入）
python3 scripts/electronic-library/rebuild_library_metadata.py --dry-run

# 增量重建全库；中断后重复执行即可续跑
python3 scripts/electronic-library/rebuild_library_metadata.py --workers 4

# 单本复核或强制重建
python3 scripts/electronic-library/rebuild_library_metadata.py --book-id 59998 --force
```

每本书生成
`txt80/全局索引/阅读目录/<catalog_id>.json`，记录准确章节顺序、名称、字节
区间、逐章字数、全书字数和解析状态；书架准确统计写入 MySQL，避免任何
SQLite 写锁。
未识别到可靠章节标题的作品不会伪造章节数，而是保留分片阅读并在报告中标记
`fallback_index`。默认报告写入
`txt80/全局索引/library-metadata-rebuild-report.json`。

全量任务使用两个并行服务：书目发现进程运行 `--discover-only`，正文下载
进程运行 `--skip-discovery --watch`，因此小说会边扫描边进入电子书库。

## 授权 / 公版远程书源

全局作品/作者搜索支持按单一来源选择：

- 本地电子书库：只查询已经收录的书目；
- `xbiquge.info`：站点所有者授权的全章节抓取来源；
- `ixdzs8.com`：站点所有者授权的爱下电子书来源，支持作品名/作者搜索，下载 ZIP 后安全提取 TXT；
- `linovelib.com`：用户配置的日译中轻小说来源，经官方跳转域抓取全章节与封面，统一归入“轻小说”分类；
- `txt80.cc`：站点所有者授权的在线 TXT 来源，与本地已下载目录分开查询；
- 番茄小说官方下载器：按准确 `bookId` 下载；
- `z-library.im`：站点所有者明确授权的测试来源，使用隔离浏览器完成站点
  JavaScript 校验，只导入 TXT/EPUB，默认单文件上限 120MB；
- Project Gutenberg：公版 TXT 来源。

远程来源都只在用户选择具体作品后下载。下载后由已配置 AI 在现有分类中保守归类，
统一转成 UTF-8 TXT，再写入 `txt80/书籍/<分类>/` 和本地目录数据库。
Z-Library EPUB 会按 OPF 书脊顺序抽取正文后转为 TXT。爱下、txt80.cc 与
Z-Library 适配器均限制请求频率、文件大小和下载跳转主机。

```bash
python3 scripts/electronic-library/txt80_crawler.py --search-txt80-online "超神机械师"
python3 scripts/electronic-library/txt80_crawler.py --import-txt80 13602 \
  --txt80-source-ref '/wangyou/txt13602.html'
python3 scripts/electronic-library/txt80_crawler.py --search-zlibrary "超神机械师"
python3 scripts/electronic-library/txt80_crawler.py --import-zlibrary 2J3jQ9mzko \
  --zlibrary-source-ref '/book/2J3jQ9mzko/%E8%B6%85%E7%A5%9E%E6%9C%BA%E6%A2%B0%E5%B8%88.html'
python3 scripts/electronic-library/txt80_crawler.py --search-public "Pride and Prejudice"
python3 scripts/electronic-library/txt80_crawler.py --import-gutenberg 1342
```

默认浏览器会话名是 `webnovel_zlibrary`。匿名额度不足时，可以在隔离的有界面
会话中登录一次；Web 后端和爬虫 CLI 会复用该会话，不需要把账号密码写入配置：

```bash
agent-browser --session webnovel_zlibrary open https://z-library.im/login --headed
```

可选环境变量：

```bash
WEBNOVEL_ZLIBRARY_ENABLED=1
WEBNOVEL_ZLIBRARY_DELAY=1.2
WEBNOVEL_ZLIBRARY_MAX_MB=120
WEBNOVEL_ZLIBRARY_DOWNLOAD_HOST_SUFFIXES=.ncdn.ec
WEBNOVEL_TXT80_ONLINE_ENABLED=1
WEBNOVEL_TXT80_ONLINE_DELAY=1.0
WEBNOVEL_TXT80_ONLINE_MAX_MB=120
WEBNOVEL_TXT80_DOWNLOAD_HOSTS=www.txt80.cc,txt80.cc,d.txt80.la,d.txt80.com
```

## 番茄扫榜下载桥

线上扫榜拿到作品的数字 `bookId` 后，可以调用官方
[Fanqie Novel Downloader](https://github.com/POf-L/Fanqie-novel-Downloader)
完成下载和全局入库：

```bash
python3 scripts/electronic-library/fanqie_library_bridge.py --status
python3 scripts/electronic-library/fanqie_library_bridge.py \
  --book-id 7246248681131740175 \
  --title '末日：看好了，校花是这么用的' \
  --author '田师傅'
```

默认下载完整 TXT；也可传 `--format epub`，入库时会按 EPUB 书脊顺序抽取正文并
统一写成 UTF-8 TXT。下载完成后，作品会由 AI 保守归类到
`txt80/书籍/<分类>/`，写入全局目录并自动排队刷新基调索引；剧情索引保持手动。
指定
`--start-chapter` 或 `--end-chapter` 的区间下载仅允许配合
`--download-only` 做验证，不会把残缺作品写入全局书库。

上游公开仓库只发布桌面安装包，核心源码仓库为私有，并且当前版本没有 CLI、HTTP
API 或 deep link。因此这里不复制其私有实现，而是固定使用经官方校验和验证的
Linux `v2026.7.26-709` 安装包，通过独立 Xvfb 会话自动操作桌面界面，再从官方
状态文件校验完成结果。运行依赖为 `fanqie-desktop`、`Xvfb` 和 `xdotool`；升级
桌面应用后必须先重新验证界面坐标和导出协议，未验证版本会被桥接器拒绝。
