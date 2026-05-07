# fxbaogao-deep-research

一个 Claude Code Skill，通过“先想后搜”的系统化方法，把行业、公司或主题研究转化为可追溯、可复核、可交付的研报深度研究工作区。

本项目专门对接发现报告（fxbaogao）接口，支持研报搜索、详情拉取、PDF 下载与文本抽取、核心材料筛选、事实卡片沉淀和最终调研报告生成。

## 痛点

在用研报做行业或公司研究时，你经常面临：

- **信息过载**：候选研报数量多，标题相近，难以快速筛出核心材料
- **检索粗放**：只搜一个宽泛关键词，容易遗漏产业链、竞争格局、政策、需求等关键维度
- **来源混乱**：结论来自哪篇报告、哪个报告 ID、哪个机构不清楚
- **事实不可追溯**：关键数据和判断没有事实卡片承接，后续难以复核
- **PDF 与摘要混用**：detail 摘要、正文摘录和 PDF 原文边界不清，容易把摘要当作精读
- **交付成本高**：从搜索结果到老板能读的报告，中间缺少稳定工作区和模板

传统做法耗时长，且结论容易停留在“看起来像这样”，无法稳定复用。

## 解决方案

fxbaogao-deep-research 提供一套面向发现报告的研报研究流程：

- **先想后搜**：先定义研究边界，再用 MECE 拆解 3-7 个关键子问题
- **按子问题定向检索**：每个子问题都有独立关键词，避免只依赖宽泛主题词
- **候选报告自动汇总**：合并去重搜索结果，生成候选研报清单
- **核心报告预拉取**：默认预拉取不少于 50 篇详情，覆盖核心子问题
- **PDF 精读模式**：可下载 PDF 并抽取文本，强制基于 PDF 原文开展精读
- **事实卡片机制**：每条关键事实标注来源、报告 ID 和可信度
- **显式推导链**：通过“事实卡片 -> 对比框架 -> 推导过程”形成结论
- **可交付输出**：生成结构化工作区和 `FINAL_调研报告.md`

## 环境要求
- Claude Code CLI
- 有效的发现报告 API key，开通请咨询[发现报告客服（工作日9:00-18:00）](https://www.fxbaogao.com/seo/kefu)

项目依赖写在 `requirements.txt`：

- `pypdf`：PDF 精读模式下抽取本地 PDF 文本
- `certifi`：提供稳定的 HTTPS 证书路径

## 安装方式

### 1：Git Clone

```bash
git clone git@github.com:fxbaogao/fxbaogao-deep-research.git
cd fxbaogao-deep-research
python3 -m pip install -r requirements.txt
cp .env.example .env
```

然后在 `.env` 中填写发现报告 API key：

```dotenv
FXBAOGAO_API_KEY=your_key
FXBAOGAO_API_URL=https://api.fxbaogao.com
FXBAOGAO_USE_PDF_READING=true
```

### 2：作为本地 Skill 使用

把本项目目录放入你的 Codex skills 目录，或在当前仓库中直接使用 `SKILL.md`。

```bash
cp -r fxbaogao-deep-research ~/.claude/skills/
```

如果你的客户端使用其它 skills 目录，请按实际路径调整。

## 配置说明

`.env` 会在脚本启动时自动读取；如果系统环境变量中已经存在同名 `FXBAOGAO_*`，系统环境变量优先。

```dotenv
# 必填：发现报告 API key
FXBAOGAO_API_KEY=your_key

# 可选：发现报告 API URL
FXBAOGAO_API_URL=https://api.fxbaogao.com

# 可选：开启后主题研究会下载 PDF，并要求精读和后续流程基于 PDF 原文
FXBAOGAO_USE_PDF_READING=false
```

PDF 精读模式可用值：

- 开启：`true`
- 关闭：`false`

## 使用方法

自然语言触发：

- “深度调研 [行业/公司/主题]”
- “帮我从发现报告调研 [主题]”
- “分析 [公司] 的竞争力和增长前景”
- “基于研报写一份 [主题] 调研报告”

## 工作流程（9 步法）

```text
Step 1: 定义研究主题和边界
Step 2: 按 MECE 原则拆解 3-7 个子问题
Step 3: 按子问题搜索候选研报
Step 4: 筛选核心报告，区分核心必读、补充参考、背景材料
Step 5: 拉取核心报告详情，准备研究工作区
Step 6: 精读报告，填写 REPORT_READER_精读分析.md
Step 7: 沉淀事实卡片，标注来源、可信度、适用范围
Step 8: 建立对比框架与推导链
Step 9: 汇总输出 FINAL_调研报告.md
```

硬性规则：调用搜索、详情或下载脚本前，必须先产出可审阅的研究边界和 MECE 子问题搜索矩阵。

## 输出结构

主题研究会在当前项目目录的 `workspace/` 下生成工作区：

```text
workspace/topic-<主题>-<日期>/
├── REPORT_READER_精读分析.md
├── FINAL_调研报告.md
└── intermediate/
    ├── research-brief.md
    ├── candidate-reports.md
    ├── selected-reports.md
    ├── topic-search-results.json
    ├── hydrated-reports.json
    ├── downloaded-reports.json
    ├── reports/
    ├── downloads/
    ├── 00_问题拆解.md
    ├── 01_资料来源.md
    ├── 02_事实卡片.md
    ├── 03_对比框架.md
    └── 04_推导过程.md
```

其中：

- `candidate-reports.md`：候选研报清单
- `selected-reports.md`：核心报告筛选记录
- `reports/`：预拉取的报告详情 JSON 和 Markdown
- `downloads/`：PDF 精读模式下保存 PDF 原文和抽取文本
- `02_事实卡片.md`：事实、来源、可信度沉淀
- `04_推导过程.md`：从事实到结论的显式推理链

## 示例

```text
用户：深度调研 荣耀终端股份有限公司竞争力与增长前景

Codex：
- 判断主题：公司竞争力与增长前景研究
- 界定边界：智能终端、AI 手机、渠道、供应链、海外扩张
- 拆解子问题：市场位置、产品组合、技术能力、渠道表现、风险约束
- 按子问题检索发现报告
- 预拉取核心报告详情并筛选
- 生成事实卡片、对比框架、推导过程
- 输出 FINAL_调研报告.md
```

## 核心特性

| 特性 | 说明 |
| --- | --- |
| **先想后搜** | 先明确研究边界和核心问题，再进入检索 |
| **MECE 拆解** | 将模糊主题拆成 3-7 个互斥且覆盖完整的子问题 |
| **定向检索** | 每个子问题绑定关键词，保留搜索维度映射 |
| **核心报告筛选** | 将候选报告分为核心必读、补充参考和背景材料 |
| **批量详情拉取** | 默认预拉取不少于 50 篇报告详情 |
| **PDF 精读模式** | 支持下载 PDF、抽取文本，并强制基于原文精读 |
| **事实卡片** | 每条事实都标注来源、报告 ID 和可信度 |
| **显式推导** | 通过对比框架和推导过程形成可复核结论 |
| **可交付工作区** | 自动生成中间产物和最终报告模板 |

## PDF 精读模式

默认情况下，主题研究使用 detail 接口返回的摘要、目录和正文摘录。开启 PDF 精读模式后，脚本会下载入选报告 PDF，并要求精读、事实卡片、对比框架和最终结论优先基于 PDF 原文。

开启方式：

```dotenv
FXBAOGAO_USE_PDF_READING=true
```

PDF 模式硬性规则：

- 开始 PDF 研究前先运行 `python3 scripts/validate.py .`
- 如果缺依赖，先运行 `python3 -m pip install -r requirements.txt`
- 不要在缺依赖时回退到 detail 模式
- 不要把 detail 摘要或正文摘录伪装成 PDF 原文精读

## 接口说明

本项目对接 3 个发现报告接口：

- 报告搜索：`/mofoun/report/searchReport/search`
- 报告详情：`/mofoun/report/searchReport/detail`
- 下载报告：`GET /mofoun/report/report/file/downloadReport`

更多字段约定见 `references/runtime-config.md`。

## 常见问题

**Q: 这和直接搜索发现报告有什么区别？**

A: 本项目不是只返回搜索结果，而是强制先拆解研究问题，再按子问题检索、筛选核心报告、沉淀事实卡片，并输出可追溯的研究工作区。

**Q: 没有 API key 能用吗？**

A: 不能使用。搜索、详情和下载接口都需要有效的 `FXBAOGAO_API_KEY`。

**Q: detail 模式和 PDF 模式怎么选？**

A: 快速初筛或轻量研究可以用 detail 模式；正式研究、需要引用原文或需要更高可复核性时，用 PDF 精读模式。

**Q: PDF 精读模式无法使用**

A:  PDF 精读需要扣取发现报告下载 pdf 的权益，请检查您的权益是否充足。

**Q: 可以只研究一篇报告吗？**

A: 可以，使用 `prepare_research_workspace.py` 创建单篇报告研究工作区。

## 致谢

本项目部分思路参考 [deep-research](https://github.com/wshuyi/deep-research)。
