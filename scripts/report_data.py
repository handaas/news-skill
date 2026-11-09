"""Data-presence rules shared by one skill's report surfaces; never mutate sources."""
from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

_MISSING = frozenset({"", "-", "--", "—", "–", "null", "none", "nan", "n/a", "na",
                      "暂无", "暂无数据", "无数据", "未查询", "未提供", "未返回", "未披露",
                      "未知", "不详", "待评估", "暂无评级", "无相关数据"})
_METADATA = frozenset({"sections", "source_status", "quality_report", "content_revision",
                       "content_quality", "pagination", "tool_errors", "warnings", "error",
                       "errors", "success", "code", "message", "msg", "note", "hint"})
_LABEL_KEYS = frozenset({"label", "name", "title", "字段", "指标", "名称"})
_VALUE_KEYS = frozenset({"value", "数值", "内容", "数量", "记录数", "占比", "比例", "评分"})


def has_data(value: Any) -> bool:
    """Zero/False are facts; placeholders, transport metadata and empty shells are not."""
    if value is None:
        return False
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float)):
        return math.isfinite(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.casefold() in _MISSING:
            return False
        if stripped.startswith(("[", "{")):
            try:
                return has_data(json.loads(stripped))
            except ValueError:
                pass
        return True
    if isinstance(value, Mapping):
        items = {key: item for key, item in value.items()
                 if key not in _METADATA and not str(key).startswith("_")}
        if items.keys() & _VALUE_KEYS and items.keys() <= _LABEL_KEYS | _VALUE_KEYS:
            return any(has_data(item) for key, item in items.items() if key in _VALUE_KEYS)
        return any(has_data(item) for item in items.values())
    if isinstance(value, (list, tuple)):
        return any(has_data(item) for item in value)
    return False


def numeric_value(value: Any) -> float | None:
    """Parse an actual chart number, with no invented zero or percent rescaling."""
    if isinstance(value, bool) or not has_data(value):
        return None
    if isinstance(value, Mapping):
        value = value.get("value")
    try:
        number = float(str(value).strip().replace(",", "").replace("%", "").replace("万", ""))
    except (ValueError, TypeError):
        return None
    return number if math.isfinite(number) else None


def column_keys(rows: Sequence[Any], columns: Sequence[Any] | None = None) -> list[str]:
    sample = dict.fromkeys(key for row in rows if isinstance(row, Mapping) for key in row)
    if not columns:
        return list(sample)
    keys = []
    for pair in columns:
        if not isinstance(pair, (list, tuple)) or not pair:
            continue
        first, second = (list(pair) + [None])[:2]
        key = first if first in sample else second if second in sample else first
        if isinstance(key, str):
            keys.append(key)
    return keys


def clean_rows(rows: Any, columns: Sequence[Any] | None = None) -> list[dict[str, Any]]:
    if not isinstance(rows, (list, tuple)):
        rows = [rows] if isinstance(rows, dict) else []
    keys = column_keys(rows, columns) if columns else None
    return [row for row in rows if isinstance(row, dict)
            and has_data({key: row.get(key) for key in keys} if keys is not None else row)]


def section_has_data(core: Mapping[str, Any], spec: Mapping[str, Any]) -> bool:
    if not isinstance(spec, Mapping):
        return False
    body = core.get(spec.get("key"))
    kind = spec.get("kind")
    chart = spec.get("chart") or {}
    if kind == "radar":
        chart = chart if chart.get("series") else body if isinstance(body, Mapping) else {}
        return any(numeric_value(value) is not None for series in chart.get("series", [])
                   if isinstance(series, Mapping) for value in series.get("value", []))
    if kind == "gauge":
        return isinstance(body, Mapping) and (numeric_value(body.get(chart.get("value_key", "风险评分"))) is not None
                                             or has_data(body))
    if kind in {None, "table", "line", "bar", "pie", "donut", "multi_line"}:
        return bool(clean_rows(body, spec.get("columns")))
    return has_data(body)


def visible_sections(core: Mapping[str, Any], sections: Any) -> list[dict[str, Any]]:
    return [dict(spec) for spec in sections if isinstance(spec, Mapping) and section_has_data(core, spec)] \
        if isinstance(sections, (list, tuple)) else []
