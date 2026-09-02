import argparse
import hashlib
import json
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
HOST = "127.0.0.1"
PORTS = range(8765, 8776)
APP_ID = "multifactor-weight-control-" + hashlib.sha256(
    str(ROOT.resolve()).casefold().encode("utf-8")
).hexdigest()[:12]
LOG_PATH = ROOT / "logs" / "weight_control_server.log"


def find_server():
    for port in PORTS:
        try:
            with urllib.request.urlopen(f"http://{HOST}:{port}/health", timeout=0.4) as response:
                payload = json.loads(response.read().decode("utf-8"))
                if response.status == 200 and payload.get("app") == APP_ID:
                    return port
        except Exception:
            continue
    return None


def start_server():
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log = LOG_PATH.open("a", encoding="utf-8")
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    subprocess.Popen(
        [sys.executable, str(ROOT / "src" / "weight_control_server.py"), "--no-open"],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
        close_fds=True,
    )
    log.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("page", choices=("portal", "weights", "dashboard", "research"), default="portal", nargs="?")
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()

    port = find_server()
    if port is None:
        start_server()
        for _ in range(40):
            time.sleep(0.25)
            port = find_server()
            if port is not None:
                break
    if port is None:
        raise SystemExit(f"权重设置服务启动失败，请查看日志：{LOG_PATH}")
    if not args.no_open:
        webbrowser.open(f"http://{HOST}:{port}/{args.page}")


if __name__ == "__main__":
    main()
