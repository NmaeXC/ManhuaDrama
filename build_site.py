#!/usr/bin/env python3
"""一键发布：抓取真实数据 → 生成索引 → 内联生成静态站点。

用法：
    python3 build_site.py                    # 抓取红果热播榜并发布完整站点
    python3 build_site.py --skip-crawl       # 用现有 hongguo_crawl.json 重新生成
    python3 build_site.py --source sample    # 用样例数据发布（离线、无网络）

产物：
    drama_index.json    主索引（真实数据，供 index.html 托管版 fetch）
    search.html         自包含单文件搜索页（双击即用）
    privacy.html        自包含合规页
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from builder.config import Config  # noqa: E402
from builder.exporter import setup_logging  # noqa: E402
from build_frontend import build, build_privacy, load_site_config  # noqa: E402

ROOT = Path(__file__).resolve().parent


def _is_valid_cache(path: Path) -> bool:
    """缓存是否可安全复用：JSON 可解析、含剧目、且不是样例占位数据。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return (
            isinstance(data.get("dramas"), list)
            and len(data["dramas"]) > 0
            and data.get("meta", {}).get("is_sample") is not True
        )
    except (OSError, ValueError, AttributeError, TypeError):
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="一键构建并发布静态检索站点")
    parser.add_argument(
        "--source", choices=("hot", "sitemap", "sample"), default="hot",
        help="数据来源：hot/sitemap 均走累计采集（平台由 sources.json 决定）；sample=样例",
    )
    parser.add_argument("--limit", type=int, default=None, help="每平台抓取条数上限")
    parser.add_argument("--skip-crawl", action="store_true", help="跳过抓取与重算，直接用现有 hongguo_crawl.json")
    parser.add_argument("--reset", action="store_true", help="清空累计池后重新抓取")
    parser.add_argument("--offline", action="store_true", help="不联网，仅从累计池重新导出索引")
    parser.add_argument("--site", type=Path, default=ROOT / "site.json", help="站点合规配置")
    args = parser.parse_args(argv)

    config = Config()
    setup_logging(config)
    site = load_site_config(args.site)

    # 1) 数据
    if args.source == "sample":
        if args.skip_crawl:
            data_file = ROOT / "drama_index.json"
        else:
            from main import build_parser  # noqa: F401  # 确保主入口可导入
            import subprocess
            subprocess.run([sys.executable, str(ROOT / "main.py"), "--no-backup"], check=True)
            data_file = ROOT / "drama_index.json"
        print("[1/3] 样例数据就绪")
    else:
        cache_file = ROOT / "hongguo_crawl.json"
        data_file = cache_file
        if args.skip_crawl:
            print("[1/3] 跳过抓取，使用现有 hongguo_crawl.json")
        else:
            from crawl_all import run as cumulative_run
            try:
                print("[1/3] 累计抓取（多平台，限速，约需 1-2 分钟）…")
                cumulative_run(
                    config, limit=args.limit, reset=args.reset,
                    offline=args.offline, output=cache_file,
                )
            except Exception as exc:  # noqa: BLE001
                # 网络失败不致命：优先从累计池重建，其次降级到缓存文件
                print(f"      ⚠️  抓取失败：{exc}")
                rebuilt = False
                if config.raw_pool_path.exists():
                    try:
                        cumulative_run(config, offline=True, output=cache_file)
                        print("      ↳ 自动降级：从累计池重新导出（数据可能不是最新）")
                        rebuilt = True
                    except Exception as rebuild_exc:  # noqa: BLE001
                        print(f"      ↳ 累计池重建失败：{rebuild_exc}")
                if not rebuilt:
                    if cache_file.exists() and _is_valid_cache(cache_file):
                        print(f"      ↳ 降级：复用已有缓存 {cache_file.name}")
                    else:
                        raise
        # 同步为主索引，供 index.html 托管版 fetch
        (ROOT / "drama_index.json").write_bytes(data_file.read_bytes())
        print("      已同步到 drama_index.json（托管版可 fetch）")

    # 把站点合规配置写入主索引 meta.site：index.html 是模板、其 __SITE_CONFIG__
    # 是写死的占位符，Cloudflare 托管版靠 drama_index.json 拿到真实备案号/邮箱。
    index_path = ROOT / "drama_index.json"
    index_payload = json.loads(index_path.read_text(encoding="utf-8"))
    if not isinstance(index_payload, dict):
        raise RuntimeError("数据文件格式不正确，无法写入站点配置")
    index_payload.setdefault("meta", {})["site"] = site
    index_path.write_text(
        json.dumps(index_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 2) 搜索页
    info = build(ROOT / "index.html", data_file, ROOT / "search.html", site)
    print(f"[2/3] 搜索页：{info['dramas']} 剧目 / {info['aliases']} 别名 / {info['size_kb']} KB")

    # 3) 合规页
    pinfo = build_privacy(ROOT / "privacy.template.html", ROOT / "privacy.html", site)
    if pinfo:
        print(f"[3/3] 合规页：{pinfo['size_kb']} KB")
    print("\n完成。双击 search.html 即可离线搜索；部署时上传 index.html + drama_index.json + privacy.html。")

    # 合规配置完整性提醒
    missing = [k for k in ("complaint_email", "icp_no") if "NEED_FILL" in str(site.get(k, ""))]
    if missing:
        print(f"⚠️  上线前请填写 site.json 中的：{', '.join(missing)}，然后重新运行本脚本。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
