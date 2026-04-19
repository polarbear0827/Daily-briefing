# 部署與設定指引 · Daily Briefing

## 一、Repo 結構總覽

將下列檔案全部放進你的 GitHub repo：

```
your-repo/
├── index.html                  ← 首頁（讀 data/latest.json）
├── archive.html                ← 歷史期數列表
├── assets/
│   ├── styles.css              ← 所有樣式
│   └── app.js                  ← 渲染 + 閱讀器邏輯
├── config/
│   └── sources.yaml            ← 新聞來源設定（可隨時編輯）
├── data/
│   ├── latest.json             ← 最新一期（每天覆蓋）
│   ├── archive.json            ← 所有期數索引
│   └── issues/
│       └── YYYY-MM-DD.json     ← 每日一份
├── docs/
│   ├── cowork-task-prompt.md   ← Cowork 任務 prompt
│   └── setup-guide.md          ← 本文件
└── README.md
```

---

## 二、GitHub Pages 啟用

1. 到 repo 的 **Settings → Pages**
2. Source 選 `Deploy from a branch`
3. Branch 選 `main`，資料夾選 `/ (root)`
4. 儲存後，約 1 分鐘後網址會出現：`https://<user>.github.io/<repo>/`

> 若你已經設定好 workflow（你說有），確認 workflow 檔案在 `.github/workflows/` 下，且產出會寫到 `main` branch 或 deploy 到 `gh-pages` branch — 兩種方式對應的 Pages 設定不同。

---

## 三、Claude Desktop MCP 連接器設定

這是最關鍵的部分 — **你的 GitHub token 存在這裡，而不是 prompt 裡**。

### 3.1 GitHub MCP 連接器

1. 打開 **Claude Desktop → Settings → Connectors**
2. 找到 **GitHub**，點 **Connect**
3. 授權時選擇「僅授權這個 repo」(Only select repositories) — 限定只讓 Cowork 操作這一個 repo，降低風險
4. 授權完成後，Cowork 就能 read/write 這個 repo

**不要**把 token 貼到 prompt 或對話裡。MCP 連接器會在 tool call 時自動帶入憑證，永遠不會進入 LLM context。

### 3.2 Firecrawl MCP 連接器

1. 到 https://firecrawl.dev 註冊，拿到 API key
2. 在 Claude Desktop 的 Connectors 頁找到 Firecrawl（或用 MCP marketplace 安裝）
3. 設定時貼入 API key — 一樣，**只存在 Claude Desktop 本機設定檔，不會進對話**

Firecrawl 免費額度每月 500 次 scrape，你每天最多用幾十次，綽綽有餘。

---

## 四、建立 Cowork Scheduled Task

### 4.1 開啟 Cowork

開 Claude Desktop，切到 **Cowork** 分頁。

### 4.2 建立任務

**方法 A**（推薦）：新對話中輸入 `/schedule`，會跳出排程設定 modal。

**方法 B**：直接到 Cowork 左側 **Scheduled** 分頁，點 **+ New task**。

### 4.3 填入 prompt

把 `docs/cowork-task-prompt.md` 裡 `---` 分隔線之後的**整段內容**複製貼上。

### 4.4 排程設定

- **Frequency**: Daily
- **Time**: 07:00 (Asia/Taipei)
- **Name**: Daily Briefing
- **Skills**: 不用加（prompt 自含所有指令）

### 4.5 儲存並手動試跑一次

儲存後，點 **Run now** 試跑一次。第一次跑可能會有問題（例如某個 RSS 壞掉、Firecrawl quota、YAML 格式錯誤），這樣可以先 debug 而不用等到隔天。

---

## 五、常見問題排查

### Q1. Cowork 說讀不到 repo
→ 確認 GitHub MCP 授權時有把這個 repo 加到允許清單。

### Q2. RSS feed 都讀不到
→ Cowork 可能沒有原生 RSS 解析工具。實際上它會用 `fetch` + 手動解 XML。如果某個 feed 一直失敗，考慮：
- 換一個 feed reader API（例如 Feedbin、Inoreader）
- 改用 Firecrawl 直接抓那個網站

### Q3. 中文顯示不正常
→ 檢查 `assets/styles.css` 中 `--font-display-zh` 的 fallback 字體。Google Fonts 的 `Noto Serif TC` 需要網路連線；離線時會 fallback 到系統字體。

### Q4. 歷史期數一直累積會不會太慢？
→ `archive.json` 只存索引（標題 + 日期），不存全文。1000 期也才幾百 KB。前端是靜態檔案，不會影響效能。

### Q5. 萬一某天沒內容怎麼辦？
→ Prompt 裡的 Failure mode #5 有處理：會生一個空 issue 並在 log 記錄。這比中斷排程好（保持每日連續性）。

---

## 六、自訂與延伸

### 6.1 調整新聞來源
編輯 `config/sources.yaml`，commit，下次任務執行就會吃新設定。

### 6.2 調整分類
編輯你的第一份 `data/issues/*.json` 的 `categories[]`。Prompt 裡要求「複用既有分類」，所以改一次就會延續。

### 6.3 改變視覺風格
編輯 `assets/styles.css` 的 `:root` CSS variables 即可調色、換字體。

### 6.4 訂閱每日推送
想要每天早上自動收到 email？再加一個 Cowork 任務：每天 07:15 讀 `data/latest.json`，透過 Gmail 連接器寄給自己。

---

## 七、本地快速預覽

如果要先在本機看模板長什麼樣（不等 Cowork 產內容）：

```bash
cd /path/to/your-repo
python3 -m http.server 8000
```

打開 `http://localhost:8000`，你會看到範例資料渲染的結果。

---

## 八、安全檢查清單

- [ ] GitHub token 只給單一 repo 存取權
- [ ] Firecrawl API key 存在 Claude Desktop 設定，非 prompt
- [ ] repo 是 private 或 public？如果包含任何敏感資訊（你的新聞偏好 ≠ 敏感），public 也可以
- [ ] GitHub Pages 設定是否暴露了不該暴露的目錄（例如 `config/` 含 API key 就不行 — 目前不含，OK）
