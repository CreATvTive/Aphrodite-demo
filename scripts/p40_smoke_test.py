"""P40 — LLM API smoke test. Read DEEPSEEK_API_KEY from env only.

不打印、不记录、不提交 API key。仅从环境变量读取。
"""
import os
import sys

# 确保项目根目录在 sys.path
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from agentlib.ds_client import DSClient
from agentlib.env_loader import load_local_env_once


def main() -> None:
    # 从 .env 加载环境变量
    load_local_env_once()

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key or not api_key.strip():
        print("SKIP: DEEPSEEK_API_KEY not set")
        return

    client = DSClient(api_key=api_key.strip())
    ok = client.ping()
    print(f"Ping: {'OK' if ok else 'FAIL'}")
    if not ok:
        print("FATAL: API unreachable or key invalid")
        sys.exit(2)

    # Minimal test call
    r = client.chat_completion(
        [{"role": "user", "content": "Hello"}],
        max_tokens=10,
    )
    print(f"Response: {r[:80]}")


if __name__ == "__main__":
    main()
