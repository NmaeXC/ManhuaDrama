"""静态索引导出（README 第二节资源层、第六节容量策略）。

输出 drama_index.json：meta + dramas + aliases 三段结构，单条剧目字段严格遵循
README 第三节。导出前自动备份旧索引，防止单次构建炸库；同时按 5000 条阈值
标注前端应使用的检索方案（Fuse.js / MiniSearch）。
"""
from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from . import __version__
from .config import Config
from .crawler import NormalizeResult
from .models import CrawlStats

logger = logging.getLogger(__name__)


def setup_logging(config: Config) -> None:
    """配置构建日志：同时输出到控制台与 log/build.log。"""
    config.ensure_dirs()
    handlers: list[logging.Handler] = [
        logging.StreamHandler(),
        logging.FileHandler(config.log_path, encoding="utf-8"),
    ]
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


def _backup_existing(config: Config) -> Path | None:
    """备份已有索引，返回备份路径；不存在则返回 None。"""
    if not config.output_path.exists():
        return None
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = config.backup_dir / f"drama_index_{timestamp}.json"
    config.backup_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(config.output_path, backup_path)

    # 仅保留最近 5 份备份
    backups = sorted(config.backup_dir.glob("drama_index_*.json"))
    for old in backups[:-5]:
        old.unlink()
    return backup_path


def _load_platforms(config: Config) -> dict[str, Any]:
    """读取平台白名单；文件缺失时返回空清单，不中断构建。"""
    if not config.platforms_path.exists():
        logger.warning("平台清单不存在：%s", config.platforms_path)
        return {"platforms": [], "excluded": []}
    try:
        data = json.loads(config.platforms_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.warning("平台清单解析失败（%s），本次不纳入索引", exc)
        return {"platforms": [], "excluded": []}
    return {
        "platforms": data.get("platforms", []),
        "excluded": data.get("excluded", []),
    }


def build_payload(
    result: NormalizeResult,
    crawl_stats: CrawlStats | None = None,
    config: Config | None = None,
    platforms: dict[str, Any] | None = None,
    *,
    is_sample: bool | None = None,
) -> dict:
    """组装 drama_index.json 内容。

    is_sample：是否演示数据。为 None 时按 crawl_stats.source 推断；演示数据的
    h5_url 为占位地址、valid=null 表示未检测，前端不得当作真实跳转展示。
    """
    config = config or Config()
    platforms = platforms if platforms is not None else _load_platforms(config)
    total = len(result.records)
    search_engine = "minisearch" if total >= config.minisearch_threshold else "fuse"
    ready_platforms = [p for p in platforms["platforms"] if p.get("status") == "active"]
    if is_sample is None:
        is_sample = bool(crawl_stats and crawl_stats.source == "sample")
    return {
        "meta": {
            "version": __version__,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "total": total,
            "search_engine": search_engine,
            "minisearch_threshold": config.minisearch_threshold,
            "is_sample": is_sample,
            "crawl": crawl_stats.model_dump() if crawl_stats else None,
            "normalize": {
                "blacklisted": result.blacklisted,
                "merged": result.merged,
                "alias_count": len(result.alias_pool),
            },
            "platforms": {
                "available": len(ready_platforms),
                "listed": len(platforms["platforms"]),
                "excluded": len(platforms["excluded"]),
                "note": "listed 为白名单总量；available 仅统计 status=active。多数平台域名/scheme 待核验，接入跳转前须核实。",
            },
        },
        "dramas": [record.model_dump() for record in result.records],
        "aliases": [entry.model_dump() for entry in result.alias_pool],
        "platforms": platforms["platforms"],
    }


def export_index(
    result: NormalizeResult,
    crawl_stats: CrawlStats | None = None,
    config: Config | None = None,
    *,
    backup: bool = True,
) -> Path:
    """写入 drama_index.json，返回输出路径。"""
    config = config or Config()
    config.ensure_dirs()

    if backup:
        backup_path = _backup_existing(config)
        if backup_path:
            logger.info("已备份旧索引：%s", backup_path)

    payload = build_payload(result, crawl_stats, config)
    config.output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    logger.info(
        "索引导出完成：%s（%d 条剧目，%d 个别名，引擎 %s）",
        config.output_path,
        payload["meta"]["total"],
        len(payload["aliases"]),
        payload["meta"]["search_engine"],
    )
    return config.output_path
