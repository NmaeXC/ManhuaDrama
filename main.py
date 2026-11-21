#!/usr/bin/env python3
"""短剧索引构建入口：采集 → 清洗合并 → （可选）链接检测 → 导出。

常用命令：
    python3 main.py                       # 样例数据源，阶段 1 本地验证
    python3 main.py --check-links         # 同时执行链接存活检测
    python3 main.py --url https://api.example.com/rank \
        --list-path data.list \
        --field-map '{"standard_title":"title","aliases":"alias"}'   # JSON API 采集
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from builder.cleaner import load_typo_map  # noqa: E402
from builder.config import Config  # noqa: E402
from builder.crawler import SampleSource, WebSource, normalize_items  # noqa: E402
from builder.exporter import export_index, setup_logging  # noqa: E402
from builder.link_checker import LinkChecker  # noqa: E402
from builder.models import CrawlStats  # noqa: E402

# 无备案号时经人工确认后才合并的标题组（README 第三节合并策略）
MANUAL_MERGE_GROUPS: list[list[str]] = [
    ["穿越古代当王爷", "王爷穿越古代"],
]


def _load_lines(path: Path) -> set[str]:
    """加载按行排列的名单文件，忽略注释与空行。"""
    if not path.exists():
        return set()
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="短剧别名索引构建脚本")
    parser.add_argument("--source", choices=("sample", "web"), default="sample", help="数据源类型")
    parser.add_argument("--sample", type=Path, default=None, help="样例数据 JSON 路径")
    parser.add_argument("--url", action="append", default=[], help="采集入口 URL，可重复指定")
    parser.add_argument("--list-path", default="", help="JSON 响应中条目数组的点分路径，如 data.list")
    parser.add_argument("--field-map", default="", help="字段映射 JSON，如 '{\"standard_title\":\"title\"}'")
    parser.add_argument("--check-links", action="store_true", help="执行链接存活检测")
    parser.add_argument("--no-backup", action="store_true", help="不备份旧索引")
    parser.add_argument("--output", type=Path, default=None, help="自定义输出路径")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = Config()
    if args.output:
        config = replace(config, output_path=args.output)
    setup_logging(config)

    if args.source == "web" and not args.url:
        print("错误：--source web 需要至少指定一个 --url", file=sys.stderr)
        return 2

    typo_map = load_typo_map(config.typo_map_path)
    blacklist = _load_lines(config.blacklist_path)

    started = time.perf_counter()
    if args.source == "sample":
        source = SampleSource(config, args.sample)
    else:
        try:
            field_map = json.loads(args.field_map) if args.field_map else {}
        except json.JSONDecodeError as exc:
            print(f"错误：--field-map 不是合法 JSON：{exc}", file=sys.stderr)
            return 2
        source = WebSource(
            config,
            args.url,
            list_path=args.list_path,
            field_map=field_map,
        )

    items = source.fetch()
    # WebSource 自带失败/跳过/耗时统计，优先复用；SampleSource 则简单构造
    stats = getattr(source, "stats", None) or CrawlStats(
        source=source.name, fetched=len(items), succeeded=len(items)
    )

    result = normalize_items(items, config, typo_map, blacklist, MANUAL_MERGE_GROUPS)
    stats.duration_seconds = round(time.perf_counter() - started, 2)

    if args.check_links:
        LinkChecker(config).check_records(result.records)

    output = export_index(result, stats, config, backup=not args.no_backup)

    print(
        f"构建完成：{output}\n"
        f"  采集条目：{stats.fetched}（来源 {stats.source}）\n"
        f"  输出剧目：{len(result.records)}，合并：{result.merged}，黑名单过滤：{result.blacklisted}\n"
        f"  别名条目：{len(result.alias_pool)}，耗时：{stats.duration_seconds}s"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
