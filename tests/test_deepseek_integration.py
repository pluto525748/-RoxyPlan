"""Opt-in, single-request DeepSeek integration check.

This file intentionally does nothing unless the user explicitly enables it.
It never exposes tools and therefore cannot mutate RoxyPlan data.
"""

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.llm.deepseek_provider import DeepSeekProvider
from modules.llm.settings import ModelSettings


def run_integration_test():
    enabled = os.environ.get("ROXY_RUN_DEEPSEEK_INTEGRATION", "").strip() == "1"
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not enabled or not key:
        print("deepseek integration test skipped")
        return

    settings = ModelSettings.from_mapping({"request_timeout_seconds": 60})
    provider = DeepSeekProvider(
        api_key=key,
        base_url=settings.deepseek_base_url,
        model_name=settings.default_model,
        timeout_seconds=settings.request_timeout_seconds,
        max_retries=0,
    )
    response = provider.chat(
        [{"role": "user", "content": "只回复：连接正常"}],
        thinking=False,
        max_tokens=32,
    )
    assert response.ok, response.error.code if response.error else "unknown_error"
    assert response.content
    print("deepseek integration test passed (one read-only chat request)")


if __name__ == "__main__":
    run_integration_test()
