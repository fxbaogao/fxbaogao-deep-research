# 运行配置

这个 skill 对接的是发现报告接口与页面结构。

## 已使用的接口

以下 3 个发现报告接口都需要 `Authorization` 鉴权请求头。

- 报告搜索：
  - `/mofoun/report/searchReport/search`
- 报告详情：
  - `/mofoun/report/searchReport/detail`
- 下载报告：
  - `GET /mofoun/report/report/file/downloadReport`

## 关键返回字段

- `searchReport`
  - 当前主要使用：`paragraphObjs`、`authors`、`orgName`、`industryName`、`fileUrl`
- `detail`
  - 当前主要使用：`summary`、`questions`、`catalogList`、`readReport`、`charts`
  - `readReport` 中只读取 `pdfUrl`，相对路径会使用前缀 `https://dr.fxbaogao.com/` 拼接
- `downloadReport`
  - 当前从 `data.resources[*].pdfUrl` / `data.resources[*].url` 读取 PDF 地址，相对路径会使用前缀 `https://dr.fxbaogao.com/` 拼接

## 下载能力

下载脚本通过 GET 调用 `/mofoun/report/report/file/downloadReport`，查询参数为 `reportId`，然后从返回体 `data.resources[*].pdfUrl` / `data.resources[*].url` 取 PDF 地址下载。地址如果是相对路径，脚本会用固定前缀 `https://dr.fxbaogao.com/` 拼接完整下载地址。默认会使用 `pypdf` 从下载后的 PDF 抽取文本；如只需要下载文件，可加 `--no-extract-text`。

PDF 模式硬性规则：

- PDF 模式依赖 `requirements.txt` 中的 Python 包。
- 开始 PDF 研究前先运行 `python3 scripts/validate.py .`。
- 如果校验提示缺依赖，运行 `python3 -m pip install -r requirements.txt` 后再继续。
- 不要在缺依赖时回退到 detail，也不要把 detail 摘要或正文摘录伪装成 PDF 原文精读。

## 主题研究入口

当前主入口脚本是：

```bash
python3 scripts/prepare_topic_research.py "5G" --time last1year --size 60 --detail-count 50 --detail-per-subquery 10
```

提供 `--subquery` 时，脚本会按子问题分别搜索并去重合并结果，默认再补充一轮宽泛主题词搜索。默认每个核心子问题优先预拉取约 10 篇详情，总量不少于 50 篇；如果核心子问题超过 5 个且未显式指定 `--detail-count`，总量会按 `核心子问题数 × --detail-per-subquery` 自动上调。使用 `--no-broad-topic` 可以关闭宽泛主题词补充。是否批量下载入选精读报告 PDF 由 `.env` 的 `FXBAOGAO_USE_PDF_READING` 控制，也可以用 `--download-pdfs` 或 `--no-download-pdfs` 覆盖单次运行。

单篇报告工作区脚本 `prepare_research_workspace.py` 仍保留，但它现在属于辅助能力。

## `.env` 配置

克隆项目后先安装 Python 依赖：

```bash
python3 -m pip install -r requirements.txt
cp .env.example .env
```

脚本启动时会自动读取 skill 根目录下的 `.env` 文件：

```dotenv
FXBAOGAO_API_KEY=your_key
FXBAOGAO_API_URL=https://api.fxbaogao.com
FXBAOGAO_USE_PDF_READING=false
```

如果同名系统环境变量已经存在，系统环境变量优先，不会被 `.env` 覆盖。

## 环境变量

- `FXBAOGAO_API_URL`
  - 默认 `https://api.fxbaogao.com`
  - 用于配置发现报告 API URL
  - 兼容旧变量名 `FXBAOGAO_BASE_URL`
- `FXBAOGAO_API_KEY`
  - 推荐优先放在 `.env`
  - 会注入为请求头 `Authorization: Bearer <FXBAOGAO_API_KEY>`
  - 如果配置值已经包含 `Bearer ` 前缀，则不会重复添加
- `FXBAOGAO_USE_PDF_READING`
  - 默认 `false`
  - 关闭时：主题研究只拉取 detail，`REPORT_READER_精读分析.md` 和后续流程基于 detail 摘要、目录和正文摘录
  - 开启时：主题研究下载 PDF、使用 `requirements.txt` 中的 `pypdf` 抽取 PDF 文本，`REPORT_READER_精读分析.md` 和后续流程必须基于 PDF 原文
  - 可用值：`true/false`、`1/0`、`yes/no`、`on/off`、`pdf/detail`
  - 兼容旧变量名 `FXBAOGAO_DOWNLOAD_PDFS`

## 细节说明

- 发现报告的 key 写在 `fxbaogao-deep-research/.env`
- 代码只保留上面列出的 3 个接口，不再保留其它接口
