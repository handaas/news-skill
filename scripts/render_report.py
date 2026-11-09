#!/usr/bin/env python3
"""Render a HandaaS data report as standalone HTML or Markdown.

This renderer implements the unified professional research-report visual style
shared across all handaas-skills (A4-like white pages, grey striped top rule,
blue report banner, left cover sidebar with catalogue/scope, navy section
headings, dark-blue table headers, light-blue zebra rows, print-friendly page
breaks). It consumes the unified report JSON skeleton defined in AGENTS.md.

Usage::

    python render_report.py --input report.json --output report.html
    python render_report.py --input report.json --output report.md
    python render_report.py --input report.json --output report.pdf
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import pathlib
import sys
from typing import Any, Dict, List, Mapping, Sequence

from common import json_dumps
from static_charts import embed_static_charts
from report_data import (
    clean_rows,
    has_data,
    numeric_value,
    section_has_data,
    visible_sections,
)


# --------------------------------------------------------------------------- #
# IO helpers
# --------------------------------------------------------------------------- #

def load_payload(input_path: str | None) -> Dict[str, Any]:
    if input_path:
        path = pathlib.Path(input_path).expanduser()
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        raw = sys.stdin.read().strip()
        if not raw:
            raise SystemExit("请提供 --input <result.json> 或通过 stdin 传入 JSON")
        data = json.loads(raw)
    if not isinstance(data, dict):
        raise SystemExit("报告输入必须是 JSON object")
    return data


# --------------------------------------------------------------------------- #
# Generic formatting helpers
# --------------------------------------------------------------------------- #

def as_list(value: Any) -> List[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    return [value]


def text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json_dumps(value, pretty=True)
    return str(value)


def esc(value: Any) -> str:
    return html.escape(text(value), quote=True)


def compact(value: Any, limit: int = 0) -> str:
    t = " ".join(text(value).replace("\n", " ").split()).strip(" ；;。")
    if limit and len(t) > limit:
        return t[: limit - 1].rstrip() + "…"
    return t


def tag_items(value: Any) -> List[str]:
    """Readable, exact-deduplicated labels; never serialize a list as prose."""
    if not has_data(value):
        return []
    if isinstance(value, str) and value.strip().startswith("["):
        try:
            decoded = json.loads(value)
        except ValueError:
            decoded = value
        if isinstance(decoded, list):
            value = decoded
    if isinstance(value, (list, tuple)):
        items = [label for item in value for label in tag_items(item)]
    elif isinstance(value, dict):
        readable = next((value[key] for key in ("name", "label", "text", "title")
                         if isinstance(value.get(key), str)), None)
        if readable is not None:
            items = tag_items(readable)
        else:
            items = []
            for key, item in value.items():
                nested = tag_items(item)
                if nested:
                    items.append(f"{key}：{'、'.join(nested)}")
    else:
        items = [" ".join(str(value).split())]
    return list(dict.fromkeys(item for item in items if item and has_data(item)))



def infer_title(data: Mapping[str, Any], explicit: str | None = None) -> str:
    if explicit:
        return explicit
    if data.get("title"):
        return str(data["title"])
    subject = data.get("subject") or {}
    name = subject.get("enterprise") or subject.get("matchKeyword") or subject.get("match_raw")
    return f"{name or '目标'} 数据分析报告" if name else "HandaaS 数据分析报告"


def infer_banner(data: Mapping[str, Any]) -> str:
    return str(data.get("banner") or data.get("report_title") or "HandaaS 数据分析报告")


# --------------------------------------------------------------------------- #
# Table rendering
# --------------------------------------------------------------------------- #


def _safe_unwrap_json(value: Any) -> Any:
    """Safety net: if a cell value is a JSON string (e.g. MCP returned a
    stringified object), parse it into a readable scalar."""
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if len(stripped) <= 3 or stripped[0] != "{" or stripped[-1] != "}":
        return value
    try:
        obj = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return value
    if not isinstance(obj, dict):
        return value
    # Prefer common readable fields
    for field in ("value", "name", "label", "text", "title"):
        if obj.get(field) is not None:
            return obj[field]
    # Address-like objects
    parts = [obj.get("province"), obj.get("city"), obj.get("district")]
    joined = "".join(str(p) for p in parts if p)
    if joined:
        return joined
    return value


def _cell_html(value: Any) -> str:
    """Render a table cell; render http(s) URLs as clickable links."""
    if value in (None, ""):
        return "-"
    value = _safe_unwrap_json(value)
    s = text(value)
    if isinstance(value, str) and (value.startswith("http://") or value.startswith("https://")):
        return f'<a class="source-link" href="{esc(value)}" target="_blank" rel="noopener">{esc(value)}</a>'
    return esc(s)




def _normalize_columns(rows: Sequence[Mapping[str, Any]], columns: Sequence[tuple[str, str]] | None = None) -> List[tuple[str, str]]:
    """Normalize (key, label) / (label, key) specs and drop empty columns."""
    sample = rows[0] if rows else {}
    normalized: List[tuple[str, str]] = []
    for pair in columns or []:
        if not isinstance(pair, (list, tuple)) or not pair:
            continue
        first, second = (list(pair) + [None, None])[:2]
        first_ok = isinstance(first, str) and first in sample
        second_ok = isinstance(second, str) and second in sample
        if first_ok and second_ok:
            key, label = first, second
        elif second_ok:
            key, label = second, first
        else:
            key, label = first, second
        if not isinstance(key, str) or not key:
            continue
        if not any(has_data(row.get(key)) for row in rows):
            continue
        normalized.append((key, str(label or key)))
    if normalized:
        return normalized
    return [(str(key), str(key)) for key in sample
            if any(has_data(row.get(str(key))) for row in rows)]


def render_table(rows: Sequence[Any], columns: Sequence[tuple[str, str]] | None = None) -> str:
    cleaned = clean_rows(rows, columns)
    if not cleaned:
        return '<p class="muted">暂无数据</p>'
    norm_cols = _normalize_columns(cleaned, columns)
    if not norm_cols:
        return '<p class="muted">暂无数据</p>'
    head = "".join(f"<th>{esc(label or '')}</th>" for _, label in norm_cols)
    body = []
    for row in cleaned:
        cells = "".join(f"<td>{_cell_html(row.get(key))}</td>" for key, _ in norm_cols)
        body.append(f"<tr>{cells}</tr>")
    return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def render_kv_grid(data: Mapping[str, Any]) -> str:
    items = [(k, v) for k, v in data.items() if has_data(v)]
    if not items:
        return ""
    cells = "".join(
        f'<div class="biz-scope-item"><span>{esc(k)}</span><strong>{esc(v)}</strong></div>'
        for k, v in items
    )
    return f'<div class="biz-scope">{cells}</div>'


def render_info_list(data: Mapping[str, Any]) -> str:
    """Professional definition-list for body sections (caliber/source).

    Left label strip (deep-blue accent) + right content that wraps naturally.
    Use this for key/value info inside report body chapters instead of the
    compact card grid, which is reserved for the cover sidebar.
    """
    items = [(k, v) for k, v in data.items() if has_data(v)]
    if not items:
        return ""
    rows = "".join(
        f'<div class="info-row"><span class="info-label">{esc(CALIBER_LABELS.get(k, k))}</span><span class="info-value">{esc(v)}</span></div>'
        for k, v in items
    )
    return f'<div class="info-list">{rows}</div>'


def _visible_metrics(metrics: Any) -> List[Dict[str, Any]]:
    """Only metrics with a real label and a real value (0/False are facts)."""
    return [m for m in as_list(metrics)
            if isinstance(m, dict) and has_data(m.get("label")) and has_data(m.get("value"))]


def render_metrics(metrics: Sequence[Any]) -> str:
    visible = _visible_metrics(metrics)
    if not visible:
        return ""
    cells = []
    for m in visible:
        val = esc(m.get("value", "-"))
        label = esc(m.get("label", ""))
        delta = m.get("delta") or m.get("trend")
        # delta 放在标签下方独立行，无 delta 时预留占位
        tag_html = '<span class="m-tag"></span>'
        if has_data(delta):
            d = str(delta)
            if any(c in d for c in "↑▲增升涨高"):
                tag_html = f'<span class="m-tag t-up">{esc(d)}</span>'
            elif any(c in d for c in "↓▼减降跌低"):
                tag_html = f'<span class="m-tag t-down">{esc(d)}</span>'
            else:
                tag_html = f'<span class="m-tag t-neutral">{esc(d)}</span>'
        hint_html = f'<span class="m-hint">{esc(m["hint"])}</span>' if has_data(m.get("hint")) else ""
        cells.append(f'<div class="biz-metric"><span class="m-name">{label}</span>{tag_html}<span class="m-value">{val}</span>{hint_html}</div>')
    remainder = len(cells) % 4
    if remainder:
        for _ in range(4 - remainder):
            cells.append('<div class="biz-metric-empty"></div>')
    return f'<div class="biz-metrics">{"".join(cells)}</div>' if cells else ""


def _visible_insights(insights: Any) -> List[Dict[str, Any]]:
    """Only insight objects that carry at least one real field."""
    return [item for item in as_list(insights)
            if isinstance(item, dict)
            and any(has_data(item.get(field)) for field in ("feature", "evidence", "interpretation"))]


def render_insights(insights: Sequence[Any]) -> str:
    cards = []
    for item in _visible_insights(insights):
        heading = f'<h3>{esc(item["feature"])}</h3>' if has_data(item.get("feature")) else ""
        evidence = f'<p class="structural-evidence">{esc(item["evidence"])}</p>' if has_data(item.get("evidence")) else ""
        interpretation = f'<p>{esc(item["interpretation"])}</p>' if has_data(item.get("interpretation")) else ""
        cards.append(
            f'''<article class="structural-feature-card">
  {heading}
  {evidence}
  {interpretation}
</article>'''
        )
    return f'<div class="structural-feature-grid">{"".join(cards)}</div>' if cards else ""


def render_records(records: Sequence[Any]) -> str:
    cleaned = clean_rows(records)
    if not cleaned:
        return ""
    return render_table(cleaned, [(str(key), str(key)) for key in cleaned[0].keys()])


# --------------------------------------------------------------------------- #
# ECharts embedding + chart-box rendering
# --------------------------------------------------------------------------- #

ECHARTS_VENDOR_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "assets" / "vendor" / "echarts.min.js"
)


def _load_echarts() -> str:
    """Embed the skill-local library; never generate a hidden online dependency."""
    try:
        javascript = ECHARTS_VENDOR_PATH.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise RuntimeError(f"缺少或无法读取本地 ECharts 图表库：{ECHARTS_VENDOR_PATH}；请恢复 skill 的 assets/vendor/echarts.min.js 后重试。") from exc
    if not javascript.strip():
        raise RuntimeError(f"本地 ECharts 图表库为空：{ECHARTS_VENDOR_PATH}；不能生成离线图表报告。")
    return javascript


# The dependency and DOM can become ready in either order. Retry with a bound,
# recover on script load, and initialize every canvas at most once.
_HANDAAS_CHART_JS = r"""
(function(){
  if(window.__handaas_boot_charts){window.__handaas_boot_charts();return;}
  window.__echarts_instances=window.__echarts_instances||[];
  var status=window.__handaas_chart_status={state:'waiting',total:0,ready:0,failed:0};
  var timer=null, retries=0, MAX_RETRIES=100;
  var layoutRetries=0, MAX_LAYOUT_RETRIES=50;
  var MSG_LOADING='图表库正在加载，数据明细仍可查看。';
  var MSG_MISSING='图表交互不可用，已保留静态图表，数据明细仍可查看。';
  var MSG_LAYOUT='图表等待布局完成后渲染，数据明细仍可查看。';
  var MSG_FAILED='图表交互初始化失败，已保留静态图表，数据明细仍可查看。';
  function summarize(nodes){
    status.total=nodes.length;status.ready=0;status.failed=0;
    nodes.forEach(function(el){
      if(el.getAttribute('data-chart-state')==='ready'){status.ready++;}
      else if(el.getAttribute('data-chart-state')==='error'){status.failed++;}
    });
    status.state=status.ready===status.total?'ready':
      (status.ready+status.failed===status.total?(status.ready?'degraded':'error'):'waiting');
  }
  function frameOf(el){
    var p=el.parentElement;
    if(p&&(' '+p.className+' ').indexOf(' chart-frame ')>-1){return p;}
    return null;
  }
  function setStatus(el,msg){
    var f=frameOf(el);
    if(f){
      var p=f.querySelector('.chart-status');
      if(p){p.textContent=msg;}
    }else{
      el.textContent=msg;
    }
  }
  function raf(fn){if(window.requestAnimationFrame){window.requestAnimationFrame(fn);}else{fn();}}
  function hasGeometry(chart,el){
    if(!chart || chart.getWidth()<2 || chart.getHeight()<2){return false;}
    var svg=el.querySelector('svg');
    if(!svg){return false;}
    return !!svg.querySelector('path,rect,circle,line,text,polygon,polyline');
  }
  function boot(){
    if(document.readyState==='loading'){return;}
    var nodes=document.querySelectorAll('.chart-canvas');
    if(!nodes.length){summarize(nodes);return;}
    if(!window.echarts || typeof window.echarts.init!=='function' || typeof window.handaasChartOption!=='function'){
      var exhausted=retries>=MAX_RETRIES;
      nodes.forEach(function(el){
        if(el.__handaas_chart_ready){return;}
        el.__handaas_dependency_error=true;
        el.setAttribute('data-chart-state',exhausted?'error':'waiting');
        setStatus(el,exhausted?MSG_MISSING:MSG_LOADING);
        var f=frameOf(el);
        if(f){f.setAttribute('data-chart-state',exhausted?'error':'static');}
      });
      summarize(nodes);
      if(!exhausted && timer===null){
        retries++;
        timer=setTimeout(function(){timer=null;boot();},100);
      }
      return;
    }
    if(timer!==null){clearTimeout(timer);timer=null;}
    var pendingLayout=0;
    nodes.forEach(function(el){
      if(el.__handaas_chart_ready){return;}
      var f=frameOf(el);
      if(el.getAttribute('data-chart-state')==='error' && !el.__handaas_dependency_error){return;}
      if(window.ResizeObserver && !el.__handaas_resize_observer){
        el.__handaas_resize_observer=new ResizeObserver(function(){
          var current=el.__handaas_chart;
          if(current && typeof current.resize==='function'){try{current.resize();}catch(ignored){}}
          else{boot();}
        });
        el.__handaas_resize_observer.observe(el);
      }
      if((el.getClientRects && el.getClientRects().length===0) || el.clientWidth<2 || el.clientHeight<2){
        el.setAttribute('data-chart-state','waiting');
        setStatus(el,MSG_LAYOUT);
        if(f){f.setAttribute('data-chart-state','static');}
        pendingLayout++;
        return;
      }
      var chart=null;
      try{
        var raw=el.getAttribute('data-spec');
        if(!raw){throw new Error('Missing chart configuration');}
        // getAttribute already decodes HTML entities; a second decode corrupts labels.
        var spec=JSON.parse(raw);
        var opt=window.handaasChartOption(spec);
        if(!opt){throw new Error('Unsupported chart type');}
        el.textContent='';
        chart=window.echarts.init(el,null,{renderer:'svg'});
        chart.setOption(opt);
        // Real synchronous flush: setOption already flushes internally, but
        // drive the zrender painter once more before the geometry gate. Any
        // throw here falls into catch and keeps the static SVG.
        var zr=chart.getZr();
        if(zr && typeof zr.flush==='function'){zr.flush();}
        // Never reveal the interactive layer (and never hide the static SVG)
        // before real SVG geometry exists on the canvas overlay.
        if(!hasGeometry(chart,el)){throw new Error('Empty chart geometry');}
        el.__handaas_chart=chart;
        el.__handaas_chart_ready=true;
        el.__handaas_dependency_error=false;
        el.setAttribute('data-chart-state','ready');
        window.__echarts_instances.push(chart);
        if(f){
          f.setAttribute('data-chart-state','ready');
          var p=f.querySelector('.chart-status');
          if(p){p.textContent='';}
        }
        raf(function(){try{chart.resize();}catch(ignored){}});
      }catch(e){
        if(chart && typeof chart.dispose==='function'){try{chart.dispose();}catch(ignored){}}
        el.__handaas_dependency_error=false;
        el.setAttribute('data-chart-state','error');
        setStatus(el,MSG_FAILED);
        if(f){f.setAttribute('data-chart-state','error');}
      }
    });
    summarize(nodes);
    if(pendingLayout && layoutRetries<MAX_LAYOUT_RETRIES){
      layoutRetries++;
      setTimeout(boot,100);
    }
  }
  window.__handaas_boot_charts=boot;
  window.__echarts_resize_all=function(){
    (window.__echarts_instances||[]).forEach(function(c){try{c.resize();}catch(e){}});
  };
  function bootAfterLayout(){
    raf(function(){boot();window.__echarts_resize_all();});
  }
  window.addEventListener('resize',window.__echarts_resize_all);
  window.addEventListener('load',bootAfterLayout);
  window.addEventListener('pageshow',bootAfterLayout);
  window.addEventListener('handaas:echarts-ready',bootAfterLayout);
  window.addEventListener('orientationchange',bootAfterLayout);
  // A very late external script can still recover after the bounded retry ends.
  document.addEventListener('load',function(event){
    if(event.target && event.target.tagName==='SCRIPT'){boot();}
  },true);
  document.addEventListener('DOMContentLoaded',bootAfterLayout);
  document.addEventListener('visibilitychange',boot);
  if(document.fonts && document.fonts.ready){document.fonts.ready.then(function(){window.__echarts_resize_all();});}
  [0,300,1000,2500].forEach(function(ms){setTimeout(function(){boot();window.__echarts_resize_all();},ms);});
  boot();
})();
"""





def _chart_fallback_table(rows: Sequence[Mapping[str, Any]], columns: Sequence[tuple[str, str]]) -> str:
    """Data table shown below the chart (also the no-JS fallback)."""
    return f'<details class="chart-data"><summary>数据明细</summary>{render_table(rows, columns)}</details>'


def _looks_temporal(values: Sequence[str]) -> bool:
    """Detect if x-axis values are time-like (years, YYYY-MM, dates)."""
    if not values:
        return False
    import re as _re
    cnt = 0
    for v in values:
        s = str(v).strip()
        # 4-digit year, YYYY-MM, YYYY-MM-DD, or YYYY年
        if _re.match(r"^\d{4}([-/]\d{1,2}([-/]\d{1,2})?)?(年)?$", s):
            cnt += 1
    return cnt >= len(values) * 0.6


def _sort_rows_by_time(rows: Sequence[Mapping[str, Any]], x_key: str) -> list:
    """Sort rows by their x_key value in ascending chronological order.

    Handles years (2017), YYYY-MM (2023-08), YYYY-MM-DD, and YYYY年.
    Returns a new sorted list; original order preserved if not temporal.
    """
    out = list(rows)
    vals = [str(r.get(x_key, "")).strip() for r in out]
    if not _looks_temporal(vals):
        return out

    def sort_key(v: str) -> tuple:
        import re as _re
        s = str(v).strip().rstrip("年")
        # Normalize to comparable tuple of ints
        parts = _re.split(r"[-/]", s)
        try:
            return tuple(int(p) for p in parts)
        except ValueError:
            return (9999,)

    return [r for _, r in sorted(enumerate(out), key=lambda iv: sort_key(vals[iv[0]]))]


def chart_line(title: str, rows: Sequence[Mapping[str, Any]], x_key: str, y_key: str, *, area: bool = False, columns: Sequence[tuple[str, str]] | None = None) -> str:
    cleaned = clean_rows(rows)
    plot_rows = [r for r in cleaned
                 if has_data(r.get(x_key)) and numeric_value(r.get(y_key)) is not None]
    if not plot_rows:
        return ""
    plot_rows = _sort_rows_by_time(plot_rows, x_key)
    cleaned = _sort_rows_by_time(cleaned, x_key)
    cols = _normalize_columns(cleaned, columns or [(x_key, x_key), (y_key, y_key)])
    data = [{"name": text(r.get(x_key)), "value": numeric_value(r.get(y_key))} for r in plot_rows]
    spec = {"type": "line", "area": area, "x": [d["name"] for d in data], "series": [{"name": title, "data": [d["value"] for d in data]}]}
    head = f'<h4 class="chart-title">{esc(title)}</h4>' if title else ""
    return f'<div class="chart-box">{head}<div class="chart-canvas" data-chart="line" data-spec="{esc(json_dumps(spec))}"></div>{_chart_fallback_table(cleaned, cols)}</div>'


def chart_multi_line(title: str, rows: Sequence[Mapping[str, Any]], x_key: str, series_keys: Sequence[str], *, area: bool = False, columns: Sequence[tuple[str, str]] | None = None) -> str:
    cleaned = clean_rows(rows)
    series_keys = [k for k in series_keys if isinstance(k, str) and k]
    plot_rows = [r for r in cleaned
                 if has_data(r.get(x_key))
                 and any(numeric_value(r.get(k)) is not None for k in series_keys)]
    if not series_keys or not plot_rows:
        return ""
    plot_rows = _sort_rows_by_time(plot_rows, x_key)
    cleaned = _sort_rows_by_time(cleaned, x_key)
    series = []
    for key in series_keys:
        values = [numeric_value(r.get(key)) for r in plot_rows]
        if any(value is not None for value in values):
            series.append({"name": key, "data": values})
    if not series:
        return ""
    x = [text(r.get(x_key)) for r in plot_rows]
    spec = {"type": "line", "area": area, "stack": area, "x": x, "series": series}
    cols = _normalize_columns(cleaned, columns or [(x_key, x_key)] + [(k, k) for k in series_keys])
    head = f'<h4 class="chart-title">{esc(title)}</h4>' if title else ""
    return f'<div class="chart-box">{head}<div class="chart-canvas" data-chart="line" data-spec="{esc(json_dumps(spec))}"></div>{_chart_fallback_table(cleaned, cols)}</div>'


def chart_bar(title: str, rows: Sequence[Mapping[str, Any]], name_key: str, value_key: str, *, orient: str = "v", columns: Sequence[tuple[str, str]] | None = None) -> str:
    cleaned = clean_rows(rows)
    plot_rows = [r for r in cleaned
                 if has_data(r.get(name_key)) and numeric_value(r.get(value_key)) is not None]
    if not plot_rows:
        return ""
    cols = _normalize_columns(cleaned, columns or [(name_key, name_key), (value_key, value_key)])
    data = [{"name": text(r.get(name_key)), "value": numeric_value(r.get(value_key))} for r in plot_rows]
    spec = {"type": "bar", "orient": orient, "names": [d["name"] for d in data], "values": [d["value"] for d in data]}
    head = f'<h4 class="chart-title">{esc(title)}</h4>' if title else ""
    return f'<div class="chart-box">{head}<div class="chart-canvas" data-chart="bar" data-spec="{esc(json_dumps(spec))}"></div>{_chart_fallback_table(cleaned, cols)}</div>'


def chart_pie(title: str, rows: Sequence[Mapping[str, Any]], name_key: str, value_key: str, *, donut: bool = False, columns: Sequence[tuple[str, str]] | None = None) -> str:
    cleaned = clean_rows(rows)
    plot_rows = [r for r in cleaned
                 if has_data(r.get(name_key)) and numeric_value(r.get(value_key)) is not None]
    if not plot_rows:
        return ""
    cols = _normalize_columns(cleaned, columns or [(name_key, name_key), (value_key, value_key)])
    data = [{"name": text(r.get(name_key)), "value": numeric_value(r.get(value_key))} for r in plot_rows]
    spec = {"type": "pie", "donut": donut, "data": data}
    head = f'<h4 class="chart-title">{esc(title)}</h4>' if title else ""
    return f'<div class="chart-box">{head}<div class="chart-canvas" data-chart="pie" data-spec="{esc(json_dumps(spec))}"></div>{_chart_fallback_table(cleaned, cols)}</div>'


def chart_gauge(title: str, value: float, *, level: str = "", max_val: float = 100.0) -> str:
    val = numeric_value(value)
    if val is None:
        return ""
    max_value = max_val if isinstance(max_val, (int, float)) and not isinstance(max_val, bool) else numeric_value(max_val)
    if max_value is None:
        max_value = 100
    spec = {"type": "gauge", "value": val, "max": max_value,
            "level": text(level) if has_data(level) else ""}
    return f'<div class="chart-box chart-box-gauge"><div class="chart-canvas" data-chart="gauge" data-spec="{esc(json_dumps(spec))}"></div></div>'


def chart_radar(title: str, indicators: Sequence[Mapping[str, Any]], series: Sequence[Mapping[str, Any]]) -> str:
    """indicators: [{name, max}], series: [{name, value:[...]}]"""
    clean_indicators = []
    for item in indicators or []:
        if not isinstance(item, dict) or not has_data(item.get("name")):
            continue
        max_value = numeric_value(item.get("max"))
        clean_indicators.append({"name": text(item["name"]), "max": max_value if max_value is not None else 100.0})
    clean_series = []
    for item in series or []:
        if not isinstance(item, dict):
            continue
        values = [numeric_value(value) for value in as_list(item.get("value"))]
        if any(value is not None for value in values):
            clean_series.append({"name": text(item.get("name")), "value": values})
    if not clean_indicators or not clean_series:
        return ""
    spec = {"type": "radar", "indicators": clean_indicators, "series": clean_series}
    return f'<div class="chart-box"><div class="chart-canvas" data-chart="radar" data-spec="{esc(json_dumps(spec))}"></div></div>'


# --------------------------------------------------------------------------- #
# Core analysis rendering — domain-driven via section specs
# --------------------------------------------------------------------------- #

def _section_note_text(value: Any, rows: Sequence[Any]) -> str:
    if not has_data(value):
        return ""
    note = str(value)
    if rows:
        for marker, unit in (("展示前 N 条", "条"), ("展示前若干条", "条"),
                             ("展示前 N 个", "个"), ("展示前 N 家", "家")):
            note = note.replace(marker, f"展示前 {len(rows)} {unit}")
    return note


def _render_one_section(core: Mapping[str, Any], spec: Mapping[str, Any]) -> str:
    """Render a single section spec into HTML (without pairing). Returns '' if empty."""
    if not section_has_data(core, spec):
        return ""
    key = str(spec.get("key") or "")
    body = core.get(key)
    kind = spec.get("kind")
    title = spec.get("title", "")
    title_html = f'<h3 class="biz-subsection-title">{esc(title)}</h3>' if has_data(title) else ""

    rows = clean_rows(as_list(body), spec.get("columns")) if kind in ("line", "bar", "pie", "donut", "multi_line", "table", None) else []
    note_text = _section_note_text(spec.get("note"), rows)
    note = f'<p class="biz-section-note">{esc(note_text)}</p>' if has_data(note_text) else ""

    if kind == "text":
        body_text = compact(body)
        if not has_data(body_text):
            return ""
        return f'{title_html}{note}<p class="biz-summary">{esc(compact(body, limit=1500))}</p>'
    elif kind == "tags":
        items = tag_items(body)
        if not items:
            return ""
        labels = "".join(f"<li>{esc(item)}</li>" for item in items)
        return f'{title_html}{note}<ul class="tag-row">{labels}</ul>'
    elif kind == "kv":
        info_html = render_info_list(body) if isinstance(body, dict) else ""
        if not info_html:
            return ""
        return f'{title_html}{note}{info_html}'
    elif kind in ("line", "bar", "pie", "donut", "multi_line"):
        if not rows:
            return ""
        ch = spec.get("chart") or {}
        norm_cols = _normalize_columns(rows, spec.get("columns"))
        x_key = ch.get("x") or (norm_cols[0][0] if norm_cols else "")
        y_key = ch.get("y") or (norm_cols[1][0] if len(norm_cols) > 1 else "")
        name_key = ch.get("name") or (norm_cols[0][0] if norm_cols else "")
        value_key = ch.get("value") or (norm_cols[1][0] if len(norm_cols) > 1 else "")
        chart_html = ""
        if kind == "line":
            chart_html = chart_line(spec.get("title", ""), rows, x_key, y_key, area=ch.get("area", False), columns=norm_cols)
        elif kind == "multi_line":
            chart_html = chart_multi_line(spec.get("title", ""), rows, x_key, ch.get("series", []), area=ch.get("area", False), columns=norm_cols)
        elif kind == "bar":
            chart_html = chart_bar(spec.get("title", ""), rows, name_key, value_key, orient=ch.get("orient", "v"), columns=norm_cols)
        else:
            chart_html = chart_pie(spec.get("title", ""), rows, name_key, value_key, donut=(kind == "donut" or bool(ch.get("donut"))), columns=norm_cols)
        if not chart_html:
            table_html = render_table(rows, spec.get("columns") or norm_cols)
            return f'{title_html}{note}{table_html}' if table_html else ""
        return f'{note}{chart_html}'
    elif kind == "gauge":
        if not isinstance(body, dict):
            return ""
        ch = spec.get("chart") or {}
        value_key = ch.get("value_key", "风险评分")
        level_key = ch.get("level_key", "风险等级")
        val = numeric_value(body.get(value_key))
        level = text(body.get(level_key)) if has_data(body.get(level_key)) else ""
        info_items = {k: v for k, v in body.items() if has_data(v)}
        info_html = render_info_list(info_items) if info_items else ""
        if val is None:
            # A missing score must not become a 0 gauge; keep the real KV only.
            return f'{title_html}{note}{info_html}' if info_html else ""
        max_value = numeric_value(ch.get("max"))
        if max_value is None:
            max_value = 100
        after = "" if spec.get("pair_with") else info_html
        return f'{title_html}{note}{chart_gauge(spec.get("title", ""), val, level=level, max_val=max_value)}{after}'
    elif kind == "radar":
        ch = spec.get("chart") or {}
        indicators = ch.get("indicators") or (body.get("indicators") if isinstance(body, dict) else []) or []
        series = ch.get("series") or (body.get("series") if isinstance(body, dict) else []) or []
        radar_html = chart_radar(spec.get("title", ""), indicators, series)
        if not radar_html:
            return ""
        return f'{title_html}{note}{radar_html}'
    elif kind == "table" or kind is None:
        if not rows:
            return ""
        table_html = render_table(rows, spec.get("columns") or _normalize_columns(rows, None))
        if not table_html:
            return ""
        return f'{title_html}{note}{table_html}'
    return ""


def render_core_analysis_html(core: Mapping[str, Any], sections: Sequence[Mapping[str, Any]]) -> str:
    """Render section specs. Supports ``pair_with`` to place two lightweight
    sections (e.g. gauge + radar) side-by-side in a two-column layout, avoiding
    large whitespace from single-chart sections.
    """
    specs = [spec for spec in sections or [] if isinstance(spec, dict)]
    blocks: list[str] = []
    skip_keys: set[str] = set()
    for i, spec in enumerate(specs):
        key = spec.get("key")
        if key in skip_keys:
            continue
        # Check if this section wants to pair with the next one
        pair_target = spec.get("pair_with")
        if pair_target:
            # Find the pairing section (usually the next one)
            paired_spec = None
            for candidate in specs[i + 1:]:
                if candidate.get("key") == pair_target:
                    paired_spec = candidate
                    skip_keys.add(pair_target)
                    break
            if paired_spec is not None:
                left = _render_one_section(core, spec)
                right = _render_one_section(core, paired_spec)
                if left and right:
                    blocks.append(f'<div class="biz-pair"><div>{left}</div><div>{right}</div></div>')
                elif left:
                    blocks.append(left)
                elif right:
                    blocks.append(right)
                continue
        html = _render_one_section(core, spec)
        if html:
            blocks.append(html)
    return "\n".join(blocks)


# --------------------------------------------------------------------------- #
# Section catalogue (domain adapter) — overridden per skill via get_sections
# --------------------------------------------------------------------------- #

# Per-skill section catalogue. Empty here: this skill drives sections entirely
# via core_analysis.sections in compose_report.py, with a generic heuristic
# fallback below (list-of-dict -> table, non-empty string -> text).
SECTION_CATALOGUE: List[Dict[str, Any]] = []

# Map internal caliber keys to reader-facing Chinese labels, so raw English
# field names (match_target / match_type / data_scope / limit) never leak into
# the rendered report.
CALIBER_LABELS = {
    "match_target": "匹配对象",
    "match_type": "匹配方式",
    "data_scope": "数据范围",
    "limit": "局限说明",
}


def get_sections(data: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Return core_analysis section specs derived from the payload.

    If `core_analysis.sections` is provided, use it. Otherwise use this skill's
    SECTION_CATALOGUE. Fallback heuristic: each list-of-dict key becomes a
    table; each non-empty string becomes a text section.
    """
    core = data.get("core_analysis") or {}
    if isinstance(core, dict) and isinstance(core.get("sections"), list):
        return core["sections"]
    if SECTION_CATALOGUE:
        return SECTION_CATALOGUE
    # Fallback: derive sections from core keys. Use a title map so section
    # headers are Chinese, not raw English snake_case keys.
    _TITLE_MAP = {
        "enterprise_base": "企业基本信息", "identity_tags": "身份标签",
        "description": "企业简介", "business": "经营范围", "tags": "标签",
        "holders": "股东信息", "investments": "对外投资",
        "branches": "分支机构", "key_persons": "主要人员",
        "shareholder_top": "股东持股排行", "shareholding_type": "股东类型分布",
        "shareholder_entity": "股东性质分布",
    }
    sections = []
    for key, value in core.items():
        if key == "sections":
            continue
        title = _TITLE_MAP.get(key, key.replace("_", " ").title())
        if isinstance(value, list) and value and isinstance(value[0], dict):
            sections.append({"key": key, "title": title, "kind": "table"})
        elif isinstance(value, str) and value.strip():
            sections.append({"key": key, "title": title, "kind": "text"})
        elif isinstance(value, list) and value:
            sections.append({"key": key, "title": title, "kind": "table"})
    return sections



# --------------------------------------------------------------------------- #
# Professional terms glossary
# --------------------------------------------------------------------------- #

_GLOSSARY = {
    "CR3": "行业集中度指标（Concentration Ratio 3），指排名前 3 的类别合计占总量的百分比。CR3 越高说明分布越集中，反之越分散。常用于衡量城市、品牌、产品类型等的集中程度。",
    "CR2": "行业集中度指标（Concentration Ratio 2），指排名前 2 的类别合计占总量的百分比。含义同 CR3，但仅统计前 2 名。",
    "CR5": "行业集中度指标（Concentration Ratio 5），指排名前 5 的类别合计占总量的百分比。",
    "同比": "与上一年度同一时期对比的变化率。例如 2024 年同比增长率 = (2024 年值 - 2023 年值) / 2023 年值 * 100%。",
    "环比": "与紧邻的上一时期（上月/上季）对比的变化率。例如 7 月环比 = (7 月值 - 6 月值) / 6 月值 * 100%。",
    "分位": "统计学概念，指某数值在全体样本中的相对位置。如「前 1.4% 分位」表示该数值高于 98.6% 的样本。",
    "SKU": "库存量单位（Stock Keeping Unit），商品的最小分类单元，每个 SKU 对应一种独立的商品规格。",
}


def _detect_glossary_terms(report: Mapping[str, Any]) -> list:
    """Scan visible report text for professional terms. Returns [(term, definition), ...]."""
    parts: list[str] = []
    if has_data(report.get("abstract")):
        parts.append(str(report["abstract"]))
    if has_data(report.get("summary")):
        parts.append(str(report["summary"]))
    for item in as_list(report.get("executive_summary")):
        if has_data(item):
            parts.append(str(item))
    for insight in _visible_insights(report.get("insights")):
        for field in ("evidence", "interpretation", "feature"):
            if has_data(insight.get(field)):
                parts.append(str(insight[field]))
    for metric in _visible_metrics(report.get("metrics")):
        for field in ("label", "hint", "delta"):
            if has_data(metric.get(field)):
                parts.append(str(metric[field]))
    core = report.get("core_analysis", {})
    if isinstance(core, Mapping):
        for section in visible_sections(core, core.get("sections")):
            for field in ("note", "summary"):
                if has_data(section.get(field)):
                    parts.append(str(section[field]))
    combined = " ".join(parts)
    found = []
    for term, definition in _GLOSSARY.items():
        if term in combined:
            found.append((term, definition))
    return found


# --------------------------------------------------------------------------- #
# HTML document
# --------------------------------------------------------------------------- #

def render_html(data: Dict[str, Any], *, sections: Sequence[Mapping[str, Any]] | None = None) -> str:
    title = infer_title(data)
    banner = infer_banner(data)
    ds = data.get("data_source") if isinstance(data.get("data_source"), Mapping) else {}
    semantic_review = ds.get("semantic_review") if isinstance(ds.get("semantic_review"), Mapping) else {}
    review_pending = semantic_review.get("status") == "pending"
    generated_at = compact(ds.get("generated_at") or dt.datetime.now().astimezone().isoformat(timespec="seconds"))
    try:
        _gd = dt.datetime.fromisoformat(str(generated_at).replace("Z", "+00:00"))
        generated_at_cn = f"{_gd.year}年{_gd.month:02d}月{_gd.day:02d}日"
    except Exception:
        generated_at_cn = str(generated_at)[:10] if len(str(generated_at)) >= 10 else str(generated_at)
    subject = data.get("subject") if isinstance(data.get("subject"), Mapping) else {}
    abstract = compact(data.get("abstract") or data.get("summary") or "")
    exec_summary = [line for line in as_list(data.get("executive_summary")) if has_data(line)]
    metrics = as_list(data.get("metrics"))
    caliber = data.get("caliber") if isinstance(data.get("caliber"), Mapping) else {}
    core = data.get("core_analysis") if isinstance(data.get("core_analysis"), Mapping) else {}
    records = clean_rows(as_list(data.get("representative_records")))
    insights = _visible_insights(data.get("insights"))
    if sections is None:
        sections = get_sections(data)
    else:
        sections = list(sections)
    core_sections = visible_sections(core, sections)

    metrics_html = render_metrics(metrics)
    core_html = render_core_analysis_html(core, core_sections)
    records_html = render_records(records)
    insights_html = render_insights(insights)
    summary_text = abstract
    has_summary = has_data(summary_text)
    has_metrics = bool(metrics_html)
    has_core = bool(core_html)
    has_records = bool(records_html)
    has_insights = bool(insights_html)

    match_target = (caliber.get("match_target") or subject.get("enterprise")
                    or subject.get("matchKeyword") or subject.get("match_raw"))
    caliber_items: Dict[str, Any] = {}
    for label, value in (
        ("匹配对象", match_target),
        ("匹配方式", caliber.get("match_type")),
        ("数据范围", caliber.get("data_scope")),
        ("局限说明", caliber.get("limit")),
    ):
        if has_data(value):
            caliber_items[label] = value
    has_caliber = bool(caliber_items)

    toc_entries: List[str] = []
    if has_summary:
        toc_entries.append("报告摘要")
    if has_caliber:
        toc_entries.append("查询对象与口径")
    if has_metrics:
        toc_entries.append("数据总览")
    if has_core:
        toc_entries.append("核心分析")
    if has_records:
        toc_entries.append("代表性记录")
    if has_insights:
        toc_entries.append("特征与洞察")
    toc_html = "".join(f"<li><span>{esc(item)}</span></li>" for item in toc_entries)
    toc_block = f'<h3 class="toc-title">目录</h3><ol class="toc-list">{toc_html}</ol>' if toc_entries else ""

    scope_items = list(caliber_items.items())
    scope_html = "".join(
        f'<div class="biz-scope-item"><span>{esc(label)}</span><strong>{esc(value)}</strong></div>'
        for label, value in scope_items
    )
    scope_block = f'<h3 class="scope-title">报告口径</h3><div class="biz-scope">{scope_html}</div>' if scope_items else ""

    exec_html = "".join(f"<li>{esc(line)}</li>" for line in exec_summary)
    exec_block = f'<ul class="exec-list">{exec_html}</ul>' if exec_html else ""
    lead_html = f'<p class="report-lead">{esc(compact(summary_text, limit=400))}</p>' if has_summary else ""

    cover_insights = insights[:4]
    findings_html = ""
    if cover_insights:
        cards = []
        for ins in cover_insights:
            feature = ins.get("feature") if has_data(ins.get("feature")) else ""
            evidence = ins.get("evidence") if has_data(ins.get("evidence")) else ""
            interpretation = compact(ins.get("interpretation"), limit=80) if has_data(ins.get("interpretation")) else ""
            body_text = evidence or interpretation
            if not body_text:
                continue
            label_html = f'<p class="f-label">{esc(feature)}</p>' if feature else ""
            cards.append(f'<div class="finding-card">{label_html}<p class="f-text">{esc(body_text)}</p></div>')
        if cards:
            findings_html = f'<div class="cover-findings"><h3 class="cover-findings-title">核心发现</h3><div class="cover-findings-grid">{"".join(cards)}</div></div>'

    dim_titles = list(dict.fromkeys(
        str(spec.get("title")) for spec in core_sections if has_data(spec.get("title"))
    ))
    _dim_tags = "".join(f'<span class="dim-tag">{esc(t)}</span>' for t in dim_titles)
    dimensions_html = ""
    if _dim_tags and has_core:
        dimensions_html = f'<div class="cover-dimensions"><span class="dim-label">分析维度</span><div class="dim-tags">{_dim_tags}</div></div>'

    scope_parts = []
    products = tag_items(caliber.get("products")) if has_data(caliber.get("products")) else []
    if products:
        scope_parts.append(("数据产品", "、".join(products)))
    if has_data(caliber.get("data_scope")):
        scope_parts.append(("覆盖范围", str(caliber["data_scope"])[:80]))
    scope_bar_html = ""
    if scope_parts:
        _scope_items = "".join(f'<span class="scope-item"><em>{esc(label)}</em>{esc(value)}</span>' for label, value in scope_parts)
        scope_bar_html = f'<div class="cover-scope-bar">{_scope_items}</div>'

    intro_html = ""
    if has_summary or has_metrics or has_core or has_records or has_insights or has_caliber:
        intro_html = '<div class="abstract-box">报告涵盖查询对象、数据总览、核心分析、代表性记录与结构洞察，所有数据均标注口径与来源。</div>'

    def _page(heading: str, note: str, tag: str, body: str) -> str:
        return (
            f'<section class="report-page biz-section">'
            f'<div class="report-topbar"><div class="report-stripe"></div><div class="report-banner">{esc(banner)}</div></div>'
            f'<div class="biz-section-head"><div><h2>{esc(heading)}</h2><p class="biz-section-note">{esc(note)}</p></div>'
            f'<span class="biz-tag">{esc(tag)}</span></div>{body}</section>'
        )

    summary_section = ""
    if has_summary:
        summary_section = _page("报告摘要", "概述分析对象、数据覆盖范围与核心发现。", "Summary", f'<p class="biz-summary">{esc(summary_text)}</p>')
    caliber_section = ""
    if has_caliber:
        caliber_section = _page("查询对象与口径", "说明匹配对象、匹配方式与数据范围。", "Caliber", render_info_list(caliber_items))
    metrics_section = _page("数据总览", "关键指标一览。", "Metrics", metrics_html) if has_metrics else ""
    core_section = _page("核心分析", "按维度展开目标核心数据与结构化明细。", "Analysis", core_html) if has_core else ""
    records_section = _page("代表性记录", "关键明细记录 Top N。", "Records", records_html) if has_records else ""
    insights_section = _page("特征与洞察", "基于数据的结构化解读。", "Insights", insights_html) if has_insights else ""

    _glossary_terms = _detect_glossary_terms(data)
    glossary_html = ""
    if _glossary_terms:
        _glossary_rows = "".join(
            f'<div class="glossary-row"><span class="glossary-term">{esc(t)}</span><span class="glossary-def">{esc(d)}</span></div>'
            for t, d in _glossary_terms
        )
        glossary_html = (
            f'<section class="report-page biz-section">'
            f'<div class="report-topbar"><div class="report-stripe"></div>'
            f'<div class="report-banner">{esc(banner)}</div></div>'
            f'<div class="biz-section-head"><div><h2>名词解释</h2>'
            f'<p class="biz-section-note">报告中涉及的专业术语与统计指标说明。</p></div>'
            f'<span class="biz-tag">Glossary</span></div>'
            f'<div class="glossary-list">{_glossary_rows}</div>'
            f'</section>'
        )

    review_banner = ""
    if review_pending:
        review_banner = (
            '<div class="draft-review-banner" style="margin:0 0 18px;padding:12px 16px;'
            'border:1px solid #d97706;border-left:6px solid #d97706;background:#fff8eb;'
            'color:#92400e;font-size:14px;font-weight:800;line-height:1.7;">'
            '数据草稿（待AI语义审校）：本报告尚未完成当前 AI 智能体的语义审校，请完成审校后再对外交付。</div>'
        )

    return embed_static_charts(f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <link rel="icon" href="data:," />
  <title>{esc(title)}</title>
  <style>
    :root {{ --bg:#eef1f5; --card:#ffffff; --text:#1f2937; --muted:#64748b; --line:#d8dee8; --blue:#003b71; --deep:#003b71; --red:#7f1d1d; --gold:#d97706; --stripe:#666; }}
    * {{ box-sizing:border-box; letter-spacing:0 !important; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font-family:"Songti SC","STSong","Noto Serif CJK SC","Source Han Serif SC","SimSun",serif; line-height:1.78; }}
    .research-doc {{ width:min(1120px,100%); margin:0 auto 42px; padding:0 22px; }}
    .report-page {{ position:relative; width:min(1080px,calc(100vw - 32px)); margin:28px auto; background:#fff; border:1px solid #a8adb5; box-shadow:0 18px 44px rgba(15,23,42,.13); }}
    .report-topbar {{ display:flex; align-items:stretch; height:54px; border-bottom:1px solid #333; }}
    .report-stripe {{ flex:1; background:repeating-linear-gradient(0deg,#575757 0,#575757 2px,#747474 2px,#747474 4px); }}
    .report-banner {{ width:420px; display:flex; align-items:center; justify-content:flex-end; padding:0 24px; color:#fff; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; font-weight:800; letter-spacing:.08em; background:linear-gradient(135deg,#0b4c84,#0284c7 48%,#0b2f55); }}
    .cover-grid {{ display:grid; grid-template-columns:330px minmax(0,1fr); min-height:860px; }}
    .cover-side {{ padding:86px 34px 96px; border-right:0; position:relative; }}
    .cover-side-title {{ color:var(--red); font-size:32px; font-weight:800; letter-spacing:.08em; margin:0 0 12px; }}
    .cover-date {{ margin:0 0 60px; color:#111827; font-size:17px; font-weight:700; }}
    .toc-title,.scope-title {{ margin:0 0 12px; color:#111827; font-size:19px; font-weight:800; border-bottom:2px solid var(--red); padding-bottom:8px; }}
    .toc-list {{ list-style:none; padding:0; margin:0 0 56px; }}
    .toc-list li {{ border-bottom:1px solid #e5e7eb; padding:9px 0; color:#475569; font-size:14px; }}
    .toc-list span {{ display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
    .cover-brand {{ position:absolute; left:38px; bottom:38px; color:#7a7a7a; font-size:24px; font-weight:900; letter-spacing:.02em; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }}
    .cover-main {{ padding:72px 54px 54px 34px; }}
    .report-kicker {{ margin:0 0 16px; color:var(--deep); font-size:16px; font-weight:800; letter-spacing:.16em; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }}
    h1 {{ margin:0 0 20px; color:#0b3768; font-size:36px; line-height:1.25; letter-spacing:-.03em; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }}
    .report-lead {{ margin:0 0 22px; color:#0b3768; font-size:19px; line-height:1.85; font-weight:800; }}
    .abstract-box {{ border-left:5px solid var(--gold); background:#fff8eb; padding:16px 18px; color:#9a4b00; font-size:17px; line-height:1.8; font-style:italic; margin:22px 0 24px; }}
    .biz-metrics {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:1px; margin:22px 0; background:#d6dee9; border:1px solid #d6dee9; }}
    .biz-metric {{ background:#fff; padding:16px 14px; display:flex; flex-direction:column; min-height:88px; }}
    .biz-metric-empty {{ background:#f8fafc; visibility:hidden; }}
    .biz-metric .m-name {{ color:#64748b; font-size:12px; font-weight:700; line-height:1.2; margin-bottom:4px; }}
    .biz-metric .m-tag {{ font-size:11px; font-weight:800; line-height:1; margin-bottom:10px; display:block; min-height:11px; }}
    .biz-metric .m-tag.t-up {{ color:#16a34a; }}
    .biz-metric .m-tag.t-down {{ color:#dc2626; }}
    .biz-metric .m-tag.t-neutral {{ color:#6366f1; }}
    .biz-metric .m-value {{ color:#0b3768; font-size:26px; line-height:1.1; font-weight:900; letter-spacing:-.02em; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; margin-top:auto; }}
    .biz-metric .m-hint {{ color:#64748b; font-size:11px; line-height:1.5; margin-top:6px; text-align:left; }}
    .biz-metric .m-value .m-tag.t-up {{ color:#16a34a; }}
    .biz-metric .m-value .m-tag.t-down {{ color:#dc2626; }}
    .biz-metric .m-value .m-tag.t-neutral {{ color:#6366f1; }}
    .exec-list {{ list-style:none; padding:0; margin:18px 0 0; }}
    .exec-list li {{ position:relative; padding:8px 0 8px 22px; color:#334155; font-size:14px; line-height:1.7; border-bottom:1px solid #eef2f7; }}
    .exec-list li:last-child {{ border-bottom:0; }}
    .exec-list li::before {{ content:""; position:absolute; left:0; top:14px; width:8px; height:8px; background:#0b5ca8; border-radius:50%; }}
    .cover-findings {{ margin:20px 0 0; }}
    .cover-findings-title {{ margin:0 0 12px; color:#0b3768; font-size:16px; font-weight:800; border-bottom:2px solid #0b5ca8; padding-bottom:6px; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }}
    .cover-findings-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:10px; }}
    .finding-card {{ border:1px solid #d6e3f0; border-left:4px solid #0b5ca8; background:#f8fbff; padding:10px 12px; }}
    .finding-card .f-label {{ color:#0b5ca8; font-size:12px; font-weight:800; margin:0 0 4px; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }}
    .finding-card .f-text {{ color:#334155; font-size:12.5px; line-height:1.6; margin:0; }}
    .cover-dimensions {{ margin:18px 0 0; display:flex; align-items:flex-start; gap:8px; }}
    .cover-dimensions .dim-label {{ flex-shrink:0; color:#0b3768; font-size:13px; font-weight:800; line-height:1.9; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }}
    .cover-dimensions .dim-tags {{ display:flex; flex-wrap:wrap; gap:4px; }}
    .dim-tag {{ display:inline-block; background:#eaf4ff; color:#0b5ca8; padding:3px 10px; font-size:11.5px; border-radius:12px; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; white-space:nowrap; }}
    .cover-scope-bar {{ margin:14px 0 0; padding:10px 14px; background:#f8fafc; border-left:4px solid #0b5ca8; display:flex; flex-direction:column; gap:4px; }}
    .cover-scope-bar .scope-item {{ font-size:12px; color:#334155; line-height:1.6; }}
    .cover-scope-bar .scope-item em {{ font-style:normal; color:#0b5ca8; font-weight:700; margin-right:6px; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }}
    .report-page.biz-section {{ padding:0 30px 34px; }}
    .report-page.biz-section > .report-topbar {{ margin:0 -30px 30px; }}
    .biz-section-head {{ display:flex; justify-content:space-between; gap:16px; align-items:flex-start; margin-bottom:18px; }}
    .biz-section h2 {{ margin:0; color:#0b3768; font-size:26px; letter-spacing:.03em; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }}
    .biz-section-note {{ margin:6px 0 0; color:var(--muted); font-size:13px; }}
    .biz-tag {{ background:#eaf4ff; color:#0b3768; padding:5px 10px; font-size:12px; font-weight:800; white-space:nowrap; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }}
    .biz-summary {{ font-size:16px; color:#243041; text-align:justify; }}
    .biz-scope {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; margin-top:18px; }}
    .cover-side .biz-scope {{ grid-template-columns:1fr; }}
    .biz-scope-item {{ border:1px solid #d6dee9; background:#f8fbff; padding:12px; }}
    .biz-scope-item span {{ display:block; color:#64748b; font-size:12px; font-weight:700; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }}
    .biz-scope-item strong {{ display:block; margin-top:4px; color:#0b3768; font-size:13px; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }}
    .info-list {{ border:1px solid #d6dee9; background:#fff; }}
    .info-row {{ display:grid; grid-template-columns:148px minmax(0,1fr); gap:0; align-items:stretch; border-bottom:1px solid #e8edf3; }}
    .info-row:last-child {{ border-bottom:0; }}
    .info-label {{ display:flex; align-items:center; gap:8px; padding:13px 14px; background:#f1f6fb; color:#0b3768; font-size:13px; font-weight:800; border-right:3px solid #0b5ca8; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }}
    .info-value {{ padding:13px 16px; color:#334155; font-size:13.5px; line-height:1.78; overflow-wrap:anywhere; }}
    .biz-subsection-title {{ margin:26px 0 12px; padding-left:12px; border-left:4px solid #0b5ca8; color:#0b3768; font-size:18px; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }}
    .biz-pair {{ display:grid; grid-template-columns:1fr 1fr; gap:24px; align-items:stretch; }}
    .biz-pair > div {{ display:flex; flex-direction:column; }}
    .biz-pair .biz-subsection-title {{ margin-top:0; }}
    .chart-box-gauge {{ width:100%; margin:0 auto; }}
    .chart-box {{ min-height:300px; }}
    .biz-pair .chart-box {{ flex:1; min-height:340px; }}
    .biz-pair .chart-canvas {{ height:300px !important; }}
    .biz-pair .table-wrap {{ flex:1; }}
    .biz-pair .info-list {{ flex:1; }}
    @media (max-width:860px) {{ .biz-pair {{ grid-template-columns:1fr; }} }}
    .tag-row {{ background:#f8fbff; border:1px solid #d6dee9; padding:14px 16px; color:#0b3768; font-size:14px; line-height:1.8; }}
    .structural-feature-grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; }}
    .structural-feature-card {{ border:1px solid #cbd5e1; background:#fff; padding:16px; }}
    .structural-feature-card h3 {{ margin:0 0 10px; color:#0b3768; font-size:17px; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }}
    .structural-feature-card p {{ margin:8px 0 0; color:#475569; font-size:13px; line-height:1.7; }}
    .structural-feature-card .structural-evidence {{ border-left:4px solid #d97706; background:#fff8eb; padding:9px 11px; color:#92400e; font-weight:700; }}
    .table-wrap {{ overflow:auto; border:1px solid #0b3768; }}
    table {{ width:100%; border-collapse:collapse; background:white; }}
    th,td {{ padding:12px 13px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; font-size:13px; overflow-wrap:anywhere; }}
    th {{ background:#0b3768; color:white; font-weight:800; white-space:nowrap; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }}
    tbody tr:nth-child(even) td {{ background:#dff1fb; }}
    tr:last-child td {{ border-bottom:0; }}
    .source-link {{ color:#0b5ca8; font-weight:800; text-decoration:none; white-space:nowrap; }}
    ul {{ margin:0 0 0 20px; padding:0; }} li {{ margin:7px 0; }}
    .muted {{ color:var(--muted); }}
    footer {{ width:min(1080px,100%); margin:0 auto; padding:0 24px 36px; color:#64748b; font-size:12px; }}
    @media (max-width: 860px) {{ .cover-grid {{ grid-template-columns:1fr; }} .cover-side {{ padding:34px; }} .cover-brand {{ position:static; margin-top:32px; }} .biz-metrics,.biz-scope {{ grid-template-columns:1fr 1fr; }} .table-wrap table {{ min-width:720px; }} .structural-feature-grid {{ grid-template-columns:1fr; }} h1 {{ font-size:30px; }} .report-banner {{ width:55%; }} }}
    @page {{ size:A4; margin:12mm; }}
    @media print {{
      body {{ background:#fff; line-height:1.55; }}
      .report-page {{ width:100%; margin:0; box-shadow:none; break-after:page; }}
      .research-doc {{ width:100%; padding:0; margin:0; }}
      .cover-grid {{ grid-template-columns:190px minmax(0,1fr); min-height:690px; }}
      .cover-side {{ padding:34px 18px 60px; }}
      .cover-side-title {{ font-size:24px; }}
      .cover-date {{ margin-bottom:28px; font-size:13px; }}
      .toc-title,.scope-title {{ font-size:14px; padding-bottom:5px; }}
      .toc-list {{ margin-bottom:24px; }}
      .toc-list li {{ padding:5px 0; font-size:10px; }}
      .cover-brand {{ left:20px; bottom:20px; font-size:16px; }}
      .cover-main {{ padding:38px 28px 28px 22px; }}
      .report-kicker {{ margin-bottom:10px; font-size:11px; }}
      h1 {{ margin-bottom:12px; font-size:27px; }}
      .report-lead {{ margin-bottom:12px; font-size:12px; line-height:1.65; }}
      .abstract-box {{ margin:12px 0 14px; padding:10px 12px; font-size:10px; line-height:1.55; }}
      .biz-metrics {{ gap:6px; margin:12px 0; }}
      .biz-metric {{ padding:8px; }}
      .biz-metric strong {{ font-size:16px; }}
      .biz-metric span {{ margin-top:4px; font-size:8px; }}
      .info-label {{ padding:7px 9px; font-size:9px; border-right-width:2px; }}
      .info-value {{ padding:7px 9px; font-size:9.5px; line-height:1.5; }}
      .info-row {{ grid-template-columns:108px minmax(0,1fr); }}
      .report-page.biz-section {{ padding:0 18px 22px; }}
      .report-page.biz-section > .report-topbar {{ margin:0 -18px 18px; }}
      .biz-section-head {{ margin-bottom:12px; }}
      .biz-section h2 {{ font-size:20px; }}
      .biz-section-note,.biz-tag {{ font-size:9px; }}
      .table-wrap {{ overflow:visible; }}
      .table-wrap table {{ min-width:0 !important; }}
      th,td {{ padding:6px 7px; font-size:8.5px; line-height:1.45; }}
      .structural-feature-card,.biz-section {{ break-inside:avoid; }}
      .structural-feature-grid {{ gap:8px; }}
      .structural-feature-card {{ padding:10px; }}
      .structural-feature-card p {{ font-size:9px; line-height:1.45; }}
      footer {{ display:none; }}
    }}
    .chart-box {{ border:1px solid #d6dee9; background:#fff; padding:16px; margin:10px 0 16px; }}
    .chart-box-gauge {{ max-width:380px; }}
    .chart-title {{ margin:0 0 8px; color:#0b3768; font-size:15px; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }}
    .chart-canvas {{ width:100%; height:300px; }}
    .chart-canvas[data-chart-state="waiting"],.chart-canvas[data-chart-state="error"] {{ display:flex; align-items:center; justify-content:center; color:#64748b; font-size:13px; text-align:center; }}
    .chart-box-gauge .chart-canvas {{ height:240px; }}
    .chart-data {{ margin-top:10px; }}
    .chart-data summary {{ cursor:pointer; color:#0b5ca8; font-size:12px; font-weight:700; padding:4px 0; }}
    .metric-rich {{ border:1px solid #cbd5e1; background:#f8fbff; padding:14px; position:relative; }}
    .metric-rich strong {{ display:block; color:#0b3768; font-size:25px; line-height:1; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }}
    .metric-rich .m-label {{ display:block; margin-top:7px; color:#475569; font-size:12px; font-weight:800; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }}
    .metric-rich .m-delta {{ display:inline-block; margin-left:6px; font-size:12px; font-weight:800; }}
    .metric-rich .m-up {{ color:#dc2626; }}
    .metric-rich .m-down {{ color:#059669; }}
    @media print {{
      .chart-box {{ break-inside:avoid; padding:10px; }}
      .chart-data {{ display:none; }}
    }}
    .glossary-list {{ border:1px solid #d6dee9; background:#fff; }}
    .glossary-row {{ display:grid; grid-template-columns:130px minmax(0,1fr); gap:0; align-items:stretch; border-bottom:1px solid #e8edf3; }}
    .glossary-row:last-child {{ border-bottom:0; }}
    .glossary-term {{ display:flex; align-items:flex-start; padding:12px 14px; background:#f1f6fb; color:#0b5ca8; font-size:13px; font-weight:800; border-right:3px solid #0b5ca8; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }}
    .glossary-def {{ padding:12px 16px; color:#334155; font-size:13px; line-height:1.7; }}
  </style>
  <script>{_load_echarts()}</script>
  <script>{_HANDAAS_CHART_JS}</script>
</head>
<body>
  <article class="report-page">
    <div class="report-topbar"><div class="report-stripe"></div><div class="report-banner">{esc(banner)}</div></div>
    <div class="cover-grid">
      <aside class="cover-side">
        <h2 class="cover-side-title">{esc(banner)}</h2>
        <p class="cover-date">{esc(generated_at_cn)}</p>
        {toc_block}
        {scope_block}
      </aside>
      <section class="cover-main">
        {review_banner}
        <p class="report-kicker">PUBLIC OPINION RESEARCH</p>
        <h1>{esc(title)}</h1>
        {lead_html}
        {intro_html}
        {metrics_html}
        {exec_block}
        {findings_html}
        {dimensions_html}
        {scope_bar_html}
      </section>
    </div>
  </article>
  <main class="research-doc">
    {review_banner}
    {summary_section}
    {caliber_section}
    {metrics_section}
    {core_section}
    {records_section}
    {insights_section}
    {glossary_html}
  </main>
</body>
</html>
""", ECHARTS_VENDOR_PATH)


# --------------------------------------------------------------------------- #
# Markdown document
# --------------------------------------------------------------------------- #

def _md_cell(value: Any) -> str:
    value = text(value) or "-"
    return (value.replace("\\", "\\\\").replace("|", "\\|")
            .replace("\r\n", " ").replace("\r", " ").replace("\n", " "))


def md_table(rows: Sequence[Any], columns: Sequence[tuple[str, str]] | None = None) -> str:
    cleaned = clean_rows(rows, columns)
    if not cleaned:
        return "_暂无数据_\n"
    norm_cols = _normalize_columns(cleaned, columns)
    if not norm_cols:
        return "_暂无数据_\n"
    head = "| " + " | ".join(_md_cell(label) for _, label in norm_cols) + " |"
    sep = "| " + " | ".join("---" for _ in norm_cols) + " |"
    body = []
    for row in cleaned:
        body.append("| " + " | ".join(_md_cell(row.get(key, "")) for key, _ in norm_cols) + " |")
    return "\n".join([head, sep, *body]) + "\n"


def render_markdown(data: Dict[str, Any]) -> str:
    title = infer_title(data)
    ds = data.get("data_source") if isinstance(data.get("data_source"), Mapping) else {}
    semantic_review = ds.get("semantic_review") if isinstance(ds.get("semantic_review"), Mapping) else {}
    subject = data.get("subject") if isinstance(data.get("subject"), Mapping) else {}
    caliber = data.get("caliber") if isinstance(data.get("caliber"), Mapping) else {}
    core = data.get("core_analysis") if isinstance(data.get("core_analysis"), Mapping) else {}
    sections = visible_sections(core, get_sections(data))
    metrics = _visible_metrics(as_list(data.get("metrics")))
    records = clean_rows(as_list(data.get("representative_records")))
    insights = _visible_insights(data.get("insights"))
    summary = compact(data.get("abstract") or data.get("summary") or "")

    lines: List[str] = []
    lines.append(f"# {title}\n")
    if semantic_review.get("status") == "pending":
        lines.append("> **数据草稿（待AI语义审校）**：本报告尚未完成当前 AI 智能体的语义审校，请完成审校后再对外交付。\n")
    if has_data(summary):
        lines.append(f"> {summary}\n")
    if has_data(ds.get("generated_at")):
        lines.append(f"_生成时间：{ds.get('generated_at')}_\n")

    _NUMERALS = ("一", "二", "三", "四", "五", "六", "七", "八")
    _number = 0

    def _heading(label: str) -> str:
        nonlocal _number
        heading = f"## {_NUMERALS[_number]}、{label}\n"
        _number += 1
        return heading

    caliber_lines = []
    match_target = (caliber.get("match_target") or subject.get("enterprise")
                    or subject.get("matchKeyword") or subject.get("match_raw"))
    for label, value in (
        ("匹配对象", match_target),
        ("匹配方式", caliber.get("match_type")),
        ("数据范围", caliber.get("data_scope")),
        ("局限说明", caliber.get("limit")),
    ):
        if has_data(value):
            caliber_lines.append(f"- {label}：{text(value)}")
    if caliber_lines:
        lines.append(_heading("查询对象与口径"))
        lines.extend(caliber_lines)
        lines.append("")

    if metrics:
            lines.append(_heading("数据总览"))
            lines.append(md_table(metrics, [("label", "指标"), ("value", "数值"), ("hint", "说明")]))
            lines.append("")

    core_start = len(lines)
    if sections:
        lines.append(_heading("核心分析"))
        for spec in sections:
            key = str(spec.get("key") or "")
            body = core.get(key)
            kind = spec.get("kind")
            rows = clean_rows(as_list(body), spec.get("columns")) if kind in ("table", "line", "bar", "pie", "donut", "multi_line") or kind is None else []
            block: List[str] = []
            if kind == "text":
                body_text = compact(body, limit=1500)
                if has_data(body_text):
                    block.append(body_text)
            elif kind == "tags":
                items = tag_items(body)
                if items:
                    block.extend(f"- {html.escape(item, quote=False)}" for item in items)
            elif kind == "kv":
                items = [(k, v) for k, v in body.items() if has_data(v)] if isinstance(body, dict) else []
                if items:
                                    block.append(md_table([{"字段": k, "内容": v} for k, v in items],
                                                          [("字段", "字段"), ("内容", "内容")]))
            elif kind == "table":
                if rows:
                    block.append(md_table(rows, spec.get("columns") or _normalize_columns(rows, None)))
            if block:
                            lines.append(f"### {esc(spec.get('title', key))}\n")
                            note_text = _section_note_text(spec.get("note"), rows)
                            if has_data(note_text):
                                lines.append(f"_{note_text}_\n")
                            lines.extend(block)
                            lines.append("")
        if len(lines) == core_start + 1:
            del lines[core_start:]
            _number -= 1

    if records:
        lines.append(_heading("代表性记录"))
        lines.append(md_table(records, _normalize_columns(records, None)))

    if insights:
        lines.append(_heading("特征与洞察"))
        for item in insights:
            feature = item.get("feature")
            if has_data(feature):
                lines.append(f"### {esc(feature)}")
            evidence = item.get("evidence")
            if has_data(evidence):
                lines.append(f"- 证据：{text(evidence)}")
            interpretation = item.get("interpretation")
            if has_data(interpretation):
                lines.append(f"- 解读：{text(interpretation)}")
            lines.append("")

    source_lines = []
    if has_data(ds.get("mcp_server")):
        source_lines.append(f"- 数据来源：{text(ds.get('mcp_server'))}")
    if has_data(ds.get("generated_at")):
        source_lines.append(f"- 生成时间：{text(ds.get('generated_at'))}")
    if has_data(ds.get("blocked_reason")):
        source_lines.append(f"- 失败原因：{text(ds.get('blocked_reason'))}")
    tool_errors = tag_items(ds.get("tool_errors"))
    if tool_errors:
        source_lines.append(f"- 数据缺口：{'、'.join(tool_errors)}")
    if "dry_run" in ds and has_data(ds.get("dry_run")):
        source_lines.append(f"- 模式：{'Dry-run 样例' if ds.get('dry_run') else '真实查询'}")
    if source_lines:
        lines.append(_heading("数据口径与来源"))
        lines.extend(source_lines)
    return "\n".join(lines)


def build_payload(*args: Any, **kwargs: Any) -> Dict[str, Any]:  # pragma: no cover (placeholder for import compat)
    return {}


def html_to_pdf(html_str: str, pdf_path: str) -> None:
    """Convert HTML string to PDF using Playwright headless Chromium."""
    import tempfile
    tmp = pathlib.Path(tempfile.mktemp(suffix=".html"))
    try:
        tmp.write_text(html_str, encoding="utf-8")
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(f"file://{tmp.resolve()}", wait_until="networkidle")
            # Wait for ECharts SVG rendering to complete
            page.wait_for_timeout(2000)
            # Force all ECharts instances to resize before PDF capture
            page.evaluate("window.__echarts_resize_all && window.__echarts_resize_all()")
            page.wait_for_timeout(500)
            page.pdf(
                path=pdf_path,
                format="A4",
                print_background=True,
                margin={"top": "12mm", "bottom": "12mm", "left": "10mm", "right": "10mm"},
            )
            browser.close()
    except ImportError:
        print("⚠️  PDF 转换需要 Playwright (pip install playwright && playwright install chromium)", file=sys.stderr)
        raise
    finally:
        tmp.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a HandaaS data report as HTML, Markdown or PDF.")
    parser.add_argument("--input", help="JSON report path; reads stdin when omitted.")
    parser.add_argument("--output", help="Output path; .html, .md or .pdf decides format.")
    parser.add_argument("--title", help="Override report title.")
    args = parser.parse_args()

    data = load_payload(args.input)
    if args.title:
        data["title"] = args.title
    out = pathlib.Path(args.output).expanduser() if args.output else None
    suffix = out.suffix.lower() if out else ""
    if not out:
        print(render_markdown(data))
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    if suffix in (".html", ".htm"):
        out.write_text(render_html(data), encoding="utf-8")
    elif suffix == ".pdf":
        html_to_pdf(render_html(data), str(out))
    else:
        out.write_text(render_markdown(data), encoding="utf-8")
    print_json_like({"ok": True, "output": str(out)})


def print_json_like(value: Dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
