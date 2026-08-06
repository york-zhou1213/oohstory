# 数亿级电子书库基础设施

目标运行栈固定为 MySQL 8.0、Redis 7 和 NAS/对象存储。

## 职责

- MySQL 是书目、书库归属、抓取游标、下载任务、对象索引和聚合统计的持久化真值。
- Redis 6379/DB6 只承担 Streams 消费者组、并发槽、锁和租约，不启用淘汰策略。
- 独立 Redis 6380/DB0 可选承担有界 JSON 热缓存，使用 `allkeys-lfu`、无 AOF/RDB；故障、清空或损坏时读请求直接回源 MySQL/NAS。
- NAS/对象存储保存正文与封面。MySQL 只保存对象键、大小和 SHA-256。

## 从空环境初始化

本仓库支持从官方 MySQL 8.0 空实例开始部署；不支持 MariaDB。初始化文件不包含
生产数据、固定密码或密钥。在仓库根目录执行：

```bash
sudo mysql < deploy/mysql/init.sql
umask 077
sudo mysql --batch --raw < deploy/mysql/runtime-users.sql \
  > /var/lib/oohstory-admin/mysql-generated-passwords.txt
```

`init.sql` 是空库专用的 MySQL client 脚本，会创建 `oohstory_library`、顺序执行
`001-022`、逐项写入 migration SHA-256，并创建无登录凭据的最小权限角色。发现目标
schema 已有任意表时会拒绝执行，禁止拿它覆盖或“修复”现有生产库。初始化途中
失败时，应核对错误并仅清理这次刚创建、尚无业务数据的 schema 后重试；不要在
已有库上强行重放 DDL。

`runtime-users.sql` 只创建三个限制在 `127.0.0.1` 的账号：

- `oohstory_library_writer`：业务管道使用，只有 DML 和临时表权限。
- `oohstory_library_reader`：本地管理后台状态页使用，只有 `SELECT`。
- `oohstory_public_reader`：公开 Reader 使用；全库只读，只能对匿名作品指标两张表执行必要的 `INSERT/UPDATE`。

MySQL 会为三者生成随机密码并仅在首次创建时输出。将 writer 密码保存到
`/etc/oohstory-admin/library-mysql-password`，reader 密码保存到
`/etc/oohstory-admin/mysql-password`，public-reader 密码保存到
`/etc/oohstory-reader/mysql-password`；密码文件不得进入 Git。完成后删除上面的 root-only 临时输出文件。

`deploy/mysql/init.sql` 由全部编号 migration 自动生成，migration 仍是唯一真源：

```bash
python3 scripts/electronic-library/render_mysql_init_sql.py --check
# 新增 migration 后：
python3 scripts/electronic-library/render_mysql_init_sql.py
```

现有数据库升级不得重跑 `init.sql`，使用：

```bash
OOHSTORY_LIBRARY_MYSQL_DATABASE=oohstory_library \
  python3 scripts/electronic-library/apply_mysql_migrations.py --admin-socket
```

`--admin-socket` 会让迁移 DDL 和 migration 台账全部通过本机 root socket 执行，
不再要求运行账号持有 DDL 权限。

## 安全切换顺序

1. 保持旧下载、目录同步、后处理、封面和拆书 timer 为 disabled/inactive。
2. 首次迁移时备份 SQLite 书目与书库归属数据库，并记录 SHA-256；生产验收、
   派生索引回填与保留期结束后删除旧 SQLite，不再作为日常回滚后端。
3. 按“从空环境初始化”创建独立 MySQL 数据库和最小权限账号。
4. 新空库已经执行完整 schema；旧库升级执行
   `apply_mysql_migrations.py --admin-socket`，再执行
   `migrate_catalog_to_mysql.py`；
   剧情旧索引使用 `migrate_plot_index_to_mysql.py` 一次性回填并核对全量行数。
   大型剧情索引可先用 `export_plot_index_tsv.py` 生成经过转义的顺序文件，再用
   `load_plot_index_tsv.py` 批量装载；装载器只在执行窗口临时开启
   `local_infile`，结束后必须恢复为关闭。
5. 比较源/目标行数、稳定字段摘要、分类统计和多组分页结果。
6. 以 `OOHSTORY_LIBRARY_CATALOG_BACKEND=mysql` 启动独立只读灰度实例。
7. 非破坏性同步正文和封面到 NAS；先快速登记，再执行完整 SHA-256 审计。
8. 生产先进入 `shadow`，观察双写错误和数据漂移；通过后切换为 `mysql`。
9. 只启用少量下载任务做受控验收。拆书任务单独确认后恢复。

首次迁移尚未验收时，失败应保持旧 SQLite 数据不变。正式切换并清理旧数据后，
生产必须在 MySQL 不可用时 fail-closed，禁止静默切回 SQLite。操作 Redis
不作为回滚依赖，可从 MySQL 的持久任务表重建；缓存 Redis 可单独重启清空并按需
回源重建，禁止把两个 Redis 端点合并。

## 运行约束

- MySQL 使用 `utf8mb4_0900_ai_ci`、InnoDB、READ COMMITTED。
- 当前 16 GiB 混合负载主机使用
  `90-oohstory-library.cnf`：6 GiB buffer pool、2 GiB redo、
  O_DIRECT、GTID、ROW binlog 和 500 ms 慢查询日志。
- 当前数据盘是单机械盘，因此 `innodb_io_capacity=200`。MySQL 达到千万行前应
  迁移到至少 2 TB 企业级 NVMe；禁止把 InnoDB datadir 放到 CIFS/NFS。
- `catalog_facets`、`catalog_status_counts` 和
  `public_catalog_facets` 由触发器增量维护。在线写入路径不得重新扫描
  `books` 执行分类或状态 `COUNT(*)`。
- 常规目录查询必须使用覆盖 library/body/category/status/word-count 的组合索引；
  深分页应逐步切换为基于 `id` 或复合排序键的 seek cursor，不能在数亿行上依赖
  大 OFFSET。
- MySQL ngram FULLTEXT 只承担当前书名/作者检索。数据接近亿级且搜索并发增长时，
  应把全文检索异步投影到 OpenSearch/Elasticsearch；MySQL 继续作为真值库。
- `download_jobs` 只保留活跃和近期完成任务。完成历史达到千万级前，应按月归档到
  独立历史表或冷存储，避免活跃队列索引无限膨胀。
- 新正文写入内容寻址对象键；迁移期允许读取 NAS 上的旧相对路径对象键。
- 书籍基调和剧情索引必须以逐书 `SHA-256 + 规则版本` 作为持久断点；常规
  增量刷新只处理缺失或失效断点，中断后禁止重新扫描已提交作品。
- 下载 dispatcher 和 worker 的后端模式完全由
  `/etc/oohstory-admin/library.env` 控制，service 文件不覆盖它。
- 下载 timer 和 worker 安装后默认不自动启用，必须在灰度验收后显式启动。

## 公共作品指标

- `book_public_metrics` 保存作品级匿名去重阅读数和下载数；
  `book_public_metric_visitors` 只保存固定 32 字节访客摘要及两类行为的首次时间，
  不保存原始 IP、User-Agent 或可逆标识。
- 访客表的 `(catalog_id, visitor_hash)` 主键同时承担按作品判重和外键索引；当前
  访问路径不按时间或跨作品查询访客，因此不增加高写放大的冗余二级索引。
- 首次行为判重和对应计数的 `counter = counter + 1` 必须放在同一个事务中；不得先
  读后写计数，也不得在事务外分别提交访客记录和聚合值。
- 迁移不创建用户或授权。现有公共 reader 使用逐表权限：部署流程需对
  `book_public_metrics` 和 `book_public_metric_visitors` 分别授予
  `SELECT, INSERT, UPDATE`。访客表的条件更新需要读取判重列，因此也必须保留
  `SELECT`；无需 `DELETE`、`ALTER`、
  `CREATE` 或 `GRANT OPTION`。

## 公共作品标识

- `book_public_ids` 为每本书保存独立的 128 位随机公开标识；OOH Story URL、封面、
  下载、章节和统计接口只使用该标识，连续的 `books.id` 保持为内部主键。
- 随机值不可由内部主键推导，也不是权限凭证；接口仍必须执行正文可用性检查、参数
  校验、同源限制和反向代理限流。
- `trg_books_public_id_after_insert` 为以后新增书目自动生成公开标识，迁移会为现有书目
  全量回填。公共 reader 只需要该映射表的 `SELECT` 权限。
