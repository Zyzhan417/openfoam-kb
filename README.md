# foamkb — OpenFOAM 源码检索层

> ## 研究结论（2026-10-26）
>
> **本项目是一次实验，结论是：作为 agent 检索层，它没有增量价值。**
>
> | 价值主张 | 实测 | 判定 |
> |---|---|---|
> | 比 `grep` 找得更准 | 机制题 7/7 vs 7/7（扣已核实的尺子偏差后） | **打平** |
> | 比 `grep` 更省 token | 12 题省 5.2%，最好一档 15.3% | **未达门槛（20%）** |
> | 引用更可信 | `grep` 臂 24/24 条自带逐字引文、23/24 过严格核对 | **非独占** |
>
> 运营成本还是反的：`grep` 零安装零存储；本项目要建 103 MB 索引（98 MiB）、26 秒，且绑死一个语料版本。
>
> **所以：如果你要查 OpenFOAM 源码，用 `rg` + `read_file` 更合理。**
>
> 这个仓库保留下来，是因为它记录了一套**可复用的验证方法论**
> （预注册判据、变异测试、双向投毒、独立复核），不是因为这个工具好用。
> 完整结论与证据：`reports/.orchestrator/project-conclusion.md`（未包含在本公开仓库中，见下方「关于本仓库」）。

给**编码 agent** 用的 OpenFOAM 源码检索层。给一句查询，返回若干
`(path, 起止行, 原文片段)`，每一段都能在原文件里按行号逐字对上。

**本项目只承担 R**（retrieval）。A（augmented）与 G（generation）由调用方 agent 承担：
检索链路**不调用任何生成式 LLM**，也不含向量通道。实现在 SQLite FTS5 上
（BM25 + 符号通道 + RRF 融合），零第三方依赖。

---

## 关于本仓库

这是一个**已收尾的实验**，不是维护中的工具。

它经历了 7 个 feature / 156 项任务的完整开发与 5 轮否证，最终结论是"没有增量价值"（见顶部）。
代码是可运行的（`379 passed`），但**不建议在生产或日常工作流中使用**。

开发期的过程文档（`reports/`、`specs/`、`AGENTS.md`、`ARCHITECTURE.md`）未随本仓库公开。
若你需要验证方法论本身（怎么设计预注册、怎么做变异测试、怎么防止自己骗自己），
那部分内容需要另外索取。

---

## 1. 为什么不是「查关键词」

OpenFOAM 的源码里，一个概念常常只在**某个类的 Description 段**里被陈述一次，而同一个词在
实现文件里出现几十次。朴素全文搜索会把「用了这个词的文件」排在「陈述这个概念的文件」前面。

本项目在切分层解决这个问题：块边界不切断结构单元（`class` / `function`）与文件级
`Description` 段，`chunks.kind` 区分 `class` / `function` / `filedoc` / `text` / `lines` /
`macro`，检索时对 `filedoc` 与 `symbol` 加权。所以「索引一个类是什么」与「索引它在哪里被用」
是两条不同的通道。

---

## 2. 安装

要求 **Python ≥ 3.12**。核心零依赖，测试只需 pytest。

```bash
git clone https://github.com/Zyzhan417/openfoam-kb.git
cd openfoam-kb
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

跑测试（**不需要 OpenFOAM 源码**，测试自带合成语料）：

```bash
.venv/bin/python -m pytest
# 期望：379 passed
```

---

## 3. 准备语料并建索引

索引需要一份 OpenFOAM 源码树。任何 OpenFOAM 版本都行；本文档以 OpenFOAM-v13 为例，
它的源码在 `$FOAM_SRC`（典型路径 `/opt/openfoam13` 或 `~/OpenFOAM/OpenFOAM-13`）。

```bash
export FOAM_SRC=/opt/openfoam13        # 改成你自己的路径

.venv/bin/python -m foamkb.cli build \
  --corpus "$FOAM_SRC" \
  --index  ~/.local/share/foamkb/openfoam.sqlite \
  --include 'src/**/*.C' --include 'src/**/*.H' \
  --include 'applications/**/*.C' --include 'applications/**/*.H'
```

期望输出（OpenFOAM-v13，8,911 文件）：

```
rev=<8 位十六进制> corpus_files=8911 corpus_lines=1609176 chunk_count=92208 ...
```

`rev` 是语料内容的指纹，会写进每个 `ref`；语料变了 `rev` 就变，旧 `ref` 会被拒绝而不是
静默返回错误结果。全库构建约 **26 秒**。

### glob 语义（一个容易踩的坑）

`--include` 用的是 `fnmatch`，它的 `**` **不匹配零层目录**：

| 模式 | 匹配 `src/foo.H`？ |
|---|---|
| `src/*.H` | ✅ |
| `**/*.H` | ✅（产品对 `**/` 前缀做了零层兜底） |
| `src/**/*.H` | ❌ 要求 `src/` 之下**至少再有一层** |

对真实的 OpenFOAM 树没有影响（文件都在两层以上），但**自建小语料时要注意**：
用 `**/*.H` 或 `src/*.H`，别用 `src/**/*.H` 去指一个扁平目录。

---

## 4. 查询

```bash
.venv/bin/python -m foamkb.cli search \
  --index ~/.local/share/foamkb/openfoam.sqlite \
  --query "incompressible turbulence model" \
  --top-k 5
```

输出是 JSON（以下为真实返回，未经改写）：

```json
{
  "rev": "2cf4316a",
  "hits": [
    {
      "ref": "2cf4316a:src/MomentumTransportModels/phaseIncompressible/phaseIncompressibleMomentumTransportModel.H::27",
      "path": "src/MomentumTransportModels/phaseIncompressible/phaseIncompressibleMomentumTransportModel.H",
      "start_line": 27, "end_line": 34,
      "text_start_line": 27,
      "symbol": null, "kind": "filedoc",
      "text": "Description\n    Templated abstract base class for multiphase incompressible\n    turbulence models.\n...",
      "truncated": false,
      "doc": null
    }
  ]
}
```

> 这个示例是 `--top-k 1` 的真实输出。注意它命中的是**多相**版本（`phaseIncompressible…`）——
> 单相版本的 Description 也讲同一件事，但它在这个查询上的 BM25 名次更低。
> 这类「同一个概念有多个同类文件」的情形，是使用本项目时最需要 agent 自己判断的地方：
> 返回的是一个**候选集合**，不是唯一答案。

字段说明：

| 字段 | 含义 |
|---|---|
| `ref` | 唯一凭据 `rev:path::start_line`，可直接引用、可事后核对 |
| `start_line` / `end_line` | 该块在文件中的行范围（1-based，闭区间） |
| `text_start_line` | `text` 第一个字符对应的行号 |
| `text` | 该块原文；`truncated=true` 表示被 `--max-chars` 截断 |
| `kind` | `class` / `function` / `filedoc` / `text` / `lines` / `macro` |
| `symbol` | 命中来自符号通道时填符号名（类型别名、类名） |
| `doc` | 该块所属文件的 `Description` 摘要（若有） |

参数：`--top-k`（**默认 5，上限 20**）、`--path-prefix`（按目录收窄）、
`--max-chars`（预览长度，**默认 1500，允许 200–8000**，越界会被拒绝）。

### 查询写法建议

- **2–4 个实词**效果最好。AND 语义下，多一个不存在的词就整条归零。
- 查**类型名 / 类名 / 函数名**最准（符号通道会兜住 typedef 与 `using` 别名）。
- 查**一句人话**（"how is turbulence dissipation handled at the wall"）命中率很低。
  这是设计取舍，不是缺陷：需要先把需求翻成语料里的词。

> **注意：这里曾写「实测 11 条用户提问原词直搜只有 2 条命中，换成语料措辞后 11 条全中，
> 缺的是词汇不是检索能力」——这个论断后来被自己的实验推翻了。**
> 那 2/11 测的是「**一次查询**能否命中」，而真实的 agent 在端到端任务里会自己减词重试：
> 三臂实验里 agent 第一次就把 "turbulence model for aerodynamics that needs only one
> equation" 简化成 3 个实词，一次命中，**重试代价为 0**。
> 所以「词汇缺口」在单次查询上成立，在 agent 端到端能力上被吸收了——不能用它论证这个工具有用。

---

## 5. 给 agent 用

在 agent 的提示或工具描述里，把 CLI 包成一条工具即可：

```
search(query, top_k=20, path_prefix=None, max_chars=300)
  → [{"ref", "path", "start_line", "end_line", "text", "kind"}, ...]
```

三条使用约定（都是从实测里来的）：

1. **只引用返回过的 `ref`。** `ref` 里的 `rev` 与当前索引不符时，工具应报错而不是返回空——
   语料换版本后旧引用必须失效。
2. **看全文就抬 `--max-chars` 重查同一条**，而不是另找一次查询：`top-k` 20 是上限，
   截断的块用 `--max-chars 3000` 重查仍是同一条命中。
3. **返回空结果时，换词重试，不要换工具。** 空结果意味着查询词在语料里不同时出现，
   是最有用的反馈信号（这也是为什么本项目没有实现「AND 为空自动降级 OR」——
   降级会把假阳性混进来，毁掉这个信号）。

---

## 6. 已知限制（都是实测取舍，不是待办）

> **最重要的一条不在下表：与 `grep` 相比它没有可测的增量。**
> 正确率打平、token 省 5.2%、引用可核对性非独占（详见顶部研究结论）。
> 下表是"即使你想用它，它也有这些短板"。

| 限制 | 说明 |
|---|---|
| **无向量 / 语义检索** | 曾实测否决：`bge-small-en-v1.5` 在 14 条查询上 hit@5 = 3/14（阈值 60%），且引入 9 条退化。改为代码专用模型后可复议 |
| **无中文直查** | 分词器是 `porter unicode61`（英文）。中文查询需调用方先翻译成英文 |
| **只索引代码，不索引 tutorials** | `tutorials/` 有 4,318 个无扩展名字典文件，需独立的键值切分规则，未做 |
| **泛词被词干化抹平** | `set`/`setting`/`settings`/`sets` 在索引里是同一个词干，命中 7,922 块（全库 8.6%）。后果不是「查不到」而是「查到一大堆无关块」——比查不到更难察觉 |
| **`--include` 的 `**` 不匹配零层** | 见 §3 |
| **单机单版本只读** | 索引按 `rev` 绑定语料；换版本要重建（26 秒） |
| **每次返回体积偏大** | 默认 `--top-k 10` 一次吐约 10,000 字节，而 `rg -l` 只吐文件名。实测 `--top-k 3 --max-chars 600` 能把输出降到 2,029 字节（−65%）而命中数不变——但即便这样，总 token 也只比 grep 少一点，因为固定成本占大头 |

---

## 7. 项目结构与文档

```
src/foamkb/          检索层（切分、索引、查询、Reader）
tests/               379 个测试（自带合成语料，不需要 OpenFOAM 源码）
scripts/             评测工具与语料校验（见下）
AGENT_PROTOCOL.md    一页 agent 检索规程
README.md            本文件
```

`scripts/` 里的东西分两类，都能在本地直接跑（需先按 §3 建好索引）：

```bash
# 评测尺子自检（不需要索引）
PYTHONPATH=src .venv/bin/python scripts/verify_accept_sets.py
PYTHONPATH=src .venv/bin/python scripts/verify_user_questions.py
PYTHONPATH=src .venv/bin/python scripts/self_test_judge.py

# 语料与检索契约校验（需要一个建好的索引）
PYTHONPATH=src .venv/bin/python scripts/verify_structure.py <index.sqlite>
PYTHONPATH=src .venv/bin/python scripts/verify_retrieval.py <index.sqlite>
PYTHONPATH=src .venv/bin/python scripts/verify_pilot.py --help
```

**未随仓库提供的东西**（都是开发过程产物，不是运行所需）：

| 缺什么 | 影响 |
|---|---|
| `reports/` 下的 agent 运行记录 | `judge_runs.py --judge`、`ta_verdict.py`、`probe_third_caliber.py` 需要它们才能复算历史分数。代码在，数据不在——这些脚本的用途是「给定一批运行记录做机械判定」，不是开箱即用 |
| `specs/`、`AGENTS.md`、开发门禁脚本 | 本项目的开发期验证链（`verify_gates.py` 等 14 道门）不在公开版里。它们检查的是过程文档与提交规范，对使用者无意义 |
| `ARCHITECTURE.md` | 设计基线。需要它才能改代码，但按发布决定未随仓库提供 |
| 预建索引 | 按 §3 自建（26 秒）。索引按语料 `rev` 绑定，跨机器分发没有意义 |

