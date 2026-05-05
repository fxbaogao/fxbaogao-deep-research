#!/usr/bin/env python3
"""Create a topic-centered research workspace with candidate reports."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from fxbaogao_client import (
    FxbaogaoError,
    RELATIVE_TIME_VALUES,
    build_report_url,
    copy_workspace_templates,
    download_report_pdf,
    format_search_output,
    get_report_detail,
    pdf_reading_enabled_from_env,
    parse_date_to_timestamp,
    render_detail_markdown,
    search_reports,
)
from workspace_utils import (
    build_workspace_manifest,
    extract_pdf_text,
    report_file_stem,
    require_pdf_text_dependencies,
    topic_intermediate_dir,
    unique_path,
    unique_report_artifact_paths,
    write_json,
)


DEFAULT_CANDIDATE_SIZE = 60
DEFAULT_DETAIL_COUNT = 50
DEFAULT_DETAIL_PER_SUBQUERY = 10


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="围绕行业、公司或主题创建深度研究工作区（需要 API key）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python3 prepare_topic_research.py "5G" --time last1year --detail-count 50
    python3 prepare_topic_research.py "新能源汽车行业" --time last1year --size 60 --detail-count 50 --detail-per-subquery 10 \\
        --subquery "市场需求=新能源汽车 销量 渗透率 价格战" \\
        --subquery "竞争格局=新能源车 比亚迪 特斯拉 新势力 份额" \\
        --subquery "三电产业链=动力电池 电驱 电控 盈利"
    python3 prepare_topic_research.py "光模块" --org "中信证券" --org "华泰证券" --detail-count 3 --json
    python3 prepare_topic_research.py "宁德时代" --start-date 2025-01-01 --end-date 2026-04-28
        """,
    )
    parser.add_argument("topic", help="研究主题，例如 5G / 光模块 / 宁德时代")
    parser.add_argument("--author", "-a", action="append", dest="authors", help="作者姓名，可重复指定")
    parser.add_argument("--org", "-o", action="append", dest="org_names", help="机构名称，可重复指定")
    parser.add_argument("--time", "-t", choices=sorted(RELATIVE_TIME_VALUES), help="相对时间范围")
    parser.add_argument("--start-date", help="开始日期，格式 YYYY-MM-DD")
    parser.add_argument("--end-date", help="结束日期，格式 YYYY-MM-DD")
    parser.add_argument("--start-ts", type=int, help="开始时间戳（毫秒）")
    parser.add_argument("--end-ts", type=int, help="结束时间戳（毫秒）")
    parser.add_argument(
        "--size",
        "-s",
        type=int,
        default=DEFAULT_CANDIDATE_SIZE,
        help=f"每个搜索维度候选研报数量，默认 {DEFAULT_CANDIDATE_SIZE}",
    )
    parser.add_argument(
        "--detail-count",
        type=int,
        default=None,
        help=(
            f"为候选研报抓取详情并写入 reports/ 的总数，默认不少于 {DEFAULT_DETAIL_COUNT}，"
            "并会按核心子问题数乘以 --detail-per-subquery 动态上调"
        ),
    )
    parser.add_argument(
        "--detail-per-subquery",
        type=int,
        default=DEFAULT_DETAIL_PER_SUBQUERY,
        help=f"每个核心子问题优先预拉取详情的报告数，默认 {DEFAULT_DETAIL_PER_SUBQUERY}",
    )
    parser.add_argument(
        "--subquery",
        action="append",
        dest="subqueries",
        default=[],
        help=(
            "按子问题定向搜索，可重复。格式：子问题=关键词；"
            "未指定时退回单一主题关键词搜索。"
        ),
    )
    parser.add_argument(
        "--no-broad-topic",
        action="store_true",
        help="提供 --subquery 时不额外补充一轮宽泛主题词搜索",
    )
    download_group = parser.add_mutually_exclusive_group()
    download_group.add_argument(
        "--download-pdfs",
        dest="download_pdfs",
        action="store_true",
        default=None,
        help="本次运行启用 PDF 精读模式，下载 PDF 并抽取文本",
    )
    download_group.add_argument(
        "--no-download-pdfs",
        dest="download_pdfs",
        action="store_false",
        help="本次运行关闭 PDF 精读模式，只使用 detail 信息",
    )
    parser.add_argument(
        "--download-count",
        type=int,
        default=None,
        help="PDF 精读模式下下载 PDF 的报告数量，默认与 --detail-count 一致",
    )
    parser.add_argument("--no-extract-text", action="store_true", help="下载 PDF 后不抽取 PDF 文本")
    parser.add_argument(
        "--output-root",
        default=None,
        help="研究工作区根目录，默认为当前项目目录下的 workspace/",
    )
    parser.add_argument("--force", action="store_true", help="覆盖已有目录中的同名文件")
    parser.add_argument("--json", "-j", action="store_true", help="输出 JSON")
    return parser


def resolve_time_args(args: argparse.Namespace) -> tuple[int | None, int | str | None]:
    has_explicit_range = any(
        [args.start_date, args.end_date, args.start_ts is not None, args.end_ts is not None]
    )
    if args.time and has_explicit_range:
        raise ValueError("--time 不能与显式时间范围参数同时使用")
    if args.time:
        return None, args.time

    start_time = args.start_ts
    end_time = args.end_ts
    if args.start_date:
        start_time = parse_date_to_timestamp(args.start_date)
    if args.end_date:
        end_time = parse_date_to_timestamp(args.end_date, end_of_day=True)
    if start_time is not None and isinstance(end_time, int) and start_time > end_time:
        raise ValueError("开始时间不能晚于结束时间")
    return start_time, end_time


def write_text(path: Path, content: str) -> None:
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def report_reading_url(report: dict[str, object]) -> str:
    report_url = report.get("report_url")
    if isinstance(report_url, str) and report_url.strip():
        return report_url.strip()
    doc_id = report.get("doc_id")
    if doc_id:
        return build_report_url(str(doc_id))
    return ""


def report_reading_link(report: dict[str, object]) -> str:
    doc_id = report.get("doc_id")
    title = str(report.get("title") or f"研报 {doc_id or ''}").strip()
    label = f"{doc_id} - {title}" if doc_id else title
    url = report_reading_url(report)
    return f"[{label}]({url})" if url else label


def slugify_topic(topic: str) -> str:
    slug = topic.strip()
    slug = re.sub(r"[\\/:*?\"<>|]+", "-", slug)
    slug = re.sub(r"\s+", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug)
    slug = slug.strip("-")
    return slug or "topic"


def parse_subqueries(
        topic: str,
        raw_subqueries: list[str],
        *,
        include_broad_topic: bool,
) -> list[dict[str, str]]:
    if not raw_subqueries:
        return [{"label": "主题关键词", "keywords": topic}]

    search_plan: list[dict[str, str]] = []
    for index, raw_value in enumerate(raw_subqueries, start=1):
        value = raw_value.strip()
        if not value:
            continue

        if "=" in value:
            label, keywords = value.split("=", 1)
        elif "|" in value:
            label, keywords = value.split("|", 1)
        else:
            label = f"子问题 {index}"
            keywords = value

        label = label.strip() or f"子问题 {index}"
        keywords = keywords.strip()
        if not keywords:
            raise ValueError(f"--subquery 第 {index} 项缺少搜索关键词")
        search_plan.append({"label": label, "keywords": keywords})

    if not search_plan:
        raise ValueError("--subquery 不能为空")

    if include_broad_topic:
        existing_keywords = {item["keywords"] for item in search_plan}
        if topic not in existing_keywords:
            search_plan.append({"label": "总览补充", "keywords": topic})
    return search_plan


def append_unique(target: list[Any], value: Any) -> None:
    if value not in target:
        target.append(value)


def core_subquery_labels(search_plan: list[dict[str, str]]) -> list[str]:
    return [
        item["label"]
        for item in search_plan
        if item["label"] not in {"总览补充", "主题关键词"}
    ]


def resolve_detail_count(
        explicit_detail_count: int | None,
        search_plan: list[dict[str, str]],
        detail_per_subquery: int,
) -> int:
    if explicit_detail_count is not None:
        return max(0, explicit_detail_count)

    core_count = len(core_subquery_labels(search_plan))
    if core_count:
        return max(DEFAULT_DETAIL_COUNT, core_count * max(0, detail_per_subquery))
    return DEFAULT_DETAIL_COUNT


def report_key(report: dict[str, object]) -> str:
    doc_id = report.get("doc_id")
    if doc_id:
        return str(doc_id)
    return "|".join(
        [
            str(report.get("title") or ""),
            str(report.get("org_name") or ""),
            str(report.get("publish_date") or ""),
        ]
    )


def merge_search_results(
        *,
        topic: str,
        search_plan: list[dict[str, str]],
        authors: list[str] | None,
        org_names: list[str] | None,
        start_time: int | None,
        end_time: int | str | None,
        page_size: int,
) -> dict[str, object]:
    if len(search_plan) == 1 and search_plan[0]["label"] == "主题关键词":
        result = search_reports(
            keywords=topic,
            authors=authors,
            org_names=org_names,
            start_time=start_time,
            end_time=end_time,
            page_size=page_size,
        )
        result["search_plan"] = search_plan
        for rank, report in enumerate(result.get("reports") or [], start=1):
            report["matched_subquestions"] = [search_plan[0]["label"]]
            report["search_keywords"] = [search_plan[0]["keywords"]]
            report["source_ranks"] = [{"subquestion": search_plan[0]["label"], "rank": rank}]
        return result

    merged_reports: list[dict[str, object]] = []
    report_index: dict[str, dict[str, object]] = {}
    search_results: list[dict[str, object]] = []
    raw_total = 0

    for query in search_plan:
        result = search_reports(
            keywords=query["keywords"],
            authors=authors,
            org_names=org_names,
            start_time=start_time,
            end_time=end_time,
            page_size=page_size,
        )
        reports = result.get("reports") or []
        raw_total += int(result.get("total") or 0)
        search_results.append(
            {
                "label": query["label"],
                "keywords": query["keywords"],
                "total": result.get("total", 0),
                "reports": reports,
            }
        )

        for rank, report in enumerate(reports, start=1):
            dedupe_key = report_key(report)
            if dedupe_key in report_index:
                existing = report_index[dedupe_key]
                append_unique(existing.setdefault("matched_subquestions", []), query["label"])
                append_unique(existing.setdefault("search_keywords", []), query["keywords"])
                existing.setdefault("source_ranks", []).append(
                    {"subquestion": query["label"], "rank": rank}
                )
                continue

            report_copy = dict(report)
            report_copy["matched_subquestions"] = [query["label"]]
            report_copy["search_keywords"] = [query["keywords"]]
            report_copy["source_ranks"] = [{"subquestion": query["label"], "rank": rank}]
            report_index[dedupe_key] = report_copy
            merged_reports.append(report_copy)

    return {
        "query": {
            "keywords": topic,
            "authors": authors or [],
            "org_names": org_names or [],
            "start_time": start_time,
            "end_time": end_time,
        },
        "search_plan": search_plan,
        "search_results": search_results,
        "total": len(merged_reports),
        "raw_total": raw_total,
        "reports": merged_reports,
    }


def select_reports_for_detail(
        reports: list[dict[str, object]],
        detail_count: int,
        *,
        detail_per_subquery: int,
        search_plan: list[dict[str, str]],
) -> list[dict[str, object]]:
    if detail_count <= 0:
        return []

    buckets: dict[str, list[dict[str, object]]] = {}
    ordered_labels: list[str] = []
    for report in reports:
        labels = report.get("matched_subquestions") or ["未归类"]
        for raw_label in labels:
            label = str(raw_label)
            if label not in buckets:
                buckets[label] = []
                ordered_labels.append(label)
            buckets[label].append(report)

    selected: list[dict[str, object]] = []
    selected_keys: set[str] = set()

    def take_next(label: str) -> dict[str, object] | None:
        while buckets.get(label):
            report = buckets[label].pop(0)
            key = report_key(report)
            if key in selected_keys:
                continue
            selected_keys.add(key)
            return report
        return None

    planned_labels = core_subquery_labels(search_plan)
    quota_labels = [label for label in planned_labels if label in buckets]
    if not quota_labels:
        quota_labels = ordered_labels

    for _ in range(max(0, detail_per_subquery)):
        added_this_round = False
        for label in quota_labels:
            if len(selected) >= detail_count:
                break
            report = take_next(label)
            if report:
                selected.append(report)
                added_this_round = True
        if not added_this_round:
            break

    while len(selected) < detail_count:
        added_this_round = False
        for label in ordered_labels:
            if len(selected) >= detail_count:
                break
            report = take_next(label)
            if report:
                selected.append(report)
                added_this_round = True
        if not added_this_round:
            break

    return selected


def render_candidate_reports_markdown(topic: str, result: dict[str, object]) -> str:
    reports = result.get("reports") or []
    total = result.get("total") or 0
    raw_total = result.get("raw_total")
    search_plan = result.get("search_plan") or []
    lines = [
        f"# 候选研报清单：{topic}",
        "",
        f"- 搜索主题：{topic}",
        f"- 去重后候选：{total}",
    ]
    if raw_total is not None:
        lines.append(f"- 子问题搜索命中合计：{raw_total}")
    lines.extend(
        [
        f"- 当前展示：{len(reports)}",
        "",
        ]
    )
    if search_plan:
        lines.extend(
            [
                "## 搜索矩阵",
                "",
                "| 子问题 | 搜索关键词 |",
                "|--------|------------|",
            ]
        )
        for query in search_plan:
            lines.append(f"| {query.get('label')} | {query.get('keywords')} |")
        lines.append("")

    lines.extend(
        [
        "## 候选列表",
        "",
        ]
    )
    for index, report in enumerate(reports, start=1):
        authors = "、".join(report.get("authors") or []) or "未知作者"
        snippets = report.get("snippets") or []
        matched_subquestions = "、".join(report.get("matched_subquestions") or [])
        search_keywords = "；".join(report.get("search_keywords") or [])
        lines.extend(
            [
                f"### {index}. {report_reading_link(report)}",
                "",
                f"- 文档 ID：{report.get('doc_id')}",
                f"- 机构：{report.get('org_name') or '未知机构'}",
                f"- 作者：{authors}",
                f"- 日期：{report.get('publish_date') or '未知日期'}",
                f"- 行业：{report.get('industry_name') or '未知行业'}",
                f"- 阅读链接：{report_reading_url(report)}",
            ]
        )
        if matched_subquestions:
            lines.append(f"- 对应子问题：{matched_subquestions}")
        if search_keywords:
            lines.append(f"- 命中关键词：{search_keywords}")
        if snippets:
            lines.append(f"- 摘要片段：{snippets[0]}")
        lines.append("")
    lines.extend(
        [
            "## 建议分层",
            "",
            "- 核心必读：",
            "- 补充参考：",
            "- 背景材料：",
        ]
    )
    return "\n".join(lines)


def render_selected_reports_markdown(topic: str, reports: list[dict[str, object]]) -> str:
    lines = [
        f"# 已选核心研报：{topic}",
        "",
        "把真正进入分析主线的报告列在这里。",
        "",
    ]
    for report in reports:
        lines.extend(
            [
                f"## {report_reading_link(report)}",
                "",
                f"- 机构：{report.get('org_name') or '未知机构'}",
                f"- 日期：{report.get('publish_date') or '未知日期'}",
                f"- 阅读链接：{report_reading_url(report)}",
                "- 选入理由：",
                f"- 主要回答哪个子问题：{'、'.join(report.get('matched_subquestions') or [])}",
                "- 还需要核对哪些关键数字或假设：",
                "",
            ]
        )
    if not reports:
        lines.append("- 暂未选定核心研报。")
    return "\n".join(lines)


def seed_final_report_markdown(topic: str, selected_reports: list[dict[str, object]]) -> str:
    lines = [
        "# 调研报告",
        "",
        "## 摘要",
        "",
        "- 核心结论：",
        "- 最重要的证据：",
        "- 最大不确定性：",
        "",
        "## 1. 研究边界与子问题回顾",
        "",
        "- 主题：",
        "- 对象：",
        "- 时间：",
        "- 地域：",
        "- 产业链环节 / 场景：",
        "",
        "### 子问题清单",
        "",
        "1.",
        "2.",
        "3.",
        "",
        "## 2. 核心发现（按子问题展开）",
        "",
        "### 子问题 1",
        "",
        "- 结论：",
        "- 关键事实：",
        "- 代表性来源：",
        "- 共识 / 分歧：",
        "",
        "### 子问题 2",
        "",
        "- 结论：",
        "- 关键事实：",
        "- 代表性来源：",
        "- 共识 / 分歧：",
        "",
        "## 3. 综合判断",
        "",
        "- 主线判断：",
        "- 关键驱动：",
        "- 反向风险：",
        "",
        "## 4. 关键风险与不确定性",
        "",
        "- ",
        "",
        "## 5. 下一步值得深入的方向",
        "",
        "- ",
        "",
        "## 参考资料",
        "",
    ]
    if selected_reports:
        lines.extend(f"- {report_reading_link(report)}" for report in selected_reports)
    else:
        lines.append("- ")
    return "\n".join(lines)


def render_research_brief(
        topic: str,
        result: dict[str, object],
        selected_count: int,
        hydrated_success_count: int,
        hydration_error_count: int,
        detail_per_subquery: int,
        workspace_dir: Path,
) -> str:
    query = result.get("query") or {}
    reports = result.get("reports") or []
    search_plan = result.get("search_plan") or []
    lines = [
        f"# 研究任务：{topic}",
        "",
        "## 研究主题",
        "",
        f"- 主题：{topic}",
        f"- 作者过滤：{'、'.join(query.get('authors') or []) if query.get('authors') else ''}",
        f"- 机构过滤：{'、'.join(query.get('org_names') or []) if query.get('org_names') else ''}",
        f"- 开始时间：{query.get('start_time') or ''}",
        f"- 结束时间：{query.get('end_time') or ''}",
        "",
        "## 当前资料状态",
        "",
        f"- 已抓取候选研报：{len(reports)}",
        f"- 已自动选入详情队列：{selected_count}",
        f"- 已成功预拉取详情：{hydrated_success_count}",
        f"- 详情拉取失败：{hydration_error_count}",
        f"- 子问题精读目标：每个核心子问题约 {detail_per_subquery} 篇",
        f"- 工作目录：{workspace_dir}",
        "",
    ]
    if search_plan:
        lines.extend(
            [
                "## 搜索矩阵",
                "",
                "| 子问题 | 搜索关键词 |",
                "|--------|------------|",
            ]
        )
        for item in search_plan:
            lines.append(f"| {item.get('label')} | {item.get('keywords')} |")
        lines.append("")

    lines.extend(
        [
        "## 推荐顺序",
        "",
        "1. 先核对 `intermediate/00_问题拆解.md`，确保研究边界和搜索矩阵完整。",
        "2. 读 `intermediate/candidate-reports.md`，把候选研报分层。",
        "3. 读 `intermediate/reports/` 下的重点报告详情，填 `intermediate/selected-reports.md`。",
        "4. 把核心报告沉淀到 `intermediate/01_资料来源.md` 和 `intermediate/02_事实卡片.md`。",
        "5. 再进入 `intermediate/03_对比框架.md`、`intermediate/04_推导过程.md` 和 `FINAL_调研报告.md`。",
    ]
    )
    return "\n".join(lines)


def render_problem_breakdown_markdown(topic: str, result: dict[str, object]) -> str:
    query = result.get("query") or {}
    search_plan = result.get("search_plan") or [{"label": "主题关键词", "keywords": topic}]
    lines = [
        "# 问题拆解",
        "",
        "## 研究主题陈述",
        "",
        f"- 主题：{topic}",
        "- 核心问题：",
        "- 希望形成的判断：",
        "",
        "## 研究边界",
        "",
        "- 对象：",
        f"- 时间：{query.get('start_time') or ''} 至 {query.get('end_time') or ''}",
        "- 地域：",
        "- 产业链环节 / 场景：",
        "- 明确排除：",
        "",
        "## MECE 子问题与搜索维度",
        "",
        "| 子问题 | 要验证的判断 | 搜索关键词 | 备注 |",
        "|--------|--------------|------------|------|",
    ]
    for item in search_plan:
        lines.append(f"| {item.get('label')} | | {item.get('keywords')} | |")
    lines.extend(
        [
            "",
            "## 搜索前置检查",
            "",
            "- 已完成研究主题和边界：",
            "- 子问题之间没有明显重叠：",
            "- 子问题合起来能覆盖核心问题：",
            "- 每个子问题都有可执行搜索关键词：",
            "",
            "## 候选研报筛选标准",
            "",
            "- 相关性：",
            "- 时效性：",
            "- 机构 / 作者权威性：",
            "- 计划保留篇数：",
            "",
            "## 当前缺口",
            "",
            "- 还缺哪些资料：",
            "- 哪些数字或假设必须二次核对：",
        ]
    )
    return "\n".join(lines)


def seed_sources_markdown(
        topic: str,
        selected_details: list[dict[str, object]],
        *,
        pdf_mode: bool = False,
) -> str:
    lines = [
        f"# 资料来源：{topic}",
        "",
        "## 使用说明",
        "",
        "- 只登记进入分析主线的资料",
        "- 优先记录核心研报，再补官方披露、财报、协会或监管数据",
        "- 每条资料都要标明它主要回答哪个子问题",
        "",
    ]
    if not selected_details:
        lines.extend(
            [
                "## 资料 1",
                "",
                "- 类型：核心研报 / 补充研报 / 官方披露 / 行业数据 / 其他",
                "- 标题：",
                "- 文档 ID / 链接：",
                "- 机构 / 作者：",
                "- 发布日期：",
                "- 对应子问题：",
                "- 选入层级：核心必读 / 补充参考 / 背景材料",
                "- 与研究问题的关系：",
                "- 摘要：",
                "- 可提取的关键事实：",
                "- 还需核对的点：",
            ]
        )
        return "\n".join(lines)

    for index, detail in enumerate(selected_details, start=1):
        authors = "、".join(detail.get("authors") or []) if detail.get("authors") else ""
        matched_subquestions = "、".join(detail.get("matched_subquestions") or [])
        lines.extend(
            [
                f"## 资料 {index}",
                "",
                "- 类型：核心研报",
                f"- 标题：{report_reading_link(detail)}",
                f"- 文档 ID：{detail.get('doc_id') or ''}",
                f"- 阅读链接：{report_reading_url(detail)}",
                f"- 机构 / 作者：{detail.get('org_name') or ''} / {authors}",
                f"- 发布日期：{detail.get('publish_date') or ''}",
                f"- 对应子问题：{matched_subquestions}",
                "- 选入层级：核心必读",
                "- 与研究问题的关系：",
                "- 摘要：可用 detail 信息初筛，精读结论必须在本地 PDF 原文核对后填写。"
                if pdf_mode
                else f"- 摘要：{(detail.get('summary') or [''])[0] if detail.get('summary') else ''}",
                "- 可提取的关键事实：",
                "- 还需核对的点：",
                "",
            ]
        )
    return "\n".join(lines)


def download_entry_by_id(downloaded_reports: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    entries: dict[str, dict[str, object]] = {}
    for item in downloaded_reports:
        doc_id = item.get("doc_id")
        if doc_id is not None:
            entries[str(doc_id)] = item
    return entries


def pdf_source_line(detail: dict[str, object], download_entries: dict[str, dict[str, object]]) -> str:
    doc_id = detail.get("doc_id")
    entry = download_entries.get(str(doc_id))
    if not entry:
        return "- 本地 PDF：未下载，先运行 `python3 scripts/download_report.py --workspace <topic工作区> --strict`。"
    if entry.get("status") != "success":
        return f"- 本地 PDF：下载失败，原因：{entry.get('message') or '未知错误'}"
    output_path = entry.get("output_path")
    return f"- 本地 PDF：{output_path or '下载索引缺少 output_path'}"


def seed_detail_report_reader_markdown(topic: str, selected_details: list[dict[str, object]]) -> str:
    lines = [
        "# REPORT_READER 精读分析",
        "",
        "## 精读总览",
        "",
        f"- 研究主题：{topic}",
        f"- 精读对象：{len(selected_details)} 篇已拉取详情资料",
        "- 生成方式：PDF 精读模式未开启，基于 detail 接口摘要、目录和正文摘录生成精读底稿。",
        "- 使用原则：detail 信息可用于后续事实卡片、对比框架和最终报告；如需原文级核对，请在 `.env` 开启 `FXBAOGAO_USE_PDF_READING=true`。",
        "",
    ]
    if not selected_details:
        lines.extend(
            [
                "## 当前状态",
                "",
                "- 尚未成功拉取报告详情，无法生成精读底稿。",
            ]
        )
        return "\n".join(lines)

    for detail in selected_details:
        doc_id = detail.get("doc_id") or ""
        title = detail.get("title") or f"研报 {doc_id}"
        authors = "、".join(detail.get("authors") or []) or "未知作者"
        matched_subquestions = "、".join(detail.get("matched_subquestions") or []) or "未归类"
        summary = detail.get("summary") or []
        content = detail.get("content") or []
        evidence_items = list(summary[:5])
        if len(evidence_items) < 5:
            evidence_items.extend(content[: 5 - len(evidence_items)])
        catalog_items = detail.get("catalog_list") or []
        report_type = detail.get("report_type") or detail.get("reportType") or detail.get("industry_name") or "行业/主题资料"
        lines.extend(
            [
                f"## {report_reading_link(detail)}",
                "",
                "### 报告基础信息",
                "",
                f"- 文档 ID：{doc_id}",
                f"- 阅读链接：{report_reading_url(detail)}",
                f"- 机构：{detail.get('org_name') or '未知机构'}",
                f"- 作者：{authors}",
                f"- 日期：{detail.get('publish_date') or '未知日期'}",
                f"- 报告类型：{report_type}",
                f"- 对应子问题：{matched_subquestions}",
                "",
                "### 这篇报告到底在回答什么",
                "",
                f"- 核心判断：{summary[0] if summary else 'detail 未返回明确摘要，需结合正文摘录继续判断。'}",
                f"- 关键结论：{summary[1] if len(summary) > 1 else '需从 detail 正文摘录和同类报告中交叉提炼。'}",
                "- 结论成立的前提：以报告 detail 披露的假设、口径和正文摘录为准，后续在对比框架中交叉验证。",
                "",
                "### 假设与证据",
                "",
                "- 关键假设：优先从摘要、目录和正文摘录中提取；缺失时标记为待核对。",
                "- 核心数据 / 图表：",
            ]
        )
        if evidence_items:
            for item in evidence_items:
                lines.append(f"  - {item}")
        else:
            lines.append("  - detail 未返回可用摘要或正文摘录。")
        lines.extend(
            [
                f"- 主要证据来源：{'；'.join(catalog_items[:3]) if catalog_items else 'detail 摘要和正文摘录'}",
                "- 口径或样本限制：detail 内容可能不完整，关键数字需在交叉报告或原文中复核。",
                "",
                "### 与其它报告的关系",
                "",
                "- 共识：作为同主题核心资料的一部分，用于验证不同机构对同一子问题的判断。",
                "- 分歧：重点比较预测值、核心假设、风险表述和估值/行业判断口径。",
                "- 需要交叉验证的点：detail 中的预测值、目标价、政策判断和行业规模数据。",
                "",
                "### 风险提示",
                "",
                "- 报告明确写出的风险：优先从 detail 摘要、目录和正文摘录中提取。",
                "- 报告未充分展开但需要警惕的风险：标记为对比框架中的待核对项。",
                "",
                "### 可沉淀到事实卡片的内容",
                "",
                f"- 事实 1：{evidence_items[0] if evidence_items else '无可用事实摘录'}",
                f"- 事实 2：{evidence_items[1] if len(evidence_items) > 1 else '需继续提取第二个事实点'}",
                "",
                "### 一句话判断",
                "",
                f"- {title} 可作为对应子问题的 detail 证据来源，最终结论需放入对比框架中统一归纳。",
                "",
            ]
        )
    return "\n".join(lines)


def seed_report_reader_markdown(
        topic: str,
        selected_details: list[dict[str, object]],
        *,
        downloaded_reports: list[dict[str, object]] | None = None,
        pdf_mode: bool = False,
) -> str:
    if not pdf_mode:
        return seed_detail_report_reader_markdown(topic, selected_details)

    download_entries = download_entry_by_id(downloaded_reports or [])
    lines = [
        "# REPORT_READER 精读分析",
        "",
        "## 精读总览",
        "",
        f"- 研究主题：{topic}",
        f"- 精读对象：{len(selected_details)} 篇核心资料",
        "- 生成方式：detail 仅用于筛选、元数据和 PDF 下载地址；本表只预置元数据和本地 PDF 路径。",
        "- 使用原则：所有核心判断、关键数据、图表证据和风险提示必须来自 `intermediate/downloads/` 下的 PDF 原文。",
        "",
    ]
    if not selected_details:
        lines.extend(
            [
                "## 当前状态",
                "",
                "- 尚未成功拉取报告元数据，无法生成 PDF 精读工作表。",
            ]
        )
        return "\n".join(lines)

    for detail in selected_details:
        doc_id = detail.get("doc_id") or ""
        authors = "、".join(detail.get("authors") or []) or "未知作者"
        matched_subquestions = "、".join(detail.get("matched_subquestions") or []) or "未归类"
        report_type = detail.get("report_type") or detail.get("reportType") or detail.get("industry_name") or "行业/主题资料"
        lines.extend(
            [
                f"## {report_reading_link(detail)}",
                "",
                "### 报告基础信息",
                "",
                f"- 文档 ID：{doc_id}",
                f"- 阅读链接：{report_reading_url(detail)}",
                pdf_source_line(detail, download_entries),
                f"- 机构：{detail.get('org_name') or '未知机构'}",
                f"- 作者：{authors}",
                f"- 日期：{detail.get('publish_date') or '未知日期'}",
                f"- 报告类型：{report_type}",
                f"- 对应子问题：{matched_subquestions}",
                "",
                "### PDF 原文核对记录",
                "",
                "- 已打开 PDF：否",
                "- 已核对页码范围：",
                "- 重点页码 / 图表：",
                "- 不采用详情接口摘要作为证据：是",
                "",
                "### 这篇报告到底在回答什么",
                "",
                "- 核心判断：",
                "- 关键结论：",
                "- 结论成立的前提：",
                "",
                "### 假设与证据",
                "",
                "- 关键假设：",
                "- 核心数据 / 图表：",
                "  - 页码：",
                "  - 原文数据：",
                "  - 使用口径：",
                "- 主要证据来源：PDF 原文页码 / 图表编号",
                "- 口径或样本限制：",
            ]
        )
        lines.extend(
            [
                "### 与其它报告的关系",
                "",
                "- 共识：",
                "- 分歧：",
                "- 需要交叉验证的点：",
                "",
                "### 风险提示",
                "",
                "- 报告明确写出的风险：",
                "- 报告未充分展开但需要警惕的风险：",
                "",
                "### 可沉淀到事实卡片的内容",
                "",
                "- 事实 1：",
                "- 来源页码：",
                "- 事实 2：",
                "- 来源页码：",
                "",
                "### 一句话判断",
                "",
                "- ",
                "",
            ]
        )
    return "\n".join(lines)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        pdf_mode = pdf_reading_enabled_from_env(default=False) if args.download_pdfs is None else args.download_pdfs
    except ValueError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        sys.exit(1)
    if pdf_mode and not args.no_extract_text:
        try:
            require_pdf_text_dependencies()
        except RuntimeError as exc:
            print(f"错误: {exc}", file=sys.stderr)
            sys.exit(1)

    try:
        start_time, end_time = resolve_time_args(args)
        search_plan = parse_subqueries(
            args.topic,
            args.subqueries,
            include_broad_topic=not args.no_broad_topic,
        )
        detail_count = resolve_detail_count(
            args.detail_count,
            search_plan,
            args.detail_per_subquery,
        )
        result = merge_search_results(
            topic=args.topic,
            search_plan=search_plan,
            authors=args.authors,
            org_names=args.org_names,
            start_time=start_time,
            end_time=end_time,
            page_size=args.size,
        )
    except (ValueError, FxbaogaoError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        sys.exit(1)

    slug = slugify_topic(args.topic)
    date_suffix = datetime.now().strftime("%Y%m%d")
    default_root = Path.cwd() / "workspace"
    output_root = Path(args.output_root).expanduser().resolve() if args.output_root else default_root
    workspace_dir = output_root / f"topic-{slug}-{date_suffix}"
    if workspace_dir.exists() and not args.force:
        print(f"错误: 目录已存在: {workspace_dir}。如需覆盖请加 --force", file=sys.stderr)
        sys.exit(1)

    workspace_dir.mkdir(parents=True, exist_ok=True)
    intermediate_dir = topic_intermediate_dir(workspace_dir)
    intermediate_dir.mkdir(exist_ok=True)
    reports_dir = intermediate_dir / "reports"
    reports_dir.mkdir(exist_ok=True)
    copy_workspace_templates(Path(__file__).resolve().parent.parent, intermediate_dir)
    for root_template_name in ("REPORT_READER_精读分析.md", "FINAL_调研报告.md"):
        generated_template = intermediate_dir / root_template_name
        if generated_template.exists():
            generated_template.replace(workspace_dir / root_template_name)

    search_json = intermediate_dir / "topic-search-results.json"
    candidate_md = intermediate_dir / "candidate-reports.md"
    selected_md = intermediate_dir / "selected-reports.md"
    brief_md = intermediate_dir / "research-brief.md"
    problem_md = intermediate_dir / "00_问题拆解.md"
    report_index_json = intermediate_dir / "hydrated-reports.json"
    download_index_json = intermediate_dir / "downloaded-reports.json"
    search_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_text(problem_md, render_problem_breakdown_markdown(args.topic, result))
    write_text(candidate_md, render_candidate_reports_markdown(args.topic, result))

    selected_reports = select_reports_for_detail(
        result.get("reports") or [],
        detail_count,
        detail_per_subquery=args.detail_per_subquery,
        search_plan=search_plan,
    )
    hydrated_reports: list[dict[str, object]] = []
    detail_by_id: dict[str, dict[str, object]] = {}
    reserved_report_paths: set[Path] = set()
    for report in selected_reports:
        doc_id = report.get("doc_id")
        if not doc_id:
            continue
        try:
            detail_keyword = (report.get("search_keywords") or [args.topic])[0]
            detail = get_report_detail(int(doc_id), keyword=str(detail_keyword))
        except FxbaogaoError as exc:
            hydrated_reports.append(
                {
                    "doc_id": doc_id,
                    "title": report.get("title") or "",
                    "error": str(exc),
                    "status": "error",
                }
            )
            continue

        detail["matched_subquestions"] = report.get("matched_subquestions") or []
        detail["search_keywords"] = report.get("search_keywords") or []
        detail_by_id[str(detail.get("doc_id"))] = detail
        json_path, markdown_path = unique_report_artifact_paths(
            reports_dir,
            detail,
            reserved=reserved_report_paths,
        )
        reserved_report_paths.update({json_path, markdown_path})
        hydrated_reports.append(
            {
                "doc_id": detail.get("doc_id"),
                "title": detail.get("title"),
                "matched_subquestions": report.get("matched_subquestions") or [],
                "file_stem": json_path.stem,
                "path_json": str(json_path),
                "path_markdown": str(markdown_path),
                "status": "hydrated",
            }
        )
        json_path.write_text(
            json.dumps(detail, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        write_text(markdown_path, render_detail_markdown(detail))

    downloaded_reports: list[dict[str, object]] = []
    if pdf_mode:
        download_count = args.download_count if args.download_count is not None else detail_count
        download_dir = intermediate_dir / "downloads"
        text_dir = intermediate_dir / "text"
        download_dir.mkdir(exist_ok=True)
        if not args.no_extract_text:
            text_dir.mkdir(exist_ok=True)
        reserved_download_paths: set[Path] = set()
        for report in selected_reports[: max(0, download_count)]:
            doc_id = report.get("doc_id")
            if not doc_id:
                continue
            output_path = unique_path(
                download_dir,
                report_file_stem(report),
                ".pdf",
                reserved=reserved_download_paths,
            )
            reserved_download_paths.add(output_path)
            try:
                detail_for_download = detail_by_id.get(str(doc_id))
                preferred_url = detail_for_download.get("pdf_url") if detail_for_download else None
                download_result = download_report_pdf(int(doc_id), output_path, preferred_url=preferred_url)
                downloaded_path = Path(str(download_result.get("output_path") or output_path))
                reserved_download_paths.add(downloaded_path)
                download_result["title"] = report.get("title") or ""
                download_result["org_name"] = report.get("org_name") or ""
                if not args.no_extract_text:
                    download_result["text_extraction"] = extract_pdf_text(downloaded_path, text_dir / f"{doc_id}.txt")
            except FxbaogaoError as exc:
                downloaded_reports.append(
                    {
                        "doc_id": doc_id,
                        "title": report.get("title") or "",
                        "status": "error",
                        "message": str(exc),
                    }
                )
                continue
            downloaded_reports.append(download_result)

    write_text(selected_md, render_selected_reports_markdown(args.topic, selected_reports))
    write_text(
        brief_md,
        render_research_brief(
            args.topic,
            result,
            len(selected_reports),
            len([item for item in hydrated_reports if not item.get("error")]),
            len([item for item in hydrated_reports if item.get("error")]),
            args.detail_per_subquery,
            workspace_dir,
        ),
    )
    report_index_json.write_text(json.dumps(hydrated_reports, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if pdf_mode:
        download_index_json.write_text(json.dumps(downloaded_reports, ensure_ascii=False, indent=2) + "\n",
                                       encoding="utf-8")

    # 用实际候选报告初始化资料来源，而不是保留完全空白模板。
    selected_details: list[dict[str, object]] = []
    for item in hydrated_reports:
        detail_file = Path(str(item.get("path_json") or ""))
        if detail_file.exists():
            selected_details.append(json.loads(detail_file.read_text(encoding="utf-8")))
    write_text(intermediate_dir / "01_资料来源.md", seed_sources_markdown(args.topic, selected_details, pdf_mode=pdf_mode))
    write_text(
        workspace_dir / "REPORT_READER_精读分析.md",
        seed_report_reader_markdown(
            args.topic,
            selected_details,
            downloaded_reports=downloaded_reports,
            pdf_mode=pdf_mode,
        ),
    )
    write_text(
        workspace_dir / "FINAL_调研报告.md",
        seed_final_report_markdown(args.topic, selected_reports),
    )
    manifest_path = intermediate_dir / "workspace-manifest.json"
    write_json(manifest_path, build_workspace_manifest(workspace_dir, generated_by="prepare_topic_research.py"))

    payload = {
        "topic": args.topic,
        "workspace_dir": str(workspace_dir),
        "search_total": result.get("total", 0),
        "selected_count": len(selected_reports),
        "hydrated_success_count": len([item for item in hydrated_reports if not item.get("error")]),
        "hydration_error_count": len([item for item in hydrated_reports if item.get("error")]),
        "pdf_reading_enabled": pdf_mode,
        "hydrated_reports": hydrated_reports,
        "downloaded_reports": downloaded_reports,
        "files": {
            "problem_breakdown": str(problem_md),
            "research_brief": str(brief_md),
            "candidate_reports": str(candidate_md),
            "selected_reports": str(selected_md),
            "search_json": str(search_json),
            "hydrated_index": str(report_index_json),
            "downloaded_index": str(download_index_json) if pdf_mode else "",
            "manifest": str(manifest_path),
        },
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print(f"已创建主题研究工作区: {workspace_dir}")
    print(format_search_output(result))
    print("")
    print(f"问题拆解: {problem_md}")
    print(f"研究摘要: {brief_md}")
    print(f"候选清单: {candidate_md}")
    print(f"重点报告目录: {reports_dir}")
    print(f"工作区清单: {manifest_path}")
    print(f"精读模式: {'PDF 原文' if pdf_mode else 'detail 信息'}")
    if pdf_mode:
        print(f"PDF 下载索引: {download_index_json}")


if __name__ == "__main__":
    main()
