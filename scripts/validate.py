#!/usr/bin/env python3
"""Validate the fxbaogao deep research skill structure and workspaces."""

from __future__ import annotations

import argparse
import importlib.util
import py_compile
import sys
from pathlib import Path

from workspace_utils import topic_workspace_dirs, validate_topic_workspace

REQUIRED_FILES = [
    "SKILL.md",
    "requirements.txt",
    ".env.example",
    "references/runtime-config.md",
    "references/research-playbook.md",
    "references/report-reader-playbook.md",
    "scripts/fxbaogao_client.py",
    "scripts/search_reports.py",
    "scripts/get_report_detail.py",
    "scripts/download_report.py",
    "scripts/prepare_topic_research.py",
    "scripts/prepare_research_workspace.py",
    "scripts/workspace_utils.py",
    "assets/workspace_templates/00_问题拆解.md",
    "assets/workspace_templates/01_资料来源.md",
    "assets/workspace_templates/02_事实卡片.md",
    "assets/workspace_templates/03_对比框架.md",
    "assets/workspace_templates/04_推导过程.md",
    "assets/workspace_templates/FINAL_调研报告.md",
    "assets/workspace_templates/REPORT_READER_精读分析.md",
]
REQUIRED_IMPORTS = {
    "pypdf": "pypdf",
    "certifi": "certifi",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="校验 skill 结构和研究工作区")
    parser.add_argument(
        "skill_dir",
        nargs="?",
        default=None,
        help="skill 根目录，默认取脚本上级目录",
    )
    parser.add_argument(
        "--workspace",
        action="append",
        dest="workspaces",
        default=[],
        help="要校验的 topic 工作区或 workspace 根目录，可重复指定",
    )
    parser.add_argument(
        "--all-workspaces",
        action="store_true",
        help="校验当前项目目录下 workspace/ 中的所有 topic-* 工作区",
    )
    parser.add_argument(
        "--strict-placeholders",
        action="store_true",
        help="把模板占位内容也视为错误",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    skill_dir = Path(args.skill_dir).resolve() if args.skill_dir else Path(__file__).resolve().parent.parent
    errors: list[str] = []

    for relative_path in REQUIRED_FILES:
        path = skill_dir / relative_path
        if not path.exists():
            errors.append(f"缺失文件: {relative_path}")

    for script_path in sorted((skill_dir / "scripts").glob("*.py")):
        try:
            py_compile.compile(str(script_path), doraise=True)
        except py_compile.PyCompileError as exc:
            errors.append(f"{script_path.name} 语法错误: {exc.msg}")

    for package_name, import_name in REQUIRED_IMPORTS.items():
        if importlib.util.find_spec(import_name) is None:
            errors.append(f"缺少 Python 依赖 {package_name}: 请运行 python3 -m pip install -r requirements.txt")

    workspace_targets: list[Path] = []
    for raw_workspace in args.workspaces:
        raw_path = Path(raw_workspace).expanduser().resolve()
        targets = topic_workspace_dirs(raw_path)
        if not targets:
            errors.append(f"未找到可校验的 topic 工作区: {raw_path}")
        workspace_targets.extend(targets)
    if args.all_workspaces:
        workspace_targets.extend(topic_workspace_dirs(Path.cwd() / "workspace"))

    seen_workspaces: set[Path] = set()
    for workspace_dir in workspace_targets:
        resolved = workspace_dir.resolve()
        if resolved in seen_workspaces:
            continue
        seen_workspaces.add(resolved)
        errors.extend(
            validate_topic_workspace(
                resolved,
                strict_placeholders=args.strict_placeholders,
            )
        )

    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    print("验证通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
