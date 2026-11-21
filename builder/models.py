"""剧目数据模型（对应 README 第三节 JSON 结构）。

实现策略：使用标准库 dataclasses 作为统一数据载体，保证零第三方依赖可运行；
模型提供 model_dump() / from_dict() 兼容 pydantic 风格的调用习惯，便于后续
按需切换到 pydantic v2 而不改变上层代码。
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from enum import Enum
from typing import Any, get_args, get_origin, get_type_hints


class AliasType(str, Enum):
    """别名分类标记（README 第三节 alias_pool）。"""

    TYPO = "typo"            # 错别字
    MARKETING = "marketing"  # 营销标题
    SHORT = "short"          # 简称
    OLD_NAME = "old_name"    # 曾用名


class ModelMixin:
    """为 dataclass 提供 pydantic 风格的序列化 / 反序列化接口。"""

    def model_dump(self) -> dict[str, Any]:
        """转为可 JSON 序列化的字典，可选字段显式保留 null 以保持结构稳定。"""
        return _serialize(asdict(self))

    def model_dump_json(self, **kwargs: Any) -> str:
        import json

        kwargs.setdefault("ensure_ascii", False)
        return json.dumps(self.model_dump(), **kwargs)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelMixin":
        """从字典构造模型，递归转换嵌套 dataclass 与 list 元素。

        兼容 Python 3.9：本模块启用 `from __future__ import annotations`，
        注解均为字符串；3.9 无法求值 `X | None`（PEP 604），故不调用
        get_type_hints（其会解析字符串），改用 _annotation_spec 安全解析。
        """
        kwargs: dict[str, Any] = {}
        for f in fields(cls):  # type: ignore[arg-type]
            if f.name not in data:
                continue
            ann = cls.__annotations__.get(f.name, Any)
            spec = _annotation_spec(ann)
            kwargs[f.name] = _coerce_spec(data[f.name], spec)
        return cls(**kwargs)  # type: ignore[return-value]


def _serialize(obj: Any) -> Any:
    """递归序列化：Enum 转原生值，其余类型（含 None）原样保留。"""
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize(v) for v in obj]
    return obj


def _annotation_spec(annotation: Any) -> dict[str, Any]:
    """把类型注解（字符串或对象）安全解析为规范说明，不触发 PEP 604 求值。

    返回形如 {"kind": "list"|"dict"|"dataclass"|"enum"|"bool"|"scalar",
              "type": <运行时类或 None>}。
    """
    spec: dict[str, Any] = {"kind": "scalar", "type": None}

    # 注解可能是字符串（from __future__ annotations）或运行时类型对象
    text = annotation if isinstance(annotation, str) else getattr(annotation, "__name__", "")

    if isinstance(annotation, str):
        # 去掉 Optional / X|None / None|X 包装
        parts = [p.strip() for p in annotation.split("|")]
        parts = [p for p in parts if p not in ("None", "NoneType")]
        if len(parts) == 1:
            text = parts[0]
        elif len(parts) > 1:
            return spec  # 多类型 Union 无法安全转换，保留原值

        m = re.match(r"^(list|List)\[(.+)\]$", text)
        if m:
            inner = _annotation_spec(m.group(2))
            return {"kind": "list", "type": inner}
        m = re.match(r"^(dict|Dict)\[", text)
        if m:
            return {"kind": "dict", "type": None}

    # 运行时类型对象（未启用 future annotations 时）
    origin = get_origin(annotation)
    if origin is list:
        args = get_args(annotation)
        return {"kind": "list", "type": _annotation_spec(args[0] if args else Any)}
    if origin is dict:
        return {"kind": "dict", "type": None}

    # 解析具名类型（从模块全局命名空间查找）
    named = _resolve_named_type(annotation, text)
    if named is not None:
        if isinstance(named, type) and is_dataclass(named) and issubclass(named, ModelMixin):
            return {"kind": "dataclass", "type": named}
        if isinstance(named, type) and issubclass(named, Enum):
            return {"kind": "enum", "type": named}
        if named is bool:
            return {"kind": "bool", "type": bool}
    return spec


def _resolve_named_type(annotation: Any, name: str) -> Any:
    """把类型名解析为运行时类：优先用注解对象本身，否则按名字查全局/内建。"""
    if isinstance(annotation, type):
        return annotation
    if not name:
        return None
    import builtins
    import sys
    mod = sys.modules.get(__name__)
    found = getattr(mod, name, None) if mod else None
    if found is None:
        found = getattr(builtins, name, None)
    return found


def _coerce_spec(value: Any, spec: dict[str, Any]) -> Any:
    """按 _annotation_spec 生成的规范递归转换值。"""
    if value is None:
        return None
    kind = spec["kind"]
    if kind == "list":
        inner = spec["type"] or {"kind": "scalar", "type": None}
        return [_coerce_spec(item, inner) for item in value] if isinstance(value, list) else value
    if kind == "dict":
        return dict(value) if isinstance(value, dict) else value
    if kind == "dataclass":
        return spec["type"].from_dict(value) if isinstance(value, dict) else value
    if kind == "enum":
        try:
            return spec["type"](value)
        except ValueError:
            return value
    if kind == "bool" and isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y"}
    return value


@dataclass
class PlatformLink(ModelMixin):
    """平台跳转链接。

    valid 采用三态：True=实测可达；False=实测失效；None=未检测（占位/尚未校验）。
    前端仅展示 valid=True 的入口；None 明确表示"未知"，不再默认为有效。
    """

    platform: str
    h5_url: str
    scheme: str | None = None
    valid: bool | None = None


@dataclass
class DramaRecord(ModelMixin):
    """输出给前端的单条剧目记录（README 第三节标准结构）。"""

    id: str
    standard_title: str
    aliases: list[str] = field(default_factory=list)
    record_no: str | None = None
    actors: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    is_ai_drama: bool = False
    cover_url: str | None = None
    intro: str = ""
    platform_links: list[PlatformLink] = field(default_factory=list)
    updated_at: str = ""

    @property
    def merge_key(self) -> str:
        """合并主键：有备案号用 record_no，否则退回标准剧名。"""
        return f"no:{self.record_no}" if self.record_no else f"title:{self.standard_title}"


@dataclass
class AliasEntry(ModelMixin):
    """别名池条目。"""

    text: str
    standard_title: str = ""
    alias_type: AliasType = AliasType.SHORT
    source_id: str = ""


@dataclass
class RawDramaItem(ModelMixin):
    """采集层原始条目：来源字段 + 标准化字段，尚未清洗与合并。"""

    source: str
    source_url: str = ""
    standard_title: str = ""
    aliases: list[str] = field(default_factory=list)
    record_no: str | None = None
    actors: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    is_ai_drama: bool = False
    cover_url: str | None = None
    intro: str = ""
    platform_links: list[PlatformLink] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class CrawlStats(ModelMixin):
    """单次采集统计，用于构建日志。"""

    source: str
    fetched: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    duration_seconds: float = 0.0
    notes: str = ""
