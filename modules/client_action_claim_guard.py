from __future__ import annotations

import re
from typing import Iterable

from modules.client_action_result import ClientActionResult


class ClientActionClaimGuard:
    """Keep visible dance claims aligned with local dispatcher acceptance."""

    DANCE_CLAIM = re.compile(
        r"(?:我(?:再|来)?跳|开始跳|跳一小段|播放舞蹈|转一圈|跳啦)"
    )

    def validate(
        self,
        message: str,
        results: Iterable[ClientActionResult],
        *,
        action_expected: bool = False,
    ) -> str:
        text = str(message or "").strip()
        items = [item for item in results if item.name == "play_dance"]
        if not items:
            if action_expected:
                return "这次没有成功派发舞蹈动作，请再叫我一次。"
            return text
        result = items[-1]
        if result.accepted and result.status in {"accepted", "running", "completed"}:
            return text or "好，我跳一小段。"
        if result.status == "skipped_busy":
            return "我刚才的动作还没结束，稍等一下再跳。"
        if result.status == "expired":
            return "这次动作请求已经过期了，可以再叫我一次。"
        if action_expected or self.DANCE_CLAIM.search(text):
            return result.display_message or "这次舞蹈没有成功播放，我需要检查一下动作系统。"
        return text
