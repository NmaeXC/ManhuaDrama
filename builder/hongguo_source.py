"""红果短剧官方 SEO 详情页采集器。

合规性：
- 数据源为 hongguoduanju.com sitemap 公开的 /detail?series_id= 详情页；
  该路径未被 robots.txt 禁止（禁止的是 /query/、/series/、/player/*）。
- 仅提取 ld+json 中的剧目元数据：剧名、简介、封面、集数；不访问播放接口。
- 继承 WebSource 的 UA 轮换、限速、重试、robots.txt 检查与降级机制。
"""
from __future__ import annotations

import json
import logging
import re

from .cleaner import clean_title
from .crawler import WebSource
from .models import PlatformLink, RawDramaItem

logger = logging.getLogger(__name__)

LD_JSON_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.S | re.I,
)


class HongguoDetailSource(WebSource):
    """从红果官方详情页的 schema.org 结构化数据提取剧目元数据。"""

    name = "hongguo_detail"

    def fetch(self) -> list[RawDramaItem]:
        """抓取成功后，用「该 URL 已成功返回页面」这一事实回填 valid。

        parse_html 阶段一律 valid=None（未检测）；只有真正请求成功的详情页
        才标记为可达，避免写死 True 造成占位/失效链接被误当作有效。
        """
        items = super().fetch()
        for item in items:
            for link in item.platform_links:
                if link.h5_url == item.source_url:
                    link.valid = True
        return items

    def parse_html(self, html: str, url: str) -> list[RawDramaItem]:
        blocks = LD_JSON_RE.findall(html)
        if not blocks:
            logger.debug("页面无 ld+json：%s", url)
            return []

        season = None
        for block in blocks:
            try:
                data = json.loads(block)
            except json.JSONDecodeError:
                continue
            for node in data.get("@graph", []):
                if node.get("@type") == "TVSeason":
                    season = node
                    break
            if season:
                break
        if not season:
            return []

        title = (season.get("name") or "").strip()
        if not title:
            return []

        intro = (season.get("description") or "").strip()
        cover = season.get("image")
        episodes = season.get("numberOfEpisodes")

        tags: list[str] = []
        if isinstance(episodes, int) and episodes > 0:
            tags.append(f"全{episodes}集")

        # 别名基于清洗后的标题派生，保证与 standard_title 形态一致
        aliases = derive_aliases(clean_title(title))

        return [
            RawDramaItem(
                source=self.name,
                source_url=url,
                standard_title=title,
                aliases=aliases,
                record_no=None,
                actors=[],
                tags=tags,
                is_ai_drama=False,
                cover_url=cover,
                intro=intro,
                platform_links=[
                    PlatformLink(
                        platform="红果短剧",
                        h5_url=url,
                        scheme=None,  # 官方 scheme 未核实，不填写
                        valid=None,   # 解析阶段不假定可达，由调用方用 LinkChecker 实测回填
                    )
                ],
                raw={"episodes": episodes},
            )
        ]


# 季/部标识：第一季、第2季、第五部、下部、上、完整版 等
_SEASON_RE = re.compile(
    r"第[一二三四五六七八九十百零〇\d]+[季部篇集部]|"
    r"[上下]部|完整版|全集|大结局|番外|前传|续集",
)
_CN_NUM = {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5",
           "六": "6", "七": "7", "八": "8", "九": "9", "十": "10"}


def derive_aliases(title: str) -> list[str]:
    """从标准剧名派生高置信度别名（简称 / 季号变体），供别名检索命中。

    仅生成确定性变体，不猜测剧情或演员：
    - 去季标识的主名：万妖图录传第一季 → 万妖图录传
    - 阿拉伯数字季号：万妖图录传第一季 → 万妖图录传1
    """
    aliases: list[str] = []
    base = _SEASON_RE.sub("", title).strip(" -—·:：（）()《》")
    if base and base != title and len(base) >= 2:
        aliases.append(base)
        m = re.search(r"第([一二三四五六七八九十\d]+)[季部篇]", title)
        if m:
            token = m.group(1)
            digit = _CN_NUM.get(token, token if token.isdigit() else None)
            if digit:
                variant = f"{base}{digit}"
                if variant != title and variant not in aliases:
                    aliases.append(variant)
    # 去重保序
    return list(dict.fromkeys(a for a in aliases if a))


def load_detail_urls(sitemap_xml: str, limit: int = 5) -> list[str]:
    """从 sitemap 提取 /detail 页面 URL（仅公开元数据页）。"""
    urls = re.findall(r"<loc>(https?://[^<]+/detail\?series_id=\d+)</loc>", sitemap_xml)
    return urls[:limit]


def load_hot_urls(home_html: str, limit: int = 30) -> list[str]:
    """从官方首页提取热播榜 /detail 链接（去重、保序）。

    首页为服务端渲染，剧目以 href="/detail?series_id=..." 形式出现，
    这些是平台公开推荐位，属于允许的公开元数据入口。
    """
    ids = re.findall(r'href="/detail\?series_id=(\d+)"', home_html)
    urls: list[str] = []
    seen: set[str] = set()
    for sid in ids:
        if sid in seen:
            continue
        seen.add(sid)
        urls.append(f"https://hongguoduanju.com/detail?series_id={sid}")
        if len(urls) >= limit:
            break
    return urls
