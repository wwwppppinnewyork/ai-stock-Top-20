import argparse
import csv
import json
from pathlib import Path
from typing import Dict, List


def parse_args():
    parser = argparse.ArgumentParser(description="AI选股 workflow（默认按周更新排名）")
    parser.add_argument("--ai-universe", required=True, help="AI赛道股票池 CSV")
    parser.add_argument("--etf-holdings", required=True, help="ETF 前十持仓 CSV")
    parser.add_argument("--factor-data", required=True, help="因子数据 CSV")
    parser.add_argument("--config", required=True, help="权重配置 JSON")
    parser.add_argument("--top-n", type=int, default=20, help="输出前 N 只股票")
    parser.add_argument("--output-dir", default="output", help="输出目录")
    return parser.parse_args()


def load_config(config_path: str) -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def read_csv_rows(path: str) -> List[Dict[str, str]]:
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv_rows(path: Path, rows: List[Dict[str, object]], fieldnames: List[str]):
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def to_float(value, default=0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def to_int(value, default=0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def min_max_scale(values: List[float], reverse: bool = False) -> List[float]:
    # Current workflow uses cross-sectional min-max scaling within the candidate pool.
    clean = [v for v in values if v is not None]
    if not clean:
        return [0.5 for _ in values]
    min_v = min(clean)
    max_v = max(clean)
    if min_v == max_v:
        scaled = [1.0 for _ in values]
    else:
        scaled = [(v - min_v) / (max_v - min_v) for v in values]
    if reverse:
        scaled = [1 - v for v in scaled]
    return scaled


def validate_fields(rows: List[Dict[str, str]], required_fields: set, label: str):
    if not rows:
        raise ValueError(f"{label} 没有数据")
    fields = set(rows[0].keys())
    missing = required_fields - fields
    if missing:
        raise ValueError(f"{label} 缺少字段: {sorted(missing)}")


def build_candidate_pool(ai_rows: List[Dict[str, str]], etf_rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    merged: Dict[str, Dict[str, str]] = {}
    for row in ai_rows:
        code = row["ts_code"]
        merged.setdefault(code, {"ts_code": code, "name": row["name"], "source_set": set()})
        merged[code]["source_set"].add("ai_theme")

    for row in etf_rows:
        code = row["ts_code"]
        merged.setdefault(code, {"ts_code": code, "name": row["name"], "source_set": set()})
        merged[code]["source_set"].add("etf_top50_top10")

    result = []
    for item in merged.values():
        result.append(
            {
                "ts_code": item["ts_code"],
                "name": item["name"],
                "source": ",".join(sorted(item["source_set"])),
            }
        )
    return sorted(result, key=lambda x: (x["source"], x["ts_code"]))


def compute_quality_score(row: Dict[str, str], scaled_maps: Dict[str, Dict[str, float]], subweights: dict) -> float:
    code = row["ts_code"]
    return (
        scaled_maps["roe_yoy"][code] * subweights["roe_yoy"]
        + scaled_maps["gross_margin_yoy"][code] * subweights["gross_margin_yoy"]
        + scaled_maps["net_profit_yoy"][code] * subweights["net_profit_yoy"]
    )


def compute_qoq_score(row: Dict[str, str], base_score: float, qoq_rules: dict) -> float:
    q1_flag = to_int(row["q1_acceleration_flag"]) == 1
    cycle_flag = to_int(row["quarter_cycle_positive_flag"]) == 1

    if cycle_flag:
        return qoq_rules["quarter_cycle_positive_full_ratio"]
    if q1_flag:
        return max(base_score, qoq_rules["q1_acceleration_floor_ratio"])
    return base_score


def build_scaled_maps(factor_rows: List[Dict[str, str]]) -> Dict[str, Dict[str, float]]:
    metric_names = [
        "analyst_profit_yoy",
        "peg",
        "net_profit_qoq_growth",
        "analyst_net_profit_revision_change",
        "roe_yoy",
        "gross_margin_yoy",
        "net_profit_yoy",
    ]

    value_lists = {metric: [] for metric in metric_names}
    codes = []
    for row in factor_rows:
        codes.append(row["ts_code"])
        for metric in metric_names:
            value_lists[metric].append(to_float(row[metric]))

    scaled_maps = {}
    for metric in metric_names:
        reverse = metric == "peg"
        scaled_values = min_max_scale(value_lists[metric], reverse=reverse)
        scaled_maps[metric] = {code: scaled_values[i] for i, code in enumerate(codes)}
    return scaled_maps


def score_stocks(candidate_pool: List[Dict[str, str]], factor_rows: List[Dict[str, str]], config: dict) -> List[Dict[str, object]]:
    factor_map = {row["ts_code"]: row for row in factor_rows}
    scaled_maps = build_scaled_maps(factor_rows)
    weights = config["weights"]
    qoq_rules = config["qoq_rules"]
    quality_subweights = config["quality_subweights"]

    ranked_rows = []
    for stock in candidate_pool:
        code = stock["ts_code"]
        factor_row = factor_map.get(code)
        if factor_row is None:
            continue

        score_analyst_profit = scaled_maps["analyst_profit_yoy"][code]
        score_peg = scaled_maps["peg"][code]
        score_qoq = compute_qoq_score(factor_row, scaled_maps["net_profit_qoq_growth"][code], qoq_rules)
        score_revision = scaled_maps["analyst_net_profit_revision_change"][code]
        score_quality = compute_quality_score(factor_row, scaled_maps, quality_subweights)

        weighted_analyst_profit = score_analyst_profit * weights["analyst_profit_yoy"] * 100
        weighted_peg = score_peg * weights["peg"] * 100
        weighted_qoq = score_qoq * weights["qoq_growth"] * 100
        weighted_revision = score_revision * weights["analyst_net_profit_revision_change"] * 100
        weighted_quality = score_quality * weights["quality"] * 100
        total_score = (
            weighted_analyst_profit
            + weighted_peg
            + weighted_qoq
            + weighted_revision
            + weighted_quality
        )

        ranked_rows.append(
            {
                "ts_code": code,
                "name": stock["name"],
                "source": stock["source"],
                "analyst_profit_yoy": factor_row["analyst_profit_yoy"],
                "peg": factor_row["peg"],
                "net_profit_qoq_growth": factor_row["net_profit_qoq_growth"],
                "q1_acceleration_flag": factor_row["q1_acceleration_flag"],
                "quarter_cycle_positive_flag": factor_row["quarter_cycle_positive_flag"],
                "analyst_net_profit_revision_change": factor_row["analyst_net_profit_revision_change"],
                "roe_yoy": factor_row["roe_yoy"],
                "gross_margin_yoy": factor_row["gross_margin_yoy"],
                "net_profit_yoy": factor_row["net_profit_yoy"],
                "score_analyst_profit_yoy": round(score_analyst_profit, 6),
                "score_peg": round(score_peg, 6),
                "score_qoq_growth": round(score_qoq, 6),
                "score_analyst_revision": round(score_revision, 6),
                "score_quality": round(score_quality, 6),
                "weighted_analyst_profit_yoy": round(weighted_analyst_profit, 4),
                "weighted_peg": round(weighted_peg, 4),
                "weighted_qoq_growth": round(weighted_qoq, 4),
                "weighted_analyst_revision": round(weighted_revision, 4),
                "weighted_quality": round(weighted_quality, 4),
                "total_score": round(total_score, 4),
            }
        )

    ranked_rows.sort(key=lambda x: (-x["total_score"], x["ts_code"]))
    for index, row in enumerate(ranked_rows, start=1):
        row["rank"] = index
    return ranked_rows


def main():
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    ai_rows = read_csv_rows(args.ai_universe)
    etf_rows = read_csv_rows(args.etf_holdings)
    factor_rows = read_csv_rows(args.factor_data)
    config = load_config(args.config)

    validate_fields(ai_rows, {"ts_code", "name"}, "ai_universe")
    validate_fields(etf_rows, {"ts_code", "name"}, "etf_holdings")
    validate_fields(
        factor_rows,
        {
            "ts_code",
            "name",
            "analyst_profit_yoy",
            "peg",
            "net_profit_qoq_growth",
            "q1_acceleration_flag",
            "quarter_cycle_positive_flag",
            "analyst_net_profit_revision_change",
            "roe_yoy",
            "gross_margin_yoy",
            "net_profit_yoy",
        },
        "factor_data",
    )

    candidate_pool = build_candidate_pool(ai_rows, etf_rows)
    ranked_rows = score_stocks(candidate_pool, factor_rows, config)
    top_rows = ranked_rows[: args.top_n]

    write_csv_rows(output_dir / "candidate_pool.csv", candidate_pool, ["ts_code", "name", "source"])
    if ranked_rows:
        ranked_fields = list(ranked_rows[0].keys())
        write_csv_rows(output_dir / "ranked_stocks.csv", ranked_rows, ranked_fields)
        write_csv_rows(output_dir / "top_picks.csv", top_rows, ranked_fields)
    else:
        write_csv_rows(output_dir / "ranked_stocks.csv", [], ["ts_code"])
        write_csv_rows(output_dir / "top_picks.csv", [], ["ts_code"])

    print(f"候选池数量: {len(candidate_pool)}")
    print(f"有因子覆盖的股票数量: {len(ranked_rows)}")
    print(f"成功输出排名结果: {output_dir / 'ranked_stocks.csv'}")
    print(f"成功输出前{args.top_n}只: {output_dir / 'top_picks.csv'}")
    for row in top_rows:
        print(f"{row['rank']:>2} | {row['ts_code']} | {row['name']} | {row['total_score']}")


if __name__ == "__main__":
    main()
