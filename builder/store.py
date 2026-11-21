"""累计原始条目池：跨多轮抓取、多平台持久化的原始剧目仓库。

与单次抓取产物（hongguo_crawl.json）的区别：
- 单次抓取产物会被下一轮覆盖；累计池则持续累积，直到显式清理。
- 幂等键为 (source, source_url)：同一来源同一详情页重复抓取会「更新」而非「新增」，
  避免累计过程中产生重复条目；字段取非空最新值，valid 取最可信值。

池本身不做清洗/合并，保持原始层语义；清洗、去重、跨源合并统一由 normalize_items 完成。
数据文件：data/raw_pool.json（含 version / items）。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .models import PlatformLink, RawDramaItem

logger = logging.getLogger(__name__)

POOL_VERSION = 1


@dataclass
class UpsertStats:
    """一次累计写入的统计。"""

    added: int = 0      # 新增条目
    updated: int = 0    # 幂等命中并更新
    unchanged: int = 0  # 命中但无字段变化


def item_key(item: RawDramaItem) -> tuple[str, str]:
    """幂等键：来源 + 详情页 URL。无 URL 时退化到 来源 + 标准剧名。"""
    url = (item.source_url or "").strip()
    title = (item.standard_title or "").strip()
    return (item.source.strip(), url or f"title:{title}")


def _is_more_trusted(new: bool | None, old: bool | None) -> bool:
    """valid 可信度：True > False > None。仅在更新时取更可信值。"""
    rank = {None: 0, False: 1, True: 2}
    return rank.get(new, 0) > rank.get(old, 0)


def _merge_link(old: PlatformLink, new: PlatformLink) -> PlatformLink:
    """合并同一 (平台, URL) 的链接，valid 取更可信值。"""
    valid = new.valid if _is_more_trusted(new.valid, old.valid) else old.valid
    scheme = new.scheme or old.scheme
    return PlatformLink(platform=old.platform, h5_url=old.h5_url, scheme=scheme, valid=valid)


def merge_item(old: RawDramaItem, new: RawDramaItem) -> tuple[RawDramaItem, bool]:
    """把 new 合并进 old，返回 (合并结果, 是否发生变化)。

    规则：标量/列表取非空最新值；platform_links 按 (平台, URL) 幂等合并。
    """
    changed = False

    def pick(field_name: str) -> object:
        nonlocal changed
        old_v = getattr(old, field_name)
        new_v = getattr(new, field_name)
        if new_v and new_v != old_v:
            changed = True
            return new_v
        return old_v

    merged = RawDramaItem.from_dict({
        **old.model_dump(),
        "standard_title": pick("standard_title"),
        "aliases": pick("aliases"),
        "record_no": pick("record_no"),
        "actors": pick("actors"),
        "tags": pick("tags"),
        "is_ai_drama": bool(old.is_ai_drama or new.is_ai_drama),
        "cover_url": pick("cover_url"),
        "intro": pick("intro"),
        "raw": pick("raw"),
    })
    if old.is_ai_drama != merged.is_ai_drama:
        changed = True

    # 链接：按 (平台, URL) 合并
    by_key: dict[tuple[str, str], PlatformLink] = {}
    for link in list(old.platform_links) + list(new.platform_links):
        key = (link.platform.strip(), link.h5_url.strip())
        if key in by_key:
            merged_link = _merge_link(by_key[key], link)
            if merged_link != by_key[key]:
                changed = True
            by_key[key] = merged_link
        else:
            by_key[key] = link
    merged.platform_links = list(by_key.values())

    # 新条目带来了旧条目没有的链接
    if len(merged.platform_links) > len(old.platform_links):
        changed = True

    return merged, changed


class RawPool:
    """累计原始池，负责持久化与幂等累计。"""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> list[RawDramaItem]:
        """读取池；文件缺失或损坏时返回空池，不中断构建。"""
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("累计池读取失败（%s），按空池处理：%s", self.path, exc)
            return []
        rows = payload.get("items", []) if isinstance(payload, dict) else []
        items: list[RawDramaItem] = []
        for row in rows:
            try:
                items.append(RawDramaItem.from_dict(row))
            except (TypeError, ValueError):
                continue
        return items

    def save(self, items: Iterable[RawDramaItem]) -> int:
        """写入池（原子替换），返回写入条数。"""
        rows = [item.model_dump() for item in items]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": POOL_VERSION,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "total": len(rows),
            "items": rows,
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)
        return len(rows)

    def upsert(self, items: Iterable[RawDramaItem]) -> UpsertStats:
        """把本轮抓取条目累计进池：幂等键命中则合并更新，否则新增。"""
        pool = self.load()
        index: dict[tuple[str, str], int] = {}
        for idx, item in enumerate(pool):
            index.setdefault(item_key(item), idx)

        stats = UpsertStats()
        for item in items:
            key = item_key(item)
            pos = index.get(key)
            if pos is None:
                index[key] = len(pool)
                pool.append(item)
                stats.added += 1
                continue
            merged, changed = merge_item(pool[pos], item)
            pool[pos] = merged
            if changed:
                stats.updated += 1
            else:
                stats.unchanged += 1

        self.save(pool)
        logger.info(
            "累计池写入：新增 %d / 更新 %d / 未变 %d / 池总量 %d",
            stats.added, stats.updated, stats.unchanged, len(pool),
        )
        return stats

    def clear(self) -> None:
        """显式清空累计池（供 --reset 使用）。"""
        self.save([])
