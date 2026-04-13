import pandas as pd
file_path = "/root/.openclaw/media/inbound/PH新版日报2026年3月1-30日---905aebe7-5c15-4432-8662-00a921e1af24.xlsx"
xl = pd.ExcelFile(file_path)
for sheet in ["WP_ro", "GJP_ro", "SBET_ro", "Sugarplay_ro"]:
    if sheet in xl.sheet_names:
        df = xl.parse(sheet)
        print(f"--- {sheet} ---")
        print("Columns:", df.columns.tolist())
        print("First 2 rows:\n", df.head(2))
        print("Tail 2 rows:\n", df.tail(2))
