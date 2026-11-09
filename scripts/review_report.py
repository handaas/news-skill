#!/usr/bin/env python3
"""Prepare source-bound AI review inputs or apply actual agent decisions offline."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from semantic_review import apply_review, review_request


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="采集草稿或历史报告JSON，只读")
    parser.add_argument("--request", help="输出供当前AI智能体阅读的审校请求JSON")
    parser.add_argument("--review", help="当前AI智能体实际完成的逐项审校JSON")
    parser.add_argument("--output", help="审校后最终报告JSON")
    parser.add_argument("--report-output", help="同时输出最终HTML和同名Markdown")
    parser.add_argument("--pdf-output", help="同时导出最终PDF")
    args = parser.parse_args()
    if bool(args.request) == bool(args.review):
        parser.error("须选择 --request 准备审校，或 --review 应用真实AI判断")
    if args.request and any((args.output, args.report_output, args.pdf_output)):
        parser.error("准备审校阶段不输出最终报告")
    if args.review and not args.output:
        parser.error("应用审校时必须指定 --output")
    source = Path(args.input).expanduser().resolve()
    output_root = Path(__file__).resolve().parents[1] / "output"
    output_root = output_root.resolve()
    output = Path(args.output).expanduser().resolve() if args.output else None
    request = Path(args.request).expanduser().resolve() if args.request else None
    html = Path(args.report_output).expanduser().resolve().with_suffix(".html") if args.report_output else None
    markdown = html.with_suffix(".md") if html else None
    pdf = Path(args.pdf_output).expanduser().resolve() if args.pdf_output else None
    targets = [path for path in (request, output, html, markdown, pdf) if path]
    inputs = [source] + ([Path(args.review).expanduser().resolve()] if args.review else [])
    if any(path in inputs for path in targets) or len(targets) != len(set(targets)):
        parser.error("输出不能覆盖原报告、AI决策文件，且各输出路径须不同")
    if any(not path.is_relative_to(output_root) for path in targets):
        parser.error("审校请求和报告须输出到本Skill的output目录")
    report = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        parser.error("原报告必须为JSON对象")
    if request:
        result = review_request(report)
        request.parent.mkdir(parents=True, exist_ok=True)
        request.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"ok": True, "request": str(request), "source_sha256": result["source_sha256"],
                          "review_required": True, "new_mcp_calls": 0}, ensure_ascii=False))
        return
    review = json.loads(Path(args.review).expanduser().read_text(encoding="utf-8"))
    try:
        result = apply_review(report, review)
    except ValueError as error:
        parser.error(str(error))
    rendered_html = rendered_markdown = None
    if html or pdf:
        from render_report import render_html, render_markdown
        rendered_html = render_html(result)
        rendered_markdown = render_markdown(result) if markdown else None
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if html:
        html.parent.mkdir(parents=True, exist_ok=True)
        html.write_text(rendered_html, encoding="utf-8")
        markdown.write_text(rendered_markdown, encoding="utf-8")
    if pdf:
        from render_report import html_to_pdf
        pdf.parent.mkdir(parents=True, exist_ok=True)
        html_to_pdf(rendered_html, str(pdf))
    print(json.dumps({"ok": True, "json": str(output), "html": str(html) if html else None,
                      "markdown": str(markdown) if markdown else None, "pdf": str(pdf) if pdf else None,
                      "semantic_review": "reviewed", "new_mcp_calls": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
