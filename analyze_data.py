import pandas as pd
import numpy as np

file_path = "/root/.openclaw/media/inbound/PH新版日报2026年3月1-19日---54ca058d-ce20-4a34-8dba-f299499485d5.xlsx"
sheets = ["WP_ro", "GJP_ro", "SBET_ro", "Sugarplay_ro"]

def analyze():
    all_data = []
    for sheet in sheets:
        df = pd.read_excel(file_path, sheet_name=sheet, header=1)
        df.columns = [str(c).replace('\n', '').strip() for c in df.columns]
        
        mapping = {
            '日期': 'Date',
            '总登录人数': 'Logins',
            '总新注册人数': 'Regs',
            '总首充人数': 'FirstDeps',
            '总存款人数': 'DepUsers',
            '总存款金额': 'Amount',
            '总存款额': 'Amount'
        }
        
        rename_map = {k: v for k, v in mapping.items() if k in df.columns}
        if 'Amount' not in rename_map.values():
             df['Amount'] = df.iloc[:, 5]
             rename_map['Amount'] = 'Amount'

        df = df.rename(columns=rename_map)
        df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
        df = df[df['Date'].notnull()]
        df = df[(df['Date'].dt.month == 3) & (df['Date'].dt.day <= 19)]
        
        for col in ['Logins', 'Regs', 'FirstDeps', 'DepUsers', 'Amount']:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
            
        # Select rows where Logins is an integer
        df = df[df['Logins'].apply(lambda x: x == int(x))]
        df = df[df['Logins'] > 0]
        
        # Deduplicate by Date, taking the first instance (since they should be unique daily rows)
        df = df.drop_duplicates('Date', keep='first')
        
        df['Platform'] = sheet.replace('_ro', '')
        all_data.append(df[['Date', 'Platform', 'Logins', 'Regs', 'FirstDeps', 'DepUsers', 'Amount']])

    df_full = pd.concat(all_data)
    
    # Analyze Trends
    for p_name in df_full['Platform'].unique():
        p_data = df_full[df_full['Platform'] == p_name].set_index('Date').sort_index()
        
        latest_date = p_data.index.max()
        latest = p_data.loc[latest_date]
        
        # Check if latest is a Series (it should be)
        if isinstance(latest, pd.DataFrame):
            latest = latest.iloc[0]

        # 7-day average (prior to latest)
        mask = (p_data.index < latest_date) & (p_data.index >= (latest_date - pd.Timedelta(days=7)))
        avg_7d = p_data[mask].mean(numeric_only=True)
        
        # Growth vs Yesterday
        prev_date = latest_date - pd.Timedelta(days=1)
        growth_text = ""
        if prev_date in p_data.index:
            prev = p_data.loc[prev_date]
            if isinstance(prev, pd.DataFrame): prev = prev.iloc[0]
            
            def get_growth(cur, old):
                diff = cur - old
                pct = (diff / old) * 100 if old != 0 else 0
                return f"{diff:+.0f} ({pct:+.1f}%)"
            
            growth_text = f"  增长(环比): 登录 {get_growth(latest['Logins'], prev['Logins'])}, 注册 {get_growth(latest['Regs'], prev['Regs'])}, 首充 {get_growth(latest['FirstDeps'], prev['FirstDeps'])}, 存款 {get_growth(latest['Amount'], prev['Amount'])}"

        print(f"[{p_name}] {latest_date.strftime('%Y-%m-%d')}")
        print(f"  数据: 登录 {latest['Logins']:.0f}, 注册 {latest['Regs']:.0f}, 首充 {latest['FirstDeps']:.0f}, 存款人数 {latest['DepUsers']:.0f}, 存款金额 {latest['Amount']:.2f}")
        
        if growth_text:
            print(growth_text)
            
        if not avg_7d.isna().all():
            def get_diff_avg(cur, avg_val):
                diff = cur - avg_val
                pct = (diff / avg_val) * 100 if avg_val != 0 else 0
                return f"{diff:+.0f} ({pct:+.1f}%)"
            print(f"  趋势(对比7日均值): 登录 {get_diff_avg(latest['Logins'], avg_7d['Logins'])}, 存款 {get_diff_avg(latest['Amount'], avg_7d['Amount'])}")
        
        conv = (latest['FirstDeps'] / latest['Regs'] * 100) if latest['Regs'] > 0 else 0
        arpu = (latest['Amount'] / latest['DepUsers']) if latest['DepUsers'] > 0 else 0
        print(f"  效率: 转化率 {conv:.1f}%, ARPU {arpu:.2f}")

if __name__ == "__main__":
    analyze()
