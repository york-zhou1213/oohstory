import pandas as pd
import sqlite3
import os
import sys

def process_report(file_path, db_path):
    sheets = ['WP_ro', 'GJP_ro', 'SBET_ro', 'Sugarplay_ro']
    data_to_insert = []
    
    try:
        xls = pd.ExcelFile(file_path)
        for sheet in sheets:
            if sheet not in xls.sheet_names:
                print(f"Sheet {sheet} not found")
                continue
                
            df = pd.read_excel(xls, sheet_name=sheet)
            # Find the latest date (usually last row or sorted)
            # Metrics: 日期, 总登录人数, 总新注册人数, 总首充人数, 总存款人数, 总存款次数, 总存款金额
            
            # Assuming the date is in the first column and rows are chronological
            # Filter rows where date is valid
            df = df[df.iloc[:, 0].astype(str).str.contains(r'\d{4}-\d{2}-\d{2}', na=False)]
            if df.empty:
                continue
                
            last_row = df.iloc[-1]
            metrics = {
                'platform': sheet.replace('_ro', ''),
                'date': str(last_row.iloc[0]).split(' ')[0],
                'logins': int(last_row.iloc[1]) if not pd.isna(last_row.iloc[1]) else 0,
                'new_registers': int(last_row.iloc[2]) if not pd.isna(last_row.iloc[2]) else 0,
                'first_deposits': int(last_row.iloc[3]) if not pd.isna(last_row.iloc[3]) else 0,
                'total_depositors': int(last_row.iloc[4]) if not pd.isna(last_row.iloc[4]) else 0,
                'total_deposits_count': int(last_row.iloc[5]) if not pd.isna(last_row.iloc[5]) else 0,
                'total_deposits_amount': float(last_row.iloc[6]) if not pd.isna(last_row.iloc[6]) else 0.0
            }
            data_to_insert.append(metrics)
            
        if not data_to_insert:
            print("No data extracted")
            return

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS daily_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT,
                date TEXT,
                logins INTEGER,
                new_registers INTEGER,
                first_deposits INTEGER,
                total_depositors INTEGER,
                total_deposits_count INTEGER,
                total_deposits_amount REAL,
                UNIQUE(platform, date)
            )
        ''')
        
        summary = []
        report_date = data_to_insert[0]['date']
        
        for item in data_to_insert:
            cursor.execute('''
                INSERT OR REPLACE INTO daily_reports 
                (platform, date, logins, new_registers, first_deposits, total_depositors, total_deposits_count, total_deposits_amount)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (item['platform'], item['date'], item['logins'], item['new_registers'], 
                  item['first_deposits'], item['total_depositors'], item['total_deposits_count'], item['total_deposits_amount']))
            
            summary.append(f"• {item['platform']}: 登录{item['logins']}, 新增{item['new_registers']}, 首充{item['first_deposits']}, 存款人数{item['total_depositors']}, 存款额{item['total_deposits_amount']:.2f}")
            
        conn.commit()
        conn.close()
        
        print(f"Data for {report_date} ingested successfully.")
        print("\n".join(summary))
        
    except Exception as e:
        print(f"Error: {str(e)}")

if __name__ == "__main__":
    process_report(sys.argv[1], sys.argv[2])
