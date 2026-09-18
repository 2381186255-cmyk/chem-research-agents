---
name: chem-research-loop
description: 化学领域的自主科研闭环工作流，采用多角色专业分工架构：侦察员（文献检索与语料治理）→ 分析师（概念结构化与空白探测）→ 假设官（生成可检验假设）→ 实验员（定向反证实验）→ 审稿人（22 项检查 + 9 阻断项 + 确定性证据门禁）→ 作家（研究报告）。接入 OpenAlex、Crossref、Europe PMC、arXiv 四个权威公开数据库，覆盖材料化学、有机合成方法学、计算化学、分析化学与传感四个方向。具备结构化知识库沉淀与断点续跑能力。当用户提出「查化学论文并总结研究思路」「找研究方向/研究空白」「做文献综述并给出切入建议」「化学选题调研」「帮我看看这个方向还能做什么」等需求时使用。也可用于任何需要「先摸清前人工作、再形成自己的假设、并用数据验证」的学术调研任务。
agent_created: true
---

# 化学自主科研闭环

把「看以前的研究 → 入手自己的研究 → 反复试验迭代 → 接入权威数据库 → 根据结果调整」这条链路，做成**可执行、可复核、可复现、可积累**的工作流。

## 三条不可妥协的原则

### 一、结论必须经过反证，不能只看统计

普通的文献综述工具跑到「统计出低共现组合」就结束了，直接把它当作研究空白输出。

**这是错的。** 实测数据：在 MOF CO2 capture 主题下，统计层面识别出 3 条「语料内零共现」的组合，看起来是完美空白 —— 定向检索一查，本领域早就有 7 篇、4 篇交叉研究。**语料内零共现 ≠ 真空白**，多半只是采样偏差。

所以本工作流强制加入**定向反证**：主动构造精准查询去打数据库，用真实结果去证伪每一条候选。

### 二、反证的比较基准必须一致

反证检索必须带**领域锚点**（如「MOF」）。若不带锚点，查 `catalysis selectivity` 会拉回全学科的通用催化文献，把本该成立的空白全部误判为「已被研究」（实测 8/8 全军覆没）。加锚点后同一批候选的交叉数从 13 降至 7，判定才回归合理。

### 三、审稿人不接受上游的自我声明

上游各阶段「自证清白」是不可靠的：统计口径会悄悄漂移、数据会静默丢失、结论会与证据脱节。因此设有独立审稿层，**一律自己重算**：

- 上游说「语料 103 篇」→ 审稿人自己数
- 上游说「MOF 出现 78 次」→ 审稿人从原始语料重算概念频次
- 上游说「这是空白」→ 审稿人复核证据锚点是否真实存在

任何阻断项未通过，结论一律不得交付。这不是形式主义 —— 实测中它抓出了「命题库静默丢失 3 条记录」「复算口径不一致」等真实缺陷。

---

## 快速开始

```bash
cd <skill_dir>/scripts

python run.py probe                                   # 数据源健康体检
python run.py list                                    # 列出四个研究方向
python run.py --topic "MOF CO2 capture" --domain materials --depth standard

python selftest.py                                    # 72 项离线单元测试（不联网）
python test_reviewer.py <产物目录>                     # 对已有产物单独跑审稿门禁
```

| 参数 | 说明 |
|---|---|
| `--topic` | 研究主题，中英文皆可。越具体越好 |
| `--domain` | `materials` / `synthesis` / `computational` / `analytical`，支持中文别名 |
| `--depth` | `quick`(≈35篇/1轮) / `standard`(≈100篇/3轮) / `deep`(≈200篇/5轮) |
| `--workspace` | 工作区根目录，每个主题自动建独立子目录 |
| `--no-resume` | 忽略历史进度，从头跑 |
| `--reset [阶段...]` | 重置指定阶段（连同其产物一起清理）后重跑 |

---

## 七个角色与职责边界

**关键分工：脚本负责可计算的部分，Agent 负责需要理解的部分。**

| 角色 | 职责 | 产出 |
|---|---|---|
| 侦察员 Scout | 多源并行检索 + 跨源去重 + 语料治理 | `Corpus/papers.json` |
| 分析师 Analyst | 概念抽取、年代趋势、共现网络、空白探测 | `analysis.json` |
| 假设官 Hypothesizer | 把缺口转成可检验假设卡（含否证条件） | `Propos/*.json` |
| 实验员 Experimenter | 逐条执行定向反证，给出确认/存疑/推翻 | `Verified/`、`Rejected/` |
| 审稿人 Reviewer | 37 项检查 + 9 阻断 + 确定性证据门禁 | `review_report.md` |
| 作家 Writer | 组装正式研究报告 | `Reports/research_report.md` |
| 可视化师 Visualizer | 生成交互式可视化（零 CDN，可离线打开） | `Reports/visualization.html` |

**Agent 应在此之上补做脚本做不到的事：**
- 阅读 `Reports/research_report.md`，理解领域脉络，写出有叙述逻辑的综述
- 对脚本给出的假设做**语义判断**：哪些真有科学意义，哪些只是概念排列组合
- 对「存疑」结论给出人工判读建议
- 把最终结论落到用户的具体研究语境里

---

## 验收门禁体系

### 9 项阻断项（任一未通过则禁止交付）

| 编号 | 检查 | 判据 |
|---|---|---|
| B01 | 语料规模足以支撑空白探测 | 去重后 ≥ 30 篇 |
| B02 | 可用数据源数量 | ≥ 2 个 |
| B03 | 元数据完整性 | 年份覆盖 ≥ 70% 且摘要覆盖 ≥ 30% |
| B04 | 主题关键词覆盖度 | ≥ 50% |
| B05 | 假设具备否证条件 | 全部 |
| B06 | 有效结论附证据锚点 | 全部 |
| B07 | 关键统计量可独立复算 | 逐项一致 |
| B08 | 无异常未来年份 | 0 条 |
| B09 | 存在可交付的有效结论 | 确认 + 存疑 > 0 |

### 24 项常规检查（告警，不阻断）

语料规模达标度、来源均衡度、去重有效性、DOI 覆盖、开放获取比例、年代跨度、
概念词典命中规模、频次分布健康度、候选评分区分度、证据锚点齐全度、
推翻率、反证留痕、领域锚点使用、命题库完整性等。

### 4 项确定性证据门禁

不依赖网络、结果可重复、可无条件作为交付前最后闸门：

| 编号 | 复核内容 |
|---|---|
| G01 | 语料计数复核 |
| G02 | 概念频次逐项复算 |
| G03 | 证据锚点真实性（锚点文献必须存在于语料中） |
| G04 | 共现计数独立复算 |

---

## 知识库结构

每次研究产出独立工作区（多任务并行隔离）：

```
workspace/<主题slug>__<方向>/
├── state.json      断点状态（当前阶段、已完成阶段、已验证组合）
├── control.json    人工干预信号（可选）
├── Problems/       研究问题清单
├── Corpus/         语料快照（可回溯）
├── Propos/         命题库（每条假设及其验证状态）
├── Methods/        方法库（可复用判据与框架）
├── Verified/       通过门禁的可信结论
├── Rejected/       已推翻结论（防止后续重复踩坑）
├── Reports/        最终交付报告
├── analysis.json  分析结果
└── run_log.json   运行台账
```

**`Rejected/` 是方法论沉淀的关键**：下次做同类研究时，已有的推翻记录能避免走弯路。

---

## 断点续跑与人工干预

```bash
# 中断后重复同一命令即可继续，已完成阶段自动跳过
python run.py --topic "..." --domain materials

# 强制重做某几个阶段（连同产物一起清理，避免陈旧文件混入）
python run.py --topic "..." --reset review write
```

运行期间在工作区写 `control.json` 可在阶段边界安全停机：

```json
{"action": "pause"}
```

`pause` / `abort` 都会在下一个检查点停机；改回 `continue` 后重跑即可继续。

---

## 四个研究方向

| key | 方向 | 空白组合维度 | 特点 |
|---|---|---|---|
| `materials` | 材料化学 | material×application、method×application、material×method、application×metric | 文献最密集，公开库覆盖最好 |
| `synthesis` | 有机合成方法学 | method×application、method×metric、material×method、application×metric | 条件/产率数据散在正文，抽取更难 |
| `computational` | 计算化学与分子模拟 | method×material、method×application、method×metric、material×application | 预印本占比高，方法学更新快 |
| `analytical` | 分析化学与传感 | material×application、method×application、material×method、application×metric | 指标高度结构化，最适合量化对比 |

新增方向只需在 `domains.py` 里继承 `ChemistryDomain` 并填领域知识，引擎无需改动。

---

## 数据源与降级策略

| 数据源 | 协议 | 状态 |
|---|---|---|
| OpenAlex | REST，免费 | 稳定，提供引用网络与概念标签 |
| Crossref | REST，免费 | 稳定，DOI 元数据权威 |
| Europe PMC | REST，免费 | 稳定，**PubMed 的完整镜像** |
| arXiv | Atom XML，免费 | 稳定，预印本前沿 |

**重要环境事实**：部分网络环境下 `pubchem.ncbi.nlm.nih.gov`、`eutils.ncbi.nlm.nih.gov`（PubMed 直连）**不可达**，Semantic Scholar 无 key 时持续 429 限流，ChemRxiv 返回 403。架构因此内置降级链：
- PubMed → **Europe PMC**（数据同源，无缝替代）
- 任一数据源失败只记录台账、不影响整体，并对 429/503 做指数退避重试

---

## 实测已知结论与局限

1. **成熟领域往往探测不到「确认」空白**。实测 MOF CO2 capture 在 103 篇语料下，所有候选要么被推翻、要么降级为「存疑」。这不是失败——说明该领域主流交叉已被充分研究，真正有价值的切入点需要更细的颗粒度或更大的语料。
2. **语料规模决定探测能力**。18 篇语料下空白候选为 0；103 篇下才有 3 条。`deep` 模式优先。
3. **概念词典决定覆盖面**。词典未收录的术语不会被统计到。若结果异常稀疏，优先补充 `domains.py` 的 `concept_seeds` 与 `COMMON_SYNONYMS`。
4. **同义词必须归并**。`MOF` / `MOFs` / `metal-organic framework` 若分开统计，频次会被稀释到跨不过门槛，空白探测直接失效。
5. **「存疑」结论需要人工判读**，脚本只负责把证据摆出来。
6. **概念粒度偏粗**，识别不了细颗粒度命题，这类真空白需 Agent 做语义推理补足。

---

## 模块结构

```
chem-research-loop/
├── SKILL.md
├── scripts/
│   ├── sources.py        数据源适配器（抽象基类 + 4 实现 + 降级链 + 双指纹去重）
│   ├── domains.py        四个方向的领域策略 + 225 条同义词归并表
│   ├── analyze.py        概念抽取 / 趋势 / 共现网络 / 空白探测与评分
│   ├── verify.py         反证实验（假设生成 / 领域锚点 / 定向检索判定）
│   ├── reviewer.py       审稿门禁（37 项检查 + 9 阻断 + 证据门禁）
│   ├── kb.py             结构化知识库 + 断点状态管理
│   ├── stats.py          统计显著性检验 + 多重比较校正
│   ├── writer.py         研究报告组装
│   ├── visualize.py      可视化产出（自包含 HTML，零 CDN）
│   ├── orchestrator.py   主控：七角色编排 + 断点续跑 + 门禁卡点
│   ├── run.py            命令行入口
│   ├── selftest.py       离线单元测试（72 项，不联网）
│   └── test_reviewer.py  对已有产物单独跑审稿
├── references/
│   └── methodology.md    方法论详解：算法定义、评分公式、调参指南
└── workspace/            各主题的独立工作区
```

零第三方依赖，仅用 Python 标准库，下载即跑。
