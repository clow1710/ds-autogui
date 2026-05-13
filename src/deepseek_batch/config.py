from __future__ import annotations

from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_CHAT_URL = "https://chat.deepseek.com/"
DEFAULT_PROMPTS_DIR = PROJECT_DIR / "prompts"
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "outputs"
DEFAULT_RUNTIME_DIR = PROJECT_DIR / "runtime"

SEARCH_TERMS = ["智能搜索", "联网搜索", "网页搜索", "网络搜索", "Search", "Web Search"]
DEEPTHINK_TERMS = ["深度思考", "深度思考模式", "DeepThink", "Deep Think", "R1"]
CHAT_MODE_MODEL_TYPES = {
    "fast": "default",
    "expert": "expert",
}
NEW_CHAT_TERMS = ["新对话", "新建对话", "开启新对话", "New chat", "New Chat"]
