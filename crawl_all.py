#!/usr/bin/env python3
"""累计抓取入口：按 sources.json 遍历启用的平台 → 累计入池 → 从全池归一化导出。

与旧的 crawl_hongguo.py（单次覆盖）区别：
- 多平台：由 data/sources.json 的 enabled + status=active 决定采集哪些平台；
- 累计：本轮抓到的条目 upsert 进 data/raw_pool.json，不覆盖历史；
- 去重：从全池一次性 normalize（含同名合并 + 链接去重），导出最终索引。

用法：
    python3 crawl_all.py                 # 抓取所有启用平台并累计导出
    python3 crawl_all.py --limit 10      # 每平台最多 10 条
    python3 crawl_all.py --reset         # 先清空累计池再抓（等价于单次快照）
    python3 crawl_all.py --no-merge-same-title  # 严格按 README：无备案号一律分开
    python3 crawl_all.py --offline       # 不联网，仅从现有池重新导出（用于修复/改配置）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from builder.cleaner import load_typo_map  # noqa: E402
from builder.config import Config  # noqa: E402
from builder.crawler import normalize_items  # noqa: E402
from builder.exporter import build_payload, setup_logging  # noqa: E402
from builder.sources import PlatformSource, discover_urls, load_sources  # noqa: E402
from builder.store import RawPool  # noqa: E402

ROOT = Path(__file__).resolve().parent


def fetch_text(url: str, config: Config) -> str:
    """抓取入口页/sitemap；带重试与可操作提示（复用 build_site 的网络诊断约定）。"""
    last_exc: Exception | None = None
    for attempt in range(config.max_retries + 1):
        request = urllib.request.Request(url, headers={"User-Agent": config.user_agents[0]})
        try:
            with urllib.request.urlopen(request, timeout=config.timeout) as response:
                return response.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, OSError) as exc:
            last_exc = exc
            if attempt < config.max_retries:
                time.sleep(1.5 * (attempt + 1))
    proxy = "已检测到代理环境变量" if os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") else "未检测到代理环境变量"
    raise RuntimeError(
        f"无法连接（{url}）。底层原因：{last_exc}。当前环境：{proxy}。"
        "请检查浏览器能否打开该站、是否需要设置 HTTP_PROXY / HTTPS_PROXY、DNS/防火墙是否拦截。"
    ) from last_exc


def run(
    config: Config,
    *,
    limit: int | None = None,
    reset: bool = False,
    offline: bool = False,
    merge_same_title: bool = True,
    output: Path | None = None,
    verbose: bool = True,
) -> dict:
    """执行累计抓取与导出，返回 payload。"""
    pool = RawPool(config.raw_pool_path)
    if reset:
        pool.clear()
        if verbose:
            print("[reset] 已清空累计池")

    sources = [s for s in load_sources(config.sources_path) if s.usable]
    if not offline and not sources:
        raise RuntimeError(
            "没有可采集的平台：data/sources.json 中没有 enabled=true 且 status=active 的配置。"
        )

    # 1) 逐平台发现 URL → 抓取 → 累计入池
    # 先取已抓 URL 快照（按来源分组），供 discover_urls 跳过、实现轮换取新。
    seen_by_source: dict[str, set[str]] = {}
    for item in pool.load():
        if item.source_url:
            seen_by_source.setdefault(item.source, set()).add(item.source_url.strip())
    if not offline:
        for src in sources:
            eff_limit = limit if limit is not None else src.limit
            src_cfg = src if eff_limit == src.limit else type(src)(**{**src.__dict__, "limit": eff_limit})
            if verbose:
                print(f"[source] {src.name}（{src.id}）发现详情页…")
            exclude = seen_by_source.get(src.id, set())
            urls = discover_urls(config, src_cfg, lambda u: fetch_text(u, config), exclude=exclude)
            if verbose:
                new_cnt = sum(1 for u in urls if u not in exclude)
                print(
                    f"         取到 {len(urls)} 个详情页 URL（其中新 {new_cnt} / 已抓跳过）"
                    f"，开始抓取（限速）…"
                )
            if not urls:
                continue
            collector = PlatformSource(config, src_cfg, urls)
            items = collector.fetch()
            stats = pool.upsert(items)
            if verbose:
                print(
                    f"         抓取 {len(items)} 条；累计：新增 {stats.added} / "
                    f"更新 {stats.updated} / 未变 {stats.unchanged}"
                )
    elif verbose:
        print("[offline] 跳过联网，从现有累计池重新导出")

    pooled = pool.load()
    if not pooled:
        raise RuntimeError(
            "累计池为空，无法导出。请先联网抓取（去掉 --offline），或用 build_site.py --skip-crawl 用缓存。"
        )
    if verbose:
        print(f"[pool] 当前累计池 {len(pooled)} 条原始条目")

    # 2) 从全池归一化（含跨源同名合并 + 链接去重）
    result = normalize_items(
        pooled,
        config,
        typo_map=load_typo_map(config.typo_map_path),
        merge_same_title=merge_same_title,
    )
    payload = build_payload(result, None, config, is_sample=False)
    payload["meta"]["cumulative"] = {
        "raw_pool_size": len(pooled),
        "merge_same_title": merge_same_title,
        "sources": [
            {"id": s.id, "name": s.name, "enabled": s.enabled, "status": s.status}
            for s in load_sources(config.sources_path)
        ],
    }

    out = output or ROOT / "hongguo_crawl.json"
    payload_text = (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if config.compact_output
        else json.dumps(payload, ensure_ascii=False, indent=2)
    )
    out.write_text(payload_text, encoding="utf-8")
    if verbose:
        print(f"[export] 输出 {out.name}：{len(result.records)} 剧目 / {len(result.alias_pool)} 别名")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="累计抓取多平台短剧元数据")
    parser.add_argument("--limit", type=int, default=None, help="覆盖每平台抓取条数")
    parser.add_argument("--reset", action="store_true", help="清空累计池后重抓")
    parser.add_argument("--offline", action="store_true", help="不联网，从现有池重新导出")
    parser.add_argument(
        "--no-merge-same-title", action="store_true",
        help="严格按 README：无备案号一律分开，不按同名合并",
    )
    parser.add_argument("--output", type=Path, default=ROOT / "hongguo_crawl.json", help="输出路径")
    args = parser.parse_args(argv)

    config = Config()
    setup_logging(config)
    try:
        run(
            config,
            limit=args.limit,
            reset=args.reset,
            offline=args.offline,
            merge_same_title=not args.no_merge_same_title,
            output=args.output,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"累计抓取失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
