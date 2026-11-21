"""构建配置：请求策略、限速、路径与阈值。

设计要点：
1. 所有参数集中在此，便于本地调试与线上 crontab 复用。
2. 限速、UA、robots.txt 遵循 README「合规边界」与「爬虫策略」约束。
3. 阈值与 README 保持一致：单域名并发≤2、间隔 1~3s、5000 条切换 MiniSearch。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# 项目根目录（builder/ 的上一级）
ROOT_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Config:
    """一次构建任务的完整配置。可用 dataclasses.replace 生成调试变体。"""

    # ---------- 路径 ----------
    root_dir: Path = ROOT_DIR
    data_dir: Path = field(default_factory=lambda: ROOT_DIR / "data")
    log_dir: Path = field(default_factory=lambda: ROOT_DIR / "log")
    output_path: Path = field(default_factory=lambda: ROOT_DIR / "drama_index.json")
    backup_dir: Path = field(default_factory=lambda: ROOT_DIR / "data" / "backup")

    # ---------- 数据文件名 ----------
    sample_file: str = "sample_dramas.json"
    typo_map_file: str = "typo_map.txt"
    blacklist_file: str = "blacklist.txt"
    platforms_file: str = "platforms.json"
    sources_file: str = "sources.json"        # 可插拔平台采集源配置
    raw_pool_file: str = "raw_pool.json"      # 跨轮次累计原始池

    # ---------- 请求策略（README 第四节） ----------
    user_agents: tuple[str, ...] = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    )
    request_interval: tuple[float, float] = (1.0, 3.0)  # 每次请求间隔范围（秒）
    max_concurrency_per_domain: int = 2                 # 单域名并发上限
    timeout: float = 10.0                               # 普通请求超时
    max_retries: int = 1                                # 失败重试次数
    respect_robots_txt: bool = True                     # 遵守 robots.txt

    # ---------- 链接存活检测（README 风险 6） ----------
    link_check_timeout: float = 5.0
    link_check_batch_size: int = 2000                   # 每日轮换校验一批

    # ---------- 容量与切换阈值（README 第六节） ----------
    minisearch_threshold: int = 5000
    max_records: int = 10000
    compact_output: bool = True                         # 索引 JSON 紧凑输出（去缩进空白，体积 -19%）

    # ---------- 合并策略（README 第三节 / 风险 2） ----------
    merge_similarity_threshold: float = 0.85           # 高置信度阈值
    auto_merge_without_record_no: bool = False         # 无备案号默认不自动合并

    # ---------- 日志 ----------
    log_level: str = "INFO"
    log_file: str = "build.log"

    # ---------- 便捷属性 ----------
    @property
    def sample_path(self) -> Path:
        return self.data_dir / self.sample_file

    @property
    def typo_map_path(self) -> Path:
        return self.data_dir / self.typo_map_file

    @property
    def blacklist_path(self) -> Path:
        return self.data_dir / self.blacklist_file

    @property
    def platforms_path(self) -> Path:
        return self.data_dir / self.platforms_file

    @property
    def sources_path(self) -> Path:
        return self.data_dir / self.sources_file

    @property
    def raw_pool_path(self) -> Path:
        return self.data_dir / self.raw_pool_file

    @property
    def log_path(self) -> Path:
        return self.log_dir / self.log_file

    def ensure_dirs(self) -> None:
        """确保输出目录存在（不修改配置本身）。"""
        for directory in (self.data_dir, self.log_dir, self.backup_dir):
            directory.mkdir(parents=True, exist_ok=True)


DEFAULT_CONFIG = Config()
