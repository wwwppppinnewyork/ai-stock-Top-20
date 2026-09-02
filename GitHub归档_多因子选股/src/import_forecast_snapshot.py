"""Import the fixed 2026-08-22 forecast snapshot used by the dashboard."""

import csv
import sys
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "data" / "wind" / "forecast_snapshot_20260822.csv"


def main():
    if len(sys.argv) != 2:
        raise SystemExit("用法：python src/import_forecast_snapshot.py <预测净利润.xlsx>")

    source = Path(sys.argv[1])
    frame = pd.read_excel(source, sheet_name=0)
    if frame.shape[1] < 6:
        raise RuntimeError("预测表至少需要证券代码、证券简称和四列预测值。")

    rows = []
    for values in frame.iloc[:, :6].itertuples(index=False, name=None):
        code, _name, growth_2026, growth_2027, profit_2026, profit_2027 = values
        if pd.isna(code):
            continue
        rows.append(
            {
                "ts_code": str(code).strip(),
                "growth_2026": "" if pd.isna(growth_2026) else round(float(growth_2026) * 100, 6),
                "growth_2027": "" if pd.isna(growth_2027) else round(float(growth_2027) * 100, 6),
                "profit_2026_billion": "" if pd.isna(profit_2026) else round(float(profit_2026), 6),
                "profit_2027_billion": "" if pd.isna(profit_2027) else round(float(profit_2027), 6),
            }
        )

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(OUTPUT, len(rows))


if __name__ == "__main__":
    main()
