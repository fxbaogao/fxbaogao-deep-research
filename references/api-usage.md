# 发现报告接口调用说明

本文件说明如何调用发现报告接口。搜索、详情和下载都通过 API 完成。

## 统一规范

- API Base URL：`https://api.fxbaogao.com`
- 鉴权 Header：`Authorization: Bearer $FXBAOGAO_API_KEY`
- `Content-Type`：`application/json`
- `FXBAOGAO_API_KEY` 从环境变量获取。
- 若未设置，提示用户：`export FXBAOGAO_API_KEY=<你的apikey>`
- 搜索、详情和下载接口都需要鉴权。

```bash
export FXBAOGAO_API_KEY=<your_api_key>
```

## 报告搜索

接口：

```text
POST /mofoun/report/searchReport/search
```

请求参数：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `keywords` | string | 否 | 搜索关键词。关键词、作者、机构至少提供一个 |
| `authors` | array | 否 | 作者列表 |
| `orgNames` | array | 否 | 机构列表 |
| `paragraphSize` | number | 否 | 摘要片段数量，建议传 `3` |
| `startTime` | number | 否 | 开始时间戳，毫秒 |
| `endTime` | number/string | 否 | 结束时间戳，毫秒；也支持 `last3day`、`last7day`、`last1mon`、`last3mon`、`last1year` |
| `pageSize` | number | 否 | 每页数量，建议 1-100 |
| `pageNum` | number | 否 | 页码，从 1 开始 |

示例：

```bash
curl -X POST "https://api.fxbaogao.com/mofoun/report/searchReport/search" \
  -H "Authorization: Bearer $FXBAOGAO_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"keywords":"光模块 800G AI算力","authors":[],"orgNames":[],"paragraphSize":3,"endTime":"last1year","pageSize":10,"pageNum":1}'
```

重点回包字段：

| 字段 | 说明                |
| --- |-------------------|
| `code` | `0` 表示成功          |
| `msg` | 错误或提示信息           |
| `data.count` / `data.total` | 命中数量              |
| `data.dataList[]` | 报告列表              |
| `data.dataList[].docId` | 报告 ID             |
| `data.dataList[].title` | 报告标题，可能包含 <em> 标签 |
| `data.dataList[].orgName` | 机构名称，可能包含 <em> 标签 |
| `data.dataList[].authors` | 分析师列表             |
| `data.dataList[].pubTime` / `pubTimeStr` | 发布时间              |
| `data.dataList[].industryName` | 行业                |
| `data.dataList[].pageNum` | 页数                |
| `data.dataList[].paragraphObjs[].content` | 命中的正文片段           |

展示搜索结果时，优先展示标题、机构、作者、日期、报告 ID、官网阅读链接：

```text
https://www.fxbaogao.com/view?id=<docId>
```

## 报告详情

接口：

```text
POST /mofoun/report/searchReport/detail
```

请求参数：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `id` | number | 是 | 报告 ID |
| `keyword` | string | 否 | 搜索关键词，用于命中上下文 |

示例：

```bash
curl -X POST "https://api.fxbaogao.com/mofoun/report/searchReport/detail" \
  -H "Authorization: Bearer $FXBAOGAO_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"id":5339478,"keyword":"光模块 800G"}'
```

重点回包字段：

| 字段 | 说明 |
| --- | --- |
| `data.report.title` | 报告标题 |
| `data.report.orgName` | 机构 |
| `data.report.authors` | 作者 |
| `data.report.pubTime` / `pubTimeStr` | 发布时间 |
| `data.summaryHtml` / `data.summary` | 摘要 |
| `data.questions` | 关键问题 |
| `data.catalogList` | 目录 |
| `data.content` / `data.paragraphObjs` | 正文摘录 |


注意：detail 回包适合快速初筛和轻量研究。正式精读或需要引用原文时，应下载 PDF 并基于 PDF 原文核对。

## PDF 下载

接口：

```text
GET /mofoun/report/report/file/downloadReport?reportId=<报告ID>
```

示例：

```bash
curl -L "https://api.fxbaogao.com/mofoun/report/report/file/downloadReport?reportId=5339478" \
  -H "Authorization: Bearer $FXBAOGAO_API_KEY"
```

重点回包字段：

| 字段 | 说明 |
| --- | --- |
| `data.resources[]` | 可下载资源列表 |
| `data.resources[].pdfUrl` / `url` | PDF 路径或 URL |

下载接口返回的 PDF 地址可能是完整 URL，也可能是相对路径。如果是相对路径，使用下面前缀拼接：

```text
https://dr.fxbaogao.com/
```

detail 接口中的 `data.readReport.pdfUrl` 也遵循同样规则。
