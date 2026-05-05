#!/usr/bin/env python3
"""Download a report PDF from fxbaogao. Requires a valid API key."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from fxbaogao_client import ENV_FILE, FxbaogaoError, download_report_pdf, get_report_detail, has_auth
from workspace_utils import (
    build_workspace_manifest,
    extract_pdf_text,
    extract_report_ids,
    find_report_artifact_paths,
    is_pdf_file,
    read_json,
    report_file_stem,
    require_pdf_text_dependencies,
    selected_report_ids,
    topic_artifact_path,
    topic_reports_dir,
    unique_path,
    write_json,
)

KNOWN_DOWNLOAD_SUFFIXES = {".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="下载研报 PDF（需要 API key）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
请先在 skill 根目录的 .env 文件中设置：
    FXBAOGAO_API_KEY=your_key

示例:
    python3 download_report.py 5288801
    python3 download_report.py 5288801 --output-dir ./downloads --json
    python3 download_report.py --workspace workspace/topic-智谱-AI-大模型-20260430 --json
        """,
    )
    parser.add_argument("doc_id", nargs="?", type=int, help="研报文档 ID")
    parser.add_argument(
        "--workspace",
        "-w",
        help="主题研究工作区目录；指定后按 REPORT_READER_精读分析.md 中的报告 ID 批量下载",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="PDF 保存目录；单篇默认 ./downloads，工作区默认 intermediate/downloads",
    )
    parser.add_argument("--force", action="store_true", help="工作区批量下载时重新下载已存在的 PDF")
    parser.add_argument("--no-extract-text", action="store_true", help="只下载 PDF，不抽取 PDF 文本")
    parser.add_argument("--strict", action="store_true", help="工作区批量下载有失败时返回非 0")
    parser.add_argument("--json", "-j", action="store_true", help="输出 JSON")
    return parser


def write_auth_error(args: argparse.Namespace) -> None:
    msg = (
        "下载功能需要 API key。\n"
        f"请在 {ENV_FILE} 中设置：FXBAOGAO_API_KEY=your_key"
    )
    if args.json:
        print(json.dumps({
            "doc_id": args.doc_id,
            "workspace": args.workspace or "",
            "status": "no_auth",
            "message": msg,
        }, ensure_ascii=False))
    else:
        print(msg, file=sys.stderr)


def resolve_output_dir(args: argparse.Namespace, workspace_dir: Path | None = None) -> Path:
    if args.output_dir:
        return Path(args.output_dir).expanduser().resolve()
    if workspace_dir is not None:
        return topic_artifact_path(workspace_dir, "downloads", for_write=True).resolve()
    return Path("downloads").expanduser().resolve()


def load_cached_detail(workspace_dir: Path, report_id: str) -> dict[str, Any] | None:
    reports_dir = topic_reports_dir(workspace_dir)
    if not reports_dir.exists():
        return None
    json_path, _ = find_report_artifact_paths(reports_dir, report_id)
    if not json_path:
        return None
    try:
        data = read_json(json_path)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def workspace_reader_report_ids(workspace_dir: Path) -> list[str]:
    reader_path = workspace_dir / "REPORT_READER_精读分析.md"
    if reader_path.exists():
        report_ids = extract_report_ids(reader_path.read_text(encoding="utf-8"))
        if report_ids:
            return report_ids
    return selected_report_ids(workspace_dir)


def load_download_index(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = read_json(path)
    except (OSError, json.JSONDecodeError):
        return []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def upsert_entry(entries: list[dict[str, Any]], entry: dict[str, Any]) -> None:
    report_id = str(entry.get("doc_id"))
    for index, existing in enumerate(entries):
        if str(existing.get("doc_id")) == report_id:
            entries[index] = entry
            return
    entries.append(entry)


def existing_downloads(entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    existing: dict[str, dict[str, Any]] = {}
    for entry in entries:
        report_id = entry.get("doc_id")
        output_path = entry.get("output_path")
        if report_id is None or not output_path or entry.get("status") != "success":
            continue
        path = Path(str(output_path))
        if path.exists() and path.suffix.lower() == ".pdf" and is_pdf_file(path):
            existing[str(report_id)] = entry
    return existing


def reserve_download_stem(result: dict[str, Any], reserved: set[Path]) -> None:
    output_path = result.get("output_path")
    if not output_path:
        return
    path = Path(str(output_path))
    reserved.update(path.with_suffix(suffix) for suffix in KNOWN_DOWNLOAD_SUFFIXES)


def attach_extracted_text(result: dict[str, Any], text_dir: Path) -> None:
    output_path = result.get("output_path")
    report_id = result.get("doc_id")
    if not output_path or report_id is None:
        return
    text_path = text_dir / f"{report_id}.txt"
    text_result = extract_pdf_text(Path(str(output_path)), text_path)
    result["text_extraction"] = text_result


def ensure_pdf_text_ready(args: argparse.Namespace) -> bool:
    if args.no_extract_text:
        return True
    try:
        require_pdf_text_dependencies()
    except RuntimeError as exc:
        if args.json:
            print(json.dumps({
                "status": "dependency_error",
                "message": str(exc),
            }, ensure_ascii=False))
        else:
            print(f"错误: {exc}", file=sys.stderr)
        return False
    return True


def download_one(
        report_id: int,
        output_dir: Path,
        *,
        detail: dict[str, Any] | None = None,
        reserved: set[Path] | None = None,
) -> dict[str, Any]:
    detail = detail or get_report_detail(report_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = unique_path(output_dir, report_file_stem(detail), ".pdf", reserved=reserved)
    result = download_report_pdf(report_id, output_path, preferred_url=detail.get("pdf_url"))
    if reserved is not None:
        reserve_download_stem(result, reserved)
    result["title"] = detail.get("title") or ""
    result["org_name"] = detail.get("org_name") or ""
    return result


def download_workspace(args: argparse.Namespace) -> int:
    workspace_dir = Path(args.workspace).expanduser().resolve()
    if not workspace_dir.is_dir():
        print(f"错误: 工作区不存在: {workspace_dir}", file=sys.stderr)
        return 1

    report_ids = workspace_reader_report_ids(workspace_dir)
    output_dir = resolve_output_dir(args, workspace_dir)
    text_dir = topic_artifact_path(workspace_dir, "text", for_write=True).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if not args.no_extract_text:
        text_dir.mkdir(parents=True, exist_ok=True)
    index_path = topic_artifact_path(workspace_dir, "downloaded-reports.json", for_write=True)
    entries = load_download_index(index_path)
    existing = existing_downloads(entries)

    downloaded: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    text_errors: list[dict[str, Any]] = []
    reserved_download_paths: set[Path] = set()

    for report_id in report_ids:
        if not args.force and report_id in existing:
            if not args.no_extract_text and existing[report_id].get("text_extraction", {}).get("status") != "success":
                attach_extracted_text(existing[report_id], text_dir)
                if existing[report_id].get("text_extraction", {}).get("status") != "success":
                    text_errors.append({
                        "doc_id": report_id,
                        "message": existing[report_id].get("text_extraction", {}).get("message", "PDF 文本抽取失败"),
                    })
                upsert_entry(entries, existing[report_id])
            skipped.append(existing[report_id])
            reserve_download_stem(existing[report_id], reserved_download_paths)
            continue
        try:
            detail = load_cached_detail(workspace_dir, report_id) or get_report_detail(int(report_id))
            result = download_one(
                int(report_id),
                output_dir,
                detail=detail,
                reserved=reserved_download_paths,
            )
        except (ValueError, OSError, FxbaogaoError) as exc:
            error_entry = {
                "doc_id": report_id,
                "title": "",
                "status": "error",
                "message": str(exc),
            }
            errors.append(error_entry)
            upsert_entry(entries, error_entry)
            continue
        if not args.no_extract_text:
            attach_extracted_text(result, text_dir)
            if result.get("text_extraction", {}).get("status") != "success":
                text_errors.append({
                    "doc_id": report_id,
                    "message": result.get("text_extraction", {}).get("message", "PDF 文本抽取失败"),
                })
        downloaded.append(result)
        upsert_entry(entries, result)

    write_json(index_path, entries)
    manifest_path = topic_artifact_path(workspace_dir, "workspace-manifest.json", for_write=True)
    write_json(manifest_path, build_workspace_manifest(workspace_dir, generated_by="download_report.py"))

    payload = {
        "workspace_dir": str(workspace_dir),
        "source": "REPORT_READER_精读分析.md" if (workspace_dir / "REPORT_READER_精读分析.md").exists() else "selected-reports.md",
        "report_ids": report_ids,
        "downloaded": downloaded,
        "skipped": skipped,
        "errors": errors,
        "text_errors": text_errors,
        "output_dir": str(output_dir),
        "text_dir": "" if args.no_extract_text else str(text_dir),
        "downloaded_index": str(index_path),
        "manifest": str(manifest_path),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"工作区: {workspace_dir}")
        print(f"精读报告: {len(report_ids)}")
        print(f"本次下载: {len(downloaded)}")
        print(f"已存在跳过: {len(skipped)}")
        print(f"失败: {len(errors)}")
        print(f"文本抽取失败: {len(text_errors)}")
        print(f"PDF 目录: {output_dir}")
        if not args.no_extract_text:
            print(f"文本目录: {text_dir}")
        print(f"下载索引: {index_path}")
        if errors:
            for item in errors:
                print(f"- {item['doc_id']}: {item['message']}", file=sys.stderr)
        if text_errors:
            for item in text_errors:
                print(f"- 文本抽取 {item['doc_id']}: {item['message']}", file=sys.stderr)

    return 1 if args.strict and (errors or text_errors) else 0


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if (args.doc_id is None) == (args.workspace is None):
        parser.error("请指定单篇 doc_id，或使用 --workspace 指定一个主题工作区，二选一")

    if not has_auth():
        write_auth_error(args)
        sys.exit(1)

    if not ensure_pdf_text_ready(args):
        sys.exit(1)

    if args.workspace is not None:
        sys.exit(download_workspace(args))

    try:
        detail = get_report_detail(args.doc_id)
        output_dir = resolve_output_dir(args)
        result = download_one(args.doc_id, output_dir, detail=detail)
        if not args.no_extract_text:
            text_dir = output_dir / "text"
            text_dir.mkdir(parents=True, exist_ok=True)
            attach_extracted_text(result, text_dir)
    except FxbaogaoError as exc:
        if args.json:
            print(json.dumps({
                "doc_id": args.doc_id,
                "status": "error",
                "message": str(exc),
            }, ensure_ascii=False))
        else:
            print(f"错误: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    print(f"下载成功: {result['output_path']}")
    print(f"文件大小: {result['file_size']} 字节")
    text_extraction = result.get("text_extraction")
    if isinstance(text_extraction, dict):
        if text_extraction.get("status") == "success":
            print(f"文本抽取: {text_extraction.get('output_path')}")
        else:
            print(f"文本抽取失败: {text_extraction.get('message')}", file=sys.stderr)
    if result.get("title"):
        print(f"标题: {result['title']}")


if __name__ == "__main__":
    main()
