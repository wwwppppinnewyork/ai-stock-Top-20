"""Wind-backed 48-question company research using the OpenAI Responses API."""

from __future__ import annotations

import json
import math
import os
import re
import threading
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path


QUESTIONS = [
    (1, "一、基本信息", "公司名称", "文字填写"),
    (2, "一、基本信息", "股票代码", "文字填写"),
    (3, "一、基本信息", "所属行业", "文字填写"),
    (4, "一、基本信息", "研究日期", "文字填写"),
    (5, "一、基本信息", "当前股价", "文字填写"),
    (6, "一、基本信息", "总市值", "文字填写"),
    (7, "一、基本信息", "是否在自己的能力圈内（100/1000/10000小时累计学习时间）", "是 / 否 / 不确定"),
    (8, "一、基本信息", "能否用一分钟说清其生意模式", "能 / 不能"),
    (9, "二、护城河分析", "护城河类型是否明确？", "是 / 否"),
    (10, "二、护城河分析", "护城河是否在持续加深？", "是 / 否 / 变浅"),
    (11, "二、护城河分析", "主要竞争对手是谁？公司的核心优势是什么？", "文字填写"),
    (12, "二、护城河分析", "护城河根基是否有被技术变革侵蚀的风险？", "低 / 中 / 高"),
    (13, "二、护城河分析", "护城河是否依赖某一个关键人物？", "是 / 否"),
    (14, "二、护城河分析", "能否通过林迪效应与反脆弱性双重检验？", "通过 / 存疑"),
    (15, "二、护城河分析", "如果新进入者带充足资本杀入，能否抢走主要客户和利润？", "能 / 不能"),
    (16, "三、财务指标", "毛利率是否长期高于同行且稳定/上升？", "是 / 否"),
    (17, "三、财务指标", "净利率是否长期稳定？", "是 / 否"),
    (18, "三、财务指标", "净利润与经营现金流是否长期匹配？", "是 / 否"),
    (19, "三、财务指标", "应收账款增速是否远超营收增速？", "是 / 否"),
    (20, "三、财务指标", "ROE长期水平？", ">15% / 10%-15% / <10%"),
    (21, "三、财务指标", "ROIC是否显著高于融资成本（WACC）？", "是 / 否"),
    (22, "三、财务指标", "自由现金流是否长期为正？", "是 / 否"),
    (23, "三、财务指标", "资本开支占净利润比例是否过高？", "是 / 否"),
    (24, "三、财务指标", "高ROE主要靠什么驱动？", "利润率 / 周转率 / 杠杆 / 组合"),
    (25, "三、财务指标", "有息负债率是否偏高？", "安全 / 警惕"),
    (26, "四、分红记录", "过去5—10年分红是否持续增长？", "持续增长 / 波动 / 下滑"),
    (27, "四、分红记录", "分红率是否稳定在合理区间？", "是 / 否"),
    (28, "四、分红记录", "分红增长动力来自利润增长还是分红率短期提高？", "文字填写"),
    (29, "四、分红记录", "每年分红到账后复投的可行性？", "高 / 中 / 低"),
    (30, "五、估值与安全边际", "当前估值是否在合理区间？", "文字填写"),
    (31, "五、估值与安全边际", "未来5—10年累计利润或分红能否覆盖买入成本？", "能 / 不能 / 不确定"),
    (32, "五、估值与安全边际", "若买入后股价跌30%—50%能否安心持有甚至加仓？", "能 / 不能 / 需本人确认"),
    (33, "五、估值与安全边际", "当前市盈率是否在历史合理分位？", "偏高 / 合理 / 偏低"),
    (34, "五、估值与安全边际", "当前股息率是否达到预期？", "是 / 否 / 需本人确认"),
    (35, "六、管理团队与公司治理", "管理层是否诚实守信、过往承诺是否兑现？", "是 / 否 / 不确定"),
    (36, "六、管理团队与公司治理", "行业景气与不景气时分别采取什么策略？", "文字填写"),
    (37, "六、管理团队与公司治理", "是否对股东保持尊重、持续稳定分红？", "是 / 否"),
    (38, "六、管理团队与公司治理", "有没有长期主义的产品态度和经营理念？", "是 / 否 / 不确定"),
    (39, "七、风险排查", "政策变化对该行业的影响程度？", "低 / 中 / 高"),
    (40, "七、风险排查", "消费趋势或代际更替对产品的冲击？", "低 / 中 / 高"),
    (41, "七、风险排查", "商业模式是否存在被降维打击的可能？", "低 / 中 / 高"),
    (42, "七、风险排查", "供应链资源端是否受制于单一外部条件？", "是 / 否 / 不确定"),
    (43, "七、风险排查", "公司是否过度依赖高杠杆运营？", "是 / 否"),
    (44, "八、最终决策", "所有核心检查项是否有否或不确定？", "无 / 有"),
    (45, "八、最终决策", "这笔买入决策是在冷静复盘后做出的吗？", "需本人确认"),
    (46, "八、最终决策", "是否愿意长期持有5—10年以上？", "是 / 否 / 需本人确认"),
    (47, "八、最终决策", "是否与现有持仓有过度重叠？", "是 / 否 / 需本人确认"),
    (48, "八、最终决策", "最终决策", "买入 / 观察 / 放弃"),
]

WIND_LOCK = threading.Lock()
ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "data" / "research_cache"


def normalize_code(value: str) -> str:
    code = re.sub(r"\s+", "", str(value or "")).upper()
    if re.fullmatch(r"\d{6}", code):
        if code.startswith(("4", "8")):
            return code + ".BJ"
        if code.startswith(("5", "6", "9")):
            return code + ".SH"
        return code + ".SZ"
    if re.fullmatch(r"[A-Z0-9.-]{1,24}", code) and "." in code:
        return code
    raise ValueError("请输入六位A股代码，或完整Wind代码（如300308.SZ、NVDA.O）")


def _clean(value):
    if isinstance(value, float) and math.isnan(value):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _require(result, label):
    if result.ErrorCode:
        raise RuntimeError(f"Wind读取{label}失败（错误码 {result.ErrorCode}）")
    return result


def _wss(w, code, fields, options=""):
    result = _require(w.wss(code, fields, options), fields)
    return {field.lower(): _clean(values[0]) for field, values in zip(result.Fields, result.Data)}


def fetch_wind_research(raw_code: str) -> dict:
    code = normalize_code(raw_code)
    with WIND_LOCK:
        from WindPy import w

        started = w.start()
        if started.ErrorCode or not w.isconnected():
            raise RuntimeError("Wind未连接，请先登录并启动Wind终端")
        trade = _require(w.tdaysoffset(0, date.today().isoformat(), ""), "最近交易日")
        trade_day = trade.Data[0][0]
        if hasattr(trade_day, "date"):
            trade_day = trade_day.date()
        trade_text = trade_day.strftime("%Y%m%d")
        current = _wss(
            w,
            code,
            "sec_name,industry_sw_2021,close,mkt_cap_ard,pe_ttm,pb_mrq,dividendyield2,west_netprofit_yoy,west_netprofit_fy1,west_netprofit_fy2,west_netprofit_fy3",
            f"tradeDate={trade_text};industryType=1;unit=1",
        )
        if not current.get("sec_name"):
            raise ValueError(f"Wind未识别股票代码：{code}")

        annual_fields = (
            "tot_oper_rev,np_belongto_parcomsh,acct_rcv,roe,roic,grossprofitmargin,netprofitmargin,"
            "cash_pay_acq_const_fiolta,net_cash_flows_oper_act,interestdebt,tot_assets,tot_equity,eps_basic,div_cashbeforetax"
        )
        annual = {}
        for year in range(date.today().year - 10, date.today().year):
            try:
                annual[str(year)] = _wss(w, code, annual_fields, f"rptDate={year}1231;rptType=1;unit=1")
            except RuntimeError:
                annual[str(year)] = {}

        valuation = {}
        try:
            history = _require(
                w.wsd(code, "pe_ttm,pb_mrq,dividendyield2", f"{date.today().year - 10}-01-01", trade_day.isoformat(), "Period=W;PriceAdj=F"),
                "历史估值",
            )
            valuation = {
                "dates": [_clean(x) for x in history.Times],
                **{field.lower(): [_clean(v) for v in values] for field, values in zip(history.Fields, history.Data)},
            }
        except RuntimeError:
            valuation = {}

    return {
        "code": code,
        "research_date": date.today().isoformat(),
        "trade_date": trade_day.isoformat(),
        "currency_note": "A股通常为人民币口径；海外证券以Wind返回的证券本币口径为准",
        "current": current,
        "annual": annual,
        "valuation_history": valuation,
    }


def _response_text(payload: dict) -> str:
    for item in payload.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                return content.get("text", "")
    raise RuntimeError("OpenAI API未返回可读取的文本结果")


def call_openai(wind_data: dict) -> dict:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("尚未设置 OPENAI_API_KEY 环境变量")
    model = os.environ.get("OPENAI_MODEL", "gpt-5.6-sol").strip() or "gpt-5.6-sol"
    question_payload = [
        {"number": n, "module": module, "question": question, "allowed_answer": allowed}
        for n, module, question, allowed in QUESTIONS
    ]
    instructions = (
        "你是一名严谨的上市公司研究助理。请依据用户提供的Wind数据并使用网页搜索核验定性信息，逐项回答固定的48个问题。"
        "不得编造数据；缺失时明确写‘数据不足’。结论必须区分事实、计算和推断。"
        "第7、32、34、45、46、47项涉及用户本人偏好或持仓，不能替用户决定，requires_user_confirmation必须为true。"
        "第48项只能给出研究流程层面的‘买入/观察/放弃’，且默认审慎；这不是个性化投资建议。"
        "evidence必须简短，只保留‘来源简称｜年份或日期｜关键数值’，不写完整句子、不重复answer、不附长网址，建议不超过40个汉字；"
        "confidence只能是高、中、低。"
    )
    input_text = json.dumps({"questions": question_payload, "wind_data": wind_data}, ensure_ascii=False, separators=(",", ":"))
    answer_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["company_name", "stock_code", "as_of", "summary", "answers", "risk_notice"],
        "properties": {
            "company_name": {"type": "string"},
            "stock_code": {"type": "string"},
            "as_of": {"type": "string"},
            "summary": {"type": "string"},
            "answers": {
                "type": "array",
                "minItems": 48,
                "maxItems": 48,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["number", "answer", "evidence", "confidence", "requires_user_confirmation"],
                    "properties": {
                        "number": {"type": "integer", "minimum": 1, "maximum": 48},
                        "answer": {"type": "string"},
                        "evidence": {"type": "string"},
                        "confidence": {"type": "string", "enum": ["高", "中", "低"]},
                        "requires_user_confirmation": {"type": "boolean"},
                    },
                },
            },
            "risk_notice": {"type": "string"},
        },
    }
    request_body = {
        "model": model,
        "instructions": instructions,
        "input": input_text,
        "tools": [{"type": "web_search_preview"}],
        "text": {"format": {"type": "json_schema", "name": "stock_research_48", "strict": True, "schema": answer_schema}},
        "store": False,
    }
    if model in {"gpt-5.6-sol", "gpt-5.6"}:
        request_body["reasoning"] = {"effort": "none"}
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(request_body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            message = json.loads(detail).get("error", {}).get("message", detail)
        except json.JSONDecodeError:
            message = detail
        raise RuntimeError(f"OpenAI API请求失败（HTTP {exc.code}）：{message[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"无法连接OpenAI API：{exc.reason}") from exc

    result = json.loads(_response_text(payload))
    numbers = [item.get("number") for item in result.get("answers", [])]
    if numbers != list(range(1, 49)):
        raise RuntimeError("模型返回的48项答案序号不完整，请重试")
    result["model"] = model
    result["wind_data"] = wind_data
    return result


def _read_cache_file(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("answers"), list):
        raise ValueError(f"历史记录格式无效：{path.name}")
    payload["cache_file"] = str(path)
    payload["from_cache"] = True
    return payload


def list_history() -> list[dict]:
    if not CACHE_DIR.exists():
        return []
    items = []
    for path in sorted(CACHE_DIR.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            payload = _read_cache_file(path)
            items.append({
                "id": path.name,
                "company_name": payload.get("company_name", "未知公司"),
                "stock_code": payload.get("stock_code", ""),
                "as_of": payload.get("as_of", ""),
                "model": payload.get("model", "未知"),
                "generated_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
            })
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return items


def load_history(cache_id: str) -> dict:
    if not isinstance(cache_id, str) or Path(cache_id).name != cache_id or not cache_id.endswith(".json"):
        raise ValueError("历史记录编号无效")
    path = (CACHE_DIR / cache_id).resolve()
    if not path.is_relative_to(CACHE_DIR.resolve()) or not path.is_file():
        raise ValueError("历史记录不存在")
    return _read_cache_file(path)


def latest_cached_research(raw_code: str) -> dict | None:
    code = normalize_code(raw_code)
    safe_code = re.sub(r"[^A-Z0-9.-]", "_", code)
    if not CACHE_DIR.exists():
        return None
    paths = sorted(CACHE_DIR.glob(f"{safe_code}_*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    for path in paths:
        try:
            return _read_cache_file(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return None


def analyze_stock(raw_code: str, force: bool = False) -> dict:
    if not force:
        cached = latest_cached_research(raw_code)
        if cached is not None:
            return cached
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        raise RuntimeError("尚未设置 OPENAI_API_KEY 环境变量")
    wind_data = fetch_wind_research(raw_code)
    result = call_openai(wind_data)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    safe_code = re.sub(r"[^A-Z0-9.-]", "_", wind_data["code"])
    path = CACHE_DIR / f"{safe_code}_{datetime.now():%Y%m%d-%H%M%S}.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    result["cache_file"] = str(path)
    result["from_cache"] = False
    return result


def public_questions() -> list[dict]:
    return [
        {"number": n, "module": module, "question": question, "allowed_answer": allowed}
        for n, module, question, allowed in QUESTIONS
    ]
