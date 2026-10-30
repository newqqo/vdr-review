# AGENTS.md

VDR 核聽工具：瀏覽器內單一 HTML，離線讀取 VDR 錄音、AIS 航跡與字幕，供核聽與逐字稿校對。

## 這是公開 repo

- 只放工具原始碼與合成測試資料。**不得**加入任何真實錄音、VDR 檔、逐字稿、案件 JSON、AIS 匯出、公文、人名、船名、MMSI、案件日期或本機路徑。
- 測試一律用程式產生的合成資料；真實案件只在本機用建置後的 HTML 開啟，不複製進本資料夾。
- `.gitignore` 已擋常見媒體與案件檔；新增檔案前仍要逐檔確認。新種類的檔案要在 `scripts/vdr_review/check_public.py` 的 `ALLOWED` 加白名單。
- 真實船名、人名、MMSI、案件日期、內部系統名只能寫在未追蹤的 `local/sensitive_patterns.txt`（每行一個正規表示式），不得寫進任何追蹤檔，包括檢查程式與測試。
- commit 身分用 GitHub noreply 信箱（本 repo 的 git config 已設定）。
- 推送、打標籤、改 Pages 設定前先問使用者。

## 開發

```sh
py -3.13 -m venv .venv
.venv/Scripts/python.exe -m pip install -r scripts/vdr_review/requirements-test.txt
.venv/Scripts/python.exe scripts/vdr_review/build.py          # 產出 output/VDR核聽工具.html 與同事版 ZIP
.venv/Scripts/python.exe scripts/vdr_review/run_tests.py --root .
.venv/Scripts/python.exe scripts/vdr_review/check_public.py     # 白名單與敏感字掃描；推送前必跑
```

需要 Node.js 與 FFmpeg；缺少時測試必須失敗，不得靜默跳過。

版本號的單一來源是 `scripts/vdr_review/VERSION`（`X.Y.Z`）。`build.py --site <目錄>` 產出 Pages 目錄（HTML、ZIP、`version.json`），目錄內有其他檔案就拒絕，避免一起被發布。

## 原則

- 工具開啟時不得發出網路請求（底圖圖磚除外，且可選無底圖）；任何更新檢查只能由使用者按按鈕觸發。
- 原始 VDR 檔唯讀；濾波放大只是聽辨輔助，不是證據結論。
- 介面字串四語系（zh-TW、en、ja、zh-CN）同步，`verify_i18n.py` 須通過。
- 原創程式碼 MIT；`vendor/ffmpeg-g726`（LGPL-2.1-or-later）與 `vendor/leaflet`（BSD-2-Clause）聲明完整保留。
- 推 main 只跑 CI；只有 `vX.Y.Z` 標籤才發版（Pages 與 Release）。標籤須與 `VERSION` 一致，否則發版失敗。
- `github-pages` 環境預設只允許預設分支部署，標籤發版前要在環境設定加標籤規則；這是 repo 設定，改之前先問使用者。
