"""Build the local VDR review HTML and the no-install colleague ZIP, or a Pages site.

The output HTML runs entirely in the browser from a local file: it reads the
selected VDR folder and optional GateHouse AIS CSV/ZIP, decodes audio locally
and uploads nothing (map tiles excepted). Source lives in scripts/vdr_review/src.
"""
from __future__ import annotations

import argparse
import html as htmllib
import json
import hashlib
import os
import re
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "scripts" / "vdr_review" / "src"
VENDOR = ROOT / "scripts" / "vdr_review" / "vendor" / "leaflet"
LGPL = ROOT / "scripts" / "vdr_review" / "vendor" / "ffmpeg-g726" / "LGPL-2.1.txt"
MIT = ROOT / "LICENSE"
VERSION_FILE = ROOT / "scripts" / "vdr_review" / "VERSION"
SEMVER = re.compile(r"(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*)){2}")
DEFAULT_OUT = ROOT / "output" / "VDR核聽工具.html"
PACKAGE_NAME = "VDR核聽工具_同事版.zip"
SITE_HTML = "vdr-review.html"
SITE_FILES = {"index.html", SITE_HTML, PACKAGE_NAME, "version.json"}
RELEASE_URL = "https://github.com/{repository}/releases/tag/v{version}"
GITHUB_REMOTE = re.compile(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([A-Za-z0-9-]+)/([A-Za-z0-9._-]+?)(?:\.git)?/?")
REPOSITORY = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+")
MAIN_MODULES = ("i18n.js", "i18n_core.js", "i18n_ui.js", "i18n_editor.js", "i18n_app.js", "vdr.js", "gatehouse.js", "dsp.js", "review_state.js", "project.js", "media.js", "stream.js", "editor.js", "update.js", "app.js")
WORKER_MODULES = ("i18n.js", "i18n_core.js", "g726.js", "dsp.js", "worker.js")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def script_safe(text: str) -> str:
    if "</script" in text.lower():
        raise ValueError("embedded script contains a closing script tag")
    return text


def read_version() -> str:
    version = read(VERSION_FILE).strip()
    if not SEMVER.fullmatch(version):
        raise ValueError(f"VERSION must be X.Y.Z, not {version!r}")
    return version


def build_info() -> dict:
    # built_at is the commit time (or SOURCE_DATE_EPOCH), not the clock: building the same
    # commit again must give byte-identical files, so a release can be checked and rebuilt.
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    try:
        if not (ROOT / ".git").exists():
            raise OSError("source snapshot has no repository identity")
        rev = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short=7", "HEAD"],
                             capture_output=True, text=True, check=True).stdout.strip()
        if epoch is None:
            epoch = subprocess.run(["git", "-C", str(ROOT), "log", "-1", "--format=%ct", "HEAD"],
                                   capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        rev = "unknown"
    try:
        when = datetime.fromtimestamp(int(epoch), timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        when = datetime.now(timezone.utc)
    source_digest = hashlib.sha256("\n".join(read(SRC / n) for n in (*MAIN_MODULES, *WORKER_MODULES, "app.html")).encode("utf-8")).hexdigest()[:12]
    return {"version": read_version(), "commit": rev, "source_digest": source_digest,
            "built_at": when.strftime("%Y-%m-%dT%H:%M:%SZ")}


def repository() -> str | None:
    """owner/name of the GitHub repository this build publishes from, or None if unknown.

    A fork that builds and publishes its own Pages site gets its own update source, so its
    users are never offered the upstream page in place of the fork's changes.
    """
    value = os.environ.get("VDR_REPOSITORY") or os.environ.get("GITHUB_REPOSITORY")
    if not value:
        try:
            url = subprocess.run(["git", "-C", str(ROOT), "remote", "get-url", "origin"],
                                 capture_output=True, text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return None
        match = GITHUB_REMOTE.fullmatch(url)
        value = f"{match[1]}/{match[2]}" if match else None
    if value and not REPOSITORY.fullmatch(value):
        raise ValueError(f"repository must be owner/name, not {value!r}")
    return value


def pages_site(repo: str) -> str:
    owner, name = repo.split("/")
    host = owner.lower() + ".github.io"
    return f"https://{host}/" if name.lower() == host else f"https://{host}/{name}/"


def build(info: dict | None = None, repo: str | None = None) -> str:
    worker = "\n".join(read(SRC / name) for name in WORKER_MODULES)
    worker_literal = json.dumps(worker, ensure_ascii=False).replace("</", "<\\/")
    # An empty update source disables the update button rather than pointing at someone else's site.
    manifest_url = pages_site(repo) + "version.json" if repo else ""
    app_js = ("const BUILD_INFO=" + json.dumps(info or build_info()) + ";\nconst UPDATE_MANIFEST=" + json.dumps(manifest_url)
              + ";\nconst WORKER_SRC=" + worker_literal + ";\n"
              + "\n".join(script_safe(read(SRC / name)) for name in MAIN_MODULES))
    leaflet_js = ("/* Leaflet 1.9.4, BSD-2-Clause; full licence text is in the page's about section */\n"
                  + script_safe(read(VENDOR / "leaflet.js")))
    html = read(SRC / "app.html")
    for marker, value in (("/*LEAFLET_CSS*/", read(VENDOR / "leaflet.css")),
                          ("/*LEAFLET_JS*/", leaflet_js), ("/*APP_JS*/", app_js),
                          ("/*LICENSE_MIT*/", htmllib.escape(read(MIT))),
                          ("/*LICENSE_LGPL*/", htmllib.escape(read(LGPL))),
                          ("/*LICENSE_LEAFLET*/", htmllib.escape(read(VENDOR / "LICENSE")))):
        if html.count(marker) != 1:
            raise ValueError(f"template marker {marker} must appear once")
        html = html.replace(marker, value)
    return html


def write_package(package: Path, payload: bytes, built_at: str) -> None:
    package.parent.mkdir(parents=True, exist_ok=True)
    instructions = """VDR 核聽工具｜同事試用版

1. 先將 ZIP 完整解壓縮，不要在壓縮檔內直接執行。
2. 用 Chrome 或 Edge 開啟「開啟VDR核聽工具.html」。不需安裝、不需 Python。
   右上角可切換繁體中文／English／日本語／简体中文，會記住選擇；不會改寫字幕或案件內容。
3. 選單一 VDR 原始資料夾。瀏覽器的「上傳」只是讀取授權，檔案不會傳到網路。
4. 按「開始載入」；地圖與底圖自動顯示。播放錄音，在右側打字，按 Enter 完成一句。
   Shift+Enter 換行；中文字選字時的 Enter 不送出。點波形可切軌與跳聽。
5. 按「儲存案件」選一個 JSON 主檔；後續更新同一檔案。
   若瀏覽器僅能下載副本，畫面會提醒「尚未正式存檔」；請將下載檔覆蓋既有主檔並自行確認。
   瀏覽器暫存只是復原用，關閉前務必確認主檔已存妥。
6. 音檔／SRT 工具可匯出片段交給任意支援 SRT 的轉錄工具，再匯回 SRT 與同包的時間對照 JSON。
   交付按「匯出調查成果」；原始 VDR 不會打進成果包。
7. 換電腦或瀏覽器續做：載入同一份 VDR 後按「開啟進度」選主檔；畫面還沒有句子時，
   按「開啟案件」完整載入，包括事件筆記與校時。
   已有內容時主要按鈕是「合併，保留目前內容」，只加入句子，前段不會被取代。
   舊版 JSON 需先核對 UTC 日期與同一份錄音；重疊內容兩份保留。

底圖預設向國土測繪中心下載案件所在區域的圖磚；錄音和逐字稿不會上傳。
敏感案件請在「開始載入」前把「地圖底圖」選成「不載入底圖」，工具會記住選擇。
選錯資料夾或時段時，按畫面上方「換資料夾／時段」重選；未存入主檔的內容仍在瀏覽器暫存。
共用電腦用完後，按頁尾「關於本工具」裡的「清除此瀏覽器的所有案件暫存」。
右上角「檢查更新」可確認有沒有新版；只有按下時才會連線。
本工具不修改原始檔。G.726／AAC 依已實作格式讀取，不代表四款 JRC 機型均已實機驗證。
遇到不支援格式請保留錯誤資訊交承辦人，不要自行改名或重編碼原始證據。
缺段不是靜音證據；加強音僅供核聽。正式引用前請獨立校時並核對原廠播放。
聽不清楚寫〔不明〕，不要補字。授權全文與版本在工具頁尾。

English
Extract the ZIP, then open the HTML file in Chrome or Edge. No installation or Python is needed.
Choose English at the top right, select one original VDR folder, then load it.
Listen and type; Enter completes a cue, Shift+Enter starts a new line. Save your case to a JSON file before closing.
Language switching does not translate or replace transcripts. Audio/transcripts are not uploaded; map tiles use the internet.
"Check for updates" at the top right connects only when you press it.
Keep the original VDR and your case JSON separately. This tool is not a certified forensic result; verify time alignment and original playback.

日本語
ZIP を展開し、HTML ファイルを Chrome または Edge で開いてください。インストールや Python は不要です。
右上で日本語を選び、単一の VDR 元データフォルダーを選択して読み込みます。
聞きながら入力し、Enter で字幕を確定、Shift+Enter で改行します。閉じる前に案件を JSON ファイルへ保存してください。
言語切替で字幕や案件内容は変更されません。音声・字幕はアップロードされず、地図の背景のみインターネットを使用します。
右上の「更新を確認」は、押したときだけ接続します。
VDR 原本と案件 JSON は別途保管してください。認証済みの鑑定結果ではありません。時刻と純正プレーヤーでの再生を照合してください。

简体中文
完整解压 ZIP，用 Chrome 或 Edge 打开 HTML 文件，无需安装或 Python。
右上角选择简体中文，选择单份 VDR 原始文件夹后加载。
边听边输入，Enter 完成一句，Shift+Enter 换行；关闭前将案件保存为 JSON 文件。
切换语言不会改写字幕或案件。录音和字幕不会上传，地图底图需要联网。
右上角“检查更新”只有按下时才会连线。
原始 VDR 与案件 JSON 请另外保管。本工具不是经认证的鉴定结果；请独立校时并核对原厂播放。
"""
    # Fixed entry times and modes keep the ZIP identical for the same build.
    stamp = tuple(max(1980, v) if i == 0 else v for i, v in enumerate(datetime.strptime(built_at, "%Y-%m-%dT%H:%M:%SZ").timetuple()[:6]))
    with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in (("開啟VDR核聽工具.html", payload), ("使用說明.txt", instructions.encode("utf-8-sig"))):
            entry = zipfile.ZipInfo(name, date_time=stamp)
            entry.compress_type, entry.external_attr = zipfile.ZIP_DEFLATED, 0o644 << 16
            zf.writestr(entry, data)


def write_site(site: Path, payload: bytes, info: dict, repo: str) -> dict:
    """Write the Pages directory. A deployment publishes all of it, so refuse strays."""
    if site.exists():
        stray = sorted(path.name for path in site.iterdir() if path.name not in SITE_FILES)
        if stray:
            raise ValueError(f"site directory has files that would be published: {', '.join(stray)}")
    site.mkdir(parents=True, exist_ok=True)
    (site / "index.html").write_bytes(payload)
    (site / SITE_HTML).write_bytes(payload)
    write_package(site / PACKAGE_NAME, payload, info["built_at"])
    manifest = {"version": info["version"], "released": info["built_at"][:10], "html": SITE_HTML,
                "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload),
                "notes_url": RELEASE_URL.format(repository=repo, version=info["version"])}
    (site / "version.json").write_bytes((json.dumps(manifest, indent=2) + "\n").encode("utf-8"))
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="output HTML path (default: output/VDR核聽工具.html)")
    parser.add_argument("--package", type=Path, help="colleague ZIP path (default: alongside HTML)")
    parser.add_argument("--site", type=Path, help="write only the Pages directory: HTML, ZIP and version.json")
    parser.add_argument("--check-tag", metavar="TAG", help="only check that a release tag matches VERSION, then exit")
    args = parser.parse_args(argv)
    if args.check_tag is not None:
        expected = "v" + read_version()
        if args.check_tag != expected:
            parser.error(f"tag {args.check_tag!r} does not match VERSION ({expected})")
        return 0
    if args.site and (args.out or args.package):
        parser.error("--site writes its own HTML and ZIP; do not combine it with --out or --package")
    info, repo = build_info(), repository()
    if args.site and not repo:
        parser.error("--site needs the repository it publishes from: set VDR_REPOSITORY=owner/name")
    payload = build(info, repo).encode("utf-8")
    if args.site:
        try:
            manifest = write_site(args.site, payload, info, repo)
        except ValueError as exc:
            parser.error(str(exc))
        print(json.dumps({"site": str(args.site), **manifest}, ensure_ascii=False))
        return 0
    out = args.out or DEFAULT_OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(payload)
    package = args.package or out.with_name(PACKAGE_NAME)
    write_package(package, payload, info["built_at"])
    print(json.dumps({"output": str(out), "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest(), "package": str(package), "package_bytes": package.stat().st_size}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
