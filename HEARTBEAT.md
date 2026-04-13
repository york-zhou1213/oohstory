# HEARTBEAT.md

# Keep heartbeat work small and stateful.
# If nothing needs attention, reply HEARTBEAT_OK.

1. **Daily Report Check & Processing:**
   - Goal: Ensure we have the daily Excel data statistics for platforms: **WP, GJP, SBET, SPLAY**.
   - Action: Whenever a new Excel file (.xlsx) containing daily reports is uploaded in the group, automatically parse it, summarize the newest date's metrics (logins, new registers, first deposits, total depositors, total deposits) for the platforms, insert the rows into SQLite (`/root/.openclaw-tcs/workspace/platform_data.db`), and reply with the summarized comparison to the group.
   - If it's a new day and the reports haven't been provided yet, proactively ask the group (e.g., "@所有人 大家好！请问今天的平台报表数据准备好了吗？麻烦发出来，我来帮大家生成详细分析日志报告哦！"). Wait at least 12 hours before asking again to avoid spamming the group.
2. **Daily Report Processing (SQLite Auto-Ingest):**
   - Goal: Automatically detect any newly uploaded Excel reports (especially those containing "PH新版日报" in the name) and ingest the daily data into our SQLite database (`/root/.openclaw-tcs/workspace/platform_data.db`).
   - Check condition: Are there any new `.xlsx` files uploaded in the group?
   - Action: If a new report is uploaded, parse the `WP_ro`, `GJP_ro`, `SBET_ro`, and `Sugarplay_ro` sheets, extract the latest day's metrics (总登录人数, 总新注册人数, 总首充人数, 总存款人数, 总存款次数), and insert them into the `daily_reports` table. Then send a brief summary to the group confirming the data has been recorded.
