import pandas as pd
import sqlite3
import os

file_path = '/root/.openclaw/media/inbound/PH新版日报2026年3月1-27日---3f630413-1d1f-46a2-bc9c-b97cec65b89e.xlsx'
db_path = '/root/.openclaw-tcs/workspace/platform_data.db'

# Define mappings
sheets = {
    'WP_ro': 'WP',
    'GJP_ro': 'GJP',
    'SBET_ro': 'SBET',
    'Sugarplay_ro': 'SPLAY'
}

# Connect to DB and ensure table has correct structure
conn = sqlite3.connect(db_path)
cursor = conn.cursor()
cursor.execute('DROP TABLE IF EXISTS daily_reports')
cursor.execute('''
    CREATE TABLE IF NOT EXISTS daily_reports (
        date TEXT,
        platform TEXT,
        logins INTEGER,
        new_registers INTEGER,
        first_deposits INTEGER,
        total_depositors INTEGER,
        total_deposits REAL,
        PRIMARY KEY (date, platform)
    )
''')

summary_data = []

try:
    for sheet_name, platform_code in sheets.items():
        # Load the sheet and find where the data starts
        # Some reports have headers in the first few rows
        df = pd.read_excel(file_path, sheet_name=sheet_name)
        
        # Look for the row that contains '日期' or 'Date'
        header_row_idx = -1
        for i, row in df.iterrows():
            if any('日期' in str(cell) or 'Date' in str(cell) for cell in row):
                header_row_idx = i
                break
        
        if header_row_idx != -1:
            # Re-read with correct header
            df = pd.read_excel(file_path, sheet_name=sheet_name, header=header_row_idx + 1)
        
        # Clean column names
        df.columns = [str(c).strip() for c in df.columns]
        
        # Remove empty rows or rows where date is null
        df = df[df['日期'].notnull()]
        
        # Convert columns to numeric, coerced to NaN
        df['总登录人数'] = pd.to_numeric(df['总登录人数'], errors='coerce')
        
        # Filter for the latest row that has some activity (logins > 0)
        data_rows = df[df['总登录人数'] > 0]
        if data_rows.empty:
            continue
            
        latest_row = data_rows.iloc[-1]
        
        # Extract data with more flexible column matching
        def get_val(row, names, default=0):
            for name in names:
                if name in row:
                    val = row[name]
                    try:
                        return float(val) if not pd.isna(val) else default
                    except:
                        return default
            return default

        data = {
            'date': str(latest_row.get('日期', '')).split(' ')[0],
            'platform': platform_code,
            'logins': int(get_val(latest_row, ['总登录人数', 'Logins'])),
            'new_registers': int(get_val(latest_row, ['总新注册人数', 'New Registers'])),
            'first_deposits': int(get_val(latest_row, ['总首充人数', 'First Deposits'])),
            'total_depositors': int(get_val(latest_row, ['总存款人数', 'Depositors'])),
            'total_deposits': float(get_val(latest_row, ['总存款金额', 'Total Deposits']))
        }
        
        # Insert into DB
        cursor.execute('''
            INSERT OR REPLACE INTO daily_reports 
            (date, platform, logins, new_registers, first_deposits, total_depositors, total_deposits)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (data['date'], data['platform'], data['logins'], data['new_registers'], data['first_deposits'], data['total_depositors'], data['total_deposits']))
        
        summary_data.append(data)

    conn.commit()
    
    # Generate summary text
    if summary_data:
        report_date = summary_data[0]['date']
        print(f"--- 报表日期: {report_date} ---")
        for d in summary_data:
            print(f"平台: {d['platform']}")
            print(f"- 登录人数: {d['logins']}")
            print(f"- 新注册: {d['new_registers']}")
            print(f"- 首充人数: {d['first_deposits']}")
            print(f"- 存款人数: {d['total_depositors']}")
            print(f"- 存款金额: {d['total_deposits']:.2f}")
            print("---")
    else:
        print("未提取到有效数据。")

except Exception as e:
    import traceback
    traceback.print_exc()
finally:
    conn.close()
