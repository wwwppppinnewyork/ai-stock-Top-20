"""Build the point-in-time training panel for the XGBoost model.

Two jobs:

1. ``archive``: copy this week's ``data/wind/factor_data.csv`` into
   ``data/history_factors/<trade_date>/factor_data.csv``.  Running this every
   week accumulates the point-in-time factor history the model learns from;
   only data that was available on the snapshot date is ever stored.
2. ``panel``: stack all archived snapshots and attach the forward return over
   ``label.horizon_trade_days`` trading days, fetched from Wind
   (``close`` with ``PriceAdj=F``).  The return window starts on the next
   trading day after the snapshot so the label never overlaps the features.

Output: ``data/xgb_panel.csv`` with columns
``snapshot_date, ts_code, name, <features...>, total_score, mkt_cap, fwd_return``
(``total_score`` is the rule-based score of the same week, used as baseline;
``mkt_cap`` is the regression weight for the WLS baseline).
"""
import argparse
import csv
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FACTOR_FILE = ROOT / "data" / "wind" / "factor_data.csv"
HISTORY_DIR = ROOT / "data" / "history_factors"
RANKED_FILE = ROOT / "output" / "ranked_stocks.csv"
PANEL_FILE = ROOT / "data" / "xgb_panel.csv"
CONFIG_FILE = ROOT / "config" / "xgboost.json"


def read_csv(path):
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def archive_snapshot():
    rows = read_csv(FACTOR_FILE)
    if not rows:
        raise ValueError("factor_data.csv 为空，无法归档")
    trade_date = rows[0].get("trade_date", "")
    if not trade_date:
        raise ValueError("factor_data.csv 缺少 trade_date，无法确定快照日期")
    target = HISTORY_DIR / trade_date
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(FACTOR_FILE, target / "factor_data.csv")
    if RANKED_FILE.exists():
        # keep the rule-based score of the same week as the XGBoost baseline
        shutil.copy2(RANKED_FILE, target / "ranked_stocks.csv")
    print(f"已归档因子快照：{target / 'factor_data.csv'}")


def forward_returns(codes, snapshot_date, horizon):
    """Return {ts_code: forward return} from the next trading day over `horizon` days."""
    from WindPy import w  # imported lazily: only the panel step needs Wind

    if not w.isconnected():
        w.start()
    start = w.tdaysoffset(1, snapshot_date, "")
    end = w.tdaysoffset(horizon + 1, snapshot_date, "")
    if start.ErrorCode != 0 or end.ErrorCode != 0:
        raise RuntimeError(f"Wind交易日偏移失败：{snapshot_date}")
    start_day = start.Data[0][0].strftime("%Y-%m-%d")
    end_day = end.Data[0][0].strftime("%Y-%m-%d")
    data = w.wss(",".join(codes), "close", f"tradeDate={start_day};priceAdj=F;cycle=D")
    data_end = w.wss(",".join(codes), "close", f"tradeDate={end_day};priceAdj=F;cycle=D")
    if data.ErrorCode != 0 or data_end.ErrorCode != 0:
        raise RuntimeError(f"Wind行情读取失败：{snapshot_date}")
    result = {}
    for code, p0, p1 in zip(data.Codes, data.Data[0], data_end.Data[0]):
        if p0 and p1 and p0 > 0:
            result[code] = p1 / p0 - 1
    return result


def build_panel():
    config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    features = config["features"]
    horizon = int(config["label"]["horizon_trade_days"])
    snapshots = sorted(p for p in HISTORY_DIR.glob("*/factor_data.csv"))
    if not snapshots:
        raise FileNotFoundError("data/history_factors 下没有归档快照，请先每周运行 archive")
    out_rows = []
    for path in snapshots:
        snapshot_date = path.parent.name
        rows = read_csv(path)
        ranked_path = path.parent / "ranked_stocks.csv"
        rule_score = {r["ts_code"]: r.get("total_score", "") for r in read_csv(ranked_path)} if ranked_path.exists() else {}
        codes = [row["ts_code"] for row in rows]
        returns = forward_returns(codes, snapshot_date, horizon)
        for row in rows:
            if row["ts_code"] not in returns:
                continue  # label not yet observable (latest week) or suspended stock
            item = {"snapshot_date": snapshot_date, "ts_code": row["ts_code"], "name": row.get("name", "")}
            item.update({f: row.get(f, "") for f in features})
            item["total_score"] = rule_score.get(row["ts_code"], "")
            item["mkt_cap"] = row.get("mkt_cap", "")  # weight for the WLS baseline
            item["fwd_return"] = round(returns[row["ts_code"]], 6)
            out_rows.append(item)
        print(f"{snapshot_date}: {len(rows)} 只股票，{sum(r['snapshot_date'] == snapshot_date for r in out_rows)} 只有标签")
    fields = ["snapshot_date", "ts_code", "name", *features, "total_score", "mkt_cap", "fwd_return"]
    with PANEL_FILE.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(out_rows)
    print(f"已写出训练面板：{PANEL_FILE}（{len(out_rows)} 行）")


def main():
    parser = argparse.ArgumentParser(description="XGBoost 训练面板：归档因子快照 / 拼接前瞻收益")
    parser.add_argument("step", choices=["archive", "panel"])
    args = parser.parse_args()
    archive_snapshot() if args.step == "archive" else build_panel()


if __name__ == "__main__":
    main()
