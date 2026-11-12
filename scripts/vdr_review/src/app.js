// VDR 核聽工具主程式：選資料夾 → 選時段 → 背景解碼 → 地圖／曲線／船體模擬／逐字稿編輯。
(function () {
'use strict';
const $ = (id) => document.getElementById(id);
// Only explicit UI leaves use language resources. Never traverse transcript
// text, speaker names, filenames, imported JSON or evidence metadata.
const tr = (key, params) => VDRI18n.t('appui.' + key, params);
const htmlBindings = new Map(), languageRefresh = new Set();
function text(id, key, params = {}, fullKey = false) {
  const node = $(id); node.dataset.i18n = fullKey ? key : 'appui.' + key;
  node.dataset.i18nParams = JSON.stringify(params); htmlBindings.delete(id);
  node.textContent = VDRI18n.t(node.dataset.i18n, params);
}
function rawText(id, value) { const node = $(id); delete node.dataset.i18n; delete node.dataset.i18nParams; htmlBindings.delete(id); node.textContent = value; }
function html(id, render) { const node = $(id); delete node.dataset.i18n; delete node.dataset.i18nParams; htmlBindings.set(id, render); node.innerHTML = render(); }
function uiError(key, params = {}) { const error = new Error(tr(key, params)); error.uiKey = key; error.uiParams = params; error.i18nKey = 'appui.' + key; error.i18nParams = params; return error; }
const errorText = error => error?.uiKey ? tr(error.uiKey, error.uiParams) : VDRI18n.errorText(error);
const language = $('uiLanguage'), locales = ['zh-TW', 'en', 'ja', 'zh-CN'];
function applyLanguage() {
  VDRI18n.apply(document);
  document.title = VDRI18n.t('app.title');
  document.documentElement.lang = { 'zh-TW': 'zh-Hant', en: 'en', ja: 'ja', 'zh-CN': 'zh-Hans' }[VDRI18n.getLocale()] || 'zh-Hant';
  if (language) language.value = VDRI18n.getLocale();
  try { localStorage.setItem('vdrreview:locale', VDRI18n.getLocale()); } catch (_) { /* preferences may be unavailable */ }
  for (const [id, render] of htmlBindings) {
    const node = $(id), opened = [...node.querySelectorAll('details')].map(details => details.open);
    node.innerHTML = render(); [...node.querySelectorAll('details')].forEach((details, i) => { details.open = opened[i] || false; });
  }
  for (const refresh of languageRefresh) refresh();
}
VDRI18n.onChange(applyLanguage);
let savedLocale = 'zh-TW';
try { const saved = localStorage.getItem('vdrreview:locale'); if (locales.includes(saved)) savedLocale = saved; } catch (_) { /* use default */ }
VDRI18n.setLocale(savedLocale); applyLanguage();
if (language) language.onchange = () => { if (locales.includes(language.value)) VDRI18n.setLocale(language.value); };
$('sourceNotice').hidden = true;
$('buildinfo').textContent = `v${BUILD_INFO.version} · git ${BUILD_INFO.commit} · ${BUILD_INFO.built_at.slice(0, 16).replace('T', ' ')} UTC`;
const pad = (n) => String(n).padStart(2, '0');
let TZ = 8 * 3600; // 當地時區（秒）
const S = { cls: null, csv: null, csvNames: [], csvSources: [], selection: 0 };
let activeEditor = null, activeLanguageRefresh = null;
VDRUpdate.init({ $, version: BUILD_INFO.version, isDirty: () => !!activeEditor?.dirty() });

function fmtDT(ms) { const d = new Date(ms + TZ * 1000); return d.getUTCFullYear() + '-' + pad(d.getUTCMonth() + 1) + '-' + pad(d.getUTCDate()) + ' ' + pad(d.getUTCHours()) + ':' + pad(d.getUTCMinutes()) + ':' + pad(d.getUTCSeconds()) + (d.getUTCMilliseconds() ? '.' + String(d.getUTCMilliseconds()).padStart(3, '0') : ''); }
function parseDT(s) { return ReviewState.parseDateTime(s, TZ); }
function fmtClock(utcSec) { let s = utcSec + TZ; s = ((s % 86400) + 86400) % 86400; const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = s % 60; return pad(h) + ':' + pad(m) + ':' + x.toFixed(1).padStart(4, '0'); }
const esc = (v) => String(v ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
// 聲道名稱依 JRC 手冊：A1＝MIC 1–2、A2＝MIC 3–4…；C1＝VHF 1–2
const chLabel = (ch, rate) => { const n = +ch.slice(1); return tr('channel.label', { channel: ch, kind: ch[0] === 'C' ? 'VHF' : 'MIC', first: 2 * n - 1, last: 2 * n, rate: rate ? tr('channel.rate', { rate: rate / 1000 }) : '' }); };
const fmtDur = s => tr(s >= 60 ? 'duration' : 'seconds', { minutes: Math.floor(s / 60), seconds: (s % 60).toFixed(s < 1 ? 3 : 1) });
const errHTML = (m) => '<span class="err">' + esc(m) + '</span>';

// ---------- 1. 選資料夾 ----------
$('fdir').onchange = async (e) => {
  const files = [...e.target.files];
  const selection = ++S.selection;
  S.cls = null; S.cover = null; S.identity = null; S.canLoad = false; $('loadbtn').disabled = true;
  const box = $('dirsum'); box.hidden = false;
  if (!files.length) { text('dirsum', 'noFiles'); return; }
  text('dirsum', 'indexing');
  try { const cls = await VDR.indexEntries(files.map((f) => ({ path: f.webkitRelativePath || f.name, file: f, size: f.size }))); if (selection !== S.selection) return; S.cls = cls; }
  catch (err) { if (selection === S.selection) html('dirsum', () => esc(errorText(err))); return; }
  const ch = Object.keys(S.cls.audio).sort();
  if (!ch.length) { html('dirsum', () => errHTML(tr('audioNotFound'))); $('loadbtn').disabled = true; return; }
  const lines = [], probs = [];
  let first = Infinity, last = -Infinity, bad = 0;
  for (const c of ch) {
    const arr = S.cls.audio[c];
    first = Math.min(first, arr[0].start); for (const f of arr) last = Math.max(last, f.start + f.duration * 1000);
    try {
      const r = VDR.readAudioRecord(new Uint8Array(await arr[0].file.arrayBuffer()), c);
      if (selection !== S.selection) return;
      arr.rate = r.rate;
      lines.push(() => esc(tr('audioRow', { channel: chLabel(c, r.rate), count: arr.length, codec: r.codec.toUpperCase() })));
    } catch (err) {
      bad++;
      probs.push(() => c + ': ' + errorText(err));
      lines.push(() => esc(c) + ': ' + esc(errorText(err)));
    }
  }
  S.cover = [first, last];
  const ok = bad < ch.length;
  const logsCount = S.cls.logs.length, unsupportedCount = S.cls.unsupportedLogs.length;
  html('dirsum', () => (ok ? '<span class="ok">' + esc(tr('audioFound', { count: ch.length - bad, start: fmtDT(first), end: fmtDT(last) })) + '</span>' : errHTML(tr('audioUnreadable')))
    + '<br>' + (logsCount ? '<span class="ok">' + esc(tr('logsFound')) + '</span>' : '<span class="warn">' + esc(tr(unsupportedCount ? 'logsUnsupported' : 'logsAbsent', { count: unsupportedCount })) + '</span>')
    + (probs.length ? '<br><span class="warn">⚠ ' + esc(probs.map(read => read()).join('; ')) + '</span>' : '')
    + '<details><summary class="note">' + esc(tr('details')) + '</summary>' + lines.map(line => line()).join('<br>') + '<br>' + esc(tr('recordTiming', { zone: (TZ >= 0 ? '+' : '') + TZ / 3600 })) + '</details>');
  $('evtdate').value = fmtDT(first).slice(0, 10) === fmtDT(last - 1).slice(0, 10) ? fmtDT(first).slice(0, 10) : '';
  $('wstart').value = fmtDT(first); $('wend').value = fmtDT(last);
  setWindowFromEvent();
  S.canLoad = ok; $('loadbtn').disabled = !ok || S.csvReading;
};

// ---------- 2. 事故時間 → 載入時段 ----------
function setWindowFromEvent() {
  S.eventError = null;
  if (!S.cover) return;
  if (!$('evt').value.trim()) { text('winsum', 'loadWindow', { start: $('wstart').value, end: $('wend').value, zone: (TZ >= 0 ? '+' : '') + TZ / 3600 }); return; }
  try {
    const [a, b] = VDRMedia.eventWindow(S.cover, $('evtdate').value, $('evt').value, +$('span').value, TZ);
    $('wstart').value = fmtDT(a); $('wend').value = fmtDT(b);
    text('winsum', 'loadWindowMinutes', { start: fmtDT(a), end: fmtDT(b), zone: (TZ >= 0 ? '+' : '') + TZ / 3600, minutes: Math.round((b - a) / 60000) });
  } catch (err) { S.eventError = err; html('winsum', () => errHTML(errorText(err))); }
}
$('evt').addEventListener('input', setWindowFromEvent); $('evtdate').addEventListener('input', setWindowFromEvent); $('span').addEventListener('change', setWindowFromEvent);
$('allRecording').onclick = () => { if (!S.cover) return; $('evt').value = ''; $('wstart').value = fmtDT(S.cover[0]); $('wend').value = fmtDT(S.cover[1]); setWindowFromEvent(); };
for (const id of ['wstart', 'wend']) $(id).addEventListener('input', () => { $('evt').value = ''; setWindowFromEvent(); });
$('tz').addEventListener('change', () => {
  const z = +$('tz').value; if (!Number.isFinite(z) || z < -12 || z > 14) return;
  const a = parseDT($('wstart').value), b = parseDT($('wend').value);
  TZ = Math.round(z * 3600); parseAis(z);
  if (!$('evt').value.trim()) { if (a != null) $('wstart').value = fmtDT(a); if (b != null) $('wend').value = fmtDT(b); if (S.cover) $('evtdate').value = fmtDT(S.cover[0]).slice(0, 10) === fmtDT(S.cover[1] - 1).slice(0, 10) ? fmtDT(S.cover[0]).slice(0, 10) : ''; }
  setWindowFromEvent();
});

// ---------- 3. AIS CSV ----------
// Times without a zone use the zone field, so keep the text and parse it again when the zone changes.
function parseAis(zone) {
  if (!S.csvTexts || (S.csv && S.csvZone === zone)) return;
  const csv = S.csv = AISCSV.merge(S.csvTexts.map(c => AISCSV.parseCsvText(c.text, zone))); S.csvZone = zone;
  const n = Object.keys(csv.tracks).length, files = S.csvNames.length;
  html('csvsum', () => esc((n ? '✓ ' : tr('csvNoPosition')) + tr('csvSummary', { files, rows: csv.rows, ships: n }) + (csv.skipped ? tr('csvSkipped', { count: csv.skipped }) : '') + (csv.outliers ? tr('csvOutliers', { count: csv.outliers }) : '')));
}
$('fcsv').onchange = async (e) => {
  const selection = S.csvSelection = (S.csvSelection || 0) + 1;
  S.csvReading = true; $('loadbtn').disabled = true;
  const box = $('csvsum'); box.hidden = false; text('csvsum', 'reading');
  const texts = [], csvSources = [];
  try {
    for (const f of e.target.files) {
      // One read per file: the recorded hash describes exactly the bytes that are parsed.
      const buf = await f.arrayBuffer();
      csvSources.push({ file: f.name, bytes: buf.byteLength, sha256: await sha256(buf) });
      if (/\.zip$/i.test(f.name)) { for (const c of await AISCSV.unzipCsvs(new Uint8Array(buf))) texts.push({ name: f.name + '／' + c.name, text: c.text }); }
      else texts.push({ name: f.name, text: new TextDecoder().decode(buf) });
    }
    if (selection !== S.csvSelection) return;
    S.csv = null; S.csvTexts = texts; S.csvNames = texts.map(c => c.name); S.csvSources = csvSources;
    parseAis(+$('tz').value);
  } catch (err) { if (selection !== S.csvSelection) return; S.csv = null; S.csvTexts = null; S.csvSources = []; html('csvsum', () => errHTML(tr('csvUnreadable')) + '<details><summary class="note">' + esc(tr('details')) + '</summary>' + esc(errorText(err)) + '</details>'); }
  finally { if (selection === S.csvSelection) { S.csvReading = false; $('loadbtn').disabled = !S.canLoad; } }
};

// ---------- 4. 載入 ----------
const WORKER_URL = URL.createObjectURL(new Blob([WORKER_SRC], { type: 'text/javascript' }));
function decodeChannel(ch, job, onProg) {
  return new Promise((resolve, reject) => {
    const w = new Worker(WORKER_URL);
    w.onmessage = (e) => { if (e.data.type === 'prog') onProg(e.data.done / e.data.n); else { w.terminate(); resolve(e.data); } };
    w.onerror = (e) => { w.terminate(); reject(e.message ? new Error(e.message) : uiError('decodeFailed')); };
    w.postMessage({ ...job, locale: VDRI18n.getLocale() }, job.parts.map((p) => p.payload));
  });
}
async function sha256(buf) {
  return VDRProject.sha256(buf);
}

async function recordingIdentity(cls, onProgress) {
  // Root folder names may change between computers. Every recognized audio/log file
  // is content-hashed, including records outside the loaded review window.
  const files = Object.values(cls.audio).flat().concat(cls.logs).sort((a, b) => a.path.localeCompare(b.path));
  const path = p => { const parts = p.replace(/\\/g, '/').split('/'), i = parts.length - 3; return parts.slice(i > 0 && parts[i - 1].toLowerCase() === 'internal_storage' ? i - 1 : i).join('/'); };
  const inventory = [];
  for (let i = 0; i < files.length; i++) {
    const f = files[i]; f.sha256 = await sha256(await f.file.arrayBuffer());
    inventory.push([path(f.path), f.size ?? f.file.size, f.start, f.codec || 'log', f.sha256]);
    onProgress(i + 1, files.length);
  }
  inventory.sort((a, b) => a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0);
  return sha256(new TextEncoder().encode(JSON.stringify({ version: 2, inventory })));
}

$('loadbtn').onclick = async () => {
  const msg = $('loadmsg'), bar = $('loadbar');
  TZ = Math.round(+$('tz').value * 3600);
  if (!Number.isFinite(TZ) || TZ < -43200 || TZ > 50400) { text('loadmsg', 'invalidZone'); return; }
  parseAis(+$('tz').value);
  if (!S.cls || !S.cover) { text('loadmsg', 'selectSource'); return; }
  if ($('evt').value.trim()) { setWindowFromEvent(); if (S.eventError) { const error = S.eventError; html('loadmsg', () => errHTML(errorText(error))); return; } }
  const W0 = parseDT($('wstart').value), W1 = parseDT($('wend').value);
  if (W0 == null || W1 == null) { html('loadmsg', () => errHTML(tr('invalidDate'))); return; }
  if (W1 <= W0) { html('loadmsg', () => errHTML(tr('endAfterStart'))); return; }
  if (W1 <= S.cover[0] || W0 >= S.cover[1]) { html('loadmsg', () => errHTML(tr('noAudio'))); return; }
  if (W0 < S.cover[0] - 1000 || W1 > S.cover[1] + 1000) { html('loadmsg', () => errHTML(tr('outsideRecording'))); return; }
  $('loadbtn').disabled = true;
  const locked = [...$('loadpane').querySelectorAll('input,select,button')];
  for (const input of locked) input.disabled = true;
  try {
    text('loadmsg', 'hashStart');
    if (!S.identity) S.identity = await recordingIdentity(S.cls, (n, total) => { text('loadmsg', 'hashProgress', { done: n, total }); bar.style.width = (n / total * 25).toFixed(0) + '%'; });
    text('loadmsg', 'readNavigation');
    const recs = []; S.logSources = [];
    const logs = S.cls.logs.filter((l) => l.start < W1 + 600000 && l.start + 60000 > W0 - 600000);
    for (const l of logs) { const buf = await l.file.arrayBuffer(); const r = VDR.parseLog(new Uint8Array(buf), l.start); for (const x of r) recs.push(x); const hash = await sha256(buf); if (hash !== l.sha256) throw uiError('sourceChanged', { path: l.path }); S.logSources.push({ file: l.path, bytes: buf.byteLength, sha256: hash }); }
    const nav = VDR.navSeries(recs), ais = VDR.decodeAis(recs);
    text('loadmsg', 'timeline');
    const store = VDRStream.createStore({ audio: S.cls.audio, W0, W1, decodeG726: decodeChannel, onUpdate: ch => { if (S.refresh) S.refresh(ch); } });
    if (!Object.keys(store.channels).length) throw uiError('noReadableAudio');
    S.skippedChannels = [];
    await startMain({ W0, W1, nav, ais, CHS: store.channels, store });
    bar.style.width = '100%'; text('loadmsg', 'complete');
  } catch (err) {
    S.identity = null;
    html('loadmsg', () => errHTML(tr('loadFailed')) + '<details><summary class="note">' + esc(tr('loadDetails')) + '</summary>' + esc(errorText(err)) + '</details>'); for (const input of locked) input.disabled = false; console.error(err);
  }
};

// 涵蓋檢查：cover＝各分鐘檔在時段內的 [起,迄]（秒）；回傳無資料區段與重疊區段（忽略0.05秒以內的取整誤差）
function coverage(cover, dur) {
  const iv = cover.map(([a, b]) => [Math.max(0, a), Math.min(dur, b)]).filter(([a, b]) => b > a).sort((x, y) => x[0] - y[0]);
  const gaps = [], overlaps = [];
  let end = 0;
  for (let i = 0; i < iv.length; i++) {
    const [a, b] = iv[i];
    if (a - end > 0.05) gaps.push([end, a]);
    if (i && end - a > 0.05) overlaps.push([a, Math.min(end, b)]);
    end = Math.max(end, b);
  }
  if (dur - end > 0.05) gaps.push([end, dur]);
  return { gaps, overlaps };
}

// ---------- 主畫面 ----------
async function startMain({ W0, W1, nav, ais, CHS, store }) {
  $('loadpane').hidden = true; $('mainpane').hidden = false;
  const T0 = W0 / 1000, DUR = (W1 - W0) / 1000;
  const navBy = new Map(nav.series.map((o) => [o.utc, o]));
  const ownM = ais.ownMmsi;
  const ownSt = ownM ? ais.statics[ownM] : null;
  const NAME = ($('cname').value.trim() || (ownSt && ownSt.name) || 'VDR').replace(/[\\/:*?"<>|]/g, '_');
  document.title = tr('documentTitle', { name: NAME });
  // 無本船 MMSI 時以來源分鐘檔雜湊區分，避免不同船都叫 VDR 而接上同一份進度。
  const SHIP = ownM || NAME, DAY = fmtDT(W0).slice(0, 10);
  const KEY = 'vdrreview4:' + S.identity + ':' + DAY;
  let editor = null;
  let exportHistory = [];
  $('sessioninfo').textContent = NAME + ' · ' + fmtDT(W0).slice(0, 10);
  const sessionTitle = () => { document.title = tr('documentTitle', { name: NAME }); $('sessioninfo').title = tr('sourceTitle', { start: fmtDT(W0), end: fmtDT(W1), zone: (TZ >= 0 ? '+' : '') + TZ / 3600, identity: S.identity }); };
  sessionTitle();
  const legacy = []; // 舊版依時段起點分開存：vdrreview:<船>:<ISO>
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i), m = k && /^vdrreview:(.+):(\d{4}-\d{2}-\d{2}T[\d:.]+Z)$/.exec(k);
      if (m && m[1] === SHIP && fmtDT(Date.parse(m[2])).slice(0, 10) === DAY) legacy.push({ key: k, t0: Date.parse(m[2]) / 1000 });
    }
  } catch (e) { /* ignore */ }
  let OFS = 0, OFS_BASIS = ''; // 錄音時間校正（秒）：實際時間＝檔名推定時間＋OFS
  const tNow = () => T0 + (au.currentTime || 0) + OFS;
  const em = $('editmode');
  em.checked = false;
  document.body.classList.toggle('editing', em.checked);
  em.onchange = () => { document.body.classList.toggle('editing', em.checked); try { localStorage.setItem('vdrreview:editmode', em.checked ? '1' : '0'); } catch (e) { /* ignore */ } };

  // 本船位置：VDR 所錄本船 AIS（VDO，天線位置）；沒有就用 GPS（GGA/RMC）
  let own = (ownM && ais.positions[ownM] || []).map((r) => [r[0], r[1], r[2]]);
  if (own.length < 10) {
    const gps = nav.series.filter((o) => o.lat != null && o.lon != null).map((o) => [o.utc * 1000, o.lat, o.lon]);
    if (gps.length) own = gps;
  }
  const ownHdgAt = (sec) => { for (const d of [0, -1, 1, -2, 2]) { const o = navBy.get(sec + d); if (o && o.hdg != null) return o.hdg; } return null; };

  // 候選對方船：VDR 收到的 AIS ＋ CSV 航跡
  const tracks = Object.create(null), names = Object.create(null);
  for (const [m, arr] of Object.entries(ais.positions)) if (m !== ownM) tracks[m] = arr.map((r) => [r[0], r[1], r[2], r[3], r[4], r[5]]);
  for (const [m, st] of Object.entries(ais.statics)) if (st.name) names[m] = st.name;
  if (S.csv) for (const [m, arr] of Object.entries(S.csv.tracks)) {
    if (m === ownM) continue;
    const inWin = arr.filter((r) => r[0] >= W0 - 600000 && r[0] <= W1 + 600000);
    if (inWin.length) tracks[m] = (tracks[m] || []).concat(inWin).sort((a, b) => a[0] - b[0]);
    if (S.csv.names[m] && !names[m]) names[m] = S.csv.names[m];
  }
  const MLAT = 111132;
  const ownMid = own.length ? own[Math.floor(own.length / 2)] : [0, 22.6, 120.3];
  const MLON = 111320 * Math.cos(ownMid[1] * Math.PI / 180);
  const ownSample = own.filter((r) => r[0] >= W0 && r[0] <= W1).sort((a, b) => a[0] - b[0]).filter((_, i) => i % 5 === 0);
  const lowerBound = t => { let a = 0, b = ownSample.length; while (a < b) { const m = (a + b) >>> 1; if (ownSample[m][0] < t) a = m + 1; else b = m; } return a; };
  const cands = [];
  for (const [m, arr] of Object.entries(tracks)) {
    let best = Infinity;
    for (const p of arr) {
      if (p[0] < W0 || p[0] > W1) continue;
      for (let i = lowerBound(p[0] - 30000); i < ownSample.length && ownSample[i][0] < p[0] + 30000; i++) { const q = ownSample[i]; if (Math.abs(q[0] - p[0]) < 30000) best = Math.min(best, Math.hypot((p[1] - q[1]) * MLAT, (p[2] - q[2]) * MLON)); }
    }
    if (best < 3000) cands.push([m, best]);
  }
  cands.sort((a, b) => a[1] - b[1]);
  const osel = $('other');
  osel.innerHTML = '<option value=""></option>' + cands.slice(0, 25).map(([m]) => '<option value="' + esc(m) + '"></option>').join('');
  function candidateLabels() {
    osel.options[0].textContent = tr('otherNone');
    cands.slice(0, 25).forEach(([m, d], i) => { osel.options[i + 1].textContent = tr('candidate', { name: names[m] || tr('unknownVessel'), mmsi: m, distance: Math.round(d) }); });
  }
  candidateLabels();
  const setDims = (p, st) => { for (const k of ['A', 'B', 'C', 'D']) $(p + k).value = st && st[k] ? st[k] : ''; };
  setDims('o', ownSt);

  // 事件標記與設定（存瀏覽器；也隨編輯檔匯出）
  const extra = {
    get: () => ({ events: $('events').value, other: osel.value, fixed: $('fixed').checked, time_offset_s: OFS, time_offset_basis: OFS_BASIS,
                  own_dims: ['A', 'B', 'C', 'D'].map((k) => +$('o' + k).value || null), other_dims: ['A', 'B', 'C', 'D'].map((k) => +$('t' + k).value || null), transcription_exports: exportHistory }),
    set: (input) => {
      const x = { ...defaultExtra, ...input };
      exportHistory = Array.isArray(x.transcription_exports) ? x.transcription_exports.slice(-100) : [];
      if (x.events != null) $('events').value = x.events;
      osel.value = [...osel.options].some((o) => o.value === x.other) ? x.other : '';
      otherChanged();
      if (x.fixed != null) $('fixed').checked = x.fixed;
      if (x.own_dims) ['A', 'B', 'C', 'D'].forEach((k, i) => { $('o' + k).value = x.own_dims[i] ?? ''; });
      if (x.other_dims) ['A', 'B', 'C', 'D'].forEach((k, i) => { $('t' + k).value = x.other_dims[i] ?? ''; });
      if (x.time_offset_s != null) setOffset(+x.time_offset_s || 0, x.time_offset_basis || '', true);
      parseEvents(); saveExtra(false); recalc(); renderDataStatus();
    },
  };
  function saveExtra(changed = true) { if (changed && editor) editor.changed(); }
  const defaultExtra = extra.get();
  // A nearby AIS target is not automatically the investigated vessel. User selects it.

  // ---- 船體模擬（逐秒）----
  let H = [], otherFixed = null, otherTrack = null, otherSt = null;
  function otherChanged() {
    const m = osel.value;
    otherTrack = m ? tracks[m] : null;
    otherSt = m ? ais.statics[m] : null;
    setDims('t', otherSt);
    if (otherTrack && otherTrack.length) {
      const med = (a) => { const s = a.filter((v) => v != null).sort((x, y) => x - y); return s.length ? s[Math.floor(s.length / 2)] : null; };
      const lat = med(otherTrack.map((r) => r[1])), lon = med(otherTrack.map((r) => r[2]));
      const sd = Math.sqrt(otherTrack.reduce((a, r) => a + ((r[1] - lat) * MLAT) ** 2 + ((r[2] - lon) * MLON) ** 2, 0) / otherTrack.length);
      const sog = med(otherTrack.map((r) => r[3]));
      otherFixed = { lat, lon, hdg: med(otherTrack.map((r) => r[5])) ?? med(otherTrack.map((r) => r[4])), sd, n: otherTrack.length };
      $('fixed').checked = sd < 20 && (sog == null || sog < 0.5);
    } else otherFixed = null;
  }
  function dims(p) { const v = ['A', 'B', 'C', 'D'].map((k) => +$(p + k).value); return v.every((x) => x > 0 || x === 0) && v[0] + v[1] > 0 && v[2] + v[3] > 0 ? { A: v[0], B: v[1], C: v[2], D: v[3] } : null; }
  function otherAt(tMs) {
    if (!otherTrack) return null;
    if ($('fixed').checked) return otherFixed && otherFixed.hdg != null ? [otherFixed.lat, otherFixed.lon, otherFixed.hdg] : null;
    const p = DSP.interp(otherTrack, tMs, 60000);
    if (!p) return null;
    const near = p[2];
    const h = near[5] != null ? near[5] : near[4];
    return h == null ? null : [p[0], p[1], h];
  }
  function recalc() {
    const od = dims('o'), td = dims('t');
    H = [];
    for (let s = Math.floor(T0); s <= T0 + DUR; s++) {
      const p = DSP.interp(own, s * 1000, 10000), h = ownHdgAt(s);
      if (!p || h == null) continue;
      const o = otherAt(s * 1000);
      let d = null;
      if (o && od && td) {
        const P = DSP.hullXY((p[1] - ownMid[2]) * MLON, (p[0] - ownMid[1]) * MLAT, h, od);
        const Q = DSP.hullXY((o[1] - ownMid[2]) * MLON, (o[0] - ownMid[1]) * MLAT, o[2], td);
        d = DSP.polyDist(P, Q);
      }
      H.push([s, p[0], p[1], h, d, o]);
    }
    const ds = H.filter((r) => r[4] != null);
    const mn = ds.length ? ds.reduce((a, r) => (r[4] < a[4] ? r : a)) : null;
    renderSetSummary(od, td, mn);
    lastGhostKey = ''; drawOtherStatic(); tick();
  }
  function renderSetSummary(od = dims('o'), td = dims('t'), mn = null) {
    if (!mn) { const ds = H.filter(r => r[4] != null); mn = ds.length ? ds.reduce((a, r) => r[4] < a[4] ? r : a) : null; }
    rawText('setsum', (osel.value ? tr('otherSelected', { name: names[osel.value] || osel.value }) + ($('fixed').checked && otherFixed ? tr('fixedPosition', { distance: otherFixed.sd.toFixed(1) }) : tr('interpolated')) : tr('noOther')) +
      (!od ? ' | ' + tr('missingOwnDimensions') : '') + (osel.value && !td ? ' | ' + tr('missingOtherDimensions') : '') +
      (mn ? ' | ' + tr('modelClosest', { distance: mn[4] < 0.05 ? tr('modelOverlap') : mn[4].toFixed(0) + ' m', time: fmtClock(mn[0]).slice(0, 8) }) : ''));
  }

  // ---- 地圖 ----
  let credit = '<a href="https://maps.nlsc.gov.tw/pro/use_clause.jsp" target="_blank" rel="noopener">' + esc(tr('map.credit')) + '</a>';
  const photo = L.tileLayer('https://wmts.nlsc.gov.tw/wmts/PHOTO2/default/GoogleMapsCompatible/{z}/{y}/{x}', { maxZoom: 20, maxNativeZoom: 19, attribution: credit });
  const emap = L.tileLayer('https://wmts.nlsc.gov.tw/wmts/EMAP/default/GoogleMapsCompatible/{z}/{y}/{x}', { maxZoom: 20, maxNativeZoom: 17, attribution: credit });
  let tOk = 0, tErr = 0;
  for (const lyr of [photo, emap]) { lyr.on('tileload', () => { tOk++; tileSt(); }); lyr.on('tileerror', () => { tErr++; tileSt(); }); }
  function tileSt() {
    const box = $('tilest');
    box.hidden = !tErr;
    box.textContent = tErr ? tr(tOk ? 'map.partial' : 'map.failed') : '';
    box.title = tr('map.tileCounts', { ok: tOk, failed: tErr });
  }
  const blank = L.layerGroup(), BASES = { [tr('map.photo')]: photo, [tr('map.emap')]: emap, [tr('map.blank')]: blank };
  const baseName = tr('map.photo');
  const map = L.map('map', { preferCanvas: true, layers: [BASES[baseName]] }).setView([ownMid[1], ownMid[2]], 15);
  $('map').dataset.basemap = baseName;
  map.on('baselayerchange', e => { $('map').dataset.basemap = e.name; if (e.layer === blank) { $('tilest').hidden = true; $('tilest').textContent = ''; } else tileSt(); });
  const over = {}, overlayLabels = [];
  // CSV 中的本船航跡依日期分圖層（可比對歷史航次）
  if (S.csv && ownM && S.csv.tracks[ownM]) {
    const byDay = {};
    for (const r of S.csv.tracks[ownM]) { const day = fmtDT(r[0]).slice(0, 10); (byDay[day] ||= []).push(r); }
    const winDay = fmtDT(W0).slice(0, 10), colors = ['#c084fc', '#60a5fa', '#f472b6', '#a3e635', '#fb923c'];
    let ci = 0;
    for (const [day, pts] of Object.entries(byDay).sort()) {
      const cur = day === winDay, col = cur ? '#ffe066' : colors[ci++ % colors.length], g = L.layerGroup();
      L.polyline(pts.map((p) => [p[1], p[2]]), { color: col, weight: cur ? 3 : 2, opacity: 0.9 }).addTo(g);
      for (const p of pts) L.circleMarker([p[1], p[2]], { radius: 2, color: col, weight: 1 }).bindTooltip(() => esc(tr('map.positionTooltip', { time: day + ' ' + fmtDT(p[0]).slice(11), speed: p[3] ?? '—', heading: p[5] ?? '—' })).replace(/\n/g, '<br>')).addTo(g);
      const key = cur ? 'map.currentDay' : 'map.otherDay';
      over[tr(key, { day })] = g; overlayLabels.push({ layer: g, key, day }); g.addTo(map);
    }
  }
  const layerControl = L.control.layers(BASES, over, { collapsed: true }).addTo(map);
  const layerToggle = layerControl.getContainer().querySelector('.leaflet-control-layers-toggle');
  function mapLabels(rebuild = false) {
    if (rebuild) {
      for (const layer of [photo, emap, blank]) layerControl.removeLayer(layer);
      for (const entry of overlayLabels) layerControl.removeLayer(entry.layer);
      layerControl.addBaseLayer(photo, tr('map.photo')).addBaseLayer(emap, tr('map.emap')).addBaseLayer(blank, tr('map.blank'));
      for (const entry of overlayLabels) layerControl.addOverlay(entry.layer, tr(entry.key, { day: entry.day }));
      const previous = credit;
      credit = '<a href="https://maps.nlsc.gov.tw/pro/use_clause.jsp" target="_blank" rel="noopener">' + esc(tr('map.credit')) + '</a>';
      photo.options.attribution = credit; emap.options.attribution = credit;
      if (map.hasLayer(photo) || map.hasLayer(emap)) { map.attributionControl.removeAttribution(previous); map.attributionControl.addAttribution(credit); }
    }
    if (layerToggle) { layerToggle.textContent = tr('map.layers'); layerToggle.title = tr('map.layers'); layerToggle.setAttribute('aria-label', tr('map.layers')); }
    for (const [selector, key] of [['.leaflet-control-zoom-in', 'map.zoomIn'], ['.leaflet-control-zoom-out', 'map.zoomOut']]) {
      const node = $('map').querySelector(selector); if (node) { node.title = tr(key); node.setAttribute('aria-label', tr(key)); }
    }
    $('map').dataset.basemap = tr(map.hasLayer(photo) ? 'map.photo' : map.hasLayer(emap) ? 'map.emap' : 'map.blank');
  }
  mapLabels();
  const mapResize = typeof ResizeObserver === 'function' ? new ResizeObserver(() => map.invalidateSize({ pan: false })) : null;
  mapResize?.observe($('map'));
  window.addEventListener('pagehide', () => mapResize?.disconnect());
  const toLL = (xy) => [ownMid[1] + xy[1] / MLAT, ownMid[2] + xy[0] / MLON];
  const hullLL = (lat, lon, hdg, st) => DSP.hullXY((lon - ownMid[2]) * MLON, (lat - ownMid[1]) * MLAT, hdg, st).map(toLL);
  const ownPoly = L.polygon([], { color: '#00a3a3', weight: 3, fillOpacity: 0.45 }).addTo(map);
  const othPoly = L.polygon([], { color: '#e07b00', weight: 2, fillOpacity: 0.35 }).addTo(map);
  // A position does not require AIS dimensions. The small symbol is a locator,
  // not a vessel-size model; a missing heading is shown as a dot, not guessed.
  const ownMarker = L.marker([0, 0], { interactive: false, zIndexOffset: 500,
    icon: L.divIcon({ className: 'ownship-pin', iconSize: [24, 24], iconAnchor: [12, 12],
      html: '<span style="display:block;text-align:center;line-height:24px;font-size:24px;color:#00a3a3;text-shadow:0 0 3px #fff,0 0 3px #fff">●</span>' }) });
  const ghosts = L.layerGroup().addTo(map), trail = L.polyline([], { color: '#00e0e0', weight: 2, dashArray: '4 4' }).addTo(map);
  if (own.length) L.polyline(own.filter((r) => r[0] >= W0 && r[0] <= W1).map((r) => [r[1], r[2]]), { color: '#00a3a3', weight: 1, opacity: 0.5 }).addTo(map);
  function drawOtherStatic() { othPoly.setLatLngs([]); othPoly.unbindTooltip(); if (osel.value) othPoly.bindTooltip(() => esc(names[osel.value] || osel.value) + ($('fixed').checked ? esc(tr('map.fixedMedian')) : '')); }
  function hAt(t) {
    if (!H.length || t < H[0][0] || t > H[H.length - 1][0]) return null;
    let lo = 0, hi = H.length - 1;
    while (hi - lo > 1) { const m = (lo + hi) >> 1; if (H[m][0] <= t) lo = m; else hi = m; }
    const a = H[lo], b = H[hi]; if (b[0] - a[0] > 5) return null;
    const w = b[0] === a[0] ? 0 : (t - a[0]) / (b[0] - a[0]);
    let dh = b[3] - a[3]; if (dh > 180) dh -= 360; if (dh < -180) dh += 360;
    return [t, a[1] + w * (b[1] - a[1]), a[2] + w * (b[2] - a[2]), a[3] + w * dh, a[4] != null && b[4] != null ? a[4] + w * (b[4] - a[4]) : null, w < 0.5 ? a[5] : b[5]];
  }
  let lastGhostKey = '';
  function drawMap(t) {
    const s = hAt(t), p = DSP.interp(own, t * 1000, 10000), od = dims('o'), td = dims('t'), box = $('map'), status = $('mapstatus');
    box.dataset.navState = p ? 'position' : 'missing';
    if (status) { status.hidden = !!p; status.textContent = p ? '' : tr(own.length ? 'map.missingNow' : 'map.noTrack'); }
    if (!p) {
      if (map.hasLayer(ownMarker)) map.removeLayer(ownMarker);
      delete box.dataset.shipLat; delete box.dataset.shipLon;
      ownPoly.setLatLngs([]); othPoly.setLatLngs([]); ghosts.clearLayers(); trail.setLatLngs([]); lastGhostKey = '';
      return s;
    }
    ownMarker.setLatLng([p[0], p[1]]); if (!map.hasLayer(ownMarker)) ownMarker.addTo(map);
    const h = s ? s[3] : ownHdgAt(Math.round(t)), symbol = ownMarker.getElement()?.firstElementChild;
    if (symbol) { symbol.textContent = h == null ? '●' : '▲'; symbol.style.transform = h == null ? '' : 'rotate(' + h + 'deg)'; }
    box.dataset.shipLat = String(p[0]); box.dataset.shipLon = String(p[1]);
    ownPoly.setLatLngs(s && od ? hullLL(s[1], s[2], s[3], od) : []);
    othPoly.setLatLngs(s?.[5] && td ? hullLL(s[5][0], s[5][1], s[5][2], td) : []);
    const gi = +$('ghost').value, key = gi + ':' + Math.floor(t / (gi || 1));
    if (key !== lastGhostKey) {
      lastGhostKey = key; ghosts.clearLayers(); const tr = [];
      for (const r of H) { if (r[0] > t) break; if (r[0] >= t - 900) tr.push([r[1], r[2]]); if (od && gi && r[0] >= t - 600 && r[0] % gi === 0) L.polygon(hullLL(r[1], r[2], r[3], od), { color: '#7fd6d6', weight: 1, fill: false, opacity: 0.8 }).addTo(ghosts); }
      trail.setLatLngs(tr);
    }
    if ($('follow').checked) map.panTo([p[0], p[1]], { animate: false });
    return s;
  }

  // ---- 音訊 ----
  const sel = $('ch'); sel.innerHTML = ''; $('exportChannel').innerHTML = '';
  for (const k of Object.keys(CHS)) { const o = document.createElement('option'); o.value = k; o.textContent = chLabel(k, CHS[k].rate); sel.appendChild(o); $('exportChannel').appendChild(o.cloneNode(true)); }
  const au = VDRStream.createPlayer({ element: $('au'), store, channel: sel.value, status: (value, meta) => {
    if (meta?.key && meta.paramKeys) html('playstatus', () => esc(VDRI18n.errorText({ i18nKey: meta.key, i18nParams: meta.params, i18nParamKeys: meta.paramKeys })));
    else if (meta?.key) text('playstatus', meta.key, meta.params, true); else rawText('playstatus', value);
  } });
  window.__vdrReview = { store, player: au, channels: CHS, W0, W1, map, ship: ownMarker };
  // Waveform look-ahead is limited to the visible rows/window. A new view
  // cancels obsolete analysis instead of queuing the whole recording.
  let waveformRequest = null;
  function requestWaveforms(channels, start, end) {
    const names = [...new Set(channels)].filter(ch => CHS[ch]).sort();
    const a = Math.max(0, start), b = Math.min(DUR, end);
    if (!names.length || !Number.isFinite(a) || !Number.isFinite(b) || b <= a) return Promise.resolve(null);
    const key = names.join(',') + ':' + a + ':' + b;
    if (waveformRequest?.key === key) return waveformRequest.promise;
    waveformRequest?.controller.abort();
    const controller = new AbortController();
    const promise = store.ensureEnvelopes(names, a, b, { signal: controller.signal });
    waveformRequest = { key, controller, promise };
    return promise;
  }
  window.addEventListener('pagehide', () => { waveformRequest?.controller.abort(); });
  function setCh() { $('exportChannel').value = sel.value; au.setChannel(sel.value).catch(e => { html('playstatus', () => esc(tr('switchFailed', { message: errorText(e) }))); }); }
  sel.addEventListener('change', setCh);
  const elapsed = s => { s = Math.max(0, s); const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), sec = Math.floor(s % 60); return (h ? pad(h) + ':' : '') + pad(m) + ':' + pad(sec); };
  function playerControls() { rawText('playbtn', (au.paused ? '▶ ' : '⏸ ') + VDRI18n.t(au.paused ? 'action.play' : 'action.pause')); $('seek').value = au.currentTime; $('playelapsed').textContent = elapsed(au.currentTime) + ' / ' + elapsed(DUR); }
  $('seek').max = DUR;
  $('seek').oninput = e => { au.currentTime = +e.target.value; playerControls(); };
  $('playbtn').onclick = () => { if (au.paused) au.play().catch(e => { rawText('playstatus', errorText(e)); }); else au.pause(); };
  for (const ev of ['timeupdate', 'seeked', 'play', 'pause', 'ended', 'loadedmetadata']) au.addEventListener(ev, playerControls);
  $('exportStart').value = fmtDT(W0); $('exportEnd').value = fmtDT(W1);
  playerControls();

  // ---- 曲線 ----
  const cv = $('cv'), ctx = cv.getContext('2d'), xmin = T0, xmax = T0 + DUR, Lp = 70, Rp = 2330, TOP = 20, B = 540;
  const inWin = nav.series.filter((o) => o.utc >= xmin && o.utc <= xmax);
  const smax = Math.max(10, Math.ceil(Math.max(0, ...inWin.map((o) => Math.max(o.sog || 0, o.stw || 0))) / 2) * 2);
  const hs = inWin.map((o) => o.hdg).filter((v) => v != null);
  let hmin = hs.length ? Math.floor(Math.min(...hs) / 20) * 20 : 0, hmax = hs.length ? Math.ceil(Math.max(...hs) / 20) * 20 : 360;
  if (hmax - hmin < 40) hmax = hmin + 40;
  const X = (t) => Lp + (t - xmin) / (xmax - xmin) * (Rp - Lp), Ys = (v) => B - (v / smax) * (B - TOP), Yh = (v) => B - (v - hmin) / (hmax - hmin) * (B - TOP), Yd = (d) => Ys(Math.min(d, 500) / 500 * smax);
  let markers = [];
  function parseEvents() {
    markers = [];
    const day = Math.floor((T0 + TZ) / 86400) * 86400;
    for (const line of $('events').value.split(/\r?\n/)) {
      const m = /^\s*(\d{1,2}):(\d{2})(?::(\d{2}(?:\.\d+)?))?\s*(.*)$/.exec(line);
      if (!m) continue;
      let t = day + +m[1] * 3600 + +m[2] * 60 + +(m[3] || 0) - TZ;
      while (t - T0 > 43200) t -= 86400; while (T0 - t > 43200) t += 86400;
      markers.push([t, (m[1].padStart(2, '0') + ':' + m[2] + (m[3] ? ':' + m[3] : '')) + ' ' + m[4].trim()]);
    }
  }
  function draw(cur) {
    ctx.clearRect(0, 0, cv.width, cv.height); ctx.font = '20px sans-serif'; ctx.lineWidth = 1; ctx.strokeStyle = '#d9e0de'; ctx.fillStyle = '#5b6b6f';
    for (let v = 0; v <= smax; v += 2) { ctx.beginPath(); ctx.moveTo(Lp, Ys(v)); ctx.lineTo(Rp, Ys(v)); ctx.stroke(); ctx.fillText(v + 'kn', 6, Ys(v) + 7); }
    const step = DUR > 3600 ? 600 : DUR > 1800 ? 300 : 120;
    for (let t = Math.ceil((xmin + TZ) / step) * step - TZ; t <= xmax; t += step) { ctx.beginPath(); ctx.moveTo(X(t), TOP); ctx.lineTo(X(t), B); ctx.stroke(); ctx.fillText(fmtClock(t).slice(0, 5), X(t) - 26, B + 28); }
    for (const [t, lab] of markers) { if (t < xmin || t > xmax) continue; ctx.strokeStyle = '#b03a2e'; ctx.setLineDash([6, 6]); ctx.beginPath(); ctx.moveTo(X(t), TOP); ctx.lineTo(X(t), B); ctx.stroke(); ctx.setLineDash([]); ctx.save(); ctx.translate(X(t) + 4, TOP + 6); ctx.rotate(Math.PI / 2); ctx.fillStyle = '#b03a2e'; ctx.fillText(lab.slice(0, 28), 0, 0); ctx.restore(); }
    const line = (get, col, yf, w) => { ctx.strokeStyle = col; ctx.lineWidth = w || 3; ctx.beginPath(); let st = false, prev = null; for (const r of inWin) { const v = get(r); if (v == null) continue; const x = X(r.utc), y = yf(v); if (!st || r.utc - prev > 10) { ctx.moveTo(x, y); st = true; } else ctx.lineTo(x, y); prev = r.utc; } ctx.stroke(); };
    line((o) => o.sog, '#277a7b', Ys); line((o) => o.stw, '#9a6b3e', Ys); line((o) => o.hdg, '#6b4fa0', Yh);
    ctx.strokeStyle = '#d0021b'; ctx.lineWidth = 2.5; ctx.beginPath(); let st = false; for (const r of H) { if (r[4] == null) { st = false; continue; } const x = X(r[0]), y = Yd(r[4]); if (!st) { ctx.moveTo(x, y); st = true; } else ctx.lineTo(x, y); } ctx.stroke();
    ctx.fillStyle = '#6b4fa0'; for (let v = hmin; v <= hmax; v += (hmax - hmin > 120 ? 40 : 20)) ctx.fillText(v + '°', Rp + 6, Yh(v) + 7);
    if (cur != null) { ctx.strokeStyle = '#000'; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(X(cur), TOP); ctx.lineTo(X(cur), B); ctx.stroke(); }
  }
  function near(t, k) { const s = Math.round(t); for (const d of [0, -1, 1, -2, 2, -3, 3, -4, 4, -5, 5]) { const o = navBy.get(s + d); if (o && o[k] != null) return [o[k], d]; } return null; }
  function navigationLabels(t, s) {
    $('clock').textContent = fmtClock(t);
    const f = (k, n) => { const r = near(t, k); return n + ' ' + (r ? r[0].toFixed(1) + (r[1] ? tr('navOffset', { seconds: (r[1] > 0 ? '+' : '') + r[1] }) : '') : '—'); };
    $('vsog').textContent = f('sog', 'SOG'); $('vstw').textContent = f('stw', 'STW'); $('vhdg').textContent = f('hdg', 'HDG');
    rawText('dist', tr('hullDistance', { distance: s && s[4] != null ? (s[4] <= 0.05 ? tr('hullOverlap') : tr('hullApprox', { distance: s[4].toFixed(0) })) : '—' }));
  }
  function tick() {
    const t = tNow(), s = drawMap(t);
    navigationLabels(t, s);
    draw(t);
  }
  au.addEventListener('timeupdate', tick); au.addEventListener('seeked', tick); setInterval(() => { if (!au.paused) tick(); }, 250);
  $('ghost').onchange = () => { lastGhostKey = ''; tick(); };
  cv.addEventListener('click', (e) => { const b = cv.getBoundingClientRect(); const x = (e.clientX - b.left) / b.width * cv.width; const t = xmin + (x - Lp) / (Rp - Lp) * (xmax - xmin); au.currentTime = Math.max(0, Math.min(DUR, t - T0 - OFS)); tick(); });
  document.querySelectorAll('[data-j]').forEach((b) => { b.onclick = () => { au.currentTime = Math.max(0, au.currentTime + Number(b.dataset.j)); }; });
  $('gobtn').onclick = () => {
    const value = $('go').value.trim(); let t = parseDT(value);
    if (t == null && fmtDT(W0).slice(0, 10) === fmtDT(W1 - 1).slice(0, 10)) t = parseDT(fmtDT(W0).slice(0, 10) + ' ' + value);
    if (t == null) { text('playstatus', 'gotoDate'); return; }
    const position = t / 1000 - T0 - OFS;
    if (position < 0 || position >= DUR) { text('playstatus', 'gotoOutside'); return; }
    au.currentTime = position;
  };
  $('events').oninput = () => { parseEvents(); saveExtra(); draw(tNow()); };
  $('mark').onclick = () => { const ta = $('events'); ta.value += (ta.value && !ta.value.endsWith('\n') ? '\n' : '') + fmtClock(tNow()).slice(0, 8) + ' ' + sel.value + ' '; ta.oninput(); ta.focus(); };

  // ---- 錄音時間校正 ----
  function setOffset(v, basis, quiet) {
    const previousOffset = OFS;
    OFS = Number.isFinite(v) ? v : 0; OFS_BASIS = OFS ? basis : '';
    for (const id of ['exportStart', 'exportEnd']) { const time = parseDT($(id).value); if (time != null) $(id).value = fmtDT(time + (OFS - previousOffset) * 1000); }
    offsetLabels();
    $('tcofs').value = OFS || ''; $('tcbasis').value = OFS_BASIS;
    if (!quiet) { saveExtra(); tick(); renderDataStatus(); if (editor) editor.render(); }
  }
  function offsetLabels() {
    const b = $('tcbtn');
    text('tcbtn', OFS ? 'offsetCorrected' : 'offsetNone', { offset: (OFS > 0 ? '+' : '') + OFS.toFixed(1) });
    b.classList.toggle('ok', !!OFS);
    b.title = OFS ? tr('offsetBasis', { basis: OFS_BASIS || tr('notProvided'), sign: OFS > 0 ? '−' : '+', seconds: Math.abs(OFS).toFixed(1) }) : tr('offsetUncorrected');
  }
  $('tcbtn').onclick = () => { $('tcpanel').hidden = !$('tcpanel').hidden; };
  $('tcok').onclick = () => {
    const v = +$('tcofs').value;
    if (!Number.isFinite(v) || Math.abs(v) > 3600) { alert(tr('offsetInvalid')); return; }
    if (v && !$('tcbasis').value.trim()) { alert(tr('offsetBasisRequired')); return; }
    setOffset(v, $('tcbasis').value.trim()); $('tcpanel').hidden = true;
  };
  $('tcclr').onclick = () => { setOffset(0, ''); $('tcpanel').hidden = true; };

  // ---- 本聲道資料狀況（缺漏／重疊／異常檔）----
  function renderDataStatus() {
    const c = CHS[sel.value], q = c.qa, box = $('datast');
    const alert = $('dataalert');
    if (alert) {
      const problems = [q.gaps.length ? tr('gaps', { count: q.gaps.length }) : '', q.overlaps.length ? tr('overlaps', { count: q.overlaps.length }) : '',
        q.bad.length ? tr('badFiles', { count: q.bad.length }) : '', S.skippedChannels.length ? tr('omittedCount', { count: S.skippedChannels.length }) : ''].filter(Boolean);
      alert.hidden = !problems.length; alert.textContent = problems.join(' · ');
      alert.onclick = () => { if ($('dataDetails')) $('dataDetails').open = true; };
    }
    const chips = arr => arr.slice(0, 6).map(([a, b]) => '<button class="chip ghost" data-t="' + a + '">' + fmtClock(T0 + a + OFS).slice(0, 8) + '–' + fmtClock(T0 + b + OFS).slice(0, 8) + ' (' + esc(fmtDur(b - a)) + ')</button>').join('') + (arr.length > 6 ? esc(tr('moreSegments', { count: arr.length })) : '');
    const omitted = (q.pending_records ? '<br><span class="note">' + esc(tr('pendingRecords', { count: q.pending_records })) + '</span>' : '') + (S.skippedChannels.length ? '<br><span class="warn">' + esc(tr('omittedChannels', { channels: S.skippedChannels.join(', ') })) + '</span>' : '');
    if (!q.gaps.length && !q.overlaps.length && !q.bad.length) { box.innerHTML = '<span class="ok">' + esc(tr('noGaps', { count: c.sources.length, codec: c.codec.toUpperCase() })) + '</span>' + omitted; return; }
    box.innerHTML = '<span class="warn">' + esc(tr('dataChannel')) + '</span> ' +
      (q.gaps.length ? esc(tr('gapDetails', { count: q.gaps.length })) + chips(q.gaps) : '') +
      (q.overlaps.length ? ' ' + esc(tr('overlapDetails', { count: q.overlaps.length })) + chips(q.overlaps) : '') +
      (q.bad.length ? ' <span title="' + esc(q.bad.map(b => b.file + ': ' + b.reason).join('\n')) + '">' + esc(tr('rejectedDetails', { count: q.bad.length })) + '</span>' : '') + omitted;
  }
  $('datast').addEventListener('click', (e) => { const b = e.target.closest('[data-t]'); if (b) au.currentTime = Math.max(0, +b.dataset.t - 1); });
  sel.addEventListener('change', renderDataStatus);
  osel.onchange = () => { otherChanged(); saveExtra(); recalc(); };
  $('fixed').onchange = () => { saveExtra(); recalc(); };
  $('recalc').onclick = () => { saveExtra(); recalc(); };
  for (const p of ['o', 't']) for (const k of ['A', 'B', 'C', 'D']) $(p + k).onchange = () => saveExtra();

  // ---- 匯出整段聲道 WAV ＋ 來源清單 ----
  const download = (name, blob) => { const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 10000); };
  function manifest(ch) {
    const c = CHS[ch];
    return { tool: 'VDR核聽工具', tool_build: BUILD_INFO, recording_id: S.identity, recording_id_method: 'v2: SHA256 of sorted relative inventory including content SHA256 of every recognized audio/log record; unrelated evidence files excluded',
      channel: ch, sample_rate: c.rate, codec: c.codec, decoder: c.codec === 'aac' ? 'Browser WebAudio AAC, mono; no time stretch; per-record decoded sample counts below' : 'G.726 3-bit big-endian; decoder reset per 60-second record', decoder_records: c.decoder,
      window_utc: [new Date(W0).toISOString(), new Date(W1).toISOString()], local_tz_hours: TZ / 3600,
      navigation_window_utc: [new Date(W0).toISOString(), new Date(W1).toISOString()], navigation_scope: 'Navigation sources only cover the loaded review window plus a ten-minute guard; they do not establish navigation coverage for an entire-source audio export.',
      timing: 'G.726 uses filename UTC; AAC uses container header UTC. Independent clock verification required. Missing intervals are zero-filled, not evidence of silence. Later records overwrite overlapping samples.',
      time_correction: { offset_s: OFS, basis: OFS_BASIS, note: 'corrected display time = source timestamp + offset_s; audio samples unchanged' },
      gaps_s: c.qa.gaps, overlaps_s: c.qa.overlaps, rejected_files: c.qa.bad, omitted_channels: S.skippedChannels,
      sources: c.sources, navigation_sources: S.logSources, external_ais_sources: S.csvSources, ais_filter: 'invalid/unavailable values removed; GateHouse obvious outliers filtered; model distances are estimates, not survey measurements' };
  }
  let exportBusy = false;
  async function audioEntries(ch, startUTC, endUTC, enhanced = false) {
    if (exportBusy) throw uiError('exportBusy');
    if (!S.cls.audio[ch] || !Number.isFinite(startUTC) || !Number.isFinite(endUTC) || endUTC <= startUTC || startUTC < S.cover[0] - 1000 || endUTC > S.cover[1] + 1000) throw uiError('exportInvalidRange');
    if (enhanced && endUTC - startUTC > 120000) throw uiError('exportEnhancedLimit');
    exportBusy = true; au.pause();
    const buttons = [$('exportRange'), $('exportWhole'), $('exWav'), $('exBundle')];
    buttons.forEach(b => { b.disabled = true; });
    let outStore;
    try {
      outStore = VDRStream.createStore({ audio: S.cls.audio, W0: startUTC, W1: endUTC, decodeG726: decodeChannel });
      const c = outStore.channels[ch];
      if (!c) throw uiError('exportNoAudio');
      const rate = c.rate, total = Math.round((endUTC - startUTC) / 1000 * rate), parts = [DSP.wavHeader(total, rate)];
      let processing = { kind: 'decoded_original', gain: 1 };
      const processingChunks = [];
      if (total * 2 + 44 >= 0xffffffff) throw uiError('exportWavLimit');
      for (let i = 0; i < total; i += rate * 60) {
        const stop = Math.min(total, i + rate * 60), r = await outStore.readRange(ch, i / rate, stop / rate);
        let pcm = r.pcm;
        if (enhanced) {
          const clip = VDRMedia.clipPCM(pcm, rate, 0, pcm.length / rate, true);
          pcm = clip.pcm; processingChunks.push({ start: i, end: stop, processing: clip.processing });
        }
        if (pcm.length !== stop - i) throw uiError('exportSampleMismatch');
        parts.push(new Blob([pcm]));
        text('exportstatus', 'exportProgress', { channel: ch, percent: Math.round(stop / total * 100) });
        await new Promise(resolve => setTimeout(resolve, 0));
      }
      if (enhanced) processing = VDRMedia.enhancedProcessing(rate, startUTC, total, processingChunks);
      const blob = new Blob(parts, { type: 'audio/wav' });
      text('exportstatus', 'exportHash');
      const hash = await sha256(blob), suffix = enhanced ? '_加強音_僅供核聽' : '';
      const base = NAME + '_' + ch + '_' + fmtDT(startUTC).replace(/[-: ]/g, '') + '-' + fmtDT(endUTC).replace(/[-: ]/g, '') + suffix;
      const alignment = { kind: 'vdr-transcription-export', version: 1, recording_id: S.identity, channel: ch,
        source_start_utc: new Date(startUTC).toISOString(), source_end_utc: new Date(endUTC).toISOString(),
        corrected_start_utc: new Date(startUTC + OFS * 1000).toISOString(), display_tz_hours: TZ / 3600,
        time_correction: { offset_s: OFS, basis: OFS_BASIS }, audio_file: base + '.wav', audio_sha256: hash,
        subtitle_zero: 'SRT 0 秒 = source_start_utc 的解碼音檔起點；SRT 不是 UTC 時鐘', processing,
        note: 'SRT 機器轉錄須逐句核聽；本工具不自動上傳。缺段零填補不是靜音證據。' };
      const receipt = { ...manifest(ch), window_utc: [alignment.source_start_utc, alignment.source_end_utc],
        sources: c.sources, decoder_records: c.decoder, gaps_s: c.qa.gaps, overlaps_s: c.qa.overlaps, rejected_files: c.qa.bad,
        processing, output_wav_sha256: hash, decoded_samples: total, indexed_only: c.qa.indexedOnly, pending_records: c.qa.pending_records, cache_verification: outStore.stats() };
      exportHistory.push(alignment); exportHistory = exportHistory.slice(-100); saveExtra();
      return [{ name: base + '.wav', data: blob }, { name: base + '_轉錄時間對照.json', data: JSON.stringify(alignment, null, 2) }, { name: base + '_來源清單.json', data: JSON.stringify(receipt, null, 2) }];
    } finally { outStore?.destroy(); exportBusy = false; buttons.forEach(b => { b.disabled = false; }); }
  }
  async function bundleEntries(ch) { return audioEntries(ch, W0, W1); }
  let downloadBusy = false;
  async function downloadAudio(ch, start, end, enhanced = false) {
    if (downloadBusy) throw uiError('downloadBusy');
    downloadBusy = true;
    try {
      const entries = await audioEntries(ch, start, end, enhanced);
      text('exportstatus', 'exportPacking');
      download(NAME + '_' + ch + '_音檔與時間對照.zip', await VDRProject.zipEntries(entries));
      text('exportstatus', 'exportReady');
    } catch (e) { html('exportstatus', () => esc(tr('exportFailed', { message: errorText(e) }))); throw e; }
    finally { downloadBusy = false; }
  }
  async function exportWav(ch) { return downloadAudio(ch, W0, W1); }
  $('exportRange').onclick = () => {
    const a = parseDT($('exportStart').value), b = parseDT($('exportEnd').value);
    // Text fields show corrected local time; convert back to unmodified source UTC.
    downloadAudio($('exportChannel').value, a == null ? NaN : a - OFS * 1000, b == null ? NaN : b - OFS * 1000).catch(() => {});
  };
  $('exportWhole').onclick = () => downloadAudio($('exportChannel').value, S.cover[0], S.cover[1]).catch(() => {});
  async function exportClip(ch, start, end, enhanced) {
    return downloadAudio(ch, W0 + Math.max(0, start) * 1000, W0 + Math.min(DUR, end) * 1000, enhanced);
  }

  // ---- 逐字稿 ----
  // VHF is the editor's legacy map name; activity candidates now cover every
  // channel. Unknown/missing samples break candidates instead of implying quiet.
  const VHF = {}, ENV = {};
  for (const [k, c] of Object.entries(CHS)) ENV[k] = c.env;
  function refreshActivity(ch) {
    for (const k of ch ? [ch] : Object.keys(CHS)) {
      const c = CHS[k];
      if (c) VHF[k] = DSP.segments(c.env, 64, 1.5, 0.5, c.envKnown);
    }
  }
  refreshActivity();
  parseEvents(); otherChanged(); recalc();
  renderDataStatus();
  const coverageInfo = {};
  const updateCoverage = () => { for (const [k, c] of Object.entries(CHS)) coverageInfo[k] = { gaps_s: c.qa.gaps, overlaps_s: c.qa.overlaps, rejected_files: c.qa.bad.length, pending_records: c.qa.pending_records, indexed_only: c.qa.indexedOnly }; };
  updateCoverage();
  activeEditor?.disposeI18n?.();
  activeEditor = editor = Editor.init({ au, sel, channels: Object.keys(CHS), T0, LT: TZ, ENV, ENVR: DSP.ENV_RATE, VHF, KEY, NAME,
    recordingId: S.identity, bundleEntries, exportClip, wav: (ch) => CHS[ch], extra, defaultExtra, exportWav, exports: () => exportHistory, ofs: () => OFS, legacy, dur: DUR, gaps: (ch) => (CHS[ch] ? CHS[ch].qa.gaps : []), pending: ch => CHS[ch]?.qa.pending_records || 0, envKnown: ch => CHS[ch]?.envKnown, coverage: coverageInfo,
    requestWaveforms, channelLabels: ch => chLabel(ch, CHS[ch]?.rate) });
  S.refresh = ch => { updateCoverage(); renderDataStatus(); refreshActivity(ch); if (editor?.refreshMedia) editor.refreshMedia(); };
  await au.ready;
  renderDataStatus(); tick();
  const refreshMainLanguage = () => {
    sessionTitle(); candidateLabels(); mapLabels(true); tileSt(); offsetLabels();
    for (const option of [...sel.options, ...$('exportChannel').options]) option.textContent = chLabel(option.value, CHS[option.value]?.rate);
    playerControls(); renderSetSummary(); renderDataStatus();
    const now = tNow(); navigationLabels(now, hAt(now));
    const mapStatus = $('mapstatus');
    if (mapStatus && $('map').dataset.navState === 'missing') mapStatus.textContent = tr(own.length ? 'map.missingNow' : 'map.noTrack');
  };
  if (activeLanguageRefresh) languageRefresh.delete(activeLanguageRefresh);
  activeLanguageRefresh = refreshMainLanguage; languageRefresh.add(refreshMainLanguage);
  window.addEventListener('pagehide', () => languageRefresh.delete(refreshMainLanguage));
}
})();
