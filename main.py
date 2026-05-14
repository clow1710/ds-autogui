from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

_existing_chromium_flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
_required_chromium_flags = (
    "--disable-renderer-backgrounding "
    "--disable-background-timer-throttling "
    "--disable-backgrounding-occluded-windows"
)
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
    f"{_existing_chromium_flags} {_required_chromium_flags}".strip()
)

from deepseek_batch.app import main


if __name__ == "__main__":
    raise SystemExit(main())
