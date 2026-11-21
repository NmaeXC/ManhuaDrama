"""文本清洗与别名归一化（README 第三节「清洗规则」）。

清洗链路：全角转半角 → 繁体转简体 → 去营销后缀 → 错别字纠正 → 去标点。
zhconv / jieba 存在时自动启用完整能力，缺失时使用内置基础映射表与正则切分。
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable

from .models import AliasEntry, AliasType, RawDramaItem

try:  # 可选增强：完整繁简转换
    import zhconv  # type: ignore

    _HAS_ZHCONV = True
except ImportError:  # pragma: no cover - 取决于运行环境
    zhconv = None  # type: ignore
    _HAS_ZHCONV = False

try:  # 可选增强：中文分词
    import jieba  # type: ignore

    _HAS_JIEBA = True
except ImportError:  # pragma: no cover
    jieba = None  # type: ignore
    _HAS_JIEBA = False


# ---------- 常量 ----------

# 尾部营销后缀（循环剥离，覆盖叠加写法如「全集大结局」）
MARKETING_TAIL_RE = re.compile(
    r"(?:全集|大结局|完整版|一口气看完|爽看|速看|抢先看|免费看|高清|"
    r"完结篇|结局篇|解说|合集|无删减|未删减)+$"
)

# 括号中的营销词
BRACKET_MARKETING_RE = re.compile(
    r"[（(【\[](?:全集|大结局|完整版|一口气看完|高清|抢先|免费|解说|速看)[）)】\]]"
)

# 用于别名分类的营销关键词
MARKETING_KEYWORDS = (
    "全集", "大结局", "完整版", "一口气看完", "爽看", "速看", "抢先",
    "免费", "高清", "解说", "必看", "燃爆", "无删减", "未删减",
)

# 非中文/字母/数字字符（含下划线）统一剔除
PUNCT_RE = re.compile(r"[^\w\u4e00-\u9fff]|_", re.UNICODE)

# 内置基础繁→简映射：仅覆盖短剧标题常见字。
# 安装 zhconv 后 to_simplified() 自动改用完整转换，此表作为零依赖兜底。
T2S_MAP: dict[str, str] = {
    "體": "体", "愛": "爱", "劇": "剧", "場": "场", "說": "说", "國": "国",
    "學": "学", "電": "电", "視": "视", "頻": "频", "錯": "错", "誤": "误",
    "標": "标", "題": "题", "後": "后", "裡": "里", "們": "们", "個": "个",
    "這": "这", "時": "时", "間": "间", "開": "开", "關": "关", "長": "长",
    "門": "门", "問": "问", "聞": "闻", "見": "见", "親": "亲", "歲": "岁",
    "數": "数", "導": "导", "員": "员", "質": "质", "線": "线", "無": "无",
    "與": "与", "萬": "万", "億": "亿", "龍": "龙", "鳳": "凤", "張": "张",
    "揚": "扬", "楊": "杨", "黃": "黄", "顏": "颜", "風": "风", "雲": "云",
    "靈": "灵", "夢": "梦", "華": "华", "麗": "丽", "舊": "旧", "書": "书",
    "讀": "读", "寫": "写", "語": "语", "詞": "词", "詩": "诗", "東": "东",
    "邊": "边", "際": "际", "實": "实", "現": "现", "發": "发", "點": "点",
    "熱": "热", "車": "车", "馬": "马", "鳥": "鸟", "魚": "鱼", "動": "动",
    "靜": "静", "變": "变", "會": "会", "羅": "罗", "漢": "汉", "創": "创",
    "業": "业", "產": "产", "節": "节", "總": "总", "監": "监", "製": "制",
    "劉": "刘", "陳": "陈", "趙": "赵", "吳": "吴", "孫": "孙", "鄭": "郑",
    "謝": "谢", "許": "许", "韓": "韩", "馮": "冯", "鄧": "邓", "為": "为",
    "對": "对", "並": "并", "來": "来", "過": "过", "還": "还", "將": "将",
    "於": "于", "從": "从", "雙": "双", "頭": "头", "條": "条", "兒": "儿",
    "錢": "钱", "銀": "银", "買": "买", "賣": "卖", "貴": "贵", "賤": "贱",
    "鐵": "铁", "銅": "铜", "鋼": "钢", "針": "针", "紅": "红", "綠": "绿",
    "藍": "蓝", "聲": "声", "護": "护", "財": "财", "權": "权", "歸": "归",
    "屬": "属", "臺": "台", "網": "网", "絡": "络", "據": "据", "連": "连",
    "號": "号", "碼": "码",
}


# ---------- 基础清洗函数 ----------


def to_halfwidth(text: str) -> str:
    """全角字符转半角（含全角空格）。"""
    result: list[str] = []
    for ch in text:
        code = ord(ch)
        if code == 0x3000:  # 全角空格
            result.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:
            result.append(chr(code - 0xFEE0))
        else:
            result.append(ch)
    return "".join(result)


def to_simplified(text: str) -> str:
    """繁体转简体：优先 zhconv，缺失时使用内置基础映射。"""
    if _HAS_ZHCONV and zhconv is not None:
        return zhconv.convert(text, "zh-cn")
    return "".join(T2S_MAP.get(ch, ch) for ch in text)


def strip_marketing_suffix(text: str) -> str:
    """去除全集/大结局等营销后缀与括号内营销词。

    先剥离括号符号，使「（全集大结局）」这类多词组合暴露到尾部再统一剥离，
    避免只匹配单个关键词导致残留。
    """
    cleaned = BRACKET_MARKETING_RE.sub("", text)
    cleaned = re.sub(r"[（()）【】\[\]]", "", cleaned)
    previous = None
    while previous != cleaned:
        previous = cleaned
        cleaned = MARKETING_TAIL_RE.sub("", cleaned).strip()
    return cleaned.strip(" -—·:：|/\\")


def normalize_punctuation(text: str) -> str:
    """去除标点与空白，保留中文、字母、数字。"""
    return PUNCT_RE.sub("", text)


def load_typo_map(path: str | Path) -> dict[str, str]:
    """加载错别字词表，每行格式：错词=>正词，# 开头为注释。"""
    result: dict[str, str] = {}
    p = Path(path)
    if not p.exists():
        return result
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=>" not in line:
            continue
        wrong, right = (part.strip() for part in line.split("=>", 1))
        if wrong and right:
            result[wrong] = right
    return result


def correct_typos(text: str, typo_map: dict[str, str] | None = None) -> str:
    """按词表纠正错别字（长词优先，避免短词先匹配破坏长词）。"""
    if not typo_map:
        return text
    result = text
    for wrong in sorted(typo_map, key=len, reverse=True):
        if wrong in result:
            result = result.replace(wrong, typo_map[wrong])
    return result


# ---------- 组合清洗 ----------


def clean_title(text: str, typo_map: dict[str, str] | None = None) -> str:
    """标准剧名清洗：转半角 → 转简体 → 去营销后缀 → 纠错 → 去标点。

    去标点后再执行一次后缀剥离，以处理标点移除后才暴露到尾部的营销词。
    """
    cleaned = strip_marketing_suffix(to_simplified(to_halfwidth(text)))
    cleaned = normalize_punctuation(correct_typos(cleaned, typo_map))
    return strip_marketing_suffix(cleaned)


def clean_alias(
    text: str,
    typo_map: dict[str, str] | None = None,
    *,
    correct: bool = True,
) -> str:
    """别名清洗：保留营销词（供检索命中），只做字符归一化。

    correct=False 时保留错别字原词，用于别名池中的 typo 类型检索命中。
    """
    cleaned = to_simplified(to_halfwidth(text)).strip()
    if correct:
        cleaned = correct_typos(cleaned, typo_map)
    return normalize_punctuation(cleaned)


# ---------- 分词 / 相似度 / 别名分类 ----------


def tokenize(text: str) -> list[str]:
    """分词：jieba 可用时使用，否则中文按单字、英文数字按连续块切分。"""
    if _HAS_JIEBA and jieba is not None:
        return [token for token in jieba.lcut(text) if token.strip()]
    return re.findall(r"[a-zA-Z0-9]+|[\u4e00-\u9fff]", text)


def similarity(left: str, right: str) -> float:
    """基于字符序列的相似度，取值 0~1。"""
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def classify_alias(
    alias: str,
    standard_title: str,
    typo_map: dict[str, str] | None = None,
) -> AliasType:
    """按 README alias_pool 的四种分类判定别名类型。"""
    typo_map = typo_map or {}

    # 1) 词表直接命中或纠错后与标准剧名一致 → 错别字
    if alias in typo_map:
        return AliasType.TYPO
    if correct_typos(alias, typo_map) == standard_title and alias != standard_title:
        return AliasType.TYPO

    # 2) 含营销词 → 营销标题
    if any(keyword in alias for keyword in MARKETING_KEYWORDS):
        return AliasType.MARKETING

    # 3) 与标准剧名高度相似但不相同 → 错别字/变体
    if similarity(alias, standard_title) >= 0.85:
        return AliasType.TYPO

    # 4) 标准剧名的子串或明显更短 → 简称；其余 → 曾用名
    if alias in standard_title or len(alias) < len(standard_title):
        return AliasType.SHORT
    return AliasType.OLD_NAME


def build_alias_pool(
    items: Iterable[RawDramaItem],
    typo_map: dict[str, str] | None = None,
) -> list[AliasEntry]:
    """从采集条目汇总别名池，按 (标准剧名, 别名) 去重。"""
    typo_map = typo_map or {}
    seen: set[tuple[str, str]] = set()
    pool: list[AliasEntry] = []

    for item in items:
        title = clean_title(item.standard_title, typo_map)
        if not title:
            continue
        for alias in item.aliases:
            cleaned = clean_alias(alias, typo_map, correct=False)
            if not cleaned or cleaned == title:
                continue
            key = (title, cleaned)
            if key in seen:
                continue
            seen.add(key)
            pool.append(
                AliasEntry(
                    text=cleaned,
                    standard_title=title,
                    alias_type=classify_alias(cleaned, title, typo_map),
                    source_id=item.source,
                )
            )
    return pool
