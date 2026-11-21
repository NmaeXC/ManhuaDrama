"""链接存活检测（README 第四节 link_checker、风险 6）。

策略：
- 只用 HEAD 请求，超时控制；HEAD 被拒（405/501）时降级为 GET。
- scheme:// 链接无法通过 HTTP 检测，跳过且不改变原 valid 值。
- 全量链接按稳定哈希分批，每日轮换校验其中一批，避免单轮耗时过长。
"""
from __future__ import annotations

import hashlib
import logging
import random
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date
from math import ceil

from .config import Config
from .models import DramaRecord

logger = logging.getLogger(__name__)


@dataclass
class LinkCheckResult:
    """链接检测统计。"""

    checked: int = 0
    alive: int = 0
    dead: int = 0
    skipped: int = 0

    def model_dump(self) -> dict[str, int]:
        return {
            "checked": self.checked,
            "alive": self.alive,
            "dead": self.dead,
            "skipped": self.skipped,
        }


class LinkChecker:
    """平台分享链接存活检测器。"""

    def __init__(self, config: Config):
        self.config = config

    def check_one(self, url: str, user_agent: str | None = None) -> bool | None:
        """检测单条链接。返回 None 表示无法检测（非 HTTP 协议）。"""
        if not url.startswith(("http://", "https://")):
            return None
        ua = user_agent or random.choice(self.config.user_agents)
        timeout = self.config.link_check_timeout

        try:
            import httpx

            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                response = client.head(url, headers={"User-Agent": ua})
                if response.status_code in (405, 501):
                    response = client.get(url, headers={"User-Agent": ua})
                return response.status_code < 400
        except ImportError:
            pass
        except Exception as exc:  # noqa: BLE001
            logger.debug("httpx 检测失败，尝试 urllib：%s", exc)

        request = urllib.request.Request(url, headers={"User-Agent": ua}, method="HEAD")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status < 400
        except urllib.error.HTTPError as exc:
            if exc.code in (405, 501):
                get_request = urllib.request.Request(url, headers={"User-Agent": ua})
                try:
                    with urllib.request.urlopen(get_request, timeout=timeout) as response:
                        return response.status < 400
                except Exception:  # noqa: BLE001
                    return False
            return False
        except Exception:  # noqa: BLE001 - 超时、DNS、证书异常均视为失效
            return False

    def check_records(
        self,
        records: list[DramaRecord],
        batch_index: int | None = None,
    ) -> LinkCheckResult:
        """批量检测并按批次轮换更新 link.valid。"""
        links = [(record, link) for record in records for link in record.platform_links]
        total = len(links)
        if total == 0:
            return LinkCheckResult()

        batch_count = max(1, ceil(total / self.config.link_check_batch_size))
        if batch_index is None:
            batch_index = date.today().toordinal() % batch_count

        result = LinkCheckResult()
        for _, link in links:
            digest = int(hashlib.md5(link.h5_url.encode("utf-8")).hexdigest(), 16)
            if digest % batch_count != batch_index:
                result.skipped += 1
                continue

            alive = self.check_one(link.h5_url)
            if alive is None:  # scheme:// 等非 HTTP 链接
                result.skipped += 1
                continue

            link.valid = alive
            result.checked += 1
            if alive:
                result.alive += 1
            else:
                result.dead += 1

        logger.info("链接检测完成：%s（批次 %d/%d）", result.model_dump(), batch_index + 1, batch_count)
        return result
