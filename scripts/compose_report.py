#!/usr/bin/env python3
"""Compose a news big-data report by orchestrating the news MCP.

Calls the upstream news-mcp-server tools and assembles a structured JSON
payload rendered into a professional HTML / Markdown report. Supports
``--dry-run`` which returns a well-formed skeleton from the bundled sample data
WITHOUT contacting the MCP.

Workflow (real run):
  1. Resolve the canonical enterprise name (fuzzy search if only a keyword).
  2. Query news_bigdata_news_stats (情感统计+趋势) and news_bigdata_news_list
     (舆情明细，可按 sentimentLabel 过滤).
  3. Build unified report JSON with domain sections.
  4. Optionally render HTML + Markdown.

This file never prints secrets; MCP credentials live in the server's own .env.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
from typing import Any, Dict, List, Mapping, Optional, Sequence

from common import REPORT_BANNER, REPORT_TYPE, attach_pagination, call_tool_once, collect_detail_pages, die, json_dumps, load_json_file, paid_call_scope, print_json
import mcp_client
from render_report import render_html, render_markdown, html_to_pdf

SAMPLE_PATH = pathlib.Path(__file__).resolve().parent.parent / "assets" / "report.example.json"

# News MCP tools.
T_FUZZY = "news_bigdata_fuzzy_search"
T_STATS = "news_bigdata_news_stats"
T_LIST = "news_bigdata_news_list"

# sentimentLabel mapping: 0=负面, 1=正面, 2=中性, 3=未知
SENTIMENT_LABELS = {0: "负面", 1: "正面", 2: "中性", 3: "未知"}
SENTIMENT_KEYS = {"negative": "负面", "positive": "正面", "neutral": "中性", "unknown": "未知"}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _is_empty_response(value: Any) -> bool:
    if value is None or value == "" or value == {}:
        return True
    if isinstance(value, str):
        text = value.strip().lower()
        return any(token in text for token in ("查询数据为空", "暂无数据", "未查询到", "未检索到", "no data"))
    if isinstance(value, dict):
        for key in ("text", "message", "msg"):
            if key in value and _is_empty_response(value.get(key)):
                return True
        containers = [value.get(key) for key in ("resultList", "list", "items", "data") if key in value]
        if containers and all(item in (None, [], {}) for item in containers):
            total = value.get("total")
            return total in (None, 0, "0")
    return False


def _is_api_error(value: Any) -> bool:
    """Return True only for failed calls; platform empty-data text is not an error."""
    if value is None or _is_empty_response(value):
        return False
    if isinstance(value, str):
        text = value.lower()
        return any(token in text for token in (
            "接口调用失败", "查询失败", "状态码：4", "状态码：5",
            "upstream timeout", "tool failed", "unauthorized", "forbidden",
        ))
    if isinstance(value, dict):
        if value.get("isError") is True or value.get("is_error") is True or value.get("success") is False:
            return True
        if any(value.get(key) not in (None, "", False) for key in ("_error", "error")):
            return True
        code = value.get("code")
        if code not in (None, 0, "0", 200, "200", 10001, "10001"):
            return True
        return any(_is_api_error(value.get(key)) for key in ("text", "message", "msg") if key in value)
    return False


def _first_list(value: Any) -> List[Any]:
    if _is_api_error(value) or _is_empty_response(value):
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("resultList", "list", "items"):
            if isinstance(value.get(key), list):
                return value[key]
        nested = value.get("data")
        if isinstance(nested, list):
            return nested
        if isinstance(nested, dict):
            return _first_list(nested)
    return []


def _first_record(value: Any) -> Dict[str, Any]:
    if _is_api_error(value) or _is_empty_response(value):
        return {}
    for record in _first_list(value):
        if isinstance(record, dict) and _has_evidence(record):
            return record
    if isinstance(value, dict):
        envelope = {"resultList", "list", "items", "data", "total", "pageNo", "pageSize", "text", "message", "msg", "code"}
        if not set(value).issubset(envelope) and _has_evidence(value):
            return value
    return {}


def _has_evidence(value: Any) -> bool:
    if value is None or value == "" or value == "-":
        return False
    if _is_api_error(value) or _is_empty_response(value):
        return False
    if isinstance(value, dict):
        return any(_has_evidence(item) for key, item in value.items() if key not in {"total", "pageNo", "pageSize"})
    if isinstance(value, (list, tuple, set)):
        return any(_has_evidence(item) for item in value)
    return True


def _meaningful_row(row: Mapping[str, Any]) -> bool:
    return any(_has_evidence(value) for value in row.values())


def _attach_call_status(report: Dict[str, Any], calls: Mapping[str, Any]) -> Dict[str, Any]:
    """Disclose failed and genuine-empty domains without exposing upstream details."""
    failed = [name for name, payload in calls.items() if _is_api_error(payload)]
    empty = [name for name, payload in calls.items() if not _is_api_error(payload) and _is_empty_response(payload)]
    source = report.setdefault("data_source", {})
    source["failed_products"] = failed
    source["empty_products"] = empty
    return report


def _apply_resolution_limit(report: Dict[str, Any], resolved: Mapping[str, Any]) -> Dict[str, Any]:
    subject = report.setdefault("subject", {})
    subject["resolved"] = False
    subject["requires_confirmation"] = True
    subject["resolve_reason"] = resolved.get("reason", "主体未唯一确认")
    subject["candidate_sample"] = list(resolved.get("candidate_sample") or [])
    if resolved.get("fuzzy_total") is not None:
        subject["fuzzy_total"] = resolved.get("fuzzy_total")
    message = "输入未唯一确认到特定企业；已停止主体专属数据调用，不生成该企业的经营、风险或舆情评价。请使用候选企业全称或企业标识重试。"
    report["abstract"] = message
    report["summary"] = message
    report["executive_summary"] = [message]
    report["metrics"] = []
    report["representative_records"] = []
    report["insights"] = [{"feature": "主体确认受限", "evidence": subject["resolve_reason"], "interpretation": message}]
    report.setdefault("data_source", {})["subject_resolution"] = "confirmation_required"
    return report


def _text(value: Any, limit: int = 0) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        t = json.dumps(value, ensure_ascii=False)
    else:
        t = str(value)
    t = " ".join(t.split())
    if limit and len(t) > limit:
        return t[: limit - 1].rstrip() + "…"
    return t


def _int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_call(tool: str, arguments: Dict[str, Any]) -> Any:
    try:
        result = call_tool_once(mcp_client.call_tool, tool, arguments)
        # Detect API error responses (405, etc.) and return error marker
        if _is_api_error(result):
            return {"_error": "API错误", "_raw": result}
        return result
    except Exception as exc:
        return {"_error": str(exc)}


def _safe_total(payload: Any) -> Any:
    if isinstance(payload, dict):
        if _is_api_error(payload):
            return None
        return payload.get("total")
    return None


def _is_error(payload: Any) -> bool:
    return isinstance(payload, dict) and ("error" in payload or "_error" in payload)


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #

def resolve_enterprise_name(raw: str) -> Dict[str, Any]:
    raw = (raw or "").strip()
    if not raw:
        return {"keyword": "", "enterprise": "", "resolved": False, "requires_confirmation": True, "reason": "关键词为空", "candidate_sample": []}
    # A full legal-name-looking input is already an explicit subject selection.
    if any(suffix in raw for suffix in ("公司", "有限", "院", "厂", "中心", "事务所", "合作社", "合伙")):
        return {"keyword": raw, "enterprise": raw, "resolved": True, "requires_confirmation": False, "reason": "用户提供企业全称", "candidate_sample": []}

    fuzzy = _safe_call(T_FUZZY, {"matchKeyword": raw, "pageSize": 10})
    records = [item for item in _first_list(fuzzy) if isinstance(item, dict)]
    total = _int(_safe_total(fuzzy))
    id_keys = ("nameId", "id", "enterpriseId", "regNumber", "socialCreditCode")
    exact = [
        item for item in records
        if raw == str(item.get("name") or "").strip()
        or any(raw == str(item.get(key) or "").strip() for key in id_keys)
    ]
    if len(exact) == 1:
        record = exact[0]
        name = str(record.get("name") or raw).strip()
        return {"keyword": raw, "enterprise": name, "resolved": True, "requires_confirmation": False, "reason": "与候选企业全称或标识精确匹配", "fuzzy_total": total, "record": record, "candidate_sample": []}
    if len(records) == 1 and total == 1:
        record = records[0]
        name = str(record.get("name") or "").strip()
        if name:
            return {"keyword": raw, "enterprise": name, "resolved": True, "requires_confirmation": False, "reason": "模糊查询唯一命中", "fuzzy_total": total, "record": record, "candidate_sample": []}

    candidates = []
    for item in records[:5]:
        candidate = {"name": str(item.get("name") or "").strip()}
        for key in id_keys:
            if item.get(key) not in (None, ""):
                candidate[key] = str(item.get(key))
        if candidate.get("name") or len(candidate) > 1:
            candidates.append(candidate)
    if candidates:
        reason = f"模糊查询命中 {total if total is not None else '多个'} 个候选，未唯一确认主体"
    else:
        reason = "模糊查询未唯一命中企业，需提供企业全称或企业标识"
    return {
        "keyword": raw, "enterprise": raw, "resolved": False, "requires_confirmation": True,
        "reason": reason, "fuzzy_total": total, "candidate_sample": candidates,
    }



def _extract_profile(record: Dict[str, Any]) -> Dict[str, Any]:
    """Extract enterprise profile fields from a fuzzy_search record."""
    return {
        "name": _text(record.get("name")),
        "reg_capital": record.get("regCapitalValue"),
        "reg_capital_coin": _text(record.get("regCapitalCoinType")),
        "annual_turnover": _text(record.get("annualTurnover")),
        "oper_status": _text(record.get("operStatus")),
        "enterprise_type": _text(record.get("enterpriseType")),
        "found_time": _text(record.get("foundTime")),
        "legal_rep": _text(record.get("legalRepresentative")),
        "address": _text(record.get("address")),
        "homepage": _text(record.get("homepage")),
    }


def _format_capital(val: Any, coin: str = "") -> str:
    """Format capital value: 10995210218.0 -> '109.95 亿'."""
    try:
        v = float(val)
        if v >= 1e8:
            s = f"{v / 1e8:.2f} 亿"
        elif v >= 1e4:
            s = f"{v / 1e4:.2f} 万"
        else:
            s = f"{v:.0f}"
        if coin:
            s += f" {coin}"
        return s
    except (TypeError, ValueError):
        return _text(val) if val else "-"


def _enrich_metrics_with_profile(metrics: List[Dict[str, Any]], record: Any) -> List[Dict[str, Any]]:
    """Append enterprise profile metrics from a fuzzy_search record."""
    if not isinstance(record, dict):
        return metrics
    _prof = _extract_profile(record)
    if _prof.get("reg_capital") and _prof["reg_capital"] not in ("-", "", None):
        metrics.append({"label": "注册资本", "value": _format_capital(_prof["reg_capital"], _prof.get("reg_capital_coin", "")), "hint": "工商登记注册资本"})
    if _prof.get("found_time") and _prof["found_time"] != "-":
        metrics.append({"label": "成立时间", "value": _prof["found_time"], "hint": "工商登记成立日期"})
    if _prof.get("oper_status") and _prof["oper_status"] != "-":
        metrics.append({"label": "经营状态", "value": _prof["oper_status"], "hint": "工商登记经营状态"})
    if _prof.get("enterprise_type") and _prof["enterprise_type"] != "-":
        metrics.append({"label": "企业类型", "value": _prof["enterprise_type"], "hint": "工商登记企业类型"})
    if _prof.get("legal_rep") and _prof["legal_rep"] != "-":
        metrics.append({"label": "法定代表人", "value": _prof["legal_rep"], "hint": "工商登记法定代表人"})
    return metrics


def _derive_core_metrics(metrics: List[Dict[str, Any]], core: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Derive additional metrics from core analysis sections."""
    detail = core.get("news_list", []) if isinstance(core, dict) else []
    if isinstance(detail, list) and detail:
        try:
            sources = set(str(r.get("来源", "")) for r in detail if r.get("来源"))
            if sources:
                metrics.append({"label": "资讯来源数", "value": str(len(sources)), "hint": "不同媒体来源数"})
            from collections import Counter
            src_counts = Counter(str(r.get("来源", "")) for r in detail if r.get("来源"))
            if src_counts:
                top = src_counts.most_common(1)[0]
                metrics.append({"label": "高频来源", "value": f"{top[0]}（{top[1]}篇）", "hint": "报道最多的媒体"})
        except Exception:
            pass
    return metrics


# --------------------------------------------------------------------------- #
# Section builders
# --------------------------------------------------------------------------- #

def build_subject(raw: str, resolved: Mapping[str, Any], keyword_type: str, sentiment: Optional[int]) -> Dict[str, Any]:
    return {
        "enterprise": resolved.get("enterprise") or raw,
        "matchKeyword": resolved.get("enterprise") or raw,
        "keywordType": keyword_type,
        "match_raw": raw,
        "resolved": bool(resolved.get("resolved")),
        "resolve_reason": resolved.get("reason", ""),
        "sentiment_label": sentiment,
        "sentiment_label_name": SENTIMENT_LABELS.get(sentiment, "") if sentiment is not None else "",
    }


def build_metrics(stats: Mapping[str, Any], list_total: Any, core: Optional[Mapping[str, Any]] = None) -> List[Dict[str, Any]]:
    metrics: List[Dict[str, Any]] = []
    core = core or {}
    s = stats if isinstance(stats, dict) and not _is_error(stats) else {}
    raw_sentiment_stats = s.get("newsSentimentStats")
    sentiment_stats: Mapping[str, Any] = raw_sentiment_stats if isinstance(raw_sentiment_stats, dict) else {}
    # compute total of all sentiment counts for share deltas
    sent_total = 0.0
    parsed: Dict[str, Any] = {}
    for key in ("positive", "negative", "neutral", "unknown"):
        val = sentiment_stats.get(key)
        n = _int(val)
        parsed[key] = (val, n)
        if n is not None:
            sent_total += n
    for key in ("positive", "negative", "neutral", "unknown"):
        val, n = parsed[key]
        if val is None:
            continue
        entry: Dict[str, Any] = {"label": f"{SENTIMENT_KEYS[key]}舆情", "value": _text(val), "hint": f"{SENTIMENT_KEYS[key]}情感舆情数"}
        if n is not None and sent_total > 0 and key in ("positive", "negative"):
            entry["delta"] = f"占比 {n / sent_total * 100:.0f}%"
        metrics.append(entry)
    if list_total is not None:
        metrics.append({"label": "舆情明细数", "value": _text(list_total), "hint": "本次舆情明细命中数"})
    related = core.get("related_enterprises") or []
    if related:
        metrics.append({"label": "关联企业数", "value": str(len(related)) + " 家", "hint": "共现的关联企业去重数"})
    sources = core.get("news_source") or []
    if sources:
        metrics.append({"label": "报道媒体数", "value": str(len(sources)) + " 家", "hint": "报道来源媒体去重数"})
    return [m for m in metrics if m.get("value") not in ("", None, "-")]


def build_caliber(subject: Mapping[str, Any], sentiment: Optional[int]) -> Dict[str, Any]:
    sentiment_hint = f"；sentimentLabel={sentiment}（{SENTIMENT_LABELS.get(sentiment, '')}）" if sentiment is not None else ""
    return {
        "match_target": subject.get("enterprise") or subject.get("match_raw"),
        "match_type": f"舆情数据按企业主体匹配（keywordType={subject.get('keywordType', 'name')}）；明细支持 sentimentLabel 情感过滤{sentiment_hint}",
        "data_scope": "舆情情感统计（4类情感+趋势）、舆情明细列表",
        "products": ["舆情统计", "舆情明细", "关键词模糊查询企业"],
        "limit": "数据来自公开新闻舆情；情感分布与趋势可能受数据源覆盖、更新延迟、通稿转载及重复收录影响，报告未将媒体条数等同于独立事件数。",
    }


def build_core_analysis(stats: Mapping[str, Any], news_list: Any, sentiment: Optional[int], subject_name: str = "") -> Dict[str, Any]:
    s: Mapping[str, Any] = stats if isinstance(stats, dict) and not _is_error(stats) else {}

    # 舆情统计 KV
    stats_kv: Dict[str, Any] = {}
    raw_sentiment_stats = s.get("newsSentimentStats")
    sentiment_stats: Mapping[str, Any] = raw_sentiment_stats if isinstance(raw_sentiment_stats, dict) else {}
    for key in ("positive", "negative", "neutral", "unknown"):
        val = sentiment_stats.get(key)
        if val is not None:
            stats_kv[SENTIMENT_KEYS[key]] = _text(val)
    if isinstance(s.get("sentimentLabelList"), list) and s["sentimentLabelList"]:
        stats_kv["情感类别"] = "、".join(_text(t) for t in s["sentimentLabelList"] if t)

    # 派生：情感分布饼图数据（由 KV 派生为列表）
    sentiment_dist: List[Dict[str, Any]] = []
    for key in ("positive", "negative", "neutral", "unknown"):
        val = sentiment_stats.get(key)
        if val is not None and _int(val) != 0:
            sentiment_dist.append({"类别": SENTIMENT_KEYS[key], "数量": _text(val)})

    # 趋势表
    # NOTE: 上游 newsSentimentTrend 的逐月 stats 实际只含 negative + positive（无 neutral），
    # 因此趋势图只画 正面/负面 两条线，避免渲染一条恒为空的“中性”系列。
    trend_rows: List[Dict[str, Any]] = []
    news_trend = s.get("newsSentimentTrend")
    if isinstance(news_trend, dict):
        months = news_trend.get("month")
        if isinstance(months, list):
            raw_stats_list = news_trend.get("stats")
            stats_list: List[Any] = raw_stats_list if isinstance(raw_stats_list, list) else []
            for idx, m in enumerate(months):
                row = {"周期/月份": _text(m)}
                raw_stats_item = stats_list[idx] if idx < len(stats_list) else {}
                stats_item: Mapping[str, Any] = raw_stats_item if isinstance(raw_stats_item, dict) else {}
                if isinstance(stats_item, dict):
                    for k, lbl in (("negative", "负面"), ("positive", "正面")):
                        if stats_item.get(k) is not None:
                            row[lbl] = _text(stats_item.get(k))
                trend_rows.append(row)
    elif isinstance(news_trend, list):
        for ti in news_trend:
            if isinstance(ti, dict):
                row = {"周期/月份": _text(ti.get("month"))}
                raw_stats_item = ti.get("stats")
                stats_item = raw_stats_item if isinstance(raw_stats_item, dict) else {}
                for k, lbl in (("negative", "负面"), ("positive", "正面")):
                    if stats_item.get(k) is not None:
                        row[lbl] = _text(stats_item.get(k))
                trend_rows.append(row)

    # 舆情列表表 + 关联企业共现聚合 + 媒体来源聚合
    list_rows: List[Dict[str, Any]] = []
    related_counter: Dict[str, int] = {}
    source_counter: Dict[str, int] = {}
    total = None
    if isinstance(news_list, dict):
        total = news_list.get("total")
    for item in _first_list(news_list):
        if not isinstance(item, dict):
            continue
        label_int = _int(item.get("sentimentLabel"))
        title = _text(item.get("newsTitle") or item.get("title")) or "-"
        link = _text(item.get("newsLink") or item.get("link"))
        list_rows.append({
            "标题": title,
            "来源": _text(item.get("newsSource") or item.get("source")) or "-",
            "发布时间": _text(item.get("newsPublishTime") or item.get("publishTime")) or "-",
            "情感": SENTIMENT_LABELS.get(label_int, _text(item.get("sentimentLabel"))) if label_int is not None else "-",
            "链接": link or "-",
            "简介": _text(item.get("newsBrief") or item.get("brief"), limit=80) or "-",
        })
        # 关联企业共现（排除主体自身）
        for re in _first_list(item.get("relatedEnterprises")):
            if isinstance(re, dict):
                rname = _text(re.get("name"))
                if rname and rname != subject_name:
                    related_counter[rname] = related_counter.get(rname, 0) + 1
        # 媒体来源
        src = _text(item.get("newsSource") or item.get("source"))
        if src:
            source_counter[src] = source_counter.get(src, 0) + 1

    related_rows = [{"关联企业": k, "共现次数": v} for k, v in sorted(related_counter.items(), key=lambda x: x[1], reverse=True)]
    source_rows = [{"媒体来源": k, "报道数量": v} for k, v in sorted(source_counter.items(), key=lambda x: x[1], reverse=True)]

    filter_validation: Dict[str, Any] = {}
    if sentiment is not None:
        returned_labels = [_int(item.get("sentimentLabel")) for item in _first_list(news_list) if isinstance(item, dict)]
        mismatches = sum(1 for label in returned_labels if label is not None and label != sentiment)
        stat_key = {0: "negative", 1: "positive", 2: "neutral", 3: "unknown"}.get(sentiment)
        stats_total = sentiment_stats.get(stat_key) if stat_key else None
        filter_validation = {
            "请求情感": f"{sentiment}（{SENTIMENT_LABELS.get(sentiment, '未知枚举')}）",
            "筛选状态": "返回记录含非目标情感" if mismatches else "归纳记录符合筛选枚举",
            "非目标记录数": mismatches,
            "列表命中总数": total if total is not None else "-",
            "统计口径对应数量": stats_total if stats_total is not None else "-",
            "统计口径": (
                "过滤失败：返回标签与筛选条件冲突，列表总量不可替代分类统计" if mismatches
                else "口径不一致，列表总量不可替代情感统计" if total is not None and stats_total is not None and str(total) != str(stats_total)
                else "总量暂未发现冲突，但仍不代表过滤语义与统计口径完全一致"
            ),
        }

    note_suffix = f"；sentimentLabel={sentiment}（{SENTIMENT_LABELS.get(sentiment, '')}）" if sentiment is not None else ""
    sections = [
        {"key": "news_statistics", "title": "舆情统计", "kind": "kv", "note": "4类情感（正/负/中/未知）总量分布"},
        {"key": "filter_validation", "title": "情感筛选口径核验", "kind": "kv", "note": "核验请求枚举、返回记录标签与统计总量，不将冲突口径强行合并"},
        {"key": "sentiment_dist", "title": "舆情情感分布", "kind": "pie", "note": "4类情感数量占比",
         "chart": {"name": "类别", "value": "数量", "donut": True}, "columns": [("类别", "类别"), ("数量", "数量")]},
        {"key": "sentiment_trend", "title": "舆情情感趋势", "kind": "multi_line", "note": "按月份统计正面/负面情感走势（上游逐月数据暂不含中性，故仅展示正/负两条线）",
         "chart": {"x": "周期/月份", "series": ["负面", "正面"], "area": True},
         "columns": [("周期/月份", "周期/月份"), ("负面", "负面"), ("正面", "正面")]},
        {"key": "related_enterprises", "title": "关联企业共现", "kind": "bar", "note": "与主体在同一报道中被共同提及的企业（按共现次数）",
         "chart": {"name": "关联企业", "value": "共现次数", "orient": "h"}, "columns": [("关联企业", "关联企业"), ("共现次数", "共现次数")]},
        {"key": "news_source", "title": "媒体来源分布", "kind": "bar", "note": "按报道媒体来源统计",
         "chart": {"name": "媒体来源", "value": "报道数量", "orient": "v"}, "columns": [("媒体来源", "媒体来源"), ("报道数量", "报道数量")]},
        {"key": "news_list", "title": "舆情列表", "kind": "table",
         "note": f"命中 {total if total is not None else '若干'} 条{note_suffix}（标题含可点击原文链接）",
         "columns": [("标题", "标题"), ("来源", "来源"), ("发布时间", "发布时间"), ("情感", "情感"), ("链接", "链接"), ("简介", "简介")]},
    ]

    return {
        "sections": sections,
        "news_statistics": stats_kv,
        "filter_validation": filter_validation,
        "sentiment_dist": sentiment_dist,
        "sentiment_trend": trend_rows,
        "related_enterprises": related_rows,
        "news_source": source_rows,
        "news_list": list_rows,
    }


def build_records(core: Mapping[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for item in core.get("news_list") or []:
        out.append({
            "标题": item.get("标题") or "-",
            "来源": item.get("来源") or "-",
            "发布时间": item.get("发布时间") or "-",
            "情感": item.get("情感") or "-",
        })
    return out[:20]


def _series_trend(rows: List[Mapping[str, Any]], series_key: str) -> Dict[str, Any]:
    """Compute trend direction & YoY for one sentiment series from multi-series trend rows."""
    nums = []
    for r in rows:
        try:
            nums.append(float(str(r.get(series_key, 0)).replace(",", "")))
        except (TypeError, ValueError):
            nums.append(0.0)
    if not nums:
        return {}
    peak_idx = max(range(len(nums)), key=lambda i: nums[i])
    direction = "持平"
    yoy = ""
    if len(nums) >= 2:
        last, prev = nums[-1], nums[-2]
        if prev > 0:
            pct = (last - prev) / prev * 100
            if pct > 5:
                direction = f"上升 {pct:.0f}%"
            elif pct < -5:
                direction = f"下降 {abs(pct):.0f}%"
            yoy = f"环比 {pct:+.0f}%"
    return {"peak_period": rows[peak_idx].get("周期/月份", "-"), "peak_value": nums[peak_idx], "direction": direction, "yoy": yoy, "last": nums[-1]}


def _concentration(rows: List[Mapping[str, Any]], top_n: int = 3, name_key: str = "名称", value_key: str = "数量") -> Dict[str, Any]:
    """Compute top-N concentration (CRn) and dominant category from rows."""
    items = []
    for r in rows:
        try:
            items.append((r.get(name_key, "-"), float(str(r.get(value_key, 0)).replace(",", ""))))
        except (TypeError, ValueError):
            items.append((r.get(name_key, "-"), 0.0))
    total = sum(v for _, v in items)
    if not total:
        return {}
    items.sort(key=lambda x: x[1], reverse=True)
    cr = sum(v for _, v in items[:top_n]) / total * 100
    return {"top": items[0][0], "top_share": items[0][1] / total * 100, "cr": cr, "total": total}


def build_insights(subject: Mapping[str, Any], core: Mapping[str, Any], metrics: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    insights: List[Dict[str, Any]] = []
    metric_map = {m["label"]: str(m["value"]) for m in metrics}
    stats_kv = core.get("news_statistics") or {}

    pos = stats_kv.get("正面") or metric_map.get("正面舆情")
    neg = stats_kv.get("负面") or metric_map.get("负面舆情")
    neu = stats_kv.get("中性") or metric_map.get("中性舆情")
    if pos or neg:
        ev_parts = []
        if pos:
            ev_parts.append(f"正面 {pos}")
        if neg:
            ev_parts.append(f"负面 {neg}")
        # compute positive share & neg-to-pos ratio
        try:
            pos_n = float(pos) if pos else 0.0
            neg_n = float(neg) if neg else 0.0
            denom = pos_n + neg_n + (float(neu) if neu else 0.0)
            pos_share = f"（正面占比 {pos_n / denom * 100:.0f}%）" if denom else ""
            ratio = f"，正负比 {pos_n / neg_n:.1f}:1" if neg_n > 0 and pos_n else ""
        except (TypeError, ValueError, ZeroDivisionError):
            pos_share, ratio = "", ""
        insights.append({
            "feature": "情感结构",
            "evidence": "、".join(ev_parts) + "。" + pos_share + ratio,
            "interpretation": "正负情感比例仅描述算法标签分布；需结合原文核验，不能直接等同于公众形象或声誉健康度。",
        })
    if metric_map.get("舆情明细数"):
        insights.append({
            "feature": "舆情声量",
            "evidence": f"舆情明细命中 {metric_map.get('舆情明细数')} 条。",
            "interpretation": "舆情声量反映企业在新闻舆论中的关注度，结合情感分布可评估舆情整体走向。",
        })
    if neu:
        try:
            denom = sum(float(v) for v in (pos, neg, neu) if v) if any((pos, neg, neu)) else 0
            neu_share = f"（占比约 {float(neu) / denom * 100:.0f}%）" if denom else ""
        except (TypeError, ValueError, ZeroDivisionError):
            neu_share = ""
        insights.append({
            "feature": "中性占比",
            "evidence": f"中性舆情 {neu} 条{neu_share}。",
            "interpretation": "中性仅是情感模型分类，不足以证明报道为事实陈述，也不能据此评价品牌传播效果。",
        })
    # 负面情感趋势研判（multi-line 中负面序列）
    trend_rows = core.get("sentiment_trend") or []
    if trend_rows:
        ta = _series_trend(trend_rows, "负面")
        if ta and ta.get("last", 0) > 0:
            insights.append({
                "feature": "负面舆情趋势",
                "evidence": f"负面舆情峰值出现在“{ta['peak_period']}”（{ta['peak_value']:.0f} 条），近月趋势{ta['direction']}，{ta.get('yoy', '')}。",
                "interpretation": "负面舆情趋势是声誉风险的前瞻指标；若持续上升建议结合负面明细核查事件源并启动公关应对。",
            })
    # 关联企业共现集中度（HIGHEST VALUE）
    related = core.get("related_enterprises") or []
    if related:
        conc = _concentration(related, 3, name_key="关联企业", value_key="共现次数")
        total_co = sum(int(r.get("共现次数", 0) or 0) for r in related)
        if conc:
            insights.append({
                "feature": "关联企业共现",
                "evidence": f"共出现 {len(related)} 家关联企业，合计共现 {total_co} 次；其中“{conc['top']}”共现最多（占比约 {conc['top_share']:.0f}%），前 3 家合计 {conc['cr']:.0f}%（CR3）。",
                "interpretation": "共现只表示实体出现在同一报道样本中，不能据此认定上下游、合作或竞争关系。",
            })
    # 媒体来源集中度
    sources = core.get("news_source") or []
    if sources:
        conc = _concentration(sources, 3, name_key="媒体来源", value_key="报道数量")
        if conc and len(sources) >= 2:
            insights.append({
                "feature": "媒体来源分布",
                "evidence": f"报道来自 {len(sources)} 家媒体，其中“{conc['top']}”报道最多（占比约 {conc['top_share']:.0f}%）。",
                "interpretation": "媒体来源分布只描述样本渠道覆盖，来源数量或分散程度不等同于内容可信度。",
            })
    if core.get("news_list"):
        insights.append({
            "feature": "转载与事件去重限制",
            "evidence": "列表与总量未按事件去重，同题转载可能重复计数。",
            "interpretation": "媒体条数不等于独立新闻事件数；需要按标题、正文、时间与原始链接另行去重后再做事件级判断。",
        })
    if not insights:
        insights.append({
            "feature": "数据完整性",
            "evidence": "部分维度未返回有效数据。",
            "interpretation": "建议核对匹配关键词是否为企业全称，或检查 MCP 连接与上游数据产品覆盖范围。",
        })
    return insights


def build_abstract(subject: Mapping[str, Any], core: Mapping[str, Any], metrics: Sequence[Mapping[str, Any]]) -> str:
    name = subject.get("enterprise") or subject.get("match_raw") or "目标企业"
    parts = [f"本报告以“{name}”为分析对象，基于公开新闻舆情数据，系统呈现企业舆情情感统计（4类情感+趋势）与舆情明细列表。"]
    if metrics:
        kv = "、".join(f"{m['label']} {m['value']}" for m in metrics[:5])
        parts.append(f"关键指标包括：{kv}。")
    parts.append("报告同时给出情感结构、舆情声量与中性占比的结构化解读，便于声誉管理、舆情监控与公关策略决策参考。")
    return "".join(parts)


# --------------------------------------------------------------------------- #
# Dry-run sample
# --------------------------------------------------------------------------- #

def build_dry_run_payload(raw: str, keyword_type: str, sentiment: Optional[int]) -> Dict[str, Any]:
    try:
        sample = load_json_file(SAMPLE_PATH)
    except Exception:
        sample = {}
    sample = sample if isinstance(sample, dict) else {}
    resolved = resolve_enterprise_name(raw)
    subject = build_subject(raw, resolved, keyword_type, sentiment)
    core = sample.get("core_analysis") or {}
    metrics = sample.get("metrics") or []
    return _assemble(subject, core, metrics, dry_run=True, sentiment=sentiment)


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #

def _assemble(subject: Mapping[str, Any], core: Mapping[str, Any], metrics: Sequence[Mapping[str, Any]], *, dry_run: bool, sentiment: Optional[int]) -> Dict[str, Any]:
    abstract = build_abstract(subject, core, metrics)
    records = build_records(core)
    insights = build_insights(subject, core, metrics)
    # Quality gate: count populated core-analysis sections.
    ca = core if isinstance(core, dict) else {}
    secs = ca.get("sections", [])
    if secs:
        total_secs = len(secs)
        populated = sum(1 for s in secs if isinstance(s, dict) and _has_evidence(ca.get(s.get("key"))))
    else:
        total_secs = max(1, len([k for k in ca if k != "sections"]))
        populated = sum(1 for k in ca if k != "sections" and _has_evidence(ca.get(k)))
    quality_report = {
        "total_sections": total_secs,
        "populated_sections": populated,
        "empty_sections": total_secs - populated,
        "coverage_pct": round(populated / max(1, total_secs) * 100),
    }
    if populated == 0:
        import sys
        print("⚠️ 质量门禁警告: 所有核心分析维度均无数据", file=sys.stderr)
    title = f"{subject.get('enterprise') or '目标企业'} 舆情大数据报告"
    return {
        "report_type": REPORT_TYPE,
        "title": title,
        "banner": REPORT_BANNER,
        "subject": dict(subject),
        "abstract": abstract,
        "summary": abstract,
        "executive_summary": [item["interpretation"] for item in insights][:5] or [abstract[:120]],
        "metrics": list(metrics),
        "caliber": build_caliber(subject, sentiment),
        "core_analysis": dict(core),
        "representative_records": records,
        "insights": insights,
        "data_source": {
            "mcp_server": "news-mcp-server",
            "products": [
                {"name": "舆情统计", "product_id": "66b338e274bf098447db7efd"},
                {"name": "舆情明细", "product_id": "66b485eadaf8c77fb249a455"},
                {"name": "关键词模糊查询企业", "product_id": "675cea1f0e009a9ea37edaa1"},
            ],
            "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "dry_run": dry_run,
            "quality_report": quality_report,
        },
    }


@paid_call_scope
def build_payload(raw: str, keyword_type: str, sentiment: Optional[int], page_size: int, max_pages: int = 1) -> Dict[str, Any]:
    if isinstance(max_pages, bool) or not isinstance(max_pages, int) or max_pages < 1:
        raise ValueError("max_pages 必须为正整数")
    resolved = resolve_enterprise_name(raw)
    if resolved.get("requires_confirmation"):
        subject = build_subject(raw, resolved, keyword_type, sentiment)
        return _apply_resolution_limit(_assemble(subject, {"sections": []}, [], dry_run=False, sentiment=sentiment), resolved)
    enterprise = resolved["enterprise"]
    mk_args: Dict[str, Any] = {"matchKeyword": enterprise, "keywordType": keyword_type}

    stats = _safe_call(T_STATS, mk_args)

    list_args: Dict[str, Any] = {"matchKeyword": enterprise, "keywordType": keyword_type, "pageSize": page_size}
    if sentiment is not None:
        list_args["sentimentLabel"] = sentiment
    news_list = collect_detail_pages(
        _safe_call, T_LIST, list_args,
        get_rows=_first_list, get_total=_safe_total, is_error=_is_api_error,
        max_pages=max_pages,
    )
    list_total = _safe_total(news_list) if isinstance(news_list, dict) else None

    subject = build_subject(raw, resolved, keyword_type, sentiment)
    core = build_core_analysis(stats, news_list, sentiment, subject.get("enterprise") or enterprise)
    metrics = build_metrics(stats if isinstance(stats, dict) else {}, list_total, core)
    _derive_core_metrics(metrics, core if isinstance(core, dict) else {})
    # --- Enterprise profile enrichment (from fuzzy_search) ---
    _enrich_metrics_with_profile(metrics, resolved.get("record") if isinstance(resolved, dict) else None)
    report = _assemble(subject, core, metrics, dry_run=False, sentiment=sentiment)
    report = _attach_call_status(report, {"舆情统计": stats, "舆情明细": news_list})
    return attach_pagination(report, {"舆情明细": news_list})


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main() -> None:
    parser = argparse.ArgumentParser(description="Compose a news big-data report via the news MCP.")
    parser.add_argument("--enterprise", required=True, help="企业全称或关键词（关键词将自动模糊补全）")
    parser.add_argument("--keyword-type", default="name", help="主体类型：name/nameId/regNumber/socialCreditCode")
    parser.add_argument("--sentiment", type=int, default=None, choices=[0, 1, 2, 3], help="舆情明细情感过滤：0=负面/1=正面/2=中性/3=未知")
    parser.add_argument("--page-size", type=int, default=50, help="舆情明细分页大小（最多 50）")
    parser.add_argument("--max-pages", type=int, default=1, help="舆情明细最多付费翻页数（默认 1；仅在明确需要更多结论/证据时增大）")
    parser.add_argument("--dry-run", action="store_true", help="不调用真实 MCP，使用样例数据组装报告骨架")
    parser.add_argument("--output", help="输出 JSON 路径；省略则打印到 stdout")
    parser.add_argument("--report-output", help="同时输出 HTML 报告（.html）与 Markdown 报告（.md）")
    parser.add_argument("--pdf-output", help="额外输出 PDF 报告（.pdf）；需要 Playwright + Chromium")
    args = parser.parse_args()

    if not args.dry_run:
        if args.max_pages is not None and args.max_pages < 1:
            die("--max-pages 必须为正整数")

    if args.dry_run:
        payload = build_dry_run_payload(args.enterprise, args.keyword_type, args.sentiment)
    else:
        payload = build_payload(args.enterprise, args.keyword_type, args.sentiment, args.page_size, args.max_pages)

    if args.output:
        out = pathlib.Path(args.output).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json_dumps(payload, pretty=True), encoding="utf-8")
        print_json({"ok": True, "json": str(out), "dry_run": args.dry_run})
    else:
        print_json(payload)

    if args.report_output:
        base_out = pathlib.Path(args.report_output).expanduser()
        base_out.parent.mkdir(parents=True, exist_ok=True)
        html_path = base_out.with_suffix(".html") if base_out.suffix.lower() not in (".html", ".htm") else base_out
        md_path = html_path.with_suffix(".md")
        html_path.write_text(render_html(payload), encoding="utf-8")
        md_path.write_text(render_markdown(payload), encoding="utf-8")
        if args.pdf_output:
            pdf_path = pathlib.Path(args.pdf_output).expanduser()
            pdf_path.parent.mkdir(parents=True, exist_ok=True)
            html_to_pdf(render_html(payload), str(pdf_path))
        print_json({"ok": True, "html": str(html_path), "markdown": str(md_path), "pdf": str(pdf_path) if args.pdf_output else None, "dry_run": args.dry_run})


if __name__ == "__main__":
    main()
