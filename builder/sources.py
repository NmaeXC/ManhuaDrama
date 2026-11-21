"""可插拔短剧平台采集源（配置驱动）。

新增平台无需改采集代码：在 data/sources.json 追加一条配置即可。
- entry=home    ：抓首页，用 detail_id_pattern 提取详情页 id，套 detail_url_template 生成详情 URL
- entry=sitemap ：抓 sitemap，取 <loc> 中的详情页 URL
- entry=api     ：直接抓 JSON 列表（走 WebSource.parse_json + field_map）
- parser=ldjson_tvseason：从 schema.org ld+json 的 TVSeason 节点提取元数据（红果模式，通用化）
- parser=nextjs_bookinfo：从 Next.js __NEXT_DATA__.props.pageProps.bookInfoVo 提取（河马模式，含主演/标签）
- parser=json           ：用 list_path / field_map 从 JSON 响应提取

合规：仅处理配置中 enabled=true 且 status=active 的源；沿用 WebSource 的 robots/限速/重试。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .config import Config
from .crawler import WebSource
from .hongguo_source import LD_JSON_RE, derive_aliases
from .models import PlatformLink, RawDramaItem
from .cleaner import clean_title

logger = logging.getLogger(__name__)

FetchText = Callable[[str], str]

# Next.js 服务端注入数据块（河马剧场等使用）
NEXT_DATA_RE = re.compile(
    r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', re.S
)


@dataclass
class SourceConfig:
    """单个平台的采集配置（对应 sources.json 的一条记录）。"""

    id: str
    name: str
    platform: str = ""
    enabled: bool = False
    status: str = "need_verify"
    entry: str = "home"
    parser: str = "ldjson_tvseason"
    home_url: str = ""
    sitemap_url: str = ""
    detail_url_template: str = ""
    detail_id_pattern: str = ""
    limit: int = 30
    list_path: str = ""
    field_map: dict[str, str] = field(default_factory=dict)
    note: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceConfig":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    @property
    def usable(self) -> bool:
        """是否可用于采集：显式启用 + 已核验 + 具备入口 URL。"""
        return (
            self.enabled
            and self.status == "active"
            and bool(self.home_url or self.sitemap_url)
            and bool(self.parser)
        )


def load_sources(path: Path) -> list[SourceConfig]:
    """读取平台源配置；文件缺失或损坏返回空列表。"""
    if not path.exists():
        logger.warning("平台源配置不存在：%s", path)
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("平台源配置解析失败（%s）：%s", path, exc)
        return []
    rows = payload.get("sources", []) if isinstance(payload, dict) else []
    sources: list[SourceConfig] = []
    for row in rows:
        if isinstance(row, dict) and row.get("id"):
            try:
                sources.append(SourceConfig.from_dict(row))
            except TypeError as exc:
                logger.warning("跳过无效平台配置 %s：%s", row.get("id"), exc)
    return sources


def discover_urls(
    config: Config,
    source: SourceConfig,
    fetch_text: FetchText,
    exclude: set[str] | None = None,
) -> list[str]:
    """根据 entry 类型发现详情页 URL 列表（保序去重）。

    exclude 为累计池中已抓过的 source_url 集合：命中则跳过，从而在 limit
    额度内优先发现新剧目，实现跨轮次轮换（否则每次都取 sitemap 前 limit 个）。
    """
    if source.entry == "api":
        return [source.home_url] if source.home_url else []

    if source.entry == "sitemap":
        if not source.sitemap_url:
            return []
        xml = fetch_text(source.sitemap_url)
        urls = re.findall(r"<loc>(https?://[^<]+)</loc>", xml)
    elif source.entry == "home":
        if not source.home_url:
            return []
        html = fetch_text(source.home_url)
        if source.detail_id_pattern:
            ids = re.findall(source.detail_id_pattern, html)
        else:
            # 兜底：直接匹配详情页 href
            ids = re.findall(r'href="([^"]*?/detail[^"]*)"', html)
        if source.detail_url_template:
            # 兼容命名占位符 {id} 与位置占位符 {}
            def render(i: str) -> str:
                try:
                    return source.detail_url_template.format(id=i)
                except (KeyError, IndexError):
                    try:
                        return source.detail_url_template.format(i)
                    except (KeyError, IndexError):
                        return i

            urls = [render(i) for i in ids]
        else:
            urls = list(ids)
    else:
        logger.warning("未知 entry=%s（source=%s）", source.entry, source.id)
        return []

    exclude = exclude or set()
    result: list[str] = []
    seen: set[str] = set()
    for url in urls:
        url = url.strip()
        if url in seen:
            continue
        seen.add(url)
        if url in exclude:
            continue
        result.append(url)
        if len(result) >= source.limit:
            break
    return result


class PlatformSource(WebSource):
    """通用平台采集器：解析行为由 SourceConfig 决定。"""

    def __init__(self, config: Config, source_cfg: SourceConfig, urls: list[str]):
        super().__init__(
            config,
            urls,
            list_path=source_cfg.list_path,
            field_map=source_cfg.field_map,
            name=source_cfg.id,
        )
        self.source_cfg = source_cfg

    def fetch(self) -> list[RawDramaItem]:
        """请求成功后回填 valid=True（详情页 URL 可达这一事实）。

        仅对详情页型解析器（ldjson_tvseason / nextjs_bookinfo）回填；
        json 列表型由数据源自身携带 valid，不在此假定。
        """
        items = super().fetch()
        if self.source_cfg.parser not in ("ldjson_tvseason", "nextjs_bookinfo"):
            return items
        for item in items:
            for link in item.platform_links:
                if link.h5_url == item.source_url:
                    link.valid = True
        return items

    def parse_response(self, text: str, url: str) -> list[RawDramaItem]:
        if self.source_cfg.parser == "json":
            stripped = text.lstrip()
            if stripped.startswith(("{", "[")):
                try:
                    return self.parse_json(json.loads(stripped), url)
                except json.JSONDecodeError:
                    return []
            return []
        return self.parse_html(text, url)

    def parse_html(self, html: str, url: str) -> list[RawDramaItem]:
        """按 parser 分派详情页解析。"""
        if self.source_cfg.parser == "nextjs_bookinfo":
            return self._parse_nextjs_bookinfo(html, url)
        return self._parse_ldjson(html, url)

    def _parse_nextjs_bookinfo(self, html: str, url: str) -> list[RawDramaItem]:
        """河马模式：从 __NEXT_DATA__.props.pageProps.bookInfoVo 提取元数据。

        bookInfoVo 字段：bookName / introduction / coverWap / actor / actress /
        bookTypeThree([{name}]) / totalChapterNum。含主演与题材标签，弥补红果无主演的缺口。
        """
        match = NEXT_DATA_RE.search(html)
        if not match:
            return []
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            return []
        book = (
            data.get("props", {})
            .get("pageProps", {})
            .get("bookInfoVo", {})
        )
        if not isinstance(book, dict):
            return []

        raw_title = str(book.get("bookName") or "").strip()
        if not raw_title:
            return []
        title = clean_title(raw_title) or raw_title

        actors = [
            str(book.get(k)).strip()
            for k in ("actor", "actress")
            if book.get(k) and str(book.get(k)).strip()
        ]

        tags: list[str] = []
        for t in book.get("bookTypeThree") or []:
            name = (t.get("name") if isinstance(t, dict) else t)
            if name and str(name).strip():
                tags.append(str(name).strip())
        episodes = book.get("totalChapterNum")
        if isinstance(episodes, int) and episodes > 0:
            tags.append(f"全{episodes}集")

        cover = book.get("coverWap") or None
        intro = str(book.get("introduction") or "").strip()
        platform = self.source_cfg.platform or self.source_cfg.name

        return [
            RawDramaItem(
                source=self.name,
                source_url=url,
                standard_title=title,
                aliases=derive_aliases(title),
                record_no=None,
                actors=actors,
                tags=tags,
                is_ai_drama=False,
                cover_url=cover,
                intro=intro,
                platform_links=[
                    PlatformLink(
                        platform=platform,
                        h5_url=url,
                        scheme=None,
                        valid=None,
                    )
                ],
                raw={"book_id": book.get("bookId"), "episodes": episodes},
            )
        ]

    def _parse_ldjson(self, html: str, url: str) -> list[RawDramaItem]:
        """通用 schema.org 详情页解析：取 TVSeason / TVSeries 节点。"""
        season = None
        for block in LD_JSON_RE.findall(html):
            try:
                data = json.loads(block)
            except json.JSONDecodeError:
                continue
            nodes = data.get("@graph", []) if isinstance(data, dict) else []
            for node in nodes:
                if node.get("@type") in ("TVSeason", "TVSeries"):
                    season = node
                    break
            if season:
                break
        if not season:
            return []

        raw_title = (season.get("name") or "").strip()
        if not raw_title:
            return []
        title = clean_title(raw_title) or raw_title
        episodes = season.get("numberOfEpisodes")
        tags = [f"全{episodes}集"] if isinstance(episodes, int) and episodes > 0 else []

        return [
            RawDramaItem(
                source=self.name,
                source_url=url,
                standard_title=title,
                aliases=derive_aliases(title),
                record_no=None,
                actors=[],
                tags=tags,
                is_ai_drama=False,
                cover_url=season.get("image"),
                intro=(season.get("description") or "").strip(),
                platform_links=[
                    PlatformLink(
                        platform=self.source_cfg.platform or self.source_cfg.name,
                        h5_url=url,
                        scheme=None,
                        valid=None,
                    )
                ],
                raw={"episodes": episodes},
            )
        ]

    def parse_json(self, payload: Any, url: str) -> list[RawDramaItem]:
        """JSON 列表解析：沿用基类 field_map，并补齐平台链接。"""
        items = super().parse_json(payload, url)
        for item in items:
            if not item.platform_links and item.source_url:
                item.platform_links = [
                    PlatformLink(
                        platform=self.source_cfg.platform or self.source_cfg.name,
                        h5_url=item.source_url,
                        valid=None,
                    )
                ]
        return items
