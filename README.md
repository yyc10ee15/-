# 一鍵開吃

這是另外建立的第三版，所有程式與資料都在 `restaurant_selector_v3`，不會覆蓋第一版與第二版。V3 已改用 Google／訪客身分，舊版以名稱識別的使用者資料會在升級時清除一次。

## 第三版主要功能

- 預設單人模式，需要時才切換多人模式。
- 可選擇既有使用者繼續使用原本的喜好與評分紀錄，或建立新的本機使用者。
- 訪客名稱重複時會依序顯示為「名稱(2)」、「名稱(3)」。
- 條件改為選項：距離、每人預算、Google 評分、菜系、營業時間。
- 每人預算可直接按「NT$0–200、200–400、400–600、600 以上」篩選，不用拖曳滑桿。
- 每次推薦最多顯示五間，可換一批或隨機選一間。
- 隨機或手動決定後，再完整顯示一次選中餐廳資訊。
- 推薦卡可按喜歡／不喜歡。
- 「我的偏好」分開顯示喜歡、不喜歡與用餐星級。
- 不喜歡的餐廳會從後續推薦移除，相似類型也會降低排序。
- 決定餐廳後仍可留下 1～5 星，與喜歡／不喜歡分開。
- 多人模式仍是一人一票，最高票平手時繼續投票。
- 多人模式會顯示候選餐廳資訊，並產生可直接加入房間的分享連結。
- 可用 Google Maps 搜尋地址、點選地圖或拖曳標記來決定搜尋中心。
- 推薦結果使用 Google Maps 餐廳標記，點擊可查看店家資訊。
- 多人房間會為所有加入者顯示完整候選資訊，並提供手機分享、複製與長按連結。
- 使用 Open WebUI Chat Completions API 分析目前使用者過往的喜歡、不喜歡與 1～5 星紀錄。
- 顯示一段 AI 偏好摘要，並為每間單人／多人候選餐廳提供 1～2 句挑選建議。
- AI 只收到餐廳名稱、類型、評分等必要欄位，不傳使用者姓名、定位或完整地址。
- 相同歷史與候選名單會快取一小時，減少重複 API 費用；AI 暫時失敗時一般推薦仍可使用。

## 執行 V3

在專案根目錄執行：

```powershell
.\.venv\Scripts\python.exe -m streamlit run restaurant_selector_v3\streamlit_app.py
```

或進入新版資料夾：

```powershell
cd restaurant_selector_v3
..\.venv\Scripts\python.exe -m streamlit run streamlit_app.py
```

## Google Places API

沒有 API Key 時會使用虛構示範餐廳，所有單人、多人与偏好流程仍可測試。

若要搜尋真實餐廳：

1. 啟用 Google Places API（New）。
2. 將 `.streamlit/secrets.toml.example` 複製成 `.streamlit/secrets.toml`。
3. 填入 `GOOGLE_MAPS_API_KEY`。

若要啟用互動地圖與手機邀請，還要設定：

```toml
GOOGLE_MAPS_BROWSER_API_KEY = "前端 Maps JavaScript API Key"
GOOGLE_MAP_ID = "JavaScript Map ID"
APP_BASE_URL = "https://其他裝置能開啟的網站網址"
```

同一個 Wi-Fi 臨時測試可將 `APP_BASE_URL` 設成電腦區網網址，例如 `http://192.168.0.13:8501`；正式環境建議使用 HTTPS。

## Google 帳戶登入

Google 登入使用 Streamlit 內建 OIDC。先在 Google Cloud 建立「網頁應用程式」OAuth 用戶端，並將下列網址加入「已授權的重新導向 URI」：

```text
https://你的網站名稱.streamlit.app/oauth2callback
```

接著在 Streamlit Community Cloud 的 App Secrets 加入：

```toml
[auth]
redirect_uri = "https://你的網站名稱.streamlit.app/oauth2callback"
cookie_secret = "一段夠長且隨機的秘密字串"

[auth.google]
client_id = "你的用戶端ID.apps.googleusercontent.com"
client_secret = "你的用戶端密碼"
server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"
```

本機測試時，將 `redirect_uri` 改為 `http://localhost:8501/oauth2callback`，並把同一網址加入 Google Cloud。OAuth 用戶端密碼與 `cookie_secret` 都不能上傳 GitHub。

Google 帳戶以 Google 提供的唯一 `sub` 保存跨裝置偏好；訪客使用隨機 ID。兩者的顯示名稱都不是資料庫識別鍵，因此不會因同名而混用資料。

## Open WebUI Web AI

1. 在 Open WebUI 的 `Settings → Account → API Keys` 建立 API Key。
2. 確認 Open WebUI 網址可從執行 V3 的主機連線，並取得實際模型 ID。
3. 在 V3 的 `.streamlit/secrets.toml` 加入：

```toml
OPEN_WEBUI_BASE_URL = "https://你的-open-webui-網址"
OPEN_WEBUI_API_KEY = "你的 Open WebUI API Key"
OPEN_WEBUI_MODEL = "gpt-oss:20b"
```

`OPEN_WEBUI_MODEL` 必須與 Open WebUI `/api/models` 回傳的模型 ID 完全相同。程式會呼叫 `{OPEN_WEBUI_BASE_URL}/api/chat/completions`；Base URL 可填 `https://host` 或 `https://host/api`，但不要包含聊天室路徑、查詢 token 或 `#/chat/...`。

也可改用同名的 Windows 環境變數。請勿把真實 Key 上傳到 Git；本專案的 `.gitignore` 已忽略 `secrets.toml`。

若 V3 與 Open WebUI 都在同一台電腦執行，可使用 `OPEN_WEBUI_BASE_URL = "http://localhost:3000"`。若 V3 部署在 Streamlit Cloud，Open WebUI 則必須提供雲端能連線的公開 HTTPS API 網址。

## 程式結構

```text
restaurant_selector_v3/
├── streamlit_app.py       # Streamlit UI 與操作流程
├── app_core/
│   ├── models.py          # Restaurant、SearchFilters、Room
│   ├── services.py        # Google 搜尋、條件過濾、個人化排序
│   ├── ai_advisor.py      # Open WebUI API 與結構化偏好分析
│   ├── identity.py        # Google／訪客應用程式身分
│   ├── database.py        # 偏好、星級、房間與投票
│   ├── google_map.py      # Google Maps 搜尋、點選與餐廳標記
│   └── share_link.py      # 手機系統分享與複製邀請連結
├── tests/                 # 自動測試
├── data/                  # V3 SQLite，與 V1／V2 分開
└── .streamlit/            # 新版主題與 secrets 範例
```

### `SearchFilters`

保存 UI 選項轉換後的條件：

- `radius_meters`：距離半徑。
- `minimum_rating`：最低 Google 評分。
- `price_levels`：每人預算按鈕所對應的 Google Places 概略價位等級。
- `cuisines`：可複選菜系。
- `opening_mode`：不限、現在營業或指定時間。
- `selected_datetime`：指定日期時間。

### `GooglePlacesClient`

- `search()`：接收 `SearchFilters`，不接收使用者自由文字。
- `_request_google()`：把選項轉換成 Places Text Search request。
- `_is_open_at()`：依每週營業時段判斷指定時間。
- `_distance_km()`：計算使用者與餐廳直線距離。
- `_demo_restaurants()`：沒有 API Key 時提供展示資料。

### `RankingService`

`rank()` 的工作順序：

1. 移除使用者明確按過不喜歡的餐廳。
2. 優先顯示曾按喜歡的餐廳。
3. 參考喜歡／不喜歡的菜系傾向。
4. 參考本系統 1～5 星與 Google 評分。
5. 距離較近者略微優先。

這些只影響推薦排序，不影響多人投票票數。

### `AIAdvisor`

- `analyze()`：把匿名化歷史與候選資料送到 Open WebUI Chat Completions API。
- `PreferenceAnalysis`：用 Pydantic 固定回傳「偏好摘要＋每間候選建議」。
- 模型只負責解讀與說明，不會改變多人投票票數。

### `Database`

- `upsert_user()`：依 Google 或訪客的穩定身分鍵建立／更新個人資料。
- `register_guest()`：建立訪客，並自動替重複名稱加上 `(2)`、`(3)`。
- `save_preference()`：儲存喜歡或不喜歡。
- `remove_preference()`：移除偏好紀錄。
- `preference_map()`：取得個人對餐廳的偏好。
- `cuisine_affinity()`：計算個人菜系傾向。
- `list_preferences()`：建立喜歡／不喜歡清單。
- `save_rating()`：儲存 1～5 星。
- `ai_preference_history()`：整理不含姓名、定位與地址的 AI 分析資料。
- `create_room()`、`join_room()`：多人房間。
- `cast_vote()`：一人一票，再次送出為改票。
- `finalize_vote()`：只用最高票決選。

## 測試

```powershell
cd restaurant_selector_v3
..\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
