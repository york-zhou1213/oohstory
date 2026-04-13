import pandas as pd
import os

file_path = '/root/.openclaw/media/inbound/PH新版日报2026年3月1-17日---8e81abc9-a9a8-4404-a444-743b8682dd08.xlsx'
sheets = {
    'WP_ro': 'WP',
    'GJP_ro': 'GJP',
    'SBET_ro': 'SBET',
    'Sugarplay_ro': 'SPLAY'
}

analysis_results = {}

for sheet_name, platform_code in sheets.items():
    df = pd.read_excel(file_path, sheet_name=sheet_name, header=None)
    df[0] = pd.to_datetime(df[0], errors='coerce')
    daily_df = df.dropna(subset=[0]).copy()
    
    # Relevant indices based on previous inspection:
    # 0: Date, 1: Logins, 4: New Reg, 7: First Dep, 10: Total Dep Users, 19: Total Dep Amt
    
    # Calculate ratios with check for zero
    daily_df['reg_to_first_dep'] = daily_df[7].astype(float) / daily_df[4].astype(float).replace(0, float('nan'))
    daily_df['avg_dep_per_user'] = daily_df[19].astype(float) / daily_df[10].astype(float).replace(0, float('nan'))
    
    # Trend (compare Mar 17 to Mar 10 for weekly check or Mar 1 to Mar 17)
    mar_17 = daily_df[daily_df[0] == "2026-03-17"].iloc[0]
    mar_01 = daily_df[daily_df[0] == "2026-03-01"].iloc[0]
    
    analysis_results[platform_code] = {
        'conv_rate': mar_17['reg_to_first_dep'],
        'arpu': mar_17['avg_dep_per_user'],
        'growth_v_start': (mar_17[19] - mar_01[19]) / mar_01[19] if mar_01[19] != 0 else 0,
        'mar_01_amt': mar_01[19],
        'mar_17_amt': mar_17[19],
        'mar_17_reg': mar_17[4],
        'mar_17_first': mar_17[7]
    }

print(analysis_results)
