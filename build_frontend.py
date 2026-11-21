#!/usr/bin/env python3
"""把静态索引与站点配置内联进模板，生成双击即用、零网络依赖的静态站点。

为什么需要它：浏览器在 file:// 协议下用 fetch() 读取本地 JSON 会被 CORS 拦截，
直接双击 index.html 无法自动加载数据。本脚本把 drama_index.json 与 site.json
注入页面，产出完全自包含的 HTML——无需服务器、无需 CDN、无需选择文件。

用法：
    python3 build_frontend.py
    python3 build_frontend.py --data hongguo_crawl.json --output search_hongguo.html
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MARKER = "var __EMBEDDED_PLACEHOLDER__ = null;"
SITE_MARKER_RE = re.compile(r"var __SITE_CONFIG__ = \{.*?\n\};", re.S)
PRIVACY_PLACEHOLDER = "__SITE_CONFIG_PLACEHOLDER__"
PRIVACY_NAME = "__SITE_NAME__"


def _js_literal(obj: dict) -> str:
    """安全内联为 JS 对象字面量：转义 </script> 与行分隔符。"""
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return raw.replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def load_site_config(path: Path) -> dict:
    if not path.exists():
        print(f"警告：站点配置不存在 {path}，使用模板默认值", file=sys.stderr)
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError as exc:
        print(f"警告：站点配置解析失败（{exc}），使用默认值", file=sys.stderr)
        return {}


def build(template: Path, data_file: Path, output: Path, site: dict) -> dict:
    html = template.read_text(encoding="utf-8")
    if MARKER not in html:
        raise ValueError(f"模板缺少索引注入标记：{MARKER!r}")

    payload = json.loads(data_file.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("dramas"), list):
        raise ValueError("索引格式不正确：需要包含 dramas 数组的对象")

    html = html.replace(MARKER, f"var __EMBEDDED_PLACEHOLDER__ = {_js_literal(payload)};", 1)
    html = html.replace(
        "var inline = window.__EMBEDDED_INDEX__;",
        "var inline = window.__EMBEDDED_INDEX__ || (typeof __EMBEDDED_PLACEHOLDER__ !== 'undefined' ? __EMBEDDED_PLACEHOLDER__ : null);",
        1,
    )
    if site:
        html, n = SITE_MARKER_RE.subn(f"var __SITE_CONFIG__ = {_js_literal(site)};", html, count=1)
        if n == 0:
            print("警告：模板中未找到站点配置占位符", file=sys.stderr)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    return {
        "output": str(output),
        "dramas": len(payload["dramas"]),
        "aliases": len(payload.get("aliases", [])),
        "is_sample": bool((payload.get("meta") or {}).get("is_sample")),
        "size_kb": round(output.stat().st_size / 1024, 1),
    }


def build_privacy(source: Path, output: Path, site: dict) -> dict | None:
    """生成自包含的合规页（隐私/版权/投诉/备案）。"""
    if not source.exists():
        return None
    html = source.read_text(encoding="utf-8")
    html = html.replace(PRIVACY_PLACEHOLDER, _js_literal(site))
    html = html.replace(PRIVACY_NAME, site.get("site_name", "短剧别名检索"))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    return {"output": str(output), "size_kb": round(output.stat().st_size / 1024, 1)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成自包含的单文件静态检索页")
    parser.add_argument("--template", type=Path, default=ROOT / "index.html", help="页面模板")
    parser.add_argument("--data", type=Path, default=ROOT / "drama_index.json", help="静态索引 JSON")
    parser.add_argument("--output", type=Path, default=ROOT / "search.html", help="输出 HTML")
    parser.add_argument("--site", type=Path, default=ROOT / "site.json", help="站点合规配置")
    parser.add_argument("--no-privacy", action="store_true", help="不生成合规页")
    args = parser.parse_args(argv)

    for p in (args.template, args.data):
        if not p.exists():
            print(f"错误：文件不存在 {p}", file=sys.stderr)
            return 2
    site = load_site_config(args.site)
    try:
        info = build(args.template, args.data, args.output, site)
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    print(f"已生成：{info['output']}")
    print(f"  剧目 {info['dramas']} · 别名 {info['aliases']} · 演示数据 {info['is_sample']}")
    print(f"  文件大小 {info['size_kb']} KB（含内联索引，双击即可离线搜索）")

    if not args.no_privacy:
        # 模板与产物分离，避免覆盖模板导致占位符丢失
        pinfo = build_privacy(ROOT / "privacy.template.html", ROOT / "privacy.html", site)
        if pinfo:
            print(f"  合规页：{pinfo['output']}（{pinfo['size_kb']} KB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
