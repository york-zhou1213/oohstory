import pandas as pd
import sqlite3
import os
import sys

file_path = "/root/.openclaw/media/inbound/PH新版日报2026年3月1-30日---905aebe7-5c15-4432-8662-00a921e1af24.xlsx"
db_path = "/root/.openclaw-tcs/workspace/platform_data.db"

sheets = ["WP_ro", "GJP_ro", "SBET_ro", "Sugarplay_ro"]
metrics_mapping = {
    "总登录人数": "logins",
    "总新注册人数": "new_registers",
    "总首充人数": "first_deposits",
    "总存款人数": "total_depositors",
    "总存款次数": "total_deposit_count",
    "总存款金额": "total_deposits"
}

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Create table if not exists
cursor.execute('DROP TABLE IF EXISTS daily_reports')
cursor.execute('''
CREATE TABLE daily_reports (
    date TEXT,
    platform TEXT,
    logins INTEGER,
    new_registers INTEGER,
    first_deposits INTEGER,
    total_depositors INTEGER,
    total_deposit_count INTEGER,
    total_deposits REAL,
    PRIMARY KEY (date, platform)
)
''')

summary = []

try:
    xl = pd.ExcelFile(file_path)
    for sheet in sheets:
        if sheet in xl.sheet_names:
            df = xl.parse(sheet)
            # Find the latest date row. Assuming column 0 is Date.
            # Convert to datetime to find the latest
            df.iloc[:, 0] = pd.to_datetime(df.iloc[:, 0], errors='coerce')
            latest_row = df.dropna(subset=[df.columns[0]]).sort_values(df.columns[0], ascending=False).iloc[0]
            
            report_date = latest_row[df.columns[0]].strftime('%Y-%m-%d')
            platform_name = sheet.replace("_ro", "")
            
            data = {"date": report_date, "platform": platform_name}
            
            for col_name in df.columns:
                clean_col = str(col_name).strip()
                if clean_col in metrics_mapping:
                    val = latest_row[col_name]
                    # Handle NaN
                    if pd.isna(val):
                        val = 0
                    data[metrics_mapping[clean_col]] = val
            
            # Insert into DB
            cursor.execute('''
            INSERT OR REPLACE INTO daily_reports 
            (date, platform, logins, new_registers, first_deposits, total_depositors, total_deposit_count, total_deposits)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                data.get("date"),
                data.get("platform"),
                int(data.get("logins", 0)),
                int(data.get("new_registers", 0)),
                int(data.get("first_deposits", 0)),
                int(data.get("total_depositors", 0)),
                int(data.get("total_deposit_count", 0)),
                float(data.get("total_deposits", 0))
            ))
            summary.append(data)
            
    conn.commit()
    print("SUCCESS")
    for s in summary:
        print(f"PLATFORM:{s['platform']}|DATE:{s['date']}|LOGINS:{s.get('logins',0)}|REG:{s.get('new_registers',0)}|1ST:{s.get('first_deposits',0)}|DEP_USR:{s.get('total_depositors',0)}|AMT:{s.get('total_deposits',0)}")

except Exception as e:
    print(f"ERROR: {e}")
finally:
    conn.close()
