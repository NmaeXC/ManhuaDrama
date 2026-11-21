#!/usr/bin/env python3
"""生成 Cloudflare Pages / 任意静态托管的部署目录 dist/。

把首页模板、主索引、合规页、单文件搜索页拷到 dist/，作为一份干净、
可直接上传（或由 Cloudflare Pages 连接 Git 仓库后自动发布）的静态站点。

用法：
    python3 prepare_deploy.py                 # 从项目根生成 dist/
    python3 build_site.py && python3 prepare_deploy.py   # 完整流程

Cloudflare Pages 配置（连 GitHub 仓库时）：
    Build command:        （留空，纯静态无需构建）
    Output directory:     dist
    Root directory:       （留空，仓库根）
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"

# 需要发布的文件：源 -> dist 内文件名
FILES = (
    ("index.html", "index.html"),            # 首页：fetch ./drama_index.json
    ("drama_index.json", "drama_index.json"),  # 主索引（含 meta.site 合规配置）
    ("privacy.html", "privacy.html"),        # 隐私/版权/投诉页
    ("search.html", "search.html"),          # 自包含单文件版（备用/直接访问）
)


def main() -> int:
    missing = [src for src, _ in FILES if not (ROOT / src).exists()]
    if missing:
        print(f"错误：缺少文件 {', '.join(missing)}，请先运行 python3 build_site.py", file=sys.stderr)
        return 2

    # 先删旧 dist 再重建，避免残留过期文件被一起发布
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True, exist_ok=True)

    for src, dst in FILES:
        shutil.copy2(ROOT / src, DIST / dst)

    total = sum((DIST / dst).stat().st_size for _, dst in FILES)
    print(f"已生成部署目录：{DIST}")
    for _, dst in FILES:
        print(f"  - {dst}")
    print(f"共 {len(FILES)} 个文件，{total / 1024:.1f} KB")
    print("Cloudflare Pages：Build command 留空，Output directory 填 dist。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
