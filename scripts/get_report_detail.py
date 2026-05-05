#!/usr/bin/env python3
"""Fetch report detail from fxbaogao."""

from __future__ import annotations

import argparse
import json
import sys

from fxbaogao_client import FxbaogaoError, format_detail_output, get_report_detail


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="获取研报详情、摘要和正文摘录（需要 API key）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python3 get_report_detail.py 5288801
    python3 get_report_detail.py 5288801 --keyword "光模块"
    python3 get_report_detail.py 5288801 --json
    python3 get_report_detail.py 5288801 --max-content 8
        """,
    )
    parser.add_argument("doc_id", type=int, help="研报文档 ID")
    parser.add_argument(
        "--max-content",
        type=int,
        default=20,
        help="非 JSON 输出时最多展示多少段正文，默认 20",
    )
    parser.add_argument(
        "--keyword",
        "-k",
        help="搜索关键词；详情接口需要该字段才会返回正文段落",
    )
    parser.add_argument("--json", "-j", action="store_true", help="输出规范化 JSON")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    try:
        result = get_report_detail(args.doc_id, keyword=args.keyword)
    except FxbaogaoError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    print(format_detail_output(result, max_content_lines=max(1, args.max_content)))


if __name__ == "__main__":
    main()
