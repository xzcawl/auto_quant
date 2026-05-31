# auto_quant

**策略研究流水线**：你给思想 → 建档记录 → 实现/挂载代码 → 回测验证 → 报告 → Cursor AI 分析。

```
  ┌─────────────┐     ┌──────────────┐     ┌─────────────┐     ┌──────────────┐
  │ 1. 策略思想  │ ──► │ 2. research  │ ──► │ 3. 回测验证  │ ──► │ 4. AI 分析   │
  │  (你描述)   │     │  项目建档     │     │ 本地/聚宽   │     │ @analysis_   │
  └─────────────┘     └──────────────┘     └─────────────┘     │  request.md  │
                                                                └──────────────┘
```

## 安装

```powershell
cd e:\auto_quant
python -m venv .venv
.venv\Scripts\activate
pip install -e .
python -m playwright install chromium
```

复制 `.env.example` 为 `.env`（聚宽可选）。

---

## 核心工作流（推荐）

### ① 新建策略项目（记录思想）

```powershell
python scripts/research.py new "五福ETF轮动" --idea "动态池轮动，全球+国内ETF，按动量调仓..."
```

或从已有聚宽文件一键导入：

```powershell
python scripts/research.py import output/jq_strategies/test_wufu_V5.0_max.py --idea "五福 v5 动态池"
```

每个策略在 `research/strategies/STR-YYYYMMDD-名称/` 下有：

| 文件 | 作用 |
|------|------|
| `idea.md` | 你的策略思想（可随时改） |
| `meta.yaml` | 状态、平台、回测区间 |
| `strategy.py` | 聚宽代码（import/attach 后） |
| `runs/*.json` | 每次回测原始数据 |
| `report.md` | 汇总报告 |
| `analysis_request.md` | **给 Cursor AI 的分析稿** |

### ② 回测验证

```powershell
# 聚宽策略（自动提交聚宽，需先 login）
python scripts/run_jq_batch.py --login
python scripts/research.py run STR-20260520-五福ETF轮动

# 本地 MACD 示例策略
python scripts/research.py new "MACD底背离" --platform local_macd --idea "30min底背离+MA20"
python scripts/research.py run STR-20260520-MACD底背离
```

### ③ 生成报告 + Cursor AI 分析

```powershell
python scripts/research.py report STR-20260520-五福ETF轮动
```

在 **Cursor 聊天**里输入：

> 请阅读 `@research/strategies/STR-xxx/analysis_request.md` 和 `@research/strategies/STR-xxx/strategy.py`，按文档中的问题给出量化评审与改进建议。

Agent 行为见根目录 [`AGENTS.md`](AGENTS.md)（含 joinquant-skill 检查要点）。

### ④ 多优化点消融（idea.md → 逐项回测 → 是否合并）

与 `idea.md` 里「## 1. … ## 2. …」章节对齐，自动生成 `ablation.yaml`，每个优化点对应一份 `variants/idea-N.py`（由你从 `strategy.py` 复制后**只改该点**）。

```powershell
python scripts/research.py ablation-init STR-20260520-ETF轮动1.7.1
# 按 research/strategies/.../variants/README.md 操作，编辑 ablation.yaml 将某变体 enabled: true

python scripts/research.py ablation-run STR-20260520-ETF轮动1.7.1
python scripts/research.py ablation-report STR-20260520-ETF轮动1.7.1
```

- 结果：`runs/*_jq_ablation_*.json` + **`jq_links.yaml`**（每变体 `report_url` / `edit_url`）+ `ablation_report.md`。
- 聚宽策略名：`ETF轮动_baseline`、`ETF轮动_idea-1` 等（可区分变体）。
- 单浏览器会话跑完全部变体：`python scripts/research.py links <ID>` 查看报告链接。
- `--dry-run`：不写聚宽，只生成占位 JSON 测流水线。

### ⑤ 迭代

根据 AI 建议改 `idea.md` / `strategy.py` → 再 `research run` → 再 `report` → 状态改为 reviewed：

```powershell
python scripts/research.py status STR-xxx reviewed
```

---

## 常用命令速查

```powershell
python scripts/research.py ablation-init <ID> [--force]
python scripts/research.py ablation-run <ID> [--dry-run]
python scripts/research.py ablation-report <ID>
python scripts/research.py ablation-queue <ID> [--run]

python scripts/run_jq_single.py -f output/jq_strategies/xxx.py   # 单策略聚宽（不入 research）
python scripts/show_jq_results.py          # 聚宽任务列表

python scripts/run_backtest.py             # 仅本地 MACD（不入 research）
python scripts/run_optimize.py --rounds 20
```

---

## 两条回测通道

| 平台 | 代码位置 | 适用 |
|------|----------|------|
| `joinquant` | `research/.../strategy.py` 或 `output/jq_strategies/` | 聚宽克隆策略、ETF 轮动等 |
| `local_macd` | `src/auto_quant/strategies/` | MACD 底背离等本地快速迭代 |

---

## 聚宽自动化：页面不动 / 仍是默认模板

**原因简述**

1. **`keyboard.type()` 不适合 Ace**：大文件（如五福 1400+ 行）极慢或失败，代码不会进编辑器。
2. **默认 URL 打开的是「简单策略」模板**：`https://www.joinquant.com/algorithm/index/edit` 不带策略 id 时，改的是模板页，看起来像「没执行」。

**请按下面做**

1. 在浏览器打开你要回测的那条策略的**编辑页**，从地址栏复制**完整 URL**。
2. 写入 [`config/settings.yaml`](config/settings.yaml) 的 `joinquant.backtest_url`。
3. 再执行：`python scripts/research.py run STR-xxx`。

终端会打印 `[jq]` 日志：`注入策略代码` → `编辑器注入完成` → `已触发运行`。若出现「未找到运行回测按钮」或「无法写入编辑器」，把该段日志和页面截图发出来便于改选择器。

---

## 免责声明

回测结果不代表未来收益，仅供研究，请勿据此实盘交易。
