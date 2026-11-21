"""短剧采集核心（README 第二节构建层、第三节工作模型）。

数据源可插拔：
- SampleSource：从本地 JSON 加载样例数据，用于阶段 1 本地验证，零网络依赖。
- WebSource：通用网络采集框架，JSON API 可直接配置使用；HTML 站点通过继承并
  覆写 parse_html 接入。内置 UA 轮换、限速、重试、单域名并发控制与 robots.txt 检查。

网络请求优先使用 httpx（异步），缺失时自动降级为 urllib.request（同步顺序 + 限速）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from .cleaner import clean_title, build_alias_pool
from .config import Config
from .models import AliasEntry, CrawlStats, DramaRecord, PlatformLink, RawDramaItem

logger = logging.getLogger(__name__)


# ---------- 数据源抽象 ----------


class BaseSource(ABC):
    """数据源接口。"""

    name: str = "base"

    @abstractmethod
    def fetch(self) -> list[RawDramaItem]:
        """返回原始剧目条目列表。"""


class SampleSource(BaseSource):
    """本地样例数据源（阶段 1 使用）。JSON 文件为剧目对象数组。"""

    name = "sample"

    def __init__(self, config: Config, path: str | Path | None = None):
        self.config = config
        self.path = Path(path) if path else config.sample_path

    def fetch(self) -> list[RawDramaItem]:
        if not self.path.exists():
            logger.warning("样例数据文件不存在：%s", self.path)
            return []
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("items", [])
        items = [RawDramaItem.from_dict(dict(row)) for row in data]
        logger.info("样例数据源加载完成：%d 条（%s）", len(items), self.path)
        return items


class WebSource(BaseSource):
    """通用网络数据源。

    JSON API 场景：配置 list_path（如 "data.list"）与 field_map 即可直接使用。
    HTML 场景：继承本类并覆写 parse_html(html, url) 返回 RawDramaItem 列表。

    合规约束：默认检查 robots.txt、单域名并发≤2、请求间隔 1~3s、UA 轮换。
    """

    name = "web"

    def __init__(
        self,
        config: Config,
        urls: list[str],
        *,
        list_path: str = "",
        field_map: dict[str, str] | None = None,
        name: str = "web",
    ):
        self.config = config
        self.urls = urls
        self.list_path = list_path
        self.field_map = field_map or {}
        self.name = name
        self.stats = CrawlStats(source=name)

    # ---- robots.txt ----

    def _robots_allowed(self, url: str, user_agent: str) -> bool:
        if not self.config.respect_robots_txt:
            return True
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        parser = RobotFileParser()
        parser.set_url(robots_url)
        try:
            with urllib.request.urlopen(
                urllib.request.Request(robots_url, headers={"User-Agent": user_agent}),
                timeout=self.config.link_check_timeout,
            ) as response:
                parser.parse(response.read().decode("utf-8", errors="replace").splitlines())
        except Exception:
            # 无法获取 robots.txt 时按允许处理（与常见爬虫惯例一致）
            return True
        return parser.can_fetch(user_agent, url)

    def _pick_ua(self) -> str:
        return random.choice(self.config.user_agents)

    # ---- 抓取入口 ----

    def fetch(self) -> list[RawDramaItem]:
        started = time.perf_counter()
        try:
            import httpx  # noqa: F401

            results = asyncio.run(self._fetch_async())
        except ImportError:
            logger.warning("httpx 未安装，网络采集降级为 urllib 同步模式")
            results = self._fetch_sync()
        self.stats.fetched = len(self.urls)
        self.stats.succeeded = len(results)
        self.stats.failed = len(self.urls) - len(results)
        self.stats.duration_seconds = round(time.perf_counter() - started, 2)
        logger.info("网络采集完成：%s", self.stats.model_dump())
        return results

    async def _fetch_async(self) -> list[RawDramaItem]:
        import httpx

        semaphore = asyncio.Semaphore(self.config.max_concurrency_per_domain)
        collected: list[RawDramaItem] = []

        async with httpx.AsyncClient(timeout=self.config.timeout, follow_redirects=True) as client:
            async def fetch_one(url: str) -> list[RawDramaItem]:
                async with semaphore:
                    ua = self._pick_ua()
                    if not await asyncio.to_thread(self._robots_allowed, url, ua):
                        logger.info("robots.txt 禁止抓取，跳过：%s", url)
                        self.stats.skipped += 1
                        return []
                    for attempt in range(self.config.max_retries + 1):
                        try:
                            response = await client.get(url, headers={"User-Agent": ua})
                            response.raise_for_status()
                            return self.parse_response(response.text, url)
                        except Exception as exc:  # noqa: BLE001 - 网络异常需全部兜底
                            logger.warning("抓取失败 %s（第 %d 次）：%s", url, attempt + 1, exc)
                            if attempt < self.config.max_retries:
                                await asyncio.sleep(self.config.request_interval[0])
                    return []

            for batch in await asyncio.gather(*(fetch_one(url) for url in self.urls)):
                collected.extend(batch)

        return collected

    def _fetch_sync(self) -> list[RawDramaItem]:
        collected: list[RawDramaItem] = []
        for url in self.urls:
            ua = self._pick_ua()
            if not self._robots_allowed(url, ua):
                logger.info("robots.txt 禁止抓取，跳过：%s", url)
                self.stats.skipped += 1
                continue
            for attempt in range(self.config.max_retries + 1):
                try:
                    request = urllib.request.Request(url, headers={"User-Agent": ua})
                    with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
                        text = response.read().decode("utf-8", errors="replace")
                    collected.extend(self.parse_response(text, url))
                    break
                except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                    logger.warning("抓取失败 %s（第 %d 次）：%s", url, attempt + 1, exc)
            time.sleep(random.uniform(*self.config.request_interval))
        return collected

    # ---- 响应解析 ----

    def parse_response(self, text: str, url: str) -> list[RawDramaItem]:
        """自动识别 JSON / HTML 响应。"""
        stripped = text.lstrip()
        if stripped.startswith(("{", "[")):
            try:
                return self.parse_json(json.loads(stripped), url)
            except json.JSONDecodeError:
                pass
        return self.parse_html(text, url)

    def parse_json(self, payload: Any, url: str) -> list[RawDramaItem]:
        """按 list_path 与 field_map 从 JSON 响应提取剧目条目。"""
        node = payload
        for part in filter(None, self.list_path.split(".")):
            node = node.get(part, {}) if isinstance(node, dict) else {}
        rows = node if isinstance(node, list) else []

        items: list[RawDramaItem] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            mapped: dict[str, Any] = {}
            for target, source_path in self.field_map.items():
                value: Any = row
                for part in source_path.split("."):
                    value = value.get(part) if isinstance(value, dict) else None
                if value is not None:
                    mapped[target] = value
            mapped.setdefault("source", self.name)
            mapped.setdefault("source_url", url)
            mapped.setdefault("raw", row)
            items.append(RawDramaItem.from_dict(mapped))
        return items

    def parse_html(self, html: str, url: str) -> list[RawDramaItem]:
        """HTML 解析钩子：子类覆写此方法以适配具体站点。"""
        raise NotImplementedError(
            "WebSource 未配置 HTML 解析器。请继承 WebSource 并覆写 parse_html()，"
            "或改用 JSON API 配置 list_path / field_map。"
        )


# ---------- 归一化与合并 ----------


@dataclass
class NormalizeResult:
    """采集后处理结果，供 exporter 消费。"""

    records: list[DramaRecord] = field(default_factory=list)
    alias_pool: list[AliasEntry] = field(default_factory=list)
    blacklisted: int = 0
    merged: int = 0


def _uniq(values: Iterable[str]) -> list[str]:
    """保持顺序去重。"""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _merge_group(group: list[RawDramaItem], typo_map: dict[str, str]) -> RawDramaItem:
    """合并同一剧目的多个来源条目：字段取并集，链接按 (平台, URL) 去重。"""
    base = group[0]
    titles = [clean_title(item.standard_title, typo_map) or item.standard_title for item in group]
    links: list[PlatformLink] = []
    seen_links: set[tuple[str, str]] = set()
    for item in group:
        for link in item.platform_links:
            key = (link.platform, link.h5_url)
            if key not in seen_links:
                seen_links.add(key)
                links.append(link)

    return RawDramaItem(
        source=base.source,
        source_url=base.source_url,
        standard_title=titles[0],
        aliases=_uniq(a for item in group for a in item.aliases),
        record_no=next((item.record_no for item in group if item.record_no), None),
        actors=_uniq(a for item in group for a in item.actors),
        tags=_uniq(t for item in group for t in item.tags),
        is_ai_drama=any(item.is_ai_drama for item in group),
        cover_url=next((item.cover_url for item in group if item.cover_url), None),
        intro=next((item.intro for item in group if item.intro), ""),
        platform_links=links,
        raw={"merged_from": [item.source for item in group]},
    )


def normalize_items(
    items: list[RawDramaItem],
    config: Config,
    typo_map: dict[str, str] | None = None,
    blacklist: set[str] | None = None,
    manual_merge_groups: list[list[str]] | None = None,
    *,
    merge_same_title: bool = True,
) -> NormalizeResult:
    """清洗、黑名单过滤、合并并生成 DramaRecord 与别名池。

    合并策略严格遵循 README：
    - 有备案号：按 record_no 合并；
    - 无备案号：默认分开记录，仅人工确认组才合并；
    - cumulative 模式下可开启 merge_same_title：无备案号但「清洗后标准剧名完全一致」
      视为同一剧目合并——这是精确匹配（非相似度模糊匹配），用于跨轮次/跨平台累计去重。
      若需严格按 README 的「无备案号一律分开」，显式传 merge_same_title=False。
    """
    typo_map = typo_map or {}
    blacklist = blacklist or set()
    manual_merge_groups = manual_merge_groups or []

    # 构建人工确认映射：清洗后标题 → 组编号
    manual_group_id: dict[str, int] = {}
    for idx, group in enumerate(manual_merge_groups):
        for title in group:
            manual_group_id[clean_title(title, typo_map)] = idx

    # 1) 清洗 + 黑名单过滤
    prepared: list[RawDramaItem] = []
    blacklisted = 0
    for item in items:
        title = clean_title(item.standard_title, typo_map)
        if not title:
            continue
        if title in blacklist or (item.record_no and item.record_no in blacklist):
            blacklisted += 1
            continue
        prepared.append(
            RawDramaItem.from_dict({**item.model_dump(), "standard_title": title})
        )

    # 2) 分组（备案号优先 → 人工确认组 → 同名精确合并 → 各自独立）
    groups: dict[str, list[RawDramaItem]] = {}
    for idx, item in enumerate(prepared):
        if item.record_no:
            key = f"no:{item.record_no}"
        elif item.standard_title in manual_group_id:
            key = f"manual:{manual_group_id[item.standard_title]}"
        elif merge_same_title:
            key = f"title:{item.standard_title}"
        else:
            key = f"single:{idx}"
        groups.setdefault(key, []).append(item)

    merged_items = [_merge_group(group, typo_map) for group in groups.values()]

    # 3) 生成剧目记录
    today = date.today().isoformat()
    records: list[DramaRecord] = []
    for seq, item in enumerate(merged_items, start=10001):
        records.append(
            DramaRecord(
                id=f"dr_{seq}",
                standard_title=item.standard_title,
                aliases=_uniq(item.aliases),
                record_no=item.record_no,
                actors=item.actors,
                tags=item.tags,
                is_ai_drama=item.is_ai_drama,
                cover_url=item.cover_url,
                intro=item.intro,
                platform_links=item.platform_links,
                updated_at=today,
            )
        )

    if len(records) > config.max_records:
        logger.warning("剧目数 %d 超过上限 %d，请启用分片或 MiniSearch 预索引", len(records), config.max_records)

    return NormalizeResult(
        records=records,
        alias_pool=build_alias_pool(prepared, typo_map),
        blacklisted=blacklisted,
        merged=len(prepared) - len(merged_items),
    )
