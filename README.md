# VDR 核聽工具

在瀏覽器內執行的單一 HTML 工具：離線讀取船舶航行資料紀錄器（VDR）的錄音、航跡與 AIS，邊聽邊校對逐字稿。

- 讀取 JRC VDR 原始資料夾（已實作 G.726 與 AAC-LC 格式），多聲道同步播放，以聲音活動波形定位、跳聽。
- 讀取 VDR 航跡紀錄與 GateHouse AIS CSV／ZIP，在地圖上與錄音同步顯示。
- 逐句輸入逐字稿，標記講者與已核狀態，可分割、合併、復原。
- 匯出 WAV 片段與時間對照 JSON 給任何支援 SRT 的轉錄工具，再匯回 SRT。
- 案件進度存成 JSON 主檔，可合併他人進度而不覆蓋現有內容。
- 介面四語系：繁體中文、English、日本語、简体中文。切換語言不會改寫字幕或案件內容。

## 使用

只需要 Chrome 或 Edge，不需安裝程式或 Python。

- **線上版**：開啟 <https://newqqo.github.io/vdr-review/>。頁面載入後，所有處理都在你的瀏覽器內完成。
- **離線版**：到 [Releases](https://github.com/newqqo/vdr-review/releases/latest) 下載 `vdr-review.html`（單一檔案），或 `vdr-review-colleague.zip`（含使用說明，先完整解壓縮），用 Chrome 或 Edge 開啟。

基本流程：

1. 選擇單一 VDR 原始資料夾（含 `Audio`／`Log` 或 `Internal_Storage`）。瀏覽器顯示的「上傳」只是授權讀取，檔案不會離開電腦。
2. 按「開始載入」，播放錄音並在右側輸入。Enter 完成一句，Shift+Enter 換行。
3. 按「儲存案件」存成 JSON 主檔，之後持續更新同一檔案。瀏覽器暫存只供故障復原，不是正式存檔。

## 資料與網路

- 錄音、逐字稿、AIS 與案件 JSON 只在本機瀏覽器處理，不會上傳。
- 地圖底圖預設向國土測繪中心 WMTS 下載圖磚。地圖圖層選「不載入底圖」即不會因地圖連網。
- 開啟工具時不會檢查更新。只有按下「檢查更新」或「下載離線版」才會連到 `newqqo.github.io`，不會送出任何案件資料。
- 原始 VDR 檔唯讀，工具不會修改。

## 更新

離線版按右上角「檢查更新」，有新版時會顯示版號、日期與更新內容連結。按「下載新版」後，工具會先驗證 SHA-256，不符就拒絕；通過後再按「儲存檔案」選擇位置，要取代舊版就選舊檔。工具不會自動覆蓋檔案。如果有尚未儲存的核聽內容，下載前會先提醒。

線上版的同一個按鈕是「下載離線版」，走相同的下載與驗證流程。

## 使用限制

- 濾波、放大只是聽辨輔助，不能證明原本聽不清楚的字句、講者身分或時鐘同步。
- 已實作的格式不代表 JCY-1800／1850／1900／1950 各機型都已實機驗證。驗收清單見 [ACCEPTANCE.md](scripts/vdr_review/ACCEPTANCE.md)。
- 缺段不是靜音的證據。來源雜湊與處理紀錄可追溯衍生檔，但不取代原始載體的保全。
- 本工具不是經認證的鑑定結果。正式引用前，請以原廠播放器與獨立時鐘核對。

## 開發

需要 Python 3.13、Node.js 與 FFmpeg（測試以 FFmpeg 作為 G.726 參考解碼）。缺少 Node.js 或 FFmpeg 時測試直接失敗，不會跳過後顯示通過。

```sh
python -m venv .venv
# 啟用 .venv：Windows 為 .venv\Scripts\activate，macOS／Linux 為 source .venv/bin/activate
python -m pip install -r scripts/vdr_review/requirements-test.txt
python scripts/vdr_review/build.py
python scripts/vdr_review/run_tests.py --root .
python scripts/vdr_review/check_public.py
```

- 原始碼在 `scripts/vdr_review/src/`。`build.py` 把各模組與授權全文內嵌成單一 HTML，不壓縮、不混淆，產出 `output/VDR核聽工具.html` 與 `output/VDR核聽工具_同事版.zip`。
- 版本號的單一來源是 `scripts/vdr_review/VERSION`。`build.py --site <目錄>` 產出 GitHub Pages 目錄：`index.html`、`vdr-review.html`（兩者位元組相同）、同事版 ZIP 與 `version.json`。
- 推送 main 只跑 CI。推送與 `VERSION` 相同的 `vX.Y.Z` 標籤才發版：`vdr-release.yml` 通過檢查與測試後，部署 Pages 並建立 Release。
- 測試只用程式產生的合成資料。瀏覽器實測腳本（`verify_*.py`、`smoke_browser.py`）需另裝 Playwright，不在一般測試內。
- `scripts/vdr_audio_export.py` 是選用的命令列工具，用 FFmpeg 把 JRC AAC 紀錄重新封裝成核聽副本，不重新編碼；使用網頁工具不需要它。

### 公開內容檢查

本 repo 公開，只放工具原始碼與合成測試資料，不放任何真實錄音、逐字稿、案件檔或 AIS 匯出。

`check_public.py` 檢查所有 git 追蹤檔是否在白名單內、是否為 UTF-8 文字檔，並掃描本機路徑、電子郵件、金鑰等通用敏感字樣。CI 在每次 push 與 PR 都會執行。

真實案件用語（船名、人名、MMSI、日期等）不能寫進公開的檢查程式。請放在不進版控的 `local/sensitive_patterns.txt`，每行一個正規表示式（`#` 開頭為註解），推送前在本機執行 `check_public.py`。

## 授權

- 原創程式碼：[MIT License](LICENSE)，Copyright (c) 2026 newqqo。
- `scripts/vdr_review/src/g726.js`：移植自 FFmpeg `libavcodec/g726.c`，依 GNU LGPL 2.1 或更新版本授權，見 [vendor/ffmpeg-g726](scripts/vdr_review/vendor/ffmpeg-g726/NOTICE.md)。
- `scripts/vdr_review/vendor/leaflet/`：Leaflet 1.9.4，BSD 2-Clause License，見 [LICENSE](scripts/vdr_review/vendor/leaflet/LICENSE)。
- 底圖圖磚由國土測繪中心提供，依其[使用條款](https://maps.nlsc.gov.tw/pro/use_clause.jsp)。

建置後的 HTML 內含完整原始碼與上述授權全文，見工具頁尾「關於本工具／授權」。

## English

A single-file, in-browser workbench for reviewing Voyage Data Recorder (VDR) audio alongside the vessel track, AIS and transcripts. It runs entirely in Chrome or Edge; recordings, transcripts and case files are never uploaded. It connects only for basemap tiles from Taiwan's National Land Surveying and Mapping Center, which the "No basemap" layer switches off, and when you press "Check for updates" or "Download offline version". Downloads are SHA-256 verified and saved only where you choose. The interface is available in Traditional Chinese, English, Japanese and Simplified Chinese.

Open <https://newqqo.github.io/vdr-review/> or download the single HTML file from [Releases](https://github.com/newqqo/vdr-review/releases/latest). Build and test with Python 3.13, Node.js and FFmpeg as shown above.

Filtering and amplification are listening aids, and the tool is not a certified forensic result. Original code is MIT-licensed; the G.726 decoder port is LGPL-2.1-or-later and Leaflet is BSD-2-Clause.
