<!-- zh-TW reading guide generated 2026-08-20 (Claude design pass) -->

# CooLEVAL — 繁體中文導讀

> 繁體中文說明：內容以英文版 README 為準。
>
> 呢份文件係**導讀 + 詞彙對照**，唔係完整翻譯。所有技術名詞、schema 名、程式碼一律
> 保留英文原文。要睇完整規格、參數、輸出格式，請睇 [英文版 README](../README.md)。

---

## 一句講清楚

CooLEVAL 係一個公開嘅 dogfood agent-evaluation framework，用**真實 production
telemetry**（一個 SQLite eval DB）去量度 AI agent 嘅可靠度。核心係**誠實嘅統計**：
唔靠感覺、唔靠單次成功，凡係樣本唔夠都直接 gate 起唔俾出結論。

---

## 佢量度啲乜

CooLEVAL 唔淨止睇「個 model 答啱幾多題」，而係睇三層：

1. **Model 層** — agent 喺真實任務嘅成功率，配 Wilson CI 同 n-gate。
2. **Memory 層**（memory-eval）— 個 memory backend 決定個 model 到底見唔見到啱嘅
   fact。呢層同 model 層一樣要跑 head-to-head。
3. **成本層**（token-efficiency）— 每次成功要燒幾多 token、幾多美金。

---

## 核心洞察：Meltdown（崩潰曲線）

最重要嘅發現：**agent 喺短 session 表現好，但 session 一超過大約一個鐘就會崩。**

- 短 session（幾分鐘到十幾分鐘）：成功率高、穩定。
- 長 session（**>1 小時**）：context 累積、狀態漂移，成功率急跌。

所以英文版有 **Meltdown Curve** 同 **Survival & Hazard** 兩節，用生存分析嘅角度睇
一個 session 撐得幾耐先開始死。呢個唔係 model 蠢，係長時間運行本身係一種失效模式。

---

## 統計誠實原則(最緊要嗰part)

CooLEVAL 嘅立場：**寧可唔出結論，都唔好出錯結論。**

- **Wilson CI** — 所有成功率都俾信賴區間，唔淨止俾一個裸數字。細樣本嘅區間會好闊,
  你一眼就睇到「呢個數靠唔住」。
- **n-gate** — 樣本數唔夠 threshold，直接標記 gated，唔會攞去排名或者宣稱邊個贏。
- **飽和（saturated）唔算贏** — 例如兩個 backend 喺 8 個 fact 都係 8/8，呢個叫飽和,
  唔可以話邊個叫勝。要分高下就要跑到樣本夠大、有得區分嘅 benchmark。
- **exploratory ≠ confirmatory** — 單次跑、synthetic corpus 出嚟嘅嘢係探索性質，
  俾你生 hypothesis，唔係俾你落定論。

呢啲原則喺英文版 **Limitations** 同 **Design Principles** 有詳細版。

---

## Memory-Eval（memory 層評估）

- 用共用嘅 fact corpus 跑 backend head-to-head。
- 四個 retrieval class：**single**（單一 fact 直接 recall）、**multi**（fact 組合）、
  **noisy**（啱嘅 fact 埋喺一堆 distractor 入面）、**under**（query 講唔清、要推理）。
- **under class 會 gate 去 LLM judge**（`--llm`）；預設 dry run 保持 deterministic
  評分，唔會令 head-to-head 俾 judge 嘅波動污染。
- 結論：唔同 backend 有相反嘅失效模式（有啲叻乾淨 recall、有啲叻抗噪），冇一個係
  萬能贏家 —— **啲失效模式本身先係重點。**
- OpenViking backend **未部署**：1.2GB 安裝加重 dependency，喺 4GB 機根本唔合理,
  所以佢係 gated 咁 ship，會出 server-down trace，寧願大聲失敗都唔好靜雞雞跳過。

（詳細數字睇英文版 **Memory-Eval** 一節。）

---

## Token-Efficiency（成本效率）

- `eval-runner` 用 `--usage-file` 收集 token telemetry。
- `eval-tokeneff.py` 計 **cache-adjusted tokens-per-success**（cache 命中打折後嘅
  billable token ÷ 成功任務數）同埋 **USD-per-success**。
- **重要：呢個係成本指標，唔係質素指標。** 燒得少 token 只代表平，唔代表答得好。
  永遠要同 Wilson-CI 成功率一齊睇。一個過唔到 n-gate 嘅平 model，唔叫高效,叫平。

---

## 詞彙對照（保留英文，唔翻譯）

| 英文名 | 屬性 | 中文簡述 |
|--------|------|----------|
| `span metrics` | schema / 指標 | 一段執行區間嘅量度數據 |
| `battery_runs` | table | 一批評估跑次嘅記錄 |
| `task_events` | table | 逐個任務事件嘅明細 |
| `spec_hash` | 欄位 | 評估規格嘅 hash，用嚟確認同一份 spec |
| `Wilson CI` | 統計方法 | Wilson 信賴區間 |
| `n-gate` | 機制 | 樣本不足就唔出結論 |
| `MCP` | 協定 | Model Context Protocol |
| `FastMCP` | 框架 | 用嚟起 MCP server 嘅 Python framework，agent 就係透過佢暴露出嚟嘅 tools 做嘢。 |
| `JSON-RPC` | 協定 | MCP 底層嘅 request/response 傳輸協定，call tool 同收 result 都行呢個。 |
| `SQLite` | 資料庫 | 存 ETL 之後 run/step/event 資料嘅本地 DB，`eval-metrics.py` 直接 query 佢。 |
| `eval-runner.py` | script (dogfood battery) | 走 dogfood battery，實際觸發 agent 執行一連串 task 並記低 raw log。 |
| `eval-tokeneff.py` | script (成本效率) | 計 token 使用同成本效率，睇每個 task 用幾多 token 先做得成。 |
| `meltdown curve` | 現象 | 長 session 入面 success rate 急跌嘅曲線，反映 agent 撐唔到長 context。 |
| `survival & hazard` | 分析方法 | 借用 survival analysis 同 hazard rate 睇 agent 幾時開始「死」同失敗風險隨時間點變。 |
| `extreme tests` | 功能 (ceiling battery) | ceiling battery，專登用高難度 task 逼出 agent 能力上限。 |
| `continuous/CI` | 流程 | 喺 CI pipeline 自動走 eval，每次 code 改動都跑一次確保無 regression。 |

## 點開始（Quickstart）

跑 eval 嘅三條核心指令，順住嚟行就得：

1. `python3 scripts/eval-etl.py` — 將 raw log 抽出嚟、清好、寫入 SQLite，係所有分析嘅第一步。
2. `python3 scripts/eval-metrics.py` — 由 DB 計出 success rate、meltdown curve、survival/hazard 等 metrics。
3. `python3 scripts/eval-report.py` — 攞計好嘅 metrics 出成一份可讀嘅 report。

睇返英文版 README 攞最新嘢。
