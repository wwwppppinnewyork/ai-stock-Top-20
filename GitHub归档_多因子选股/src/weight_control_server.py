import argparse
import hashlib
import json
import os
import subprocess
import sys
import threading
import urllib.request
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "weights.json"
CONTROL_PAGE = ROOT / "src" / "weight_control.html"
RESEARCH_PAGE = ROOT / "src" / "stock_research.html"
PORTAL_PAGE = ROOT / "src" / "portal.html"
BACKTESTS_PAGE = ROOT / "src" / "backtests.html"
VIEW_DIR = ROOT / "查看结果"
HOST = "127.0.0.1"
PORT = 8765
MAX_PORT = 8775
# Include the project path in the service identity.  Otherwise a server left
# running by another copy of the project can be mistaken for this one, causing
# the launcher to open the wrong weights/config files.
APP_ID = "multifactor-weight-control-" + hashlib.sha256(
    str(ROOT.resolve()).casefold().encode("utf-8")
).hexdigest()[:12]


def load_saved_openai_settings():
    """Inherit saved per-user OpenAI settings even from an older parent process."""
    if os.name != "nt":
        return
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as environment_key:
            for name in ("OPENAI_API_KEY", "OPENAI_MODEL"):
                if os.environ.get(name, "").strip():
                    continue
                try:
                    value, _ = winreg.QueryValueEx(environment_key, name)
                except FileNotFoundError:
                    continue
                if isinstance(value, str) and value.strip():
                    os.environ[name] = value.strip()
    except OSError:
        pass


load_saved_openai_settings()

GROUPS = {
    "weights": ("analyst_profit_yoy", "peg", "qoq_growth", "consecutive_profit_growth_quarters", "analyst_net_profit_revision_change", "quality"),
    "peg_year_weights": ("fy1", "fy2", "fy3"),
    "profit_yoy_year_weights": ("fy1", "fy2", "fy3"),
    "revision_year_weights": ("fy1", "fy2"),
    "quality_year_weights": ("prior", "current"),
    "quality_subweights": ("roe", "gross_margin", "net_margin"),
}
QOQ_FIELDS = ("non_full_positive_ratio", "quarter_cycle_positive_full_ratio")
state_lock = threading.Lock()
state = {"running": False, "status": "idle", "message": "尚未开始更新", "started_at": "", "finished_at": ""}

REPORT_HOME_LINK = """<style id="report-home-style">
.report-home-link{position:fixed;top:18px;right:24px;z-index:9999;display:inline-flex;align-items:center;justify-content:center;padding:8px 14px;border:1px solid rgba(255,255,255,.55);border-radius:999px;background:#155e63;color:#fff!important;text-decoration:none!important;font:900 12px/1.2 \"Microsoft YaHei\",sans-serif;box-shadow:0 6px 18px rgba(23,32,51,.18)}
.report-home-link:hover{background:#fff;color:#155e63!important;border-color:#155e63}.report-home-link:focus-visible{outline:3px solid #e9a23b;outline-offset:3px}
@media(max-width:720px){.report-home-link{top:10px;right:10px;padding:7px 11px}}
</style><a class="report-home-link" href="/" aria-label="返回统一网页主页">← 返回网页</a>"""


def json_response(handler, status, payload):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def validate_config(payload):
    if not isinstance(payload, dict):
        raise ValueError("提交内容格式错误")
    clean = {}
    for group, fields in GROUPS.items():
        source = payload.get(group)
        if not isinstance(source, dict):
            raise ValueError(f"缺少权重组：{group}")
        values = {}
        for field in fields:
            try:
                value = float(source[field])
            except (KeyError, TypeError, ValueError):
                raise ValueError(f"{group}.{field} 不是有效数字")
            if value < 0 or value > 1:
                raise ValueError(f"{group}.{field} 必须在0%至100%之间")
            values[field] = round(value, 8)
        if abs(sum(values.values()) - 1.0) > 0.0001:
            raise ValueError(f"{group} 合计必须为100%")
        clean[group] = values
    qoq = payload.get("qoq_rules")
    if not isinstance(qoq, dict):
        raise ValueError("缺少环比规则参数")
    clean_qoq = {}
    for field in QOQ_FIELDS:
        try:
            value = float(qoq[field])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"qoq_rules.{field} 不是有效数字")
        if value < 0 or value > 1:
            raise ValueError(f"qoq_rules.{field} 必须在0%至100%之间")
        clean_qoq[field] = round(value, 8)
    clean["qoq_rules"] = clean_qoq
    # Keep the on-disk order familiar to the strategy and the user.
    return {
        "weights": clean["weights"],
        "qoq_rules": clean["qoq_rules"],
        "peg_year_weights": clean["peg_year_weights"],
        "profit_yoy_year_weights": clean["profit_yoy_year_weights"],
        "revision_year_weights": clean["revision_year_weights"],
        "quality_subweights": clean["quality_subweights"],
        "quality_year_weights": clean["quality_year_weights"],
    }


def save_config(config):
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = CONFIG_PATH.with_name(f"weights.backup.{timestamp}.json")
    if CONFIG_PATH.exists():
        backup.write_bytes(CONFIG_PATH.read_bytes())
    temporary = CONFIG_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, CONFIG_PATH)
    return backup


def run_update(backup_path):
    with state_lock:
        state.update(running=True, status="running", message="正在使用最新本地数据重新计算排名…", started_at=datetime.now().isoformat(timespec="seconds"), finished_at="")
    try:
        result = subprocess.run(
            [sys.executable, str(ROOT / "run_weight_update.py")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
        lines = [line.strip() for line in result.stdout.splitlines() if " | " in line]
        summary = "更新成功。" + (" 最新前三名：" + "；".join(lines[:3]) if lines else "")
        with state_lock:
            state.update(running=False, status="success", message=summary, finished_at=datetime.now().isoformat(timespec="seconds"))
    except subprocess.CalledProcessError as exc:
        if backup_path.exists():
            restore_path = CONFIG_PATH.with_suffix(".json.restore")
            restore_path.write_bytes(backup_path.read_bytes())
            os.replace(restore_path, CONFIG_PATH)
            # The strategy runner may have rendered the old ranking with the
            # attempted weights before failing. Re-render using the restored
            # weights so displayed maxima and stored scores cannot disagree.
            subprocess.run([sys.executable, str(ROOT / "src" / "render_dashboard.py")], cwd=ROOT, capture_output=True)
            subprocess.run([sys.executable, str(ROOT / "src" / "publish_results.py")], cwd=ROOT, capture_output=True)
        error_lines = (exc.stderr or exc.stdout or str(exc)).strip().splitlines()
        message = error_lines[-1] if error_lines else "更新失败"
        with state_lock:
            state.update(running=False, status="failed", message=f"更新失败，已恢复调整前权重：{message}", finished_at=datetime.now().isoformat(timespec="seconds"))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        if path == "/api/config":
            try:
                payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                json_response(self, 200, payload)
            except Exception as exc:
                json_response(self, 500, {"error": str(exc)})
            return
        if path == "/api/status":
            with state_lock:
                payload = dict(state)
            json_response(self, 200, payload)
            return
        if path == "/health":
            json_response(self, 200, {"ok": True, "app": APP_ID})
            return
        if path in ("/", "/portal"):
            self.serve_file(PORTAL_PAGE, "text/html; charset=utf-8")
            return
        if path == "/weights":
            self.serve_file(CONTROL_PAGE, "text/html; charset=utf-8")
            return
        if path == "/research":
            self.serve_file(RESEARCH_PAGE, "text/html; charset=utf-8")
            return
        if path == "/api/research/status":
            json_response(self, 200, {
                "ok": True,
                "api_key_configured": bool(os.environ.get("OPENAI_API_KEY", "").strip()),
                "model": os.environ.get("OPENAI_MODEL", "gpt-5.6-sol"),
            })
            return
        if path == "/api/research/questions":
            from stock_research import public_questions
            json_response(self, 200, {"questions": public_questions()})
            return
        if path == "/api/research/history":
            from stock_research import list_history
            json_response(self, 200, {"items": list_history()})
            return
        if path == "/api/research/history/item":
            try:
                from stock_research import load_history
                cache_id = parse_qs(urlparse(self.path).query).get("id", [""])[0]
                json_response(self, 200, load_history(cache_id))
            except ValueError as exc:
                json_response(self, 404, {"error": str(exc)})
            except Exception as exc:
                json_response(self, 500, {"error": str(exc)})
            return
        if path == "/dashboard":
            self.serve_file(VIEW_DIR / "多因子选股看板.html", "text/html; charset=utf-8")
            return
        if path == "/backtests":
            self.serve_file(BACKTESTS_PAGE, "text/html; charset=utf-8")
            return
        requested = (VIEW_DIR / path.lstrip("/")).resolve()
        if requested.is_relative_to(VIEW_DIR.resolve()) and requested.is_file():
            suffix_types = {".html": "text/html; charset=utf-8", ".csv": "text/csv; charset=utf-8", ".json": "application/json; charset=utf-8"}
            self.serve_file(requested, suffix_types.get(requested.suffix.lower(), "application/octet-stream"))
            return
        self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/research/analyze":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 20_000:
                    raise ValueError("提交内容为空或过大")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                from stock_research import analyze_stock
                json_response(self, 200, analyze_stock(payload.get("code", ""), bool(payload.get("force", False))))
            except (ValueError, json.JSONDecodeError) as exc:
                json_response(self, 400, {"error": str(exc)})
            except Exception as exc:
                json_response(self, 500, {"error": str(exc)})
            return
        if path != "/api/update":
            self.send_error(404)
            return
        with state_lock:
            if state["running"]:
                json_response(self, 409, {"error": "当前已有更新任务正在运行"})
                return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 100_000:
                raise ValueError("提交内容为空或过大")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            config = validate_config(payload)
            backup_path = save_config(config)
            threading.Thread(target=run_update, args=(backup_path,), daemon=True).start()
            json_response(self, 202, {"ok": True, "message": "权重已保存，开始更新排名"})
        except (ValueError, json.JSONDecodeError) as exc:
            json_response(self, 400, {"error": str(exc)})
        except Exception as exc:
            json_response(self, 500, {"error": str(exc)})

    def serve_file(self, path, content_type):
        try:
            data = path.read_bytes()
            if content_type.startswith("text/html") and path.resolve().is_relative_to(VIEW_DIR.resolve()):
                html = data.decode("utf-8")
                if "返回网页" not in html:
                    html = html.replace("</body>", REPORT_HOME_LINK + "</body>")
                data = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except OSError:
            self.send_error(404)


class ControlServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def server_is_running(port):
    try:
        with urllib.request.urlopen(f"http://{HOST}:{port}/health", timeout=1) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return response.status == 200 and payload.get("app") == APP_ID
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(description="多因子选股本机权重控制台")
    parser.add_argument("--page", choices=("portal", "weights", "dashboard", "research"), default="portal")
    parser.add_argument("--no-open", action="store_true", help="启动服务但不自动打开浏览器")
    args = parser.parse_args()
    for port in range(PORT, MAX_PORT + 1):
        if server_is_running(port):
            if not args.no_open:
                webbrowser.open(f"http://{HOST}:{port}/{args.page}")
            return
        try:
            server = ControlServer((HOST, port), Handler)
            break
        except OSError:
            continue
    else:
        raise RuntimeError(f"无法启动权重设置服务：端口 {PORT}-{MAX_PORT} 均被占用")
    url = f"http://{HOST}:{server.server_port}/{args.page}"
    if not args.no_open:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    server.serve_forever()


if __name__ == "__main__":
    main()
