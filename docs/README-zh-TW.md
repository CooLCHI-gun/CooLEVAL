<!-- zh-TW reading guide — 書面語版，2026-08-20 -->

# CooLEVAL — 繁體中文導讀

> 繁體中文說明：內容以英文版 README 為準。
>
> 本文是**導讀與詞彙對照**，不是完整翻譯。所有技術名詞、schema 名稱與程式碼一律
> 保留英文原文。如需完整規格、參數與輸出格式，請參閱 [英文版 README](../README.md)。

---

## 概述

CooLEVAL 是一套公開的 dogfood agent-evaluation framework，以**真實 production
telemetry**（一個 SQLite eval DB）量度 AI agent 的可靠度。核心原則是**誠實的統計**：
不依賴直覺、不依賴單次成功，凡樣本數不足便以 gate 阻擋，不允許逕自下結論。

---

## 量度三個層面

CooLEVAL 不只檢視「模型答對多少題」，而是檢視三個層面：

1. **Model 層** — 代理在真實任務中的成功率，搭配 Wilson CI 與 n-gate。
2. **Memory 層**（memory-eval）— 記憶後端決定模型是否能見到正確的 fact。此層與
   Model 層相同，需要執行 head-to-head 比較。
3. **成本層**（token-efficiency）— 每次成功所需消耗的 token 數與美元成本。

---

## 核心洞察：Meltdown（崩潰曲線）

最重要的發現：**agent 在短 session 表現良好，但 session 一旦超過約一小時便會崩潰。**
這不是模型能力不足，而是長時間運行本身就是一種失效模式。

- 短 session（數分鐘至十餘分鐘）：成功率高、穩定。
- 長 session（**超過 1 小時**）：context 累積、狀態漂移，成功率急遽下降。

因此英文版設有 **Meltdown Curve** 與 **Survival & Hazard** 兩節，以生存分析的角度
觀察一個 session 能維持多久才開始失敗。

---

## 統計誠實原則（最重要）

CooLEVAL 的立場：**寧可不出結論，也不可出錯結論。**

- **Wilson CI** — 所有成功率皆附信賴區間，而非只提供單一數值。小樣本的區間會非常寬，
  一眼即可看出該數值是否可靠。
- **n-gate** — 樣本數未達門檻時直接標記為 gated，不會用於排名或宣稱勝者。
- **飽和（saturated）不算勝利** — 例如兩個後端在 8 個 fact 上皆為 8/8，這稱為飽和，
  不能認定何者為勝。要分出高下，必須執行樣本夠大、具有區辨力的 benchmark。
- **exploratory ≠ confirmatory** — 單次執行、synthetic corpus 所得的結果屬於探索性質，
  用於產生 hypothesis，而非用於定論。

上述原則在英文版 **Limitations** 與 **Design Principles** 有更詳細的說明。

---

## Memory-Eval（記憶層評估）

- 使用共用的 fact corpus 執行後端 head-to-head 比較。
- 四個 retrieval class：**single**（單一 fact 直接 recall）、**multi**（fact 組合）、
  **noisy**（正確 fact 埋藏於大量 distractor 之中）、**under**（query 語意不清、需要推理）。
- **under class 會 gate 至 LLM judge**（`--llm`）；預設 dry run 保持 deterministic
  評分，不會令 head-to-head 被 judge 的波動污染。
- 結論：不同後端有相反的失效模式（有些擅長乾淨 recall、有些擅長抗噪），沒有一個是
  全能贏家——**失效模式本身才是重點。**

（詳細數據請參閱英文版 **Memory-Eval** 一節。）

---

## Token-Efficiency（成本效率）

- `eval-runner` 以 `--usage-file` 收集 token telemetry。
- `eval-tokeneff.py` 計算 **cache-adjusted tokens-per-success**（cache 命中折扣後的
  billable token ÷ 成功任務數）以及 **USD-per-success**。
- **重要：此為成本指標，而非品質指標。** 消耗較少 token 僅代表成本較低，不代表回答
  品質較佳。應永遠與 Wilson-CI 成功率一併檢視。一個無法通過 n-gate 的低成本模型，
  不能稱之為高效，只能稱之為便宜。

---

## 詞彙對照（保留英文，不翻譯）

| 英文名 | 屬性 | 中文簡述 |
|--------|------|----------|
| `span metrics` | schema / 指標 | 一段執行區間的量度數據 |
| `battery_runs` | table | 一批評估跑次的記錄 |
| `task_events` | table | 逐個任務事件的明細 |
| `spec_hash` | 欄位 | 評估規格的 hash，用以確認同一份 spec |
| `Wilson CI` | 統計方法 | Wilson 信賴區間 |
| `n-gate` | 機制 | 樣本不足即不出結論 |
| `MCP` | 協定 | Model Context Protocol |
| `FastMCP` | 框架 | 用於架設 MCP server 的 Python framework，agent 透過其暴露的工具進行操作 |
| `JSON-RPC` | 協定 | MCP 底層的 request/response 傳輸協定，呼叫工具與接收結果皆經由此機制 |
| `SQLite` | 資料庫 | 儲存 ETL 之後 run/step/event 資料的本地 DB，`eval-metrics.py` 直接查詢 |
| `eval-runner.py` | script (dogfood battery) | 執行 dogfood battery，實際觸發 agent 執行一系列 task 並記錄 raw log |
| `eval-tokeneff.py` | script (成本效率) | 計算 token 使用與成本效率，觀察每個 task 消耗多少 token 方能完成 |
| `meltdown curve` | 現象 | 長 session 中 success rate 急遽下降的曲線，反映 agent 難以支撐長 context |
| `survival & hazard` | 分析方法 | 借用 survival analysis 與 hazard rate 觀察 agent 何時開始「死亡」及失敗風險隨時間的變化 |
| `extreme tests` | 功能 (ceiling battery) | ceiling battery，刻意使用高難度 task 逼出 agent 的能力上限 |
| `continuous/CI` | 流程 | 於 CI pipeline 自動執行 eval，每次 code 改動皆執行一次以確保無 regression |

## 快速開始（Quickstart）

執行評估的三條核心指令，依序執行即可：

1. `python3 scripts/eval-etl.py` — 將 raw log 抽取、清理，並寫入 SQLite，是所有分析的第一步。
2. `python3 scripts/eval-metrics.py` — 由 DB 計算 success rate、meltdown curve、survival/hazard 等 metrics。
3. `python3 scripts/eval-report.py` — 將計算完成的 metrics 輸出為一份可讀的 report。

最新資訊請以英文版 README 為準。
