#!/usr/bin/env python3
"""Shared client helpers for the fxbaogao deep research skill."""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import ssl
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict, deque
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Deque

try:
    import certifi
except ImportError:  # pragma: no cover
    certifi = None
try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

SKILL_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = SKILL_ROOT / ".env"
TRUE_ENV_VALUES = {"1", "true", "yes", "y", "on", "pdf"}
FALSE_ENV_VALUES = {"0", "false", "no", "n", "off", "detail", ""}


def _load_local_env(path: Path) -> None:
    if not path.is_file():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


_load_local_env(ENV_FILE)

API_BASE_URL = (
        os.getenv("FXBAOGAO_API_URL")
        or os.getenv("FXBAOGAO_BASE_URL")
        or "https://api.fxbaogao.com"
).strip().rstrip("/")
DOWNLOAD_PDF_BASE_URL = "https://dr.fxbaogao.com/"
WEB_BASE_URL = "https://www.fxbaogao.com"
HTTP_TIMEOUT = 30.0
RATE_LIMIT_WINDOW_SECONDS = 10.0
RATE_LIMIT_MAX_REQUESTS = 10
RATE_LIMIT_STATE_FILE = Path(tempfile.gettempdir()) / "fxbaogao_deep_research_rate_limit.json"
RATE_LIMIT_LOCK_FILE = Path(tempfile.gettempdir()) / "fxbaogao_deep_research_rate_limit.lock"
USER_AGENT = "fxbaogao-deep-research-skill/1.0 (+https://www.fxbaogao.com)"
SEARCH_REPORT_API_PATH = "/mofoun/report/searchReport/search"
DETAIL_REPORT_API_PATH = "/mofoun/report/searchReport/detail"
DOWNLOAD_REPORT_API_PATH = "/mofoun/report/report/file/downloadReport"
RELATIVE_TIME_VALUES = {
    "last3day",
    "last7day",
    "last1mon",
    "last3mon",
    "last1year",
}
PAGE_MARKER_RE = re.compile(r"^Page\d+$", re.IGNORECASE)
_RATE_LIMIT_BUCKETS: dict[str, Deque[float]] = defaultdict(deque)


class FxbaogaoError(RuntimeError):
    """Raised when the remote API or report page cannot be parsed."""


def parse_env_bool(value: Any, *, name: str) -> bool:
    normalized = str(value or "").strip().lower()
    if normalized in TRUE_ENV_VALUES:
        return True
    if normalized in FALSE_ENV_VALUES:
        return False
    raise ValueError(f"{name} 只能设置为 true/false、1/0、yes/no、on/off、pdf/detail")


def env_bool(names: tuple[str, ...], *, default: bool = False) -> bool:
    for name in names:
        raw_value = os.getenv(name)
        if raw_value is not None:
            return parse_env_bool(raw_value, name=name)
    return default


def pdf_reading_enabled_from_env(*, default: bool = False) -> bool:
    return env_bool(("FXBAOGAO_USE_PDF_READING", "FXBAOGAO_DOWNLOAD_PDFS"), default=default)


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"br", "p", "li", "div", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def get_text(self) -> str:
        text = "".join(self.parts)
        text = html.unescape(text)
        text = re.sub(r"[ \t\f\v]+", " ", text)
        text = re.sub(r"\s*\n\s*", "\n", text)
        return text.strip()


class _SectionHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.sections: list[dict[str, Any]] = []
        self._active_tag: str | None = None
        self._parts: list[str] = []
        self._current_title = "摘要"

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li"}:
            self._flush()
            self._active_tag = tag
            self._parts = []
        elif tag == "br" and self._active_tag:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == self._active_tag:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._active_tag:
            self._parts.append(data)

    def _flush(self) -> None:
        if not self._active_tag:
            return
        text = _normalize_text("".join(self._parts))
        tag = self._active_tag
        self._active_tag = None
        self._parts = []
        if not text:
            return
        if tag.startswith("h"):
            self._current_title = text
            return
        if not self.sections or self.sections[-1]["title"] != self._current_title:
            self.sections.append({"title": self._current_title, "items": []})
        self.sections[-1]["items"].append(text)


def _normalize_text(value: str) -> str:
    text = html.unescape(value)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    return text.strip()


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    parser = _HTMLTextExtractor()
    parser.feed(value)
    parser.close()
    return parser.get_text()


def _split_text_blocks(raw_content: str | None) -> list[str]:
    if not raw_content:
        return []
    blocks: list[str] = []
    for line in raw_content.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        cleaned = _normalize_text(line)
        cleaned = re.sub(r"^[•·]+\s*", "", cleaned)
        if not cleaned or PAGE_MARKER_RE.fullmatch(cleaned):
            continue
        blocks.append(cleaned)
    return blocks


def parse_summary_html(summary_html: str | None) -> list[dict[str, Any]]:
    if not summary_html:
        return []
    parser = _SectionHTMLParser()
    parser.feed(summary_html)
    parser.close()
    return [section for section in parser.sections if section.get("items")]


def parse_date_to_timestamp(date_str: str, *, end_of_day: bool = False) -> int:
    try:
        parsed = datetime.strptime(date_str.strip(), "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"日期格式错误: {date_str}，应为 YYYY-MM-DD") from exc
    if end_of_day:
        parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999000)
    return int(parsed.timestamp() * 1000)


def _build_ssl_context() -> ssl.SSLContext:
    if certifi is not None:
        return ssl.create_default_context(cafile=certifi.where())
    return ssl.create_default_context()


def build_api_url(path: str) -> str:
    return f"{API_BASE_URL}{path}"


def build_download_pdf_url(pdf_url: Any) -> str:
    if not isinstance(pdf_url, str):
        return ""
    raw_url = pdf_url.strip()
    if not raw_url:
        return ""

    parsed = urllib.parse.urlparse(raw_url)
    if parsed.scheme and parsed.netloc:
        return raw_url
    if parsed.netloc and not parsed.scheme:
        return f"https:{raw_url}"

    base_url = DOWNLOAD_PDF_BASE_URL
    if not base_url:
        return raw_url
    if not urllib.parse.urlparse(base_url).scheme:
        base_url = f"https://{base_url.lstrip('/')}"
    if not base_url.endswith("/"):
        base_url = f"{base_url}/"
    return urllib.parse.urljoin(base_url, raw_url.lstrip("/"))


def _download_file_suffix(download_url: str, fallback: str = ".pdf") -> str:
    suffix = Path(urllib.parse.urlparse(download_url).path).suffix.lower()
    if suffix in {".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"}:
        return suffix
    return fallback if fallback.startswith(".") else f".{fallback}"


def _unique_output_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 10000):
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"无法生成不冲突的下载文件名: {path}")


def has_auth() -> bool:
    return bool(os.getenv("FXBAOGAO_API_KEY", "").strip())


def require_auth() -> None:
    if not has_auth():
        raise FxbaogaoError(
            f"发现报告接口需要 API key。请先在 {ENV_FILE} 中设置 FXBAOGAO_API_KEY。"
        )


def build_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    api_key = os.getenv("FXBAOGAO_API_KEY", "").strip()
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/html;q=0.9,*/*;q=0.8",
        "Origin": WEB_BASE_URL,
        "Referer": f"{WEB_BASE_URL}/",
    }
    if api_key:
        if api_key.lower().startswith("bearer "):
            headers["Authorization"] = api_key
        else:
            headers["Authorization"] = f"Bearer {api_key}"
    if extra:
        headers.update(extra)
    return headers


def _request(
        url: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
) -> str:
    request_headers = build_headers(headers)
    body = None
    method = method.upper()

    if method == "GET" and payload:
        query = urllib.parse.urlencode(payload, doseq=True)
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}{query}"
    elif payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
    _wait_for_rate_limit(url)
    try:
        with urllib.request.urlopen(
                request,
                timeout=HTTP_TIMEOUT,
                context=_build_ssl_context(),
        ) as response:
            return response.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        detail = _normalize_text(detail)[:300]
        raise FxbaogaoError(f"HTTP {exc.code}: {detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise FxbaogaoError(f"网络请求失败: {exc.reason}") from exc


def _request_json(
        url: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    require_auth()
    raw = _request(url, method=method, payload=payload, headers=headers)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FxbaogaoError("接口返回了无法解析的 JSON") from exc


def _format_publish_date(pub_time: Any, pub_time_str: str | None) -> str:
    if isinstance(pub_time, (int, float)) and pub_time > 0:
        timestamp = float(pub_time)
        if timestamp > 10 ** 12:
            timestamp = timestamp / 1000.0
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
    if pub_time_str:
        return pub_time_str.strip().rstrip("/").replace("/", "-")
    return ""


def _clean_name_list(values: list[str] | None) -> list[str]:
    cleaned: list[str] = []
    for value in values or []:
        text = strip_html(value)
        if text and text != "-":
            cleaned.append(text)
    return cleaned


def build_report_url(doc_id: int | str) -> str:
    return f"{WEB_BASE_URL}/view?id={doc_id}"


def build_detail_url(doc_id: int | str) -> str:
    return f"{WEB_BASE_URL}/detail/{doc_id}"


def _rate_limit_key(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc or "local"
    path = parsed.path or "/"
    return f"{host}{path}"


def _wait_for_rate_limit_in_memory(key: str) -> None:
    bucket = _RATE_LIMIT_BUCKETS[key]
    while True:
        now = time.monotonic()
        while bucket and now - bucket[0] >= RATE_LIMIT_WINDOW_SECONDS:
            bucket.popleft()
        if len(bucket) < RATE_LIMIT_MAX_REQUESTS:
            bucket.append(now)
            return
        sleep_for = RATE_LIMIT_WINDOW_SECONDS - (now - bucket[0])
        time.sleep(max(0.01, sleep_for))


def _load_rate_limit_state() -> dict[str, list[float]]:
    if not RATE_LIMIT_STATE_FILE.exists():
        return {}
    try:
        with RATE_LIMIT_STATE_FILE.open("r", encoding="utf-8") as file_obj:
            data = json.load(file_obj)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    state: dict[str, list[float]] = {}
    for key, values in data.items():
        if not isinstance(key, str) or not isinstance(values, list):
            continue
        timestamps: list[float] = []
        for value in values:
            if isinstance(value, (int, float)):
                timestamps.append(float(value))
        state[key] = timestamps
    return state


def _save_rate_limit_state(state: dict[str, list[float]]) -> None:
    try:
        RATE_LIMIT_STATE_FILE.write_text(
            json.dumps(state, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        return


def _wait_for_rate_limit_shared(key: str) -> None:
    if fcntl is None:
        _wait_for_rate_limit_in_memory(key)
        return

    RATE_LIMIT_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    while True:
        with RATE_LIMIT_LOCK_FILE.open("a+") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            now = time.time()
            state = _load_rate_limit_state()
            bucket = [
                timestamp
                for timestamp in state.get(key, [])
                if now - timestamp < RATE_LIMIT_WINDOW_SECONDS
            ]
            if len(bucket) < RATE_LIMIT_MAX_REQUESTS:
                bucket.append(now)
                state[key] = bucket
                _save_rate_limit_state(state)
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                return

            sleep_for = RATE_LIMIT_WINDOW_SECONDS - (now - bucket[0])
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        time.sleep(max(0.01, sleep_for))


def _wait_for_rate_limit(url: str) -> None:
    """Limit each endpoint to at most 10 requests per rolling 10 seconds."""
    key = _rate_limit_key(url)
    _wait_for_rate_limit_shared(key)


def _build_text_section(title: str, value: Any) -> dict[str, Any] | None:
    if not isinstance(value, str):
        return None
    items = _split_text_blocks(value)
    if not items:
        return None
    return {"title": title, "items": items}


def _build_list_section(title: str, values: Any) -> dict[str, Any] | None:
    if not isinstance(values, list):
        return None
    items = [_normalize_text(str(value)) for value in values if _normalize_text(str(value))]
    if not items:
        return None
    return {"title": title, "items": items}


def normalize_search_result(
        raw_result: dict[str, Any],
        *,
        keywords: str | None,
        authors: list[str] | None,
        org_names: list[str] | None,
        start_time: int | None,
        end_time: int | str | None,
        page_size: int,
        request_path: str,
) -> dict[str, Any]:
    if raw_result.get("code") != 0:
        raise FxbaogaoError(raw_result.get("msg") or "搜索接口返回失败")

    data = raw_result.get("data") or {}
    reports: list[dict[str, Any]] = []
    for item in data.get("dataList") or []:
        doc_id = item.get("docId")
        file_url = item.get("fileUrl") or ""
        reports.append(
            {
                "doc_id": doc_id,
                "title": strip_html(item.get("title")) or "无标题",
                "org_name": strip_html(item.get("orgName")) or "",
                "authors": _clean_name_list(item.get("authors")),
                "publish_date": _format_publish_date(item.get("pubTime"), item.get("pubTimeStr")),
                "publish_timestamp": item.get("pubTime"),
                "industry_name": strip_html(item.get("industryName")) or "",
                "page_count": item.get("pageNum"),
                "file_url": file_url,
                "doc_type": item.get("docType"),
                "report_type": item.get("reportType"),
                "language": item.get("language") or "",
                "has_ai_summary": item.get("hasAiSummary"),
                "is_third": item.get("isThird"),
                "third_name": strip_html(item.get("thirdName")) or "",
                "report_url": build_report_url(doc_id) if doc_id else "",
                "detail_url": build_detail_url(doc_id) if doc_id else "",
                "snippets": [
                    strip_html(paragraph.get("content"))
                    for paragraph in item.get("paragraphObjs") or []
                    if strip_html(paragraph.get("content"))
                ],
            }
        )

    return {
        "query": {
            "keywords": keywords,
            "authors": authors or [],
            "org_names": org_names or [],
            "start_time": start_time,
            "end_time": end_time,
        },
        "request_path": request_path,
        "total": data.get("count") or data.get("total") or 0,
        "page_size": data.get("limit") or page_size,
        "current_page": data.get("currPage") or 1,
        "reports": reports,
    }


def search_reports(
        *,
        keywords: str | None = None,
        authors: list[str] | None = None,
        org_names: list[str] | None = None,
        start_time: int | None = None,
        end_time: int | str | None = None,
        page_size: int = 10,
) -> dict[str, Any]:
    if not keywords and not authors and not org_names:
        raise ValueError("请至少指定一个搜索条件（关键词、作者或机构）")

    if isinstance(end_time, str) and end_time not in RELATIVE_TIME_VALUES:
        raise ValueError(
            f"未知的时间格式: {end_time}。支持的格式: {sorted(RELATIVE_TIME_VALUES)}"
        )

    payload = {
        "keywords": keywords,
        "authors": authors or [],
        "orgNames": org_names or [],
        "paragraphSize": 3,
        "startTime": start_time,
        "endTime": end_time,
        "pageSize": max(1, min(page_size, 100)),
        "pageNum": 1,
    }

    raw = _request_json(build_api_url(SEARCH_REPORT_API_PATH), method="POST", payload=payload)
    if raw.get("code") != 0:
        raise FxbaogaoError(raw.get("msg") or "搜索接口返回失败")
    return normalize_search_result(
        raw,
        keywords=keywords,
        authors=authors,
        org_names=org_names,
        start_time=start_time,
        end_time=end_time,
        page_size=page_size,
        request_path=SEARCH_REPORT_API_PATH,
    )


def normalize_detail_payload(raw_data: dict[str, Any], report_id: int) -> dict[str, Any]:
    data = raw_data.get("report") or raw_data
    summary_sections = parse_summary_html(raw_data.get("summaryHtml"))
    if not summary_sections:
        for section in (
                _build_text_section("摘要", raw_data.get("summary")),
                _build_text_section("问题", raw_data.get("questions")),
                _build_list_section("目录", raw_data.get("catalogList")),
        ):
            if section:
                summary_sections.append(section)
    summary = [item for section in summary_sections for item in section.get("items", [])]

    content: list[str] = []
    if isinstance(raw_data.get("content"), str):
        content = _split_text_blocks(raw_data.get("content"))
    elif isinstance(raw_data.get("paragraphObjs"), list):
        content = [
            strip_html(item.get("content"))
            for item in raw_data.get("paragraphObjs") or []
            if strip_html(item.get("content"))
        ]
    elif isinstance(raw_data.get("paragraphs"), list):
        content = [
            _normalize_text(str(item))
            for item in raw_data.get("paragraphs") or []
            if _normalize_text(str(item))
        ]

    read_report = raw_data.get("readReport") or data.get("readReport") or {}
    pdf_url = build_download_pdf_url(read_report.get("pdfUrl") or "")
    detail = {
        "doc_id": report_id,
        "title": strip_html(data.get("title")) or "",
        "org_name": strip_html(data.get("orgName")) or "",
        "authors": _clean_name_list(data.get("authors")),
        "industry_name": strip_html(data.get("industryName")) or "",
        "publish_date": _format_publish_date(data.get("pubTime"), data.get("pubTimeStr")),
        "page_count": data.get("pageNum"),
        "questions": _split_text_blocks(raw_data.get("questions")) if isinstance(raw_data.get("questions"),
                                                                                 str) else [],
        "catalog_list": [
            _normalize_text(str(item))
            for item in raw_data.get("catalogList") or []
            if _normalize_text(str(item))
        ],
        "charts_count": len(raw_data.get("charts") or []),
        "display": raw_data.get("display"),
        "report_url": build_report_url(report_id),
        "detail_url": build_detail_url(report_id),
        "pdf_url": pdf_url,
        "file_url": data.get("fileUrl") or raw_data.get("fileUrl") or "",
        "summary_sections": summary_sections,
        "summary": summary,
        "content": content,
    }
    return detail


def get_report_detail(report_id: int, keyword: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"id": report_id}
    if keyword:
        payload["keyword"] = keyword

    raw = _request_json(
        build_api_url(DETAIL_REPORT_API_PATH),
        method="POST",
        payload=payload,
    )
    if raw.get("code") != 0 or not raw.get("data"):
        raise FxbaogaoError(raw.get("msg") or "详情接口返回失败")
    detail = normalize_detail_payload(raw["data"], report_id)
    detail["detail_source"] = "detail_api"
    return detail


def render_detail_markdown(result: dict[str, Any], *, max_content_lines: int = 120) -> str:
    authors = "、".join(result.get("authors") or []) or "未知作者"
    title = result.get("title") or f"研报 {result.get('doc_id')}"
    lines = [
        f"# {title}",
        "",
        f"- 文档 ID：{result.get('doc_id')}",
        f"- 机构：{result.get('org_name') or '未知机构'}",
        f"- 作者：{authors}",
        f"- 日期：{result.get('publish_date') or '未知日期'}",
        f"- 行业：{result.get('industry_name') or '未知行业'}",
        f"- 阅读链接：{result.get('report_url') or ''}",
        f"- 详情链接：{result.get('detail_url') or ''}",
        "",
    ]

    sections = result.get("summary_sections") or []
    if sections:
        lines.append("## 摘要")
        lines.append("")
        for section in sections:
            lines.append(f"### {section.get('title') or '摘要'}")
            lines.append("")
            for item in section.get("items") or []:
                lines.append(f"- {item}")
            lines.append("")

    content = result.get("content") or []
    if content:
        lines.append("## 正文摘录")
        lines.append("")
        for block in content[:max_content_lines]:
            lines.append(f"- {block}")
        lines.append("")
        if len(content) > max_content_lines:
            lines.append(f"> 仅展示前 {max_content_lines} 段，完整内容需继续读取原报告。")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def format_search_output(result: dict[str, Any]) -> str:
    reports = result.get("reports") or []
    total = result.get("total", 0)
    lines = [f"找到 {total} 条相关研报，展示前 {len(reports)} 条。", "-" * 60]
    for index, report in enumerate(reports, start=1):
        authors = "、".join(report.get("authors") or []) or "未知作者"
        lines.extend(
            [
                f"{index}. {report.get('title') or '无标题'}",
                f"   机构: {report.get('org_name') or '未知机构'}",
                f"   作者: {authors}",
                f"   日期: {report.get('publish_date') or '未知日期'}",
                f"   文档ID: {report.get('doc_id') or 'N/A'}",
                f"   链接: {report.get('report_url') or ''}",
            ]
        )
        snippets = report.get("snippets") or []
        if snippets:
            lines.append(f"   摘要片段: {snippets[0]}")
        lines.append("")
    return "\n".join(lines).rstrip()


def format_detail_output(result: dict[str, Any], *, max_content_lines: int = 20) -> str:
    authors = "、".join(result.get("authors") or []) or "未知作者"
    lines = [
        "=" * 60,
        result.get("title") or f"研报详情 (ID: {result.get('doc_id')})",
        f"机构: {result.get('org_name') or '未知机构'}",
        f"作者: {authors}",
        f"日期: {result.get('publish_date') or '未知日期'}",
        f"详情页: {result.get('detail_url') or ''}",
        f"文件链接: {result.get('file_url') or ''}",
        f"来源: {result.get('detail_source') or 'unknown'}",
        "=" * 60,
    ]
    for section in result.get("summary_sections") or []:
        lines.append("")
        lines.append(f"## {section.get('title') or '摘要'}")
        lines.append("")
        for item in section.get("items") or []:
            lines.append(f"- {item}")
    content_lines = result.get("content") or []
    if content_lines:
        lines.append("")
        lines.append("## 正文摘录")
        lines.append("")
        for line in content_lines[:max_content_lines]:
            if len(line) > 220:
                line = f"{line[:220]}..."
            lines.append(line)
        if len(content_lines) > max_content_lines:
            lines.append("")
            lines.append(f"... (共 {len(content_lines)} 段，仅显示前 {max_content_lines} 段)")
    return "\n".join(lines)


def copy_workspace_templates(skill_dir: Path, target_dir: Path) -> None:
    template_dir = skill_dir / "assets" / "workspace_templates"
    if not template_dir.is_dir():
        raise FxbaogaoError(f"模板目录不存在: {template_dir}")
    for template in sorted(template_dir.iterdir()):
        if template.is_file() and not template.name.startswith("."):
            shutil.copyfile(template, target_dir / template.name)


def _request_bytes(
        url: str,
        *,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
) -> bytes:
    """Download binary content (e.g. PDF) from a URL."""
    request_headers = build_headers(headers)
    if payload:
        query = urllib.parse.urlencode(payload, doseq=True)
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}{query}"
    request = urllib.request.Request(url, headers=request_headers, method="GET")
    _wait_for_rate_limit(url)
    try:
        with urllib.request.urlopen(
                request,
                timeout=HTTP_TIMEOUT,
                context=_build_ssl_context(),
        ) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise FxbaogaoError(f"HTTP {exc.code}: {_normalize_text(detail)[:300] or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise FxbaogaoError(f"网络请求失败: {exc.reason}") from exc


def _is_pdf_download_url(download_url: str) -> bool:
    return _download_file_suffix(download_url) == ".pdf"


def _extract_download_urls(raw: dict[str, Any]) -> list[str]:
    if raw.get("code") != 0:
        return []

    data = raw.get("data")
    urls: list[str] = []
    if isinstance(data, dict):
        resources = data.get("resources")
        if isinstance(resources, list):
            for resource in resources:
                if isinstance(resource, str) and resource:
                    urls.append(build_download_pdf_url(resource))
                if isinstance(resource, dict):
                    for key in ("pdfUrl", "pdf_url", "url"):
                        pdf_url = resource.get(key)
                        if isinstance(pdf_url, str) and pdf_url:
                            urls.append(build_download_pdf_url(pdf_url))
    return [url for url in urls if url]


def _select_download_url(urls: list[str], preferred_url: Any = None) -> str:
    preferred = build_download_pdf_url(preferred_url)
    for url in urls:
        if _is_pdf_download_url(url):
            return url
    if preferred and _is_pdf_download_url(preferred):
        return preferred
    if urls:
        return urls[0]
    return preferred


def get_download_pdf_url(report_id: int, preferred_url: Any = None) -> str:
    raw = _request_json(
        build_api_url(DOWNLOAD_REPORT_API_PATH),
        method="GET",
        payload={"reportId": report_id},
    )
    download_url = _select_download_url(_extract_download_urls(raw), preferred_url)
    if not download_url:
        raise FxbaogaoError("下载接口未返回 data.resources 中的 pdfUrl/url")
    return download_url


def download_report_pdf(report_id: int, output_path: Path, preferred_url: Any = None) -> dict[str, Any]:
    """下载研报 PDF 文件。需要有效的 API key（FXBAOGAO_API_KEY）。"""
    if not has_auth():
        raise FxbaogaoError(
            f"下载功能需要 API key。请先在 {ENV_FILE} 中设置 FXBAOGAO_API_KEY。"
        )

    pdf_url = get_download_pdf_url(report_id, preferred_url=preferred_url)
    output_path = output_path.with_suffix(".pdf")
    output_path = _unique_output_path(output_path)
    data = _request_bytes(pdf_url)
    if not data.lstrip().startswith(b"%PDF"):
        raise FxbaogaoError(f"下载地址未返回 PDF 内容: {pdf_url}")
    output_path.write_bytes(data)

    return {
        "doc_id": report_id,
        "pdf_url": pdf_url,
        "download_source": "download_api_pdf_url",
        "output_path": str(output_path),
        "file_size": output_path.stat().st_size,
        "status": "success",
    }
