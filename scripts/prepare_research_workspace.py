#!/usr/bin/env python3
"""Create a helper workspace for one already-selected report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from fxbaogao_client import (
    FxbaogaoError,
    copy_workspace_templates,
    get_report_detail,
    render_detail_markdown,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="为单篇已选研报创建辅助工作区（需要 API key）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python3 prepare_research_workspace.py 5288801 --query "AI 大模型商业化"
    python3 prepare_research_workspace.py 5288801 --output-root ./workspace --json
        """,
    )
    parser.add_argument("doc_id", type=int, help="研报文档 ID")
    parser.add_argument("--query", help="研究问题或主题")
    parser.add_argument(
        "--output-root",
        default=None,
        help="工作区根目录，默认为当前项目目录下的 workspace/",
    )
    parser.add_argument("--force", action="store_true", help="覆盖已有目录中的同名文件")
    parser.add_argument("--json", "-j", action="store_true", help="输出 JSON")
    return parser


def write_text(path: Path, content: str) -> None:
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    try:
        detail = get_report_detail(args.doc_id, keyword=args.query)
    except FxbaogaoError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        sys.exit(1)

    output_root = Path(args.output_root).expanduser().resolve() if args.output_root else Path.cwd() / "workspace"
    workspace_dir = output_root / f"report-{args.doc_id}"
    if workspace_dir.exists() and not args.force:
        print(f"错误: 目录已存在: {workspace_dir}。如需覆盖请加 --force", file=sys.stderr)
        sys.exit(1)

    workspace_dir.mkdir(parents=True, exist_ok=True)
    copy_workspace_templates(Path(__file__).resolve().parent.parent, workspace_dir)

    report_json = workspace_dir / "report.json"
    report_md = workspace_dir / "report-detail.md"
    download_note_md = workspace_dir / "DOWNLOAD_INSTRUCTIONS.md"
    context_md = workspace_dir / "report-context.md"

    report_json.write_text(json.dumps(detail, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_text(report_md, render_detail_markdown(detail))

    write_text(
        download_note_md,
        "\n".join(
            [
                "# 下载说明",
                "",
                "如需下载原始 PDF，请先在 skill 根目录 `.env` 中配置 `FXBAOGAO_API_KEY`。",
                "如果接口返回相对 `pdfUrl`，脚本会使用固定前缀 `https://dr.fxbaogao.com/` 拼接。",
                "",
                f"示例命令：`python3 scripts/download_report.py {args.doc_id} --output-dir ./downloads --json`",
                "",
                "当前工作区只保存研究辅助文件，不代表已经下载原始文件。",
            ]
        ),
    )

    context_lines = [
        "# 研究上下文",
        "",
        f"- 文档 ID：{args.doc_id}",
        f"- 标题：{detail.get('title') or ''}",
        f"- 机构：{detail.get('org_name') or ''}",
        f"- 日期：{detail.get('publish_date') or ''}",
        f"- 研究问题：{args.query or ''}",
        "",
        "建议先补全：",
        "",
        "- `00_问题拆解.md`",
        "- `01_资料来源.md`",
        "- `REPORT_READER_精读分析.md`",
        "- `02_事实卡片.md`",
        "- `03_对比框架.md`",
        "- `04_推导过程.md`",
        "- `FINAL_调研报告.md`",
    ]
    write_text(context_md, "\n".join(context_lines))

    payload = {
        "doc_id": args.doc_id,
        "workspace_dir": str(workspace_dir),
        "files": {
            "report_json": str(report_json),
            "report_markdown": str(report_md),
            "download_instructions": str(download_note_md),
            "context": str(context_md),
        },
        "download_status": "available_via_cli",
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print(f"已创建研究工作区: {workspace_dir}")
    print(f"- 报告详情: {report_md}")
    print(f"- 下载说明: {download_note_md}")


if __name__ == "__main__":
    main()
