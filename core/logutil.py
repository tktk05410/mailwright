"""极简文件日志。"""

import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
LOG_FILE = ROOT / "data" / "logs" / "app.log"


def log(msg):
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{stamp}] {msg}\n")
    except Exception:
        pass
