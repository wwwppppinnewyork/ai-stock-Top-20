"""Refresh only the dashboard's quarterly parent-company net-profit series."""
import csv
import math
from datetime import date
from pathlib import Path

from WindPy import w


ROOT = Path(__file__).resolve().parent.parent
RANKED = ROOT / "output" / "ranked_stocks.csv"
OUTPUT = ROOT / "data" / "wind" / "quarterly_parent_net_profit.csv"


def read_rows(path):
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def finite(value):
    return value is not None and not (isinstance(value, float) and math.isnan(value))


def require_ok(result, label):
    if result.ErrorCode:
        raise RuntimeError(f"{label}: {result.ErrorCode} {result.Data}")
    return result


def report_dates(end, count=20):
    year, quarter = end.year, (end.month - 1) // 3 + 1
    result = []
    for back in range(count - 1, -1, -1):
        total = year * 4 + quarter - 1 - back
        yy, zero_based_quarter = divmod(total, 4)
        month = (zero_based_quarter + 1) * 3
        result.append(date(yy, month, 31 if month in (3, 12) else 30))
    return result


def main():
    ranked = read_rows(RANKED)
    if not ranked:
        raise RuntimeError("排名文件为空")
    codes = [row["ts_code"] for row in ranked]
    names = {row["ts_code"]: row["name"] for row in ranked}
    periods = report_dates(date.fromisoformat(ranked[0]["report_period"]))
    started = w.start()
    if started.ErrorCode or not w.isconnected():
        raise RuntimeError("WindPy connection failed")

    series = {code: [] for code in codes}
    for period in periods:
        values = []
        for start in range(0, len(codes), 300):
            batch = codes[start:start + 300]
            result = require_ok(
                w.wss(",".join(batch), "qfa_np_belongto_parcomsh", f"rptDate={period:%Y%m%d};rptType=1;unit=1"),
                f"quarterly parent net profit {period}",
            )
            values.extend(result.Data[0])
        for code, value in zip(codes, values):
            if finite(value):
                series[code].append((period, float(value)))

    rows = []
    for code in codes:
        for period, value in series[code]:
            rows.append({
                "ts_code": code,
                "name": names[code],
                "quarter": period.isoformat(),
                "net_profit": round(value / 1e8, 4),
                "data_type": "单季度归母净利润",
                "wind_field": "QFA_NP_BELONGTO_PARCOMSH",
            })
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["ts_code", "name", "quarter", "net_profit", "data_type", "wind_field"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"{OUTPUT} rows={len(rows)} stocks={len(codes)} field=QFA_NP_BELONGTO_PARCOMSH")


if __name__ == "__main__":
    main()
