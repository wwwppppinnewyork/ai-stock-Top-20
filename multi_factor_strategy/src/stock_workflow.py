import argparse
import csv
import json
import re
from pathlib import Path
from statistics import median
from typing import Dict, List


def parse_args():
    parser = argparse.ArgumentParser(description="AI选股 workflow（默认按周更新排名）")
    parser.add_argument("--ai-universe", required=True, help="AI赛道股票池 CSV")
    parser.add_argument("--etf-holdings", required=True, help="ETF 前十持仓 CSV")
    parser.add_argument("--factor-data", required=True, help="因子数据 CSV")
    parser.add_argument("--config", required=True, help="权重配置 JSON")
    parser.add_argument("--top-n", type=int, default=30, help="输出前 N 只股票")
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


def to_float(value, default=None):
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


def mad_winsorize(values: List[float], threshold: float = 3.0) -> List[float]:
    """Winsorize continuous cross-sectional values with the median ± k·1.4826·MAD rule.

    Missing observations remain missing.  If MAD is zero, the cross-section is
    left unchanged because a robust clipping range cannot be identified.
    """
    clean = [v for v in values if v is not None]
    if not clean:
        return list(values)
    center = median(clean)
    mad = median(abs(v - center) for v in clean)
    if mad == 0:
        return list(values)
    radius = threshold * 1.4826 * mad
    lower = center - radius
    upper = center + radius
    return [None if v is None else min(max(v, lower), upper) for v in values]


def min_max_scale(values: List[float], reverse: bool = False, missing_score: float = 0.5) -> List[float]:
    # Values passed here have already been winsorized within the candidate pool.
    clean = [v for v in values if v is not None]
    if not clean:
        return [missing_score for _ in values]
    min_v = min(clean)
    max_v = max(clean)
    if min_v == max_v:
        scaled = [missing_score if v is None else 1.0 for v in values]
    else:
        scaled = [missing_score if v is None else (v - min_v) / (max_v - min_v) for v in values]
    if reverse:
        scaled = [1 - v for v in scaled]
    return scaled


def signed_direction_scale(values: List[float]) -> List[float]:
    """Preserve revision direction: upgrades positive, downgrades negative, missing zero.

    Positive and negative observations are scaled separately so the strongest
    upgrade is +1 and the strongest downgrade is -1.  A zero observation or a
    missing analyst forecast contributes exactly 0.
    """
    positives = [v for v in values if v is not None and v > 0]
    negatives = [v for v in values if v is not None and v < 0]
    max_positive = max(positives) if positives else None
    max_negative_abs = max(abs(v) for v in negatives) if negatives else None
    scaled = []
    for value in values:
        if value is None or value == 0:
            scaled.append(0.0)
        elif value > 0:
            scaled.append(value / max_positive)
        else:
            scaled.append(value / max_negative_abs)
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
        merged[code]["source_set"].add("all_a_shares_ex_st_neeeq")

    for row in etf_rows:
        code = row["ts_code"]
        merged.setdefault(code, {"ts_code": code, "name": row["name"], "source_set": set()})
        merged[code]["source_set"].add("etf_top50_top10")

    result = []
    for item in merged.values():
        if re.search(r"(?i)\*?ST", item["name"]) or "退市" in item["name"] or item["name"].endswith("退"):
            continue
        result.append(
            {
                "ts_code": item["ts_code"],
                "name": item["name"],
                "source": ",".join(sorted(item["source_set"])),
            }
        )
    return sorted(result, key=lambda x: (x["source"], x["ts_code"]))


def compute_quality_score(row: Dict[str, str], scaled_maps: Dict[str, Dict[str, float]], subweights: dict, year_weights: dict) -> float:
    code = row["ts_code"]
    prior_score = (
        scaled_maps["roe_prior"][code] * subweights["roe"]
        + scaled_maps["gross_margin_prior"][code] * subweights["gross_margin"]
        + scaled_maps["net_margin_prior"][code] * subweights["net_margin"]
    )
    current_score = (
        scaled_maps["roe_current"][code] * subweights["roe"]
        + scaled_maps["gross_margin_current"][code] * subweights["gross_margin"]
        + scaled_maps["net_margin_current"][code] * subweights["net_margin"]
    )
    return prior_score * year_weights["prior"] + current_score * year_weights["current"]


def compute_qoq_score(row: Dict[str, str], base_score: float, qoq_rules: dict) -> float:
    cycle_flag = to_int(row["quarter_cycle_positive_flag"]) == 1

    if cycle_flag:
        return qoq_rules["quarter_cycle_positive_full_ratio"]
    return base_score * qoq_rules["non_full_positive_ratio"]


def build_scaled_maps(factor_rows: List[Dict[str, str]]) -> Dict[str, Dict[str, float]]:
    metric_names = [
        "analyst_profit_yoy_fy1",
        "analyst_profit_yoy_fy2",
        "analyst_profit_yoy_fy3",
        "peg_fy1",
        "peg_fy2",
        "peg_fy3",
        "net_profit_qoq_growth",
        "consecutive_profit_growth_quarters",
        "analyst_revision_fy1",
        "analyst_revision_fy2",
        "roe_prior",
        "gross_margin_prior",
        "net_margin_prior",
        "roe_current",
        "gross_margin_current",
        "net_margin_current",
    ]

    value_lists = {metric: [] for metric in metric_names}
    codes = []
    for row in factor_rows:
        codes.append(row["ts_code"])
        for metric in metric_names:
            value = to_float(row[metric])
            # Negative PEG is retained in raw output for audit, but never
            # enters the reverse min-max distribution used by valid PEGs.
            if metric.startswith("peg_") and value is not None and value < 0:
                value = None
            value_lists[metric].append(value)

    scaled_maps = {}
    for metric in metric_names:
        # The uninterrupted-growth count is deliberately not winsorized.
        winsorized_values = value_lists[metric] if metric == "consecutive_profit_growth_quarters" else mad_winsorize(value_lists[metric])
        if metric.startswith("analyst_revision_"):
            scaled_values = signed_direction_scale(winsorized_values)
        else:
            reverse = metric.startswith("peg_")
            missing_score = 0.0 if metric in {
                "roe_prior", "gross_margin_prior", "net_margin_prior",
                "roe_current", "gross_margin_current", "net_margin_current",
            } else 0.5
            scaled_values = min_max_scale(winsorized_values, reverse=reverse, missing_score=missing_score)
        scaled_maps[metric] = {code: scaled_values[i] for i, code in enumerate(codes)}
    return scaled_maps


def score_stocks(candidate_pool: List[Dict[str, str]], factor_rows: List[Dict[str, str]], config: dict) -> List[Dict[str, object]]:
    # Hard eligibility rules, effective for every current and historical week:
    # (1) the latest disclosed single-quarter consolidated net profit must
    #     exist and be positive; and
    # (2) at least one of the three analyst forecast-growth horizons must be
    #     available. Companies with no analyst forecast coverage are not ranked.
    eligible_codes = {
        row["ts_code"] for row in factor_rows
        if to_float(row.get("current_quarter_net_profit")) is not None
        and to_float(row.get("current_quarter_net_profit")) > 0
        and any(
            to_float(row.get(field)) is not None
            for field in ("analyst_profit_yoy_fy1", "analyst_profit_yoy_fy2", "analyst_profit_yoy_fy3")
        )
    }
    factor_rows = [row for row in factor_rows if row["ts_code"] in eligible_codes]
    candidate_pool = [row for row in candidate_pool if row["ts_code"] in eligible_codes]
    factor_map = {row["ts_code"]: row for row in factor_rows}
    scaled_maps = build_scaled_maps(factor_rows)
    weights = config["weights"]
    qoq_rules = config["qoq_rules"]
    peg_year_weights = config.get("peg_year_weights", {"fy1": 0.50, "fy2": 0.30, "fy3": 0.20})
    profit_yoy_year_weights = config.get("profit_yoy_year_weights", {"fy1": 0.50, "fy2": 0.30, "fy3": 0.20})
    revision_year_weights = config.get("revision_year_weights", {"fy1": 0.50, "fy2": 0.50})
    quality_subweights = config["quality_subweights"]
    quality_year_weights = config.get("quality_year_weights", {"prior": 0.50, "current": 0.50})

    ranked_rows = []
    for stock in candidate_pool:
        code = stock["ts_code"]
        factor_row = factor_map.get(code)
        if factor_row is None:
            continue

        score_analyst_profit = (
            scaled_maps["analyst_profit_yoy_fy1"][code] * profit_yoy_year_weights["fy1"]
            + scaled_maps["analyst_profit_yoy_fy2"][code] * profit_yoy_year_weights["fy2"]
            + scaled_maps["analyst_profit_yoy_fy3"][code] * profit_yoy_year_weights["fy3"]
        )
        future_peg_negative = any(
            (value := to_float(factor_row.get(field))) is not None and value < 0
            for field in ("peg_fy2", "peg_fy3")
        )
        score_peg = 0.0 if future_peg_negative else (
            scaled_maps["peg_fy1"][code] * peg_year_weights["fy1"]
            + scaled_maps["peg_fy2"][code] * peg_year_weights["fy2"]
            + scaled_maps["peg_fy3"][code] * peg_year_weights["fy3"]
        )
        score_qoq = compute_qoq_score(factor_row, scaled_maps["net_profit_qoq_growth"][code], qoq_rules)
        score_consecutive_growth = scaled_maps["consecutive_profit_growth_quarters"][code]
        score_revision = (
            scaled_maps["analyst_revision_fy1"][code] * revision_year_weights["fy1"]
            + scaled_maps["analyst_revision_fy2"][code] * revision_year_weights["fy2"]
        )
        score_quality = compute_quality_score(factor_row, scaled_maps, quality_subweights, quality_year_weights)

        weighted_analyst_profit = score_analyst_profit * weights["analyst_profit_yoy"] * 100
        weighted_peg = score_peg * weights["peg"] * 100
        weighted_qoq = score_qoq * weights["qoq_growth"] * 100
        weighted_consecutive_growth = score_consecutive_growth * weights["consecutive_profit_growth_quarters"] * 100
        weighted_revision = score_revision * weights["analyst_net_profit_revision_change"] * 100
        weighted_quality = score_quality * weights["quality"] * 100
        total_score = (
            weighted_analyst_profit
            + weighted_peg
            + weighted_qoq
            + weighted_consecutive_growth
            + weighted_revision
            + weighted_quality
        )

        ranked_rows.append(
            {
                "ts_code": code,
                "name": stock["name"],
                "source": stock["source"],
                "trade_date": factor_row.get("trade_date", ""),
                "consensus_year": factor_row.get("consensus_year", ""),
                "report_period": factor_row.get("report_period", ""),
                "close": factor_row.get("close", ""),
                "pct_chg": factor_row.get("pct_chg", ""),
                "analyst_profit_yoy": factor_row["analyst_profit_yoy"],
                "analyst_profit_yoy_fy1": factor_row["analyst_profit_yoy_fy1"],
                "analyst_profit_yoy_fy2": factor_row["analyst_profit_yoy_fy2"],
                "analyst_profit_yoy_fy3": factor_row["analyst_profit_yoy_fy3"],
                "peg": factor_row["peg"],
                "peg_fy1": factor_row["peg_fy1"],
                "peg_fy2": factor_row["peg_fy2"],
                "peg_fy3": factor_row["peg_fy3"],
                "net_profit_qoq_growth": factor_row["net_profit_qoq_growth"],
                "qoq_previous_period": factor_row.get("qoq_previous_period", ""),
                "qoq_current_period": factor_row.get("qoq_current_period", ""),
                "q1_acceleration_flag": factor_row["q1_acceleration_flag"],
                "quarter_cycle_positive_flag": factor_row["quarter_cycle_positive_flag"],
                "consecutive_profit_growth_quarters": factor_row["consecutive_profit_growth_quarters"],
                "analyst_net_profit_revision_change": factor_row["analyst_net_profit_revision_change"],
                "analyst_revision_fy1": factor_row["analyst_revision_fy1"],
                "analyst_revision_fy2": factor_row["analyst_revision_fy2"],
                "quality_prior_period": factor_row["quality_prior_period"],
                "quality_current_period": factor_row["quality_current_period"],
                "roe_prior": factor_row["roe_prior"],
                "gross_margin_prior": factor_row["gross_margin_prior"],
                "net_margin_prior": factor_row["net_margin_prior"],
                "roe_current": factor_row["roe_current"],
                "gross_margin_current": factor_row["gross_margin_current"],
                "net_margin_current": factor_row["net_margin_current"],
                "current_quarter_deducted_profit": factor_row.get("current_quarter_deducted_profit", ""),
                "current_quarter_net_profit": factor_row.get("current_quarter_net_profit", ""),
                "score_analyst_profit_yoy": round(score_analyst_profit, 6),
                "score_peg": round(score_peg, 6),
                "score_qoq_growth": round(score_qoq, 6),
                "score_consecutive_profit_growth_quarters": round(score_consecutive_growth, 6),
                "score_analyst_revision": round(score_revision, 6),
                "score_quality": round(score_quality, 6),
                "weighted_analyst_profit_yoy": round(weighted_analyst_profit, 4),
                "weighted_peg": round(weighted_peg, 4),
                "weighted_qoq_growth": round(weighted_qoq, 4),
                "weighted_consecutive_profit_growth_quarters": round(weighted_consecutive_growth, 4),
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
    if etf_rows:
        validate_fields(etf_rows, {"ts_code", "name"}, "etf_holdings")
    validate_fields(
        factor_rows,
        {
            "ts_code",
            "name",
            "analyst_profit_yoy",
            "analyst_profit_yoy_fy1",
            "analyst_profit_yoy_fy2",
            "analyst_profit_yoy_fy3",
            "peg",
            "peg_fy1",
            "peg_fy2",
            "peg_fy3",
            "net_profit_qoq_growth",
            "q1_acceleration_flag",
            "quarter_cycle_positive_flag",
            "consecutive_profit_growth_quarters",
            "analyst_net_profit_revision_change",
            "analyst_revision_fy1",
            "analyst_revision_fy2",
            "quality_prior_period",
            "quality_current_period",
            "roe_prior",
            "gross_margin_prior",
            "net_margin_prior",
            "roe_current",
            "gross_margin_current",
            "net_margin_current",
            "current_quarter_deducted_profit",
            "current_quarter_net_profit",
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
        factor_exports = {
            "analyst_profit_yoy": ("score_analyst_profit_yoy", "weighted_analyst_profit_yoy"),
            "peg": ("score_peg", "weighted_peg"),
            "qoq_growth": ("score_qoq_growth", "weighted_qoq_growth"),
            "consecutive_profit_growth_quarters": ("score_consecutive_profit_growth_quarters", "weighted_consecutive_profit_growth_quarters"),
            "analyst_revision": ("score_analyst_revision", "weighted_analyst_revision"),
            "quality": ("score_quality", "weighted_quality"),
        }
        for factor_name, (score_field, weighted_field) in factor_exports.items():
            factor_rows = sorted(ranked_rows, key=lambda row: (-float(row[score_field]), row["ts_code"]))
            export_rows = [
                {
                    "factor_rank": index,
                    "ts_code": row["ts_code"],
                    "name": row["name"],
                    "source": row["source"],
                    "factor_score": row[score_field],
                    "weighted_score": row[weighted_field],
                    "overall_rank": row["rank"],
                    "total_score": row["total_score"],
                }
                for index, row in enumerate(factor_rows, 1)
            ]
            write_csv_rows(
                output_dir / f"factor_rank_{factor_name}.csv",
                export_rows,
                ["factor_rank", "ts_code", "name", "source", "factor_score", "weighted_score", "overall_rank", "total_score"],
            )
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
