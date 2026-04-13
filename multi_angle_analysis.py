import pandas as pd
import numpy as np
import os

file_path = "/root/.openclaw/media/inbound/PH新版日报2026年3月1-19日---54ca058d-ce20-4a34-8dba-f299499485d5.xlsx"
sheets = ["WP_ro", "GJP_ro", "SBET_ro", "Sugarplay_ro"]

def analyze_full():
    all_data = []
    for sheet in sheets:
        df = pd.read_excel(file_path, sheet_name=sheet, header=1)
        df.columns = [str(c).replace('\n', '').strip() for c in df.columns]
        mapping = {'日期': 'Date', '总登录人数': 'Logins', '总新注册人数': 'Regs', '总首充人数': 'FirstDeps', '总存款人数': 'DepUsers', '总存款金额': 'Amount', '总存款额': 'Amount'}
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
        df = df[df['Logins'].apply(lambda x: x == int(x))].drop_duplicates('Date')
        df['Platform'] = sheet.replace('_ro', '')
        all_data.append(df[['Date', 'Platform', 'Logins', 'Regs', 'FirstDeps', 'DepUsers', 'Amount']])

    df = pd.concat(all_data)
    
    # 1. 规模对比 (Scale)
    scale = df.groupby('Platform')[['Logins', 'Amount']].sum()
    
    # 2. 效率分析 (Efficiency)
    efficiency = df.groupby('Platform').agg({
        'Regs': 'sum', 'FirstDeps': 'sum', 'DepUsers': 'sum', 'Amount': 'sum'
    })
    efficiency['ConvRate'] = (efficiency['FirstDeps'] / efficiency['Regs'] * 100).round(2)
    efficiency['ARPU'] = (efficiency['Amount'] / efficiency['DepUsers']).round(2)
    
    # 3. 趋势稳定性 (Volatility/Trend)
    trend = df.pivot(index='Date', columns='Platform', values='Amount').fillna(0)
    
    # 4. 份额分析 (Market Share)
    share = df.groupby('Platform')['Amount'].sum() / df['Amount'].sum() * 100

    print("=== SCALE ===")
    print(scale)
    print("\n=== EFFICIENCY ===")
    print(efficiency[['ConvRate', 'ARPU']])
    print("\n=== SHARE ===")
    print(share)
    print("\n=== LATEST GROWTH (19th vs 18th) ===")
    latest_date = df['Date'].max()
    prev_date = latest_date - pd.Timedelta(days=1)
    l_data = df[df['Date'] == latest_date].set_index('Platform')['Amount']
    p_data = df[df['Date'] == prev_date].set_index('Platform')['Amount']
    print((l_data - p_data).dropna())

if __name__ == "__main__":
    analyze_full()
