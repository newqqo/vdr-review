// JRC VDR 原始資料讀取：已觀察的 G.726 分鐘檔與 AAC 單筆容器、Log（NMEA）與 AIS。
// 格式驗證不等於機型認證；1800／1900／1950 仍須各機型實例比對，遇到不符的檔頭即停止，不猜測。
(function (root) {
  'use strict';
  const I18N = typeof module !== 'undefined' && module.exports ? require('./i18n_core.js') : root.VDRI18n;
  const ui = (key, params) => I18N.t('core.' + key, params);
function coreError(key, params = {}) {
  const error = new Error(ui(key, params));
  error.i18nKey = 'core.' + key; error.i18nParams = { ...params };
  return error;
}

  const AUDIO_RE = /^([AC]\d)(\d{2})(\d{2})(\d{2})\.dat$/i;
  const AAC_RE = /^([AC]\d)_.+\.dat$/i;
  // L0mmsscc.log：每筆以分鐘內位移計時；L_mmsscc.log（Internal_Storage）：每筆自帶 UTC 時間。
  const LOG_RE = /^L(\d|_)(\d{2})(\d{2})(\d{2})\.log$/i;
  const FOLDER_RE = /^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})$/;
  const CHANNEL_RE = /^(?:A[1-6]|C[12])$/;
  const AAC_RATES = [96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050, 16000, 12000, 11025, 8000, 7350];

  // Date.UTC 會把 2/30、24:00 等正規化到另一日，不能用來默認修正證據時標。
  function strictUtc(year, month, day, hour = 0, minute = 0, second = 0, ms = 0) {
    if (![year, month, day, hour, minute, second, ms].every(Number.isInteger)
        || year < 1000 || year > 9999 || month < 1 || month > 12 || day < 1 || day > 31
        || hour < 0 || hour > 23 || minute < 0 || minute > 59 || second < 0 || second > 59 || ms < 0 || ms > 999) return null;
    const t = Date.UTC(year, month - 1, day, hour, minute, second, ms), d = new Date(t);
    return d.getUTCFullYear() === year && d.getUTCMonth() === month - 1 && d.getUTCDate() === day ? t : null;
  }
  function folderUtc(fm) { return strictUtc(+fm[1], +fm[2], +fm[3], +fm[4], +fm[5]); }
  function pathUtc(fm, m) { return strictUtc(+fm[1], +fm[2], +fm[3], +fm[4], +m[2], +m[3], +m[4] * 10); }
  function ascii(bytes, start, end) { return String.fromCharCode(...bytes.subarray(start, end)); }
  function be32(bytes, o) { return ((bytes[o] << 24) | (bytes[o + 1] << 16) | (bytes[o + 2] << 8) | bytes[o + 3]) >>> 0; }
  function headerHex(bytes, size) { return Array.from(bytes.subarray(0, size), (b) => b.toString(16).padStart(2, '0')).join(''); }
  function recordingRoot(parts) {
    const base = parts.slice(0, -3);
    if (base[base.length - 1]?.toLowerCase() === 'internal_storage') base.pop();
    return base.join('/');
  }
  function oneRoot(roots) {
    if (roots.size > 1) throw coreError('vdr.multipleRoots');
  }
  function sortIndex(result) {
    for (const k of Object.keys(result.audio)) result.audio[k].sort((a, b) => a.start - b.start || a.path.localeCompare(b.path));
    result.logs.sort((a, b) => a.start - b.start);
    return result;
  }

  // 依路徑分類檔案；entries：[{path:'.../Audio/202601011000/A1000000.dat', file}]
  function classify(entries) {
    const audio = {}, logs = [], unsupportedLogs = [], skipped = [], roots = new Set();
    for (const e of entries) {
      const parts = e.path.split(/[\\/]/);
      const name = parts[parts.length - 1], folder = parts[parts.length - 2] || '';
      const fm = FOLDER_RE.exec(folder);
      const kind = (parts[parts.length - 3] || '').toLowerCase();
      let m;
      if (fm && kind === 'audio' && (m = AUDIO_RE.exec(name))) {
        roots.add(recordingRoot(parts));
        const start = pathUtc(fm, m);
        if (folderUtc(fm) == null || start == null || !CHANNEL_RE.test(m[1].toUpperCase())) throw coreError('vdr.audioTime', { path: e.path });
        (audio[m[1].toUpperCase()] ||= []).push({ start, name, folder, path: e.path, file: e.file, size: e.size ?? e.file?.size });
      } else if (fm && kind === 'log' && (m = LOG_RE.exec(name))) {
        roots.add(recordingRoot(parts));
        const start = pathUtc(fm, m);
        if (folderUtc(fm) == null || start == null) throw coreError('vdr.logTime', { path: e.path });
        logs.push({ start, name, folder, path: e.path, file: e.file, size: e.size ?? e.file?.size, format: m[1] === '_' ? 'stamped' : 'offset' });
      } else {
        if (kind === 'log' && /\.log$/i.test(name)) unsupportedLogs.push(e.path);
        skipped.push(e.path);
      }
    }
    oneRoot(roots);
    return sortIndex({ audio, logs, unsupportedLogs, skipped });
  }

  // 讀取 AAC 的容器時間，不以資料夾分鐘猜測。逐檔驗證，不把音訊 payload 留在索引中。
  async function indexEntries(entries) {
    const legacy = [], modern = [], roots = new Set();
    for (const e of entries) {
      const parts = e.path.split(/[\\/]/), name = parts[parts.length - 1];
      const kind = (parts[parts.length - 3] || '').toLowerCase();
      const fm = FOLDER_RE.exec(parts[parts.length - 2] || ''), am = AAC_RE.exec(name);
      if (fm && kind === 'audio' && am) {
        if (folderUtc(fm) == null) throw coreError('vdr.folderDate', { path: e.path });
        roots.add(recordingRoot(parts)); modern.push({ e, parts, am });
      } else {
        legacy.push(e);
        if (fm && ((kind === 'audio' && AUDIO_RE.test(name)) || (kind === 'log' && LOG_RE.test(name)))) roots.add(recordingRoot(parts));
      }
    }
    oneRoot(roots);
    const result = classify(legacy);
    for (const arr of Object.values(result.audio)) {
      for (const f of arr) {
        const rate = f.path.split(/[\\/]/).pop()[0].toUpperCase() === 'A' ? 14000 : 8000;
        // 檔頭於載入時再驗證；先依已驗證格式的固定長度拒絕截斷或不同取樣率。
        if (f.size != null && f.size !== rate * 60 * 3 / 8 + 34) throw coreError('vdr.minuteFile', { path: f.path });
        Object.assign(f, { codec: 'g726', rate, duration: 60 });
      }
    }
    for (const { e, parts, am } of modern) {
      if (!e.file?.arrayBuffer) throw coreError('vdr.aacRead', { path: e.path });
      const rec = readAudioRecord(new Uint8Array(await e.file.arrayBuffer()), am[1].toUpperCase());
      if (rec.codec !== 'aac') throw coreError('vdr.aacName', { path: e.path });
      (result.audio[rec.channel] ||= []).push({ start: rec.start, name: parts[parts.length - 1], folder: parts[parts.length - 2],
        path: e.path, file: e.file, size: e.size ?? e.file.size, codec: rec.codec, rate: rec.rate,
        duration: rec.duration, frames: rec.frames, recordedTimestamp: rec.recordedTimestamp });
    }
    return sortIndex(result);
  }

  // 音訊分鐘檔：32 bytes 檔頭（"VoyageDtRec "…、第26–29 byte 為 big-endian 長度＝檔長−32、第30–31 byte 為 CRLF），
  // 其後為 G.726 3-bit 資料，檔尾 CRLF 不屬音訊。
  function parseAudioRecord(bytes) {
    if (!(bytes instanceof Uint8Array) || bytes.length < 34) throw coreError('vdr.short34');
    const head = String.fromCharCode(...bytes.subarray(0, 12));
    if (head !== 'VoyageDtRec ') throw coreError('vdr.header');
    const declared = be32(bytes, 26);
    if (bytes[30] !== 13 || bytes[31] !== 10 || declared !== bytes.length - 32) {
      if (bytes.length >= 80 && bytes[44] === 0xFF && bytes[45] === 0xFF && be32(bytes, 48) === bytes.length - 80)
        throw coreError('vdr.aacNotG726');
      if (bytes[30] !== 13 || bytes[31] !== 10) throw coreError('vdr.headerCrlf');
      throw coreError('vdr.length', { declared, actual: bytes.length - 32 });
    }
    if (bytes[bytes.length - 2] !== 13 || bytes[bytes.length - 1] !== 10) throw coreError('vdr.trailer');
    const payload = bytes.subarray(32, bytes.length - 2);
    return { payload, headerHex: headerHex(bytes, 32) };
  }

  function readAudioRecord(bytes, channel) {
    if (!(bytes instanceof Uint8Array) || bytes.length < 34) throw coreError('vdr.short');
    if (channel != null && !CHANNEL_RE.test(channel)) throw coreError('vdr.channel', { channel });
    // G.726 payload 可偶然含 FF FF；已成立的 32-byte 容器不能因此被誤判成 AAC。
    const isLegacy = bytes[30] === 13 && bytes[31] === 10 && be32(bytes, 26) === bytes.length - 32;
    const isAac = !isLegacy && bytes.length >= 80 && ascii(bytes, 0, 11) === 'VoyageDtRec'
      && bytes[44] === 0xff && bytes[45] === 0xff;
    if (!isAac) {
      if (!channel) throw coreError('vdr.channelRequired');
      const rec = parseAudioRecord(bytes), rate = channel[0] === 'A' ? 14000 : 8000;
      if (rec.payload.length !== rate * 60 * 3 / 8) throw coreError('vdr.minuteFormat');
      return { ...rec, codec: 'g726', rate, duration: 60, samples: rate * 60, channel };
    }
    if (bytes.length < 87 || !/^VoyageDtRec \d{16}/.test(ascii(bytes, 0, 44))
        || ![1, 2].includes(bytes[46]) || bytes[47] !== 0) throw coreError('vdr.aacHeader');
    const trailer = [0, 0, 13, 10, 0, 0, 13, 10];
    if (be32(bytes, 48) !== bytes.length - 80 || !trailer.every((v, i) => bytes[bytes.length - 8 + i] === v)) throw coreError('vdr.aacLength');
    const stamp = ascii(bytes, 52, 68), ch = ascii(bytes, 68, 70);
    if (!/^\d{16}$/.test(stamp) || ascii(bytes, 12, 28) !== stamp) throw coreError('vdr.aacTimes');
    const start = strictUtc(+stamp.slice(0, 4), +stamp.slice(4, 6), +stamp.slice(6, 8), +stamp.slice(8, 10),
      +stamp.slice(10, 12), +stamp.slice(12, 14), +stamp.slice(14, 16) * 10);
    if (start == null) throw coreError('vdr.aacTime');
    if (!CHANNEL_RE.test(ch) || bytes[70] !== 0 || bytes[71] !== 0 || bytes[46] !== (ch[0] === 'A' ? 1 : 2)
        || (channel && channel !== ch)) throw coreError('vdr.aacChannel');
    const payload = bytes.subarray(72, bytes.length - 8);
    let pos = 0, frames = 0, rate = null, profile = null;
    while (pos < payload.length) {
      const h = payload.subarray(pos, pos + 7);
      if (h.length < 7 || h[0] !== 0xff || (h[1] & 0xf6) !== 0xf0) throw coreError('vdr.adtsHeader', { position: pos });
      const ri = (h[2] >> 2) & 15, r = AAC_RATES[ri], channels = ((h[2] & 1) << 2) | (h[3] >> 6);
      const size = ((h[3] & 3) << 11) | (h[4] << 3) | (h[5] >> 5), headerSize = h[1] & 1 ? 7 : 9;
      const currentProfile = (h[2] >> 6) + 1;
      if (currentProfile !== 2) throw coreError('vdr.aacProfile');
      if (!r || r !== (ch[0] === 'A' ? 16000 : 8000) || channels !== 1) throw coreError('vdr.aacMono');
      if (size < headerSize || pos + size > payload.length || (h[6] & 3)) throw coreError('vdr.adtsLength');
      if (rate != null && (rate !== r || profile !== currentProfile)) throw coreError('vdr.aacChanged');
      rate = r; profile = currentProfile; pos += size; frames++;
    }
    if (!frames) throw coreError('vdr.aacEmpty');
    return { payload, codec: 'aac', rate, duration: frames * 1024 / rate, frames, samples: frames * 1024,
      channel: ch, start, recordedTimestamp: stamp, profile, headerHex: headerHex(bytes, 72) };
  }

  // 由 payload 長度推定取樣率（假設每檔60秒、3-bit），並檢查是否為常見值
  function inferRate(payloadBytes, codeSize = 3, seconds = 60) {
    const r = payloadBytes * 8 / codeSize / seconds;
    const common = [8000, 11025, 12000, 14000, 16000, 22050, 24000];
    return { rate: Math.round(r), exact: Number.isInteger(r), common: common.includes(Math.round(r)) };
  }

  // Log（offset）：每筆 0xFFFF＋2 bytes 分鐘內位移（1/256 秒）＋2 bytes 埠號＋NMEA 句＋CRLF
  // Log（stamped）：見 parseStampedLog；兩者都回傳 [{t（毫秒）, port, s}]
  function parseLog(bytes, startMs, { format = 'offset', path = '' } = {}) {
    if (format === 'stamped') return parseStampedLog(bytes, path);
    const out = [];
    for (let i = 0; i + 7 < bytes.length; i++) {
      if (bytes[i] !== 0xFF || bytes[i + 1] !== 0xFF) continue;
      const c = bytes[i + 6];
      if (c !== 0x24 && c !== 0x21) continue; // $ 或 !
      let j = i + 6;
      while (j < bytes.length && bytes[j] !== 13 && bytes[j] !== 10 && j - i < 200) j++;
      if (j >= bytes.length || bytes[j] !== 13) continue;
      const off = (bytes[i + 2] << 8) | bytes[i + 3];
      const port = (bytes[i + 4] << 8) | bytes[i + 5];
      let s = '';
      for (let k = i + 6; k < j; k++) s += String.fromCharCode(bytes[k]);
      out.push({ t: startMs + off * 1000 / 256, port, s });
      i = j;
    }
    return out;
  }

  // 44 bytes 檔頭："VoyageDtRec "＋16 位數時間＋01 00＋…＋第32–35 byte 為紀錄區總長（檔長−48）＋CRLF。
  // 每筆：FF FF＋01 00＋4 bytes 句長（含 CRLF）＋16 位數 UTC 時間（百分之一秒）＋2 bytes 埠號＋6 bytes 未用＋NMEA 句＋00 00 CR LF；
  // 檔尾再一組 00 00 CR LF。依已觀察的 Internal_Storage 檔案；框架不符即停止，不猜測。
  function parseStampedLog(bytes, path) {
    const bad = (position) => { throw coreError('vdr.logFormat', { path, position }); };
    const suffix = (o) => bytes[o] === 0 && bytes[o + 1] === 0 && bytes[o + 2] === 13 && bytes[o + 3] === 10;
    if (!(bytes instanceof Uint8Array) || bytes.length < 48 || ascii(bytes, 0, 12) !== 'VoyageDtRec ' || !/^\d{16}$/.test(ascii(bytes, 12, 28))
        || bytes[28] !== 1 || bytes[29] !== 0 || be32(bytes, 32) !== bytes.length - 48 || bytes[42] !== 13 || bytes[43] !== 10
        || !suffix(bytes.length - 4)) bad(0);
    const out = [], end = bytes.length - 4;
    for (let i = 44; i < end;) {
      if (i + 36 > end || bytes[i] !== 0xFF || bytes[i + 1] !== 0xFF || bytes[i + 2] !== 1 || bytes[i + 3] !== 0) bad(i);
      const length = be32(bytes, i + 4), body = i + 32, next = body + length + 4, stamp = ascii(bytes, i + 8, i + 24);
      const t = /^\d{16}$/.test(stamp) ? strictUtc(+stamp.slice(0, 4), +stamp.slice(4, 6), +stamp.slice(6, 8), +stamp.slice(8, 10),
        +stamp.slice(10, 12), +stamp.slice(12, 14), +stamp.slice(14, 16) * 10) : null;
      if (length < 2 || length > 4096 || next > end || t == null || bytes[body + length - 2] !== 13 || bytes[body + length - 1] !== 10
          || !suffix(body + length)) bad(i);
      out.push({ t, port: (bytes[i + 24] << 8) | bytes[i + 25], s: ascii(bytes, body, body + length - 2) });
      i = next;
    }
    return out;
  }

  function checksumOk(s) {
    const star = s.lastIndexOf('*');
    if (star < 0) return true;
    if (!/^[0-9a-f]{2}$/i.test(s.slice(star + 1))) return false;
    let x = 0;
    for (let k = 1; k < star; k++) x ^= s.charCodeAt(k);
    return x === parseInt(s.slice(star + 1, star + 3), 16);
  }

  function nmeaLatLon(v, h, negative) {
    const latitude = negative === 'S', max = latitude ? 90 : 180;
    if (!v || !(latitude ? ['N', 'S'] : ['E', 'W']).includes(h)
        || !(latitude ? /^\d{4}(?:\.\d+)?$/ : /^\d{5}(?:\.\d+)?$/).test(v)) return null;
    const d = Math.floor(+v / 100), m = +v - d * 100;
    if (m >= 60 || d > max || (d === max && m !== 0)) return null;
    let x = d + m / 60;
    if (h === negative) x = -x;
    return Number.isFinite(x) ? x : null;
  }
  function finiteField(value, min, max, exclusive = false) {
    if (typeof value !== 'string' || !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(value)) return null;
    const n = +value;
    return Number.isFinite(n) && n >= min && (exclusive ? n < max : n <= max) ? n : null;
  }
  const navMode = (mode) => !mode || /^[ADEFMPRS]+$/.test(mode);

  // 逐秒航行資料：{utc（秒）, lat, lon, sog, cog, hdg, stw}；同秒多筆取最後一筆
  function navSeries(records) {
    const by = new Map();
    const zda = [];
    const get = (t) => { const k = Math.floor(t / 1000); let o = by.get(k); if (!o) { o = { utc: k }; by.set(k, o); } return o; };
    for (const r of records) {
      if (!Number.isFinite(r.t) || typeof r.s !== 'string' || r.s[0] !== '$' || !checksumOk(r.s)) continue;
      const f = r.s.split('*')[0].split(','), type = f[0].slice(3);
      const o = () => get(r.t);
      const assign = (key, value, min, max, exclusive = false) => { const n = finiteField(value, min, max, exclusive); if (n != null) o()[key] = n; };
      if (type === 'GGA' && /^[1-8]$/.test(f[6] || '')) { const a = nmeaLatLon(f[2], f[3], 'S'), b = nmeaLatLon(f[4], f[5], 'W'); if (a != null && b != null) Object.assign(o(), { lat: a, lon: b }); }
      else if (type === 'GNS' && f[6] && navMode(f[6])) { const a = nmeaLatLon(f[2], f[3], 'S'), b = nmeaLatLon(f[4], f[5], 'W'); if (a != null && b != null) Object.assign(o(), { lat: a, lon: b }); }
      else if (type === 'RMC' && f[2] === 'A' && navMode(f[12])) { const a = nmeaLatLon(f[3], f[4], 'S'), b = nmeaLatLon(f[5], f[6], 'W'); if (a != null && b != null) Object.assign(o(), { lat: a, lon: b }); assign('sog', f[7], 0, 200); assign('cog', f[8], 0, 360, true); }
      else if (type === 'VTG' && navMode(f[9])) { if (f[2] === 'T') assign('cog', f[1], 0, 360, true); if (f[6] === 'N') assign('sog', f[5], 0, 200); }
      else if (type === 'HDT' && f[2] === 'T') assign('hdg', f[1], 0, 360, true);
      else if (type === 'THS' && f[2] === 'A') assign('hdg', f[1], 0, 360, true);
      else if (type === 'VBW' && f[3] === 'A') assign('stw', f[1], -200, 200);
      else if (type === 'VHW' && f[6] === 'N') assign('stw', f[5], 0, 200);
      else if (type === 'ZDA' && f[1] && f[4]) {
        const tm = /^(\d{2})(\d{2})(\d{2})(?:\.(\d+))?$/.exec(f[1]);
        if (!tm || !/^\d{4}$/.test(f[4]) || !/^\d{1,2}$/.test(f[2]) || !/^\d{1,2}$/.test(f[3])) continue;
        const z = strictUtc(+f[4], +f[3], +f[2], +tm[1], +tm[2], +tm[3], +((tm[4] || '').padEnd(3, '0').slice(0, 3)));
        if (z != null) zda.push((r.t - z) / 1000);
      }
    }
    const series = [...by.values()].sort((a, b) => a.utc - b.utc);
    return { series, zdaOffsets: zda };
  }

  function stats(arr) {
    if (!arr.length) return null;
    const s = [...arr].sort((a, b) => a - b);
    return { n: s.length, median: s[Math.floor(s.length / 2)], min: s[0], max: s[s.length - 1],
             mean: s.reduce((a, b) => a + b, 0) / s.length };
  }

  // ---- AIS ----
  function bitsOf(payload, fill) {
    let s = '';
    for (const ch of payload) { let v = ch.charCodeAt(0) - 48; if (v > 40) v -= 8; s += v.toString(2).padStart(6, '0'); }
    return fill ? s.slice(0, s.length - fill) : s;
  }
  const U = (b, a, n) => parseInt(b.substr(a, n), 2);
  const I = (b, a, n) => { const v = U(b, a, n); return v >= 2 ** (n - 1) ? v - 2 ** n : v; };
  const TXT = '@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_ !"#$%&\'()*+,-./0123456789:;<=>?';
  const T = (b, a, n) => { let o = ''; for (let k = 0; k + 6 <= n && a + k + 6 <= b.length; k += 6) o += TXT[U(b, a + k, 6)]; return o.replace(/@/g, '').trim(); };

  // 回傳 {positions:{mmsi:[[tMs,lat,lon,sog,cog,hdg|null,own]]}, statics:{mmsi:{name,callsign,A,B,C,D,own}}, ownMmsi}
  function decodeAis(records) {
    const pos = {}, st = {}, parts = new Map();
    const ownCount = {};
    for (const r of records) {
      if (!Number.isFinite(r.t) || typeof r.s !== 'string' || !/^!..VD[MO],/.test(r.s) || !checksumOk(r.s)) continue;
      const own = r.s.slice(3, 6) === 'VDO';
      const f = r.s.split('*')[0].split(',');
      if (f.length < 7) continue;
      const total = +f[1], num = +f[2], seq = f[3], payload = f[5], fill = +(f[6] || 0);
      if (!Number.isInteger(total) || total < 1 || total > 9 || !Number.isInteger(num) || num < 1 || num > total
          || !Number.isInteger(fill) || fill < 0 || fill > 5 || (num < total && fill !== 0)
          || !payload || !/^[0-W`-w]+$/.test(payload)) continue;
      let b;
      if (total > 1) {
        const key = seq + '|' + f[4] + '|' + own + '|' + (r.port ?? '');
        let g = parts.get(key);
        if (!g || g.total !== total || Math.abs(r.t - g.t) > 10000 || num === 1) g = { total, t: r.t, pieces: {} };
        g.pieces[num] = [payload, fill];
        parts.set(key, g);
        if (Object.keys(g.pieces).length < total) continue;
        parts.delete(key);
        let ok = true; b = '';
        for (let k = 1; k <= total; k++) { if (!g.pieces[k]) { ok = false; break; } b += bitsOf(g.pieces[k][0], k === total ? g.pieces[k][1] : 0); }
        if (!ok) continue;
      } else b = bitsOf(payload, fill);
      if (b.length < 38) continue;
      const mt = U(b, 0, 6), mmsi = String(U(b, 8, 30));
      if (+mmsi < 1 || +mmsi > 999999999) continue;
      if (own) ownCount[mmsi] = (ownCount[mmsi] || 0) + 1;
      if ((mt >= 1 && mt <= 3) && b.length >= 168) {
        const lon = I(b, 61, 28) / 600000, lat = I(b, 89, 27) / 600000, hd = U(b, 128, 9);
        const speed = U(b, 50, 10), course = U(b, 116, 12);
        if (Math.abs(lat) <= 90 && Math.abs(lon) <= 180) (pos[mmsi] ||= []).push([r.t, lat, lon, speed === 1023 ? null : speed / 10, course >= 3600 ? null : course / 10, hd >= 360 ? null : hd, own]);
      } else if ((mt === 18 || mt === 19) && b.length >= 168) {
        const lon = I(b, 57, 28) / 600000, lat = I(b, 85, 27) / 600000, hd = U(b, 124, 9);
        const speed = U(b, 46, 10), course = U(b, 112, 12);
        if (Math.abs(lat) <= 90 && Math.abs(lon) <= 180) (pos[mmsi] ||= []).push([r.t, lat, lon, speed === 1023 ? null : speed / 10, course >= 3600 ? null : course / 10, hd >= 360 ? null : hd, own]);
      } else if (mt === 5 && b.length >= 420) {
        st[mmsi] = Object.assign(st[mmsi] || {}, { name: T(b, 112, 120), callsign: T(b, 70, 42), A: U(b, 240, 9), B: U(b, 249, 9), C: U(b, 258, 6), D: U(b, 264, 6), own });
      } else if (mt === 24 && b.length >= 160) {
        const d = st[mmsi] ||= { own };
        if (U(b, 38, 2) === 0) d.name = T(b, 40, 120);
        else if (U(b, 38, 2) === 1 && b.length >= 168) Object.assign(d, { A: U(b, 132, 9), B: U(b, 141, 9), C: U(b, 150, 6), D: U(b, 156, 6) });
      }
    }
    for (const k of Object.keys(pos)) pos[k].sort((a, c) => a[0] - c[0]);
    const ownMmsi = Object.entries(ownCount).sort((a, c) => c[1] - a[1])[0]?.[0] || null;
    return { positions: pos, statics: st, ownMmsi };
  }

  const api = { classify, indexEntries, readAudioRecord, parseAudioRecord, inferRate, parseLog, navSeries, decodeAis, stats, checksumOk };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.VDR = api;
})(typeof self !== 'undefined' ? self : this);
