---
name: fxbaogao-deep-research
description: 围绕行业、公司或主题做深度研究。采用"先想后搜"方法：先明确研究边界，用 MECE 原则拆解关键子问题，再基于子问题定向搜索、筛选核心研报，精读后沉淀事实、对比推导，最终形成结构化结论。
argument-hint: "[关键词] [--time last1mon|last3mon]"
---

# 发现报告深度研究 Skill

## 何时使用

- 研究行业、公司或主题（如 `5G`、`光模块`、`宁德时代`）
- 需要系统地收集研报并进行结构化分析
- 需从海量报告中快速定位核心观点，避免信息过载

## 核心思想

每项研究必须经过下面 9 个步骤。

1. 定义研究主题和边界
2. 按 MECE 原则拆解 3-7 个子问题
3. 按子问题搜索候选研报
4. 筛选核心报告，分为核心必读、补充参考和背景材料
5. 拉取核心报告详情
6. 精读报告并填写 `REPORT_READER_精读分析.md`
7. 沉淀 `intermediate/02_事实卡片.md`
8. 填写 `intermediate/03_对比框架.md` 和 `intermediate/04_推导过程.md`
9. 汇总输出 `FINAL_调研报告.md`

硬性门槛：在调用 `search_reports.py`、`prepare_topic_research.py`、`get_report_detail.py`
或 `download_report.py` 之前，必须先产出可审阅的研究边界和 MECE 子问题搜索矩阵。
如果用户没有给出边界，先做保守默认并明确写出；只有边界会显著改变结论时才停下来追问。

## 主工作流

### 第一步：定义研究主题与边界

明确回答：

- 研究什么主题/公司？
- 时间范围、地域、产业链环节等边界条件是什么？
- 希望回答的核心问题是什么？

输出一个清晰的主题陈述，例如："2022–2025 年国内光模块行业竞争格局与关键驱动因素"。
首次进入研究任务时，先在回复中给出这份主题陈述，不要先跑搜索命令。

### 第二步：MECE 拆解关键子问题

基于主题，采用 MECE 原则（相互独立、完全穷尽）拆解为 3–7 个子问题。
示例（光模块）：

1. 市场规模与增速（量、价驱动）
2. 技术迭代路径（800G/1.6T 落地节奏）
3. 下游需求结构（云计算、AI 算力、电信）
4. 主要玩家与份额变化
5. 上游芯片供应与瓶颈
6. 政策与贸易壁垒影响

每个子问题即为一个**搜索维度**。
每个子问题必须同时给出：要验证的判断、搜索关键词、是否属于核心维度。
没有搜索关键词的子问题不能进入检索阶段。

### 第三步：基于子问题定向搜索并收集候选研报

对每个子问题，使用对应关键词（可组合 `--org`、`--time` 等参数）发起搜索，合并结果形成候选报告池。
不要把宽泛主题词作为第一轮唯一查询；宽泛主题词只能作为兜底补充。

```bash
python3 scripts/prepare_topic_research.py "<研究主题>" --time last1year --size 60 --detail-count 50 --detail-per-subquery 10 --json \
  --subquery "市场规模=光模块 市场规模 增速 800G 1.6T" \
  --subquery "需求结构=光模块 AI算力 云厂商 电信 需求" \
  --subquery "竞争格局=光模块 中际旭创 新易盛 份额 毛利率"
```

建议工作方式：

- 先针对 3–6 个核心子问题搜索，避免报告过多
- 每个子问题默认取 60 篇候选；深度研究默认预拉取 50 篇详情
- 每个核心子问题优先精读约 10 篇；若子问题少于 5 个，用总览和交叉维度报告补足到约 50 篇
- 若核心子问题超过 5 个且未显式指定 `--detail-count`，总精读数按 `子问题数 × 10` 自动上调
- 用 `--subquery "子问题=关键词"` 保留报告与子问题的映射
- 默认额外补充一轮宽泛主题词搜索，捕捉行业总览和跨维度报告；只有结果过多时才加 `--no-broad-topic`
- 自动生成 `intermediate/00_问题拆解.md`、`intermediate/candidate-reports.md` 和 `intermediate/selected-reports.md`

### 第四步：筛选核心报告，剔除无关项

对候选清单执行三级筛选：

- **相关性**：标题/摘要与子问题完全无关 → 剔除
- **时效性与深度**：数据过于陈旧、仅新闻简评 → 剔除
- **机构/作者权威性**：优选知名券商、咨询公司、产业专家

保留 40–60 篇核心报告，输出 `intermediate/selected-reports.md`，并标注每篇对应解决哪一个子问题。
其中优先精读约 50 篇，覆盖所有核心子问题；不要只精读搜索结果排序最靠前的几篇。

### 第五步：调取核心报告详情，准备研究工作区

对筛选出的每一篇报告，拉取详情：

```bash
python3 scripts/get_report_detail.py <report_id> --keyword "<搜索关键词>" --json
```

主题研究和单篇报告工作区默认创建在当前项目目录的 `workspace/` 下；如需其它位置，显式传 `--output-root`。

如需下载 PDF 源文件（需 API key），先在 `.env` 中开启 PDF 精读模式：

```bash
# 先在 fxbaogao-deep-research/.env 中填写 FXBAOGAO_API_KEY=your_key
FXBAOGAO_USE_PDF_READING=true
python3 scripts/download_report.py <report_id> --output-dir ./downloads --json
# 主题研究默认下载入选精读报告 PDF；如只补已有工作区
python3 scripts/download_report.py --workspace workspace/topic-xxx-YYYYMMDD --strict --json
```

无 API key 时会明确提示，不伪装成已下载。

### 第六步：精读核心报告

参考 `references/report-reader-playbook.md`，将每篇报告的分析填入 `REPORT_READER_精读分析.md`。
如果 `.env` 中 `FXBAOGAO_USE_PDF_READING=true`，精读必须优先打开 `intermediate/downloads/` 下的 PDF 原文；详情接口的摘要和正文摘录只用于初筛和定位，不替代原文核对，精读必须信息基于 pdf 内容。
如果未开启 PDF 精读模式，精读和后续事实卡片、对比框架、最终结论可以基于 detail 接口的摘要、目录和正文摘录生成，但要明确这是 detail 模式。

PDF 模式硬性规则：

- PDF 模式依赖 `requirements.txt` 中的 Python 包。
- 开始 PDF 研究前先运行 `python3 scripts/validate.py .`。
- 如果校验提示缺依赖，运行 `python3 -m pip install -r requirements.txt` 后再继续。
- 不要在缺依赖时回退到 detail，也不要把 detail 摘要或正文摘录伪装成 PDF 原文精读。

精读要点：

- 核心结论与逻辑链条
- 关键数据、图表与假设
- 与其它报告异同
- 可能的偏差或利益相关方

### 第七步：沉淀事实卡片

将精读中提取的客观事实、数据点整理为卡片，每条标注来源（报告 ID）。
文件：`intermediate/02_事实卡片.md`，格式：事实陈述 + 来源 + 可信度（高/中/低）。

### 第八步：对比框架与推导

参考 `references/research-playbook.md`：

- `intermediate/03_对比框架.md`：不同报告对同一子问题的框架、假设、结论对比，找出分歧与共识
- `intermediate/04_推导过程.md`：基于事实卡片与对比结果进行逻辑推演，形成判断

### 第九步：输出最终结论

整合所有分析，撰写 `FINAL_调研报告.md`，必须包含：

- 研究边界与子问题回顾
- 核心发现（按子问题展开）
- 关键风险与不确定性
- 下一步值得深入的方向
- 参考资料。所有发现报告资料必须显示官网阅读链接，格式为 `[报告ID - 报告标题](https://www.fxbaogao.com/view?id=报告ID)`。

## 环境变量

鉴权信息默认从 skill 根目录的 `.env` 读取，已有的系统环境变量会优先覆盖 `.env`。
`FXBAOGAO_API_KEY` 会通过请求头 `Authorization: Bearer <FXBAOGAO_API_KEY>` 发送。
搜索、详情和下载接口都需要鉴权。

必要配置：

- `FXBAOGAO_API_KEY`

可选配置：

- `FXBAOGAO_API_URL`
  - 默认 `https://api.fxbaogao.com`

详见 `references/runtime-config.md`

## 回复约定

- 第一条实质回复必须包含：研究边界、核心问题、3–7 个 MECE 子问题、搜索关键词矩阵
- 先界定研究主题，再用 MECE 方法拆解为子问题，然后定向搜索
- 搜索结果按与子问题的相关性分层呈现，不逐篇倾倒
- 详情回答优先提供总结与 key takeaways，不整段复制正文
- 不伪造作者、机构、日期、摘要；所有引用需可溯源
- 网络错误时提示用户需允许访问 `api.fxbaogao.com`；下载失败时再检查 `https://dr.fxbaogao.com/` 拼接后的 PDF 地址
