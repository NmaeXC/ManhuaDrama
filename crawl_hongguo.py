#!/usr/bin/env python3
"""红果短剧官方公开元数据抓取。

数据源（--source）：
- hot（默认）：官方首页热播榜推荐位，剧目质量高；
- sitemap：官方 SEO sitemap 的详情页列表，数量大（2000+）。

流程：取详情页 URL → 限速抓取 → 从 ld+json 提取元数据 → 清洗合并 → 输出 JSON。
仅采集剧名/简介/封面/集数，不访问任何播放接口。
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
from builder.hongguo_source import (  # noqa: E402
    HongguoDetailSource,
    load_detail_urls,
    load_hot_urls,
)

HOME = "https://hongguoduanju.com/"
SITEMAP = "https://hongguoduanju.com/seo-important.xml"


def _fetch(config: Config, url: str) -> str:
    """抓取公开页面；网络失败按配置重试，并给出可操作的中文提示。"""
    last_exc: Exception | None = None
    for attempt in range(config.max_retries + 1):
        request = urllib.request.Request(url, headers={"User-Agent": config.user_agents[0]})
        try:
            with urllib.request.urlopen(request, timeout=config.timeout) as response:
                return response.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, OSError) as exc:
            last_exc = exc
            if attempt < config.max_retries:
                time.sleep(1.5 * (attempt + 1))  # 简单退避，避免连续冲击

    # 全部尝试失败 → 把底层网络异常翻译成人话，避免一长串 traceback
    proxy = "已检测到代理环境变量" if os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") else "未检测到代理环境变量"
    raise RuntimeError(
        f"无法连接红果站点（{url}）。底层原因：{last_exc}。"
        f"当前环境：{proxy}。"
        "请依次排查：① 浏览器能否打开 hongguoduanju.com；"
        "② 若你的网络需要代理，请设置 HTTP_PROXY / HTTPS_PROXY 环境变量；"
        "③ DNS 或防火墙是否拦截。"
        "若暂时无法联网，可运行 python3 build_site.py --skip-crawl 用已缓存数据生成站点。"
    ) from last_exc


def crawl(
    config: Config,
    source_kind: str = "hot",
    limit: int = 30,
    output: Path | None = None,
    verbose: bool = True,
) -> dict:
    """执行抓取并返回导出的 payload，供一键流水线复用。"""
    if source_kind == "hot":
        urls = load_hot_urls(_fetch(config, HOME), limit)
        label = "首页热播榜"
    else:
        urls = load_detail_urls(_fetch(config, SITEMAP), limit)
        label = "sitemap"

    if verbose:
        print(f"{label}取到 {len(urls)} 个详情页 URL（上限 {limit}）")
    if not urls:
        raise RuntimeError("未取到任何详情页 URL")

    source = HongguoDetailSource(config, urls)
    items = source.fetch()
    if verbose:
        print(f"抓取到 {len(items)} 条原始剧目元数据")

    result = normalize_items(
        items,
        config,
        typo_map=load_typo_map(config.typo_map_path),
    )
    payload = build_payload(result, source.stats, config, is_sample=False)

    out = output or (Path(__file__).resolve().parent / "hongguo_crawl.json")
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if verbose:
        print(f"输出：{out}")
        for record in result.records:
            print(f"  {record.id} | {record.standard_title} | 别名 {len(record.aliases)}")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="红果短剧公开元数据抓取")
    parser.add_argument(
        "--source", choices=("hot", "sitemap"), default="hot",
        help="数据源：hot=首页热播榜（默认），sitemap=SEO sitemap",
    )
    parser.add_argument("--limit", type=int, default=30, help="抓取条数，默认 30")
    parser.add_argument(
        "--output", type=Path,
        default=Path(__file__).resolve().parent / "hongguo_crawl.json",
        help="输出路径",
    )
    args = parser.parse_args(argv)

    config = Config()
    setup_logging(config)
    try:
        crawl(config, args.source, args.limit, args.output)
    except Exception as exc:  # noqa: BLE001
        print(f"抓取失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
