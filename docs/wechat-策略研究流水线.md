# 从「一个想法」到「可评审的策略」：auto_quant 策略研究流水线实践

> 本文介绍开源项目 **auto_quant** 如何把量化策略研究做成一条可重复、可存档、可交给 AI 评审的流水线。样例策略选用聚宽社区热门的 **五福 v5（ETF 动态池轮动）**。

---

## 一、为什么需要「流水线」，而不只是「写策略」？

很多个人量化研究者会卡在同一个循环里：

1. 在聚宽/券商里改一版代码 → 点回测 → 截图保存；
2. 过几天又改一版 → 旧结果找不到了；
3. 想请 AI 帮忙看逻辑 → 得重新粘贴代码、口述背景、数字还对不上。

**auto_quant** 的做法是：把研究过程**文件化**——思想、代码、每次回测原始 JSON、人类报告、给 AI 的分析请求，都落在同一个项目目录里。你负责想策略；工具负责建档、触发回测、汇总报告；**Cursor** 负责按固定模板做量化评审。

一句话概括：

**你给思想 → 建档记录 → 实现/挂载代码 → 回测验证 → 报告 → Cursor AI 分析**

```
  ┌─────────────┐     ┌──────────────┐     ┌─────────────┐     ┌──────────────┐
  │ 1. 策略思想  │ ──► │ 2. research  │ ──► │ 3. 回测验证  │ ──► │ 4. AI 分析   │
  │  (你描述)   │     │  项目建档     │     │ 本地/聚宽   │     │ @analysis_   │
  └─────────────┘     └──────────────┘     └─────────────┘     │  request.md  │
                                                                └──────────────┘
```

---

## 二、一个策略项目长什么样？

每个策略对应目录：`research/strategies/STR-YYYYMMDD-名称/`

| 文件 | 作用 |
|------|------|
| `idea.md` | 策略思想、优化方向（可随时改，是研究的「主文档」） |
| `meta.yaml` | 状态、平台、回测区间 |
| `strategy.py` | 聚宽实现（或从 `output/jq_strategies/` 导入） |
| `runs/*.json` | **每次**回测的原始结果（不编造数字，以这里为准） |
| `report.md` | 汇总报告 |
| `analysis_request.md` | **专门给 Cursor 的分析稿**（@ 这个文件即可开聊） |
| `ablation.yaml` + `variants/*.py` | 可选：多优化点消融实验 |

状态机也很简单：`draft` → `implemented` → `backtested` → `reviewed` → `archived`。

---

## 三、核心入口：`research.py` —— 一条命令串起全流程

`scripts/research.py` 是面向研究者的 CLI，内部调用 `src/auto_quant/research/` 里的建档、回测、报告生成逻辑。

**设计思路**：子命令对应研究阶段，而不是把聚宽、本地回测、消融拆成互不相关的脚本。

```python
#!/usr/bin/env python
"""
策略研究工作流 CLI

  思想 -> 建档 -> 实现/挂载代码 -> 回测 -> 报告 -> Cursor AI 分析
"""

# 子命令一览（节选）
#   new          新建项目，写入 idea.md / meta.yaml
#   import       从已有聚宽 .py 一键导入
#   attach       把外部策略文件挂到项目
#   run          按 meta.platform 跑回测（聚宽 or 本地 MACD）
#   report       生成 report.md + analysis_request.md
#   status       更新项目状态
#   ablation-*   消融：init / run / report / refresh-metrics ...
```

**典型命令：**

```powershell
# ① 新建（或从已有聚宽文件导入）
python scripts/research.py new "五福ETF轮动" --idea "动态池轮动，全球+国内ETF..."
python scripts/research.py import output/jq_strategies/test_wufu_V5.0_max.py --idea "五福 v5 动态池"

# ② 回测（聚宽需先登录，见下一节）
python scripts/research.py run STR-20260520-五福v5

# ③ 生成报告 + AI 分析稿
python scripts/research.py report STR-20260520-五福v5

# ④ 在 Cursor 里：
# 请阅读 @research/strategies/STR-20260520-五福v5/analysis_request.md 并给出评审
```

`run` 完成后会自动写 `runs/时间戳_jq.json`，并提示 `analysis_request.md` 路径——这是把「回测」和「可评审」绑在一起的关键一步。

---

## 四、聚宽批量通道：`run_jq_batch.py` —— 登录态 + 任务队列

聚宽策略往往上千行（五福 v5 约 1400+ 行），不适合每次手抄代码。项目用 **Playwright** 自动化：注入代码、改回测区间、点「运行」，结果写入 SQLite 队列。

`scripts/run_jq_batch.py` 是聚宽侧的薄封装：

```python
#!/usr/bin/env python
"""JoinQuant batch backtest queue."""

def main():
    # --login     保存浏览器登录态（首次必做）
    # --from-best 把本地最优策略入队
    # --dry-run   不打开浏览器，只测流水线
    if args.login:
        from auto_quant.joinquant.playwright_runner import save_login_state
        save_login_state()
        return

    from auto_quant.joinquant.playwright_runner import run_batch
    run_batch(dry_run=args.dry_run)
```

**推荐用法：**

```powershell
python scripts/run_jq_batch.py --login    # 首次：浏览器登录聚宽，保存 cookie
python scripts/research.py run STR-xxx    # 单项目回测（内部也会走队列/Playwright）
```

注意（README 里写得很实在）：

- 大文件不要用「模拟键盘输入」进 Ace 编辑器，项目改为 **JS 注入** 写入；
- `config/settings.yaml` 里的 `joinquant.backtest_url` 必须是**你要改的那条策略的编辑页完整 URL**，否则会改到默认模板页，看起来像「没执行」。

---

## 五、样例：五福 v5 —— 思想怎么写、代码怎么挂、怎么消融

### 5.1 策略在说什么？

五福 v5（聚宽帖：[五福闹新春 v5.0](https://www.joinquant.com/post/71294)）核心是：

**ETF 动量轮动 + 全球/国内双固定池 + 全市场动态行业池**；大 A 走弱时收缩到全球/海外池，正常期合并固定池与动态池，13:10 调仓。

在 `idea.md` 里我们会写清「基线已实现什么」和「待验证优化点」，例如：

- §1 5 日加速补分（加快跟上热点）
- §2 近 3 日相对局部高点回撤（减少误杀）
- §4 信号口径改为「仅用昨收」（回测可信度）
- §6 动态池行业键粒度（五福特有问题）

这样 **idea 的章节 = 消融实验的清单**，不会想到哪改到哪。

### 5.2 代码挂载

导入后项目里有 `strategy.py`（从聚宽克隆），`initialize` 里定义全球池、国内池，并打开 `avoid_future_data`：

```python
def initialize(context):
    set_option("avoid_future_data", True)
    set_option("use_real_price", True)
    # ...
    g.global_etf_pool = [ '518880.XSHG', ... ]  # 黄金、原油、海外 ETF
    g.china_etf_pool = [ '513090.XSHG', ... ]   # 港股、指数、行业 ETF
```

完整逻辑在 `research/strategies/STR-20260520-五福v5/strategy.py`，研究时以仓库版本为准。

### 5.3 消融：一次只改一个优化点

```powershell
python scripts/research.py ablation-init STR-20260520-五福v5
# 复制 strategy.py → variants/idea-N.py，只改对应开关
python scripts/research.py ablation-run STR-20260520-五福v5
python scripts/research.py ablation-report STR-20260520-五福v5
```

会生成 `ablation_report.md`：各变体年化、夏普、最大回撤对比表 + 启发式「是否合并」建议。

**重要**：合并建议不能替代人工。例如同一区间里，有的变体年化极高但夏普极低，往往是口径、样本或过拟合信号——需要对照 `idea.md` 里的「预期效果」和聚宽报告页再决定。

项目 meta 里也会记笔记，例如：`214/357不合并; strategy_B长测接近原版`——这是「研究日志」，不是推销收益。

---

## 六、第四步：Cursor AI 分析 —— 固定模板，减少重复劳动

`report` 会生成 `analysis_request.md`，里面已有：

- 策略思想摘要
- 最近一次 `runs/*.json` 摘要
- 代码预览
- 请 AI 回答的问题列表（逻辑有效性、未来函数、交易成本、是否进入下一轮等）

在 Cursor 聊天里：

> 请阅读 `@research/strategies/STR-20260520-五福v5/analysis_request.md`，并对照 `@strategy.py` 与 joinquant-skill 给出量化评审。

仓库根目录 `AGENTS.md` 规定了 Agent 必须：以 `runs/*.json` 为准、不编造回测数字、消融时读 `ablation_report.md`、并带免责声明。

---

## 七、两条回测通道，怎么选？

| 平台 | 代码位置 | 适用 |
|------|----------|------|
| `joinquant` | `research/.../strategy.py` | ETF 轮动、聚宽社区策略克隆 |
| `local_macd` | `src/auto_quant/strategies/` | MACD 底背离等，本地快速迭代 |

五福 v5 走 **joinquant**；若你在改因子参数、想几分钟内跑完，可以用 `local_macd` 通道做原型，再上聚宽做「权威」回测。

---

## 八、适合谁、不适合谁？

**适合：**

- 个人/小团队，策略以聚宽为主，希望**版本化思想 + 回测结果**；
- 已经在用 Cursor，想把 AI 变成「带上下文的量化审稿人」；
- 优化点多，需要 **ablation** 而不是凭感觉合并代码。

**不适合：**

- 期望一键实盘、或替代聚宽风控系统；
- 不愿意维护 `idea.md` 和项目目录（流水线价值会打折）。

---

## 九、免责声明

文中涉及的回测指标均来自项目内 `runs/*.json` 与聚宽报告页，**回测不代表未来收益**，仅供研究，请勿据此实盘交易。

---

## 附录：快速上手清单

```powershell
cd auto_quant
python -m venv .venv
.venv\Scripts\activate
pip install -e .
python -m playwright install chromium

python scripts/run_jq_batch.py --login
python scripts/research.py import output/jq_strategies/test_wufu_V5.0_max.py --idea "五福 v5"
python scripts/research.py run STR-20260520-五福v5
python scripts/research.py report STR-20260520-五福v5
# Cursor: @ analysis_request.md
```

项目地址与细节以仓库 `README.md`、`AGENTS.md` 为准。

---

*如果这篇文章对你有用，欢迎 Star / 转发；下一篇可以单独写「五福 v5 消融里哪些改动值得合并、哪些只是口径差异」。*
