#!/usr/bin/env python3
"""Workspace inspection helpers for fxbaogao deep research outputs."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

PDF_DEPENDENCY_INSTALL_HINT = "请先运行 `python3 -m pip install -r requirements.txt` 安装项目依赖。"

REPORT_ID_RE = re.compile(r"(?<!\d)(\d{7})(?!\d)")
INVALID_FILENAME_CHARS_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
MAX_REPORT_FILENAME_STEM_BYTES = 190
PLACEHOLDER_PATTERNS = [
    re.compile(pattern)
    for pattern in (
        r"TODO",
        r"TBD",
        r"待填",
        r"待补",
        r"请填写",
        r"\[待",
        r"^\s*-\s*(?:核心问题|希望形成的判断|对象|地域|明确排除|相关性|时效性|机构 / 作者权威性|计划保留篇数|陈述|来源（资料编号 / 报告 ID）|核心结论|最重要的证据|最大不确定性)：\s*$",
    )
]
REPORT_READER_TEMPLATE_MARKERS = [
    "- 标题：",
    "- 文档 ID：",
    "- 核心判断：",
    "- 关键结论：",
    "- 事实 1：",
    "- 事实 2：",
]
DETAIL_BASED_READER_PATTERNS = [
    re.compile(pattern)
    for pattern in (
        r"基于详情接口.*(?:摘要|正文|摘录).*(?:生成|整理|分析|判断)",
        r"详情接口(?:返回|未返回).*(?:摘要|AI 总结|正文摘录).*(?:核心判断|关键结论|事实卡片|分析)",
        r"不使用详情接口摘要作为精读依据",
        r"报告摘要和正文摘录",
    )
]
GENERATED_JUNK_NAMES = {".DS_Store"}
GENERATED_JUNK_SUFFIXES = {".pyc"}
INTERMEDIATE_DIR_NAME = "intermediate"
ROOT_TOPIC_FILE_NAMES = {
    "REPORT_READER_精读分析.md",
    "FINAL_调研报告.md",
}
INTERMEDIATE_TOPIC_FILE_NAMES = {
    "00_问题拆解.md",
    "01_资料来源.md",
    "02_事实卡片.md",
    "03_对比框架.md",
    "04_推导过程.md",
    "candidate-reports.md",
    "selected-reports.md",
    "topic-search-results.json",
    "hydrated-reports.json",
    "downloaded-reports.json",
    "research-brief.md",
    "workspace-manifest.json",
}
INTERMEDIATE_TOPIC_DIR_NAMES = {
    "reports",
    "downloads",
    "text",
}
REQUIRED_TOPIC_FILES = [
    f"{INTERMEDIATE_DIR_NAME}/00_问题拆解.md",
    f"{INTERMEDIATE_DIR_NAME}/01_资料来源.md",
    f"{INTERMEDIATE_DIR_NAME}/02_事实卡片.md",
    f"{INTERMEDIATE_DIR_NAME}/03_对比框架.md",
    f"{INTERMEDIATE_DIR_NAME}/04_推导过程.md",
    "REPORT_READER_精读分析.md",
    "FINAL_调研报告.md",
    f"{INTERMEDIATE_DIR_NAME}/candidate-reports.md",
    f"{INTERMEDIATE_DIR_NAME}/selected-reports.md",
    f"{INTERMEDIATE_DIR_NAME}/topic-search-results.json",
    f"{INTERMEDIATE_DIR_NAME}/hydrated-reports.json",
]


def extract_report_ids(text: str) -> list[str]:
    """Return report ids in first-seen order."""
    ids: list[str] = []
    seen: set[str] = set()
    for match in REPORT_ID_RE.finditer(text):
        report_id = match.group(1)
        if report_id not in seen:
            seen.add(report_id)
            ids.append(report_id)
    return ids


def extract_selected_analysis_report_ids(text: str) -> list[str]:
    """Extract ids from selected-reports.md while skipping explicit discard sections."""
    ids: list[str] = []
    seen: set[str] = set()
    include_section = True
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            include_section = not any(term in stripped for term in ("剔除", "低权重", "不选", "无关"))
        if not include_section:
            continue
        for report_id in extract_report_ids(line):
            if report_id not in seen:
                seen.add(report_id)
                ids.append(report_id)
    return ids


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as file_obj:
        return json.load(file_obj)


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _truncate_utf8(value: str, max_bytes: int) -> str:
    text = value
    while len(text.encode("utf-8")) > max_bytes and text:
        text = text[:-1].rstrip()
    return text or value[:1]


def clean_filename_part(value: Any, fallback: str) -> str:
    text = str(value or "").strip() or fallback
    text = INVALID_FILENAME_CHARS_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text)
    text = text.strip(" .")
    return text or fallback


def report_file_stem(report: dict[str, Any]) -> str:
    """Return a human-readable report filename stem: 【机构名】报告标题."""
    doc_id = report.get("doc_id") or report.get("id") or ""
    org_name = clean_filename_part(report.get("org_name") or report.get("orgName"), "未知机构")
    title_fallback = f"研报 {doc_id}" if doc_id else "无标题研报"
    title = clean_filename_part(report.get("title"), title_fallback)
    return _truncate_utf8(f"【{org_name}】{title}", MAX_REPORT_FILENAME_STEM_BYTES)


def unique_path(directory: Path, stem: str, suffix: str, *, reserved: set[Path] | None = None) -> Path:
    reserved = reserved or set()
    suffix = suffix if suffix.startswith(".") else f".{suffix}"
    for index in range(1, 10000):
        candidate_stem = stem if index == 1 else _truncate_utf8(
            f"{stem}-{index}",
            MAX_REPORT_FILENAME_STEM_BYTES,
        )
        candidate = directory / f"{candidate_stem}{suffix}"
        if not candidate.exists() and candidate not in reserved:
            return candidate
    raise RuntimeError(f"无法生成不冲突的文件名: {directory / stem}{suffix}")


def unique_report_artifact_paths(
        directory: Path,
        report: dict[str, Any],
        *,
        reserved: set[Path] | None = None,
) -> tuple[Path, Path]:
    reserved = reserved or set()
    stem = report_file_stem(report)
    for index in range(1, 10000):
        candidate_stem = stem if index == 1 else _truncate_utf8(
            f"{stem}-{index}",
            MAX_REPORT_FILENAME_STEM_BYTES,
        )
        json_path = directory / f"{candidate_stem}.json"
        markdown_path = directory / f"{candidate_stem}.md"
        if (
            not json_path.exists()
            and not markdown_path.exists()
            and json_path not in reserved
            and markdown_path not in reserved
        ):
            return json_path, markdown_path
    raise RuntimeError(f"无法生成不冲突的报告文件名: {directory / stem}")


def topic_intermediate_dir(workspace_dir: Path) -> Path:
    return workspace_dir / INTERMEDIATE_DIR_NAME


def topic_artifact_path(
        workspace_dir: Path,
        relative_path: str | Path,
        *,
        for_write: bool = False,
) -> Path:
    """Return the canonical topic artifact path, with legacy root fallback for reads."""
    relative = Path(relative_path)
    if relative.is_absolute():
        return relative

    parts = relative.parts
    if parts and parts[0] == INTERMEDIATE_DIR_NAME:
        candidate = workspace_dir / relative
        legacy_relative = Path(*parts[1:]) if len(parts) > 1 else Path()
        legacy = workspace_dir / legacy_relative if legacy_relative.parts else workspace_dir
        if for_write or candidate.exists() or not legacy.exists():
            return candidate
        return legacy

    if len(parts) == 1 and parts[0] in ROOT_TOPIC_FILE_NAMES:
        return workspace_dir / relative

    should_live_in_intermediate = (
        len(parts) == 1 and parts[0] in INTERMEDIATE_TOPIC_FILE_NAMES
    ) or (parts and parts[0] in INTERMEDIATE_TOPIC_DIR_NAMES)
    if should_live_in_intermediate:
        candidate = topic_intermediate_dir(workspace_dir) / relative
        legacy = workspace_dir / relative
        if for_write or candidate.exists() or not legacy.exists():
            return candidate
        return legacy

    return workspace_dir / relative


def topic_reports_dir(workspace_dir: Path, *, for_write: bool = False) -> Path:
    return topic_artifact_path(workspace_dir, "reports", for_write=for_write)


def extract_workspace_report_ids(workspace_dir: Path) -> dict[str, list[str]]:
    """Extract report ids from the workspace files that define the research chain."""
    sources = {
        "selected": topic_artifact_path(workspace_dir, "selected-reports.md"),
        "reader": workspace_dir / "REPORT_READER_精读分析.md",
        "facts": topic_artifact_path(workspace_dir, "02_事实卡片.md"),
        "final": workspace_dir / "FINAL_调研报告.md",
    }
    extracted: dict[str, list[str]] = {}
    for label, path in sources.items():
        if path.exists():
            text = path.read_text(encoding="utf-8")
            if label == "selected":
                extracted[label] = extract_selected_analysis_report_ids(text)
            else:
                extracted[label] = extract_report_ids(text)
        else:
            extracted[label] = []
    return extracted


def selected_report_ids(workspace_dir: Path) -> list[str]:
    selected_path = topic_artifact_path(workspace_dir, "selected-reports.md")
    if not selected_path.exists():
        return []
    return extract_selected_analysis_report_ids(selected_path.read_text(encoding="utf-8"))


def existing_report_ids(workspace_dir: Path, suffix: str) -> set[str]:
    reports_dir = topic_reports_dir(workspace_dir)
    if not reports_dir.exists():
        return set()
    report_ids: set[str] = set()
    for path in reports_dir.glob(f"*{suffix}"):
        if REPORT_ID_RE.fullmatch(path.stem):
            report_ids.add(path.stem)
            continue
        report_ids.update(report_ids_from_artifact(path))
    return report_ids


def report_ids_from_artifact(path: Path) -> list[str]:
    if not path.exists():
        return []
    if path.suffix.lower() == ".json":
        try:
            data = read_json(path)
        except (OSError, json.JSONDecodeError):
            return []
        if isinstance(data, dict):
            doc_id = data.get("doc_id") or data.get("docId") or data.get("id")
            return [str(doc_id)] if doc_id else []
        return []
    if path.suffix.lower() == ".md":
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return []
        doc_id_match = re.search(r"(?m)^-\s*文档 ID：\s*(\d{7})\s*$", text)
        if doc_id_match:
            return [doc_id_match.group(1)]
        return extract_report_ids(text)
    return []


def find_report_artifact_paths(reports_dir: Path, report_id: str) -> tuple[Path | None, Path | None]:
    legacy_json = reports_dir / f"{report_id}.json"
    legacy_markdown = reports_dir / f"{report_id}.md"
    json_path = legacy_json if legacy_json.exists() else None
    markdown_path = legacy_markdown if legacy_markdown.exists() else None
    if json_path and markdown_path:
        return json_path, markdown_path

    for path in reports_dir.glob("*.json"):
        if report_id in report_ids_from_artifact(path):
            json_path = path
            break
    for path in reports_dir.glob("*.md"):
        if report_id in report_ids_from_artifact(path):
            markdown_path = path
            break
    return json_path, markdown_path


def hydrated_index_ids(workspace_dir: Path) -> tuple[set[str], set[str], list[str]]:
    """Return successful ids, errored ids, and parse errors for hydrated-reports.json."""
    path = topic_artifact_path(workspace_dir, "hydrated-reports.json")
    if not path.exists():
        return set(), set(), []
    try:
        data = read_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        return set(), set(), [f"{path.name} 无法解析: {exc}"]
    if not isinstance(data, list):
        return set(), set(), [f"{path.name} 应为列表"]

    successes: set[str] = set()
    errors: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        doc_id = item.get("doc_id")
        if doc_id is None:
            continue
        report_id = str(doc_id)
        if item.get("error"):
            errors.add(report_id)
        else:
            successes.add(report_id)
    return successes, errors, []


def downloaded_index_entries(workspace_dir: Path) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Return successful download index entries keyed by report id."""
    path = topic_artifact_path(workspace_dir, "downloaded-reports.json")
    if not path.exists():
        return {}, []
    try:
        data = read_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        return {}, [f"{path.name} 无法解析: {exc}"]
    if not isinstance(data, list):
        return {}, [f"{path.name} 应为列表"]

    entries: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        doc_id = item.get("doc_id")
        if doc_id is None or item.get("status") != "success":
            continue
        report_id = str(doc_id)
        output_path = item.get("output_path")
        if not output_path:
            errors.append(f"{path.name}: {report_id} 缺少 output_path")
            continue
        entries[report_id] = item
    return entries, errors


def is_topic_workspace_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    return (
        topic_artifact_path(path, "topic-search-results.json").exists()
        or (
            path.name.startswith("topic-")
            and (path / "FINAL_调研报告.md").exists()
            and (path / "REPORT_READER_精读分析.md").exists()
        )
    )


def topic_workspace_dirs(root: Path) -> list[Path]:
    if is_topic_workspace_dir(root):
        return [root]
    workspace_root = root / "workspace" if (root / "workspace").is_dir() else root
    if not workspace_root.is_dir():
        return []
    return sorted(
        path
        for path in workspace_root.iterdir()
        if path.is_dir() and path.name.startswith("topic-")
    )


def find_placeholders(path: Path) -> list[str]:
    if not path.exists() or path.suffix.lower() != ".md":
        return []
    issues: list[str] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        for pattern in PLACEHOLDER_PATTERNS:
            if pattern.search(line):
                issues.append(f"{path.name}:{lineno}: {line.strip()}")
                break
    return issues


def report_reader_is_template(reader_path: Path) -> bool:
    if not reader_path.exists():
        return True
    text = reader_path.read_text(encoding="utf-8")
    if not extract_report_ids(text):
        return True
    if "PDF 原文核对记录" in text and "- 本地 PDF：" in text:
        return False
    marker_hits = sum(1 for marker in REPORT_READER_TEMPLATE_MARKERS if marker in text)
    return marker_hits >= 4 and len(text.splitlines()) < 80


def find_detail_based_reader_lines(reader_path: Path) -> list[str]:
    if not reader_path.exists():
        return []
    issues: list[str] = []
    for lineno, line in enumerate(reader_path.read_text(encoding="utf-8").splitlines(), start=1):
        for pattern in DETAIL_BASED_READER_PATTERNS:
            if pattern.search(line):
                issues.append(f"{reader_path.name}:{lineno}: {line.strip()}")
                break
    return issues


def is_pdf_file(path: Path) -> bool:
    try:
        with path.open("rb") as file_obj:
            header = file_obj.read(1024)
    except OSError:
        return False
    return b"%PDF" in header


def workspace_pdf_reading_enabled(workspace_dir: Path) -> bool:
    reader_path = workspace_dir / "REPORT_READER_精读分析.md"
    if not reader_path.exists():
        return False
    reader_text = reader_path.read_text(encoding="utf-8")
    return (
        "PDF 原文核对记录" in reader_text
        or "所有核心判断、关键数据、图表证据和风险提示必须来自" in reader_text
        or "本表只预置元数据和本地 PDF 路径" in reader_text
    )


def pdf_text_dependency_error() -> str:
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError as pypdf_exc:
        try:
            from PyPDF2 import PdfReader  # type: ignore
        except ImportError:
            return f"缺少 PDF 文本抽取依赖 pypdf。{PDF_DEPENDENCY_INSTALL_HINT}"
    return ""


def require_pdf_text_dependencies() -> None:
    message = pdf_text_dependency_error()
    if message:
        raise RuntimeError(message)


def _extract_pdf_text_with_python_lib(pdf_path: Path) -> tuple[str, str]:
    reader_class: Any
    extractor_name: str
    try:
        from pypdf import PdfReader  # type: ignore

        reader_class = PdfReader
        extractor_name = "pypdf"
    except ImportError:
        from PyPDF2 import PdfReader  # type: ignore

        reader_class = PdfReader
        extractor_name = "PyPDF2"

    reader = reader_class(str(pdf_path))
    chunks: list[str] = []
    for page_number, page in enumerate(reader.pages, start=1):
        page_text = page.extract_text() or ""
        if page_text.strip():
            chunks.append(f"[PDF page {page_number}]\n{page_text.strip()}")
    return "\n\n".join(chunks), extractor_name


def _extract_pdf_text_with_pdftotext(pdf_path: Path) -> tuple[str, str]:
    binary = shutil.which("pdftotext")
    if not binary:
        raise RuntimeError("未找到 pdftotext")
    completed = subprocess.run(
        [binary, "-layout", "-enc", "UTF-8", str(pdf_path), "-"],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if completed.returncode != 0:
        message = completed.stderr.strip() or f"pdftotext 退出码 {completed.returncode}"
        raise RuntimeError(message)
    return completed.stdout, "pdftotext"


def extract_pdf_text(pdf_path: Path, output_path: Path) -> dict[str, Any]:
    """Extract text from a local PDF into a txt file for PDF-grounded reading."""
    if not pdf_path.exists():
        return {"status": "error", "message": f"PDF 文件不存在: {pdf_path}"}
    if not is_pdf_file(pdf_path):
        return {"status": "error", "message": f"不是有效 PDF 文件: {pdf_path}"}

    errors: list[str] = []
    text = ""
    extractor = ""
    for extractor_func in (_extract_pdf_text_with_python_lib, _extract_pdf_text_with_pdftotext):
        try:
            text, extractor = extractor_func(pdf_path)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            errors.append(str(exc))
            continue
        if text.strip():
            break
        errors.append(f"{extractor} 未抽取到文本")

    if not text.strip():
        return {
            "status": "error",
            "message": "PDF 文本抽取失败: " + "；".join(error for error in errors if error),
        }

    text = text.encode("utf-8", errors="replace").decode("utf-8")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text.rstrip() + "\n", encoding="utf-8")
    return {
        "status": "success",
        "output_path": str(output_path),
        "text_char_count": len(text),
        "extractor": extractor,
    }


def find_generated_junk(path: Path) -> list[Path]:
    if not path.exists():
        return []
    junk: list[Path] = []
    for item in path.rglob("*"):
        if item.name in GENERATED_JUNK_NAMES or item.suffix in GENERATED_JUNK_SUFFIXES or item.name == "__pycache__":
            junk.append(item)
    return junk


def validate_topic_workspace(workspace_dir: Path, *, strict_placeholders: bool = False) -> list[str]:
    errors: list[str] = []
    workspace_dir = workspace_dir.resolve()

    if not workspace_dir.is_dir():
        return [f"工作区不存在: {workspace_dir}"]

    for relative_path in REQUIRED_TOPIC_FILES:
        if not topic_artifact_path(workspace_dir, relative_path).exists():
            errors.append(f"{workspace_dir.name}: 缺失文件 {relative_path}")

    root_files = sorted(
        path.name
        for path in workspace_dir.iterdir()
        if path.is_file() and path.name not in ROOT_TOPIC_FILE_NAMES
    )
    if root_files:
        errors.append(f"{workspace_dir.name}: topic 根目录存在过程文件: {', '.join(root_files)}")

    root_dirs = sorted(
        path.name
        for path in workspace_dir.iterdir()
        if path.is_dir() and path.name != INTERMEDIATE_DIR_NAME
    )
    if root_dirs:
        errors.append(f"{workspace_dir.name}: topic 根目录存在过程目录: {', '.join(root_dirs)}")

    for json_name in ("topic-search-results.json", "hydrated-reports.json", "downloaded-reports.json"):
        json_path = topic_artifact_path(workspace_dir, json_name)
        if json_path.exists():
            try:
                read_json(json_path)
            except (OSError, json.JSONDecodeError) as exc:
                errors.append(f"{workspace_dir.name}: {json_name} 无法解析: {exc}")

    extracted = extract_workspace_report_ids(workspace_dir)
    required_ids = sorted(set(extracted["selected"]) | set(extracted["reader"]))
    selected_ids = extracted["selected"]
    reader_ids = extracted["reader"]
    report_json_ids = existing_report_ids(workspace_dir, ".json")
    report_md_ids = existing_report_ids(workspace_dir, ".md")
    hydrated_success_ids, hydrated_error_ids, hydrated_errors = hydrated_index_ids(workspace_dir)
    errors.extend(f"{workspace_dir.name}: {message}" for message in hydrated_errors)
    downloaded_entries, downloaded_errors = downloaded_index_entries(workspace_dir)
    errors.extend(f"{workspace_dir.name}: {message}" for message in downloaded_errors)
    pdf_reading_enabled = workspace_pdf_reading_enabled(workspace_dir)

    if required_ids and not topic_reports_dir(workspace_dir).is_dir():
        errors.append(f"{workspace_dir.name}: selected/reader 引用了报告 ID，但缺少 reports/ 目录")
    if selected_ids and report_reader_is_template(workspace_dir / "REPORT_READER_精读分析.md"):
        errors.append(f"{workspace_dir.name}: REPORT_READER_精读分析.md 仍是空模板，缺少第 6 步精读核心报告")
    if reader_ids:
        missing_reader_ids = [report_id for report_id in selected_ids if report_id not in reader_ids]
        if missing_reader_ids:
            errors.append(
                f"{workspace_dir.name}: REPORT_READER 未覆盖 selected-reports.md 中的报告: "
                + ", ".join(missing_reader_ids)
            )
        detail_based_lines = find_detail_based_reader_lines(workspace_dir / "REPORT_READER_精读分析.md")
        if pdf_reading_enabled and detail_based_lines:
            preview = "; ".join(detail_based_lines[:5])
            suffix = "" if len(detail_based_lines) <= 5 else f"; 另有 {len(detail_based_lines) - 5} 处"
            errors.append(
                f"{workspace_dir.name}: REPORT_READER 包含基于 detail 接口生成精读内容的痕迹，"
                f"请改为打开本地 PDF 原文核对: {preview}{suffix}"
            )

    missing_json = [report_id for report_id in required_ids if report_id not in report_json_ids]
    missing_md = [report_id for report_id in required_ids if report_id not in report_md_ids]
    missing_index = [
        report_id
        for report_id in required_ids
        if report_id not in hydrated_success_ids and report_id not in hydrated_error_ids
    ]
    failed_required = [report_id for report_id in required_ids if report_id in hydrated_error_ids]

    if missing_json:
        errors.append(f"{workspace_dir.name}: 缺少 reports/*.json: {', '.join(missing_json)}")
    if missing_md:
        errors.append(f"{workspace_dir.name}: 缺少 reports/*.md: {', '.join(missing_md)}")
    if missing_index:
        errors.append(f"{workspace_dir.name}: hydrated-reports.json 未登记: {', '.join(missing_index)}")
    if failed_required:
        errors.append(f"{workspace_dir.name}: 核心报告详情拉取失败: {', '.join(failed_required)}")

    if pdf_reading_enabled:
        missing_downloads: list[str] = []
        missing_download_files: list[str] = []
        non_pdf_downloads: list[str] = []
        for report_id in reader_ids:
            entry = downloaded_entries.get(report_id)
            if not entry:
                missing_downloads.append(report_id)
                continue
            output_path = Path(str(entry.get("output_path")))
            if not output_path.is_absolute():
                output_path = workspace_dir / output_path
            if not output_path.exists():
                missing_download_files.append(report_id)
            elif output_path.suffix.lower() != ".pdf" or not is_pdf_file(output_path):
                non_pdf_downloads.append(report_id)

        if missing_downloads:
            errors.append(f"{workspace_dir.name}: REPORT_READER 引用的报告缺少 PDF 下载索引: {', '.join(missing_downloads)}")
        if missing_download_files:
            errors.append(f"{workspace_dir.name}: REPORT_READER 引用的报告缺少本地 PDF 文件: {', '.join(missing_download_files)}")
        if non_pdf_downloads:
            errors.append(f"{workspace_dir.name}: REPORT_READER 引用的报告本地原文不是 PDF: {', '.join(non_pdf_downloads)}")

    if strict_placeholders:
        placeholder_hits: list[str] = []
        markdown_paths = list(sorted(workspace_dir.glob("*.md")))
        intermediate_dir = topic_intermediate_dir(workspace_dir)
        if intermediate_dir.is_dir():
            markdown_paths.extend(sorted(intermediate_dir.glob("*.md")))
        for markdown_path in markdown_paths:
            placeholder_hits.extend(find_placeholders(markdown_path))
        if placeholder_hits:
            preview = "; ".join(placeholder_hits[:8])
            suffix = "" if len(placeholder_hits) <= 8 else f"; 另有 {len(placeholder_hits) - 8} 处"
            errors.append(f"{workspace_dir.name}: 存在未填模板占位: {preview}{suffix}")

    junk_files = find_generated_junk(workspace_dir)
    if junk_files:
        relative = ", ".join(str(path.relative_to(workspace_dir)) for path in junk_files[:8])
        suffix = "" if len(junk_files) <= 8 else f", 另有 {len(junk_files) - 8} 项"
        errors.append(f"{workspace_dir.name}: 存在生成垃圾文件: {relative}{suffix}")

    return errors


def build_workspace_manifest(workspace_dir: Path, *, generated_by: str) -> dict[str, Any]:
    extracted = extract_workspace_report_ids(workspace_dir)
    reports_dir = topic_reports_dir(workspace_dir)
    hydrated_success_ids, hydrated_error_ids, hydrated_errors = hydrated_index_ids(workspace_dir)
    downloaded_entries, downloaded_errors = downloaded_index_entries(workspace_dir)
    topic_results_path = topic_artifact_path(workspace_dir, "topic-search-results.json")
    topic_results: dict[str, Any] = {}
    if topic_results_path.exists():
        try:
            loaded = read_json(topic_results_path)
            if isinstance(loaded, dict):
                topic_results = loaded
        except (OSError, json.JSONDecodeError):
            topic_results = {}

    pdf_reading_enabled = workspace_pdf_reading_enabled(workspace_dir)

    selected_ids = extracted["selected"]
    reader_ids = extracted["reader"]
    required_ids = sorted(set(selected_ids) | set(reader_ids))
    json_ids = existing_report_ids(workspace_dir, ".json")
    md_ids = existing_report_ids(workspace_dir, ".md")
    extracted_text_success_ids = sorted(
        report_id
        for report_id, entry in downloaded_entries.items()
        if isinstance(entry.get("text_extraction"), dict)
        and entry["text_extraction"].get("status") == "success"
    )
    extracted_text_error_ids = sorted(
        report_id
        for report_id, entry in downloaded_entries.items()
        if isinstance(entry.get("text_extraction"), dict)
        and entry["text_extraction"].get("status") == "error"
    )

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "generated_by": generated_by,
        "workspace_dir": str(workspace_dir.resolve()),
        "topic": topic_results.get("query", {}).get("keywords") if isinstance(topic_results.get("query"), dict) else "",
        "search_total": topic_results.get("total", 0),
        "raw_total": topic_results.get("raw_total", 0),
        "pdf_reading_enabled": pdf_reading_enabled,
        "selected_report_ids": selected_ids,
        "reader_report_ids": reader_ids,
        "required_detail_ids": required_ids,
        "report_json_count": len(list(reports_dir.glob("*.json"))) if reports_dir.exists() else 0,
        "report_markdown_count": len(list(reports_dir.glob("*.md"))) if reports_dir.exists() else 0,
        "hydrated_success_ids": sorted(hydrated_success_ids),
        "hydrated_error_ids": sorted(hydrated_error_ids),
        "hydrated_index_errors": hydrated_errors,
        "downloaded_success_ids": sorted(downloaded_entries),
        "downloaded_index_errors": downloaded_errors,
        "extracted_text_success_ids": extracted_text_success_ids,
        "extracted_text_error_ids": extracted_text_error_ids,
        "missing_download_ids": [
            report_id
            for report_id in reader_ids
            if pdf_reading_enabled and report_id not in downloaded_entries
        ],
        "missing_json_ids": [report_id for report_id in required_ids if report_id not in json_ids],
        "missing_markdown_ids": [report_id for report_id in required_ids if report_id not in md_ids],
    }
