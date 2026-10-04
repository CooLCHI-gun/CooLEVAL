<!-- 廣東話導讀 — Cantonese reading guide · 2026-10-04 -->

# CooLEVAL — 廣東話導讀

> 內容以 [英文版 README](../README.md) 為準（English README is canonical）。
>
> 呢份係一份用**廣東話書面語**寫嘅導讀，唔係完整翻譯，亦唔係第二份規格。技術名詞、
> schema 名稱、指令同程式碼一律保留英文原文。

---

## 概述

CooLEVAL 係一套公開嘅 dogfood agent-evaluation framework，用**真實 production
telemetry**（一個 SQLite eval DB）嚟量度 AI agent 有幾可靠。核心原則係**誠實嘅統計**：
唔靠直覺、唔靠單次成功；樣本數唔夠就用 gate 擋住，唔畀你求其落結論。

---

## 量度三個層面

CooLEVAL 唔止睇「模型答啱幾多題」，而係睇三個層面：

1. **Model 層** — agent 喺真實任務入面嘅成功率，配埋 Wilson CI 同 n-gate。
2. **Memory 層**（memory-eval）— 記憶後端決定模型見唔見到啱嘅 fact。呢層同
   Model 層一樣，要做 head-to-head 比較。
3. **成本層**（token-efficiency）— 每次成功要使幾多 token 同幾多美金。

---

## 核心洞察：Meltdown（崩潰曲線）

最緊要嘅發現：**agent 喺短 session 表現好好，但 session 一過咗大概一個鐘就會崩潰。**
呢個唔係模型能力唔夠，而係長時間運行本身就係一種失效模式。

- 短 session（幾分鐘到十零分鐘）：成功率高、穩定。
- 長 session（**超過 1 小時**）：context 累積、狀態漂移，成功率急跌。

所以英文版有 **Meltdown Curve** 同 **Survival & Hazard** 兩節，用生存分析嘅角度
睇一個 session 捱得幾耐先開始失敗。

---

## 解決方法：咁其實應該點做？

> 呢啲係可測試嘅介入假設，唔係保證嘅藥方。

Meltdown 係一種 **ASSOCIATION**，唔係證實咗嘅因果：長 session 可能只係本身難啲嘅
task。所以下面呢啲係可測試嘅 intervention，每一個都配一個 CooLEVAL 已經量度緊嘅
proxy——唔係藥方。

1. **為 session 設 budget** — 封頂 elapsed time / tool calls / retries；去到上限就
   checkpoint 再 hand off，唔好夾硬繼續。Proxy：按 budget band 分嘅成功
   rate；over-budget run rate。
2. **Checkpoint 落 disk，唔係落 context** — 將完成咗嘅工作同已驗證嘅 artifact
   以精簡、atomic 嘅方式持久化，咁就算 crash 都唔會變咗假嘅 completion。
   Proxy：中斷咗但可以 resume 返去一個已驗證 artifact 嘅 run；recovery time。
3. **將 handoff / resume 當成 first-class** — objective、constraints、evidence
   references、next action；要驗證個 handoff，唔止係驗證佢有冇寫低。Proxy：
   post-handoff completion；resume 要用幾多 token 同時間；missing-state rate。
4. **用 bounded subtask 嚟 isolate sub-agent** — 合併之前先驗證結果；唔可以當
   加 agent 一定幫到手。Proxy：已驗證嘅 subtask acceptance；rework rate；對比
   single-agent condition 嘅 end-to-end success 同 cost。
5. **保持 context 精簡** — 用 summarise / retrieve 代替一路揹住成條無上限嘅
   transcript；掉走過時嘅 tool output；保留 evidence references，咁壓縮就唔會
   靜靜雞抹走咗個 requirement。Proxy：context-growth rate；重複或者無關嘅
   tool call；requirement-omission rate。
6. **喺 CooLEVAL 已經追蹤緊嘅 precursor 上面設 alert**（retries、loops、slow
   steps、context growth），配一個 threshold 同寫明嘅 response policy——假警報
   都要報。Proxy：alert precision / recall vs 之後嘅 failure；failure 之前嘅
   lead time。
7. **繼續以 artifact-verified completion 做 gate。** Proxy：false-completion
   rate。

**呢一節一定唔可以宣稱嘅嘢：** 唔可以講縮短 session 就會成功；唔可以講短 session
本身一定可靠啲；亦都唔可以講呢個 pooled contrast 可以推廣到呢個 workload 以外。
要用 matched task types、uncertainty intervals 同 artifact-based outcomes 嚟評估；
low-n bucket 一律當探索性處理。

> 想即刻試其中一項？`agent-plugin/cooleval-guard` 就係「喺 precursor 上面設 alert」
> 嗰一步——observe-only，唔會攔截任何 tool call，亦唔會寫低任何嘢。

---

## Step 層級軌跡（step trajectory）

Meltdown 話你知「session 會崩潰」，而 **step trajectory** 就話你知「**係邊一個 tool
call 先出事**」。`trace-steps.py` 由 span telemetry 砌出 per-session / per-run 嘅
step timeline，指出：

- **第一個失敗嘅 step** — 工具名稱 + `error_type` / `error_message`；
- **loop 訊號** — 同一個工具連續重複嘅長度（一種卡死嘅表徵）；
- **最慢嘅 step** — 突破延遲預算嘅離群值；
- **structure-only 預覽** — `args_shape`（key → value 型別）同 `result_summary`
  （型別 + 大小），睇得到嗰次 call 嘅**形狀**同**大小**，但**唔會持久化任何 raw
  arg/result 內容**（PII-safe）。

換句話講：成功率答「**可唔可靠**」，step trajectory 答「**邊一步、咩形狀、幾大、
有冇喺度 loop**」。

---

## 統計誠實原則（最重要）

CooLEVAL 嘅立場：**寧願唔出結論，都唔可以出錯結論。**

- **Wilson CI** — 所有成功率都附信賴區間，唔係淨係俾一個數。小樣本嘅區間會好闊，
  一眼就睇得出個數可唔可靠。
- **n-gate** — 樣本數未到門檻就直接標記做 `gated`，唔會用嚟排名或者宣稱邊個贏。
- **飽和（saturated）唔算贏** — 例如兩個後端喺 8 個 fact 上面都係 8/8，呢個叫飽和，
  唔可以認定邊個贏。要分高下，就要跑一個樣本夠大、有分辨力嘅 benchmark。
- **exploratory ≠ confirmatory** — 單次執行、synthetic corpus 得出嘅結果屬於探索性質，
  用嚟產生 hypothesis，唔係用嚟定論。

呢啲原則喺英文版 **Limitations** 同 **Design Principles** 有更詳細嘅說明。

---

## Memory-Eval（記憶層評估）

- 用共用嘅 fact corpus 做後端 head-to-head 比較。
- 四個 retrieval class：**single**（單一 fact 直接 recall）、**multi**（fact 組合）、
  **noisy**（啱嘅 fact 埋喺大量 distractor 入面）、**under**（query 語意唔清、要推理）。
- **under class 會 gate 去 LLM judge**（`--llm`）；預設 dry run 保持 deterministic
  評分，唔會俾 judge 嘅波動污染 head-to-head。
- 結論：唔同後端有相反嘅失效模式（有啲擅長乾淨 recall、有啲擅長抗噪），冇一個係
  全能贏家——**失效模式本身先係重點。**

（詳細數據請睇英文版 **Memory-Eval** 一節。）

---

## Token-Efficiency（成本效率）

- `eval-runner` 用 `--usage-file` 收集 token telemetry。
- `eval-tokeneff.py` 計 **cache-adjusted tokens-per-success**（cache 命中折扣咗嘅
  billable token ÷ 成功任務數）同埋 **USD-per-success**。
- **重要：呢個係成本指標，唔係品質指標。** 用少啲 token 淨係代表成本低啲，唔代表答得
  好啲。應該永遠同 Wilson-CI 成功率一齊睇。一個過唔到 n-gate 嘅低成本模型，
  唔可以叫高效，只能叫便宜。

---

## 詞彙對照（保留英文，唔翻譯）

| 英文名 | 屬性 | 廣東話簡述 |
|--------|------|----------|
| `span metrics` | schema / 指標 | 一段執行區間嘅量度數據 |
| `battery_runs` | table | 一批評估跑次嘅記錄 |
| `task_events` | table | 逐個任務事件嘅明細 |
| `spec_hash` | 欄位 | 評估規格嘅 hash，用嚟確認係同一份 spec |
| `Wilson CI` | 統計方法 | Wilson 信賴區間 |
| `n-gate` | 機制 | 樣本唔夠就唔出結論 |
| `MCP` | 協定 | Model Context Protocol |
| `FastMCP` | 框架 | 用嚟架 MCP server 嘅 Python framework，agent 透過佢暴露嘅工具嚟操作 |
| `JSON-RPC` | 協定 | MCP 底層嘅 request/response 傳輸協定，call 工具同收結果都經呢個機制 |
| `SQLite` | 資料庫 | 儲存 ETL 之後 run/step/event 資料嘅本地 DB，`eval-metrics.py` 直接查 |
| `eval-runner.py` | script (dogfood battery) | 執行 dogfood battery，實際觸發 agent 跑一系列 task 並記錄 raw log |
| `eval-tokeneff.py` | script (成本效率) | 計 token 使用同成本效率，睇每個 task 要用幾多 token 先搞得完 |
| `meltdown curve` | 現象 | 長 session 入面 success rate 急跌嘅曲線，反映 agent 好難支撐長 context |
| `survival & hazard` | 分析方法 | 借用 survival analysis 同 hazard rate 睇 agent 幾時開始「死亡」，同失敗風險隨時間點變 |
| `extreme tests` | 功能 (ceiling battery) | ceiling battery，刻意用高難度 task 逼出 agent 嘅能力上限 |
| `step trajectory` | 功能 (診斷) | 逐個 tool call 拆解執行過程：指出第一個失敗嘅 step、loop 訊號、最慢 step |
| `trace-steps.py` | script (step trajectory) | 由 span telemetry 砌出 per-session / per-run 嘅 step timeline |
| `continuous/CI` | 流程 | 喺 CI pipeline 自動跑 eval，每次改 code 都跑一次，確保冇 regression |

## 快速開始（Quickstart）

**先睇 meltdown 形狀——零設定、一條指令、大概 30 秒：**

```bash
pip install cooleval        # 或 git clone ... && cd CooLEVAL
cooleval demo               # 合成 NON-BENCHMARK fixture → hazard curve
```

`demo` 會印出 session 成功率由 `<15m ~93%` 跌到 `>24h 0%` 嘅曲線——同真實流量見到嘅形狀一樣；呢啲數字係 synthetic shape-checker，唔可以當結果引用。

**帶你自己嘅 telemetry**（Python 3.10+、SQLite3；schema 見 `scripts/eval-etl.py` 頂部）：

1. `cooleval etl` — 將 raw log 抽取、清理，再寫入 SQLite，係所有分析嘅第一步。
2. `cooleval metrics` — 由 DB 計 success rate、meltdown curve、survival/hazard 等 metrics。
3. `cooleval report` — 將計好嘅 metrics 輸出成一份可讀嘅 report。

最新資訊請以英文版 README 為準。
