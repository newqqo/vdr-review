// 讀取 AIS 航跡 CSV（GateHouse aisWeb「CSV Raw Data Export」或本系統分析CSV），亦可直接讀其下載的 ZIP。
// 依欄名辨識，回傳 {tracks:{mmsi:[[tMs,lat,lon,sog,cog,hdg|null]]}, names:{mmsi:name}, rows, skipped}
(function (root) {
  'use strict';
  const I18N = typeof module !== 'undefined' && module.exports ? require('./i18n_core.js') : root.VDRI18n;
  const ui = (key, params) => I18N.t('core.' + key, params);
function coreError(key, params = {}) {
  const error = new Error(ui(key, params));
  error.i18nKey = 'core.' + key; error.i18nParams = { ...params };
  return error;
}
  const COLS = {
    time: ['base station time stamp', 'timestamp_utc', 'timestamp_cst', 'timestamp', 'time', 'datetime', '時間'],
    mmsi: ['mmsi'],
    name: ['ship name', 'name', 'vessel name', '船名'],
    lat: ['latitude', 'lat', '緯度'],
    lon: ['longitude', 'lon', 'lng', '經度'],
    sog: ['speed over ground', 'sog_knots', 'sog', 'speed'],
    cog: ['course over ground', 'cog_deg', 'cog', 'course'],
    hdg: ['heading', 'heading_deg', 'hdg', 'true heading'],
  };
  const TZ = { UTC: 0, GMT: 0, Z: 0, CST: 8, TST: 8, TWT: 8, HKT: 8, SGT: 8, JST: 9, KST: 9 };
  const MON = { jan: 0, feb: 1, mar: 2, apr: 3, may: 4, jun: 5, jul: 6, aug: 7, sep: 8, oct: 9, nov: 10, dec: 11 };

  function splitCsv(text) {
    const rows = [];
    let row = [], f = '', q = false;
    for (let i = 0; i < text.length; i++) {
      const ch = text[i];
      if (q) {
        if (ch === '"') { if (text[i + 1] === '"') { f += '"'; i++; } else q = false; }
        else f += ch;
      } else if (ch === '"') q = true;
      else if (ch === ',') { row.push(f); f = ''; }
      else if (ch === '\n' || ch === '\r') {
        if (ch === '\r' && text[i + 1] === '\n') i++;
        row.push(f); f = '';
        if (row.length > 1 || row[0] !== '') rows.push(row);
        row = [];
      } else f += ch;
    }
    if (f !== '' || row.length) { row.push(f); rows.push(row); }
    return rows;
  }

  // 支援 "01 Jan 2026 08:07:02 CST"、ISO（含時區）、"2026-01-01 08:07:02"（視為 defaultTzHours）
  function calendarTime(y, month, d, h, minute, second, fraction, tzMinutes) {
    if (![y, month, d, h, minute, second, tzMinutes].every(Number.isFinite) ||
        y < 100 || y > 9999 || month < 0 || month > 11 || d < 1 || d > 31 ||
        h < 0 || h > 23 || minute < 0 || minute > 59 || second < 0 || second > 59 ||
        Math.abs(tzMinutes) > 840 || !Number.isInteger(tzMinutes)) return null;
    const ms = fraction ? +(fraction + '000').slice(0, 3) : 0;
    const local = Date.UTC(y, month, d, h, minute, second, ms), check = new Date(local);
    // Date.UTC normalizes 30 February and 24:00; those are invalid evidence labels.
    if (check.getUTCFullYear() !== y || check.getUTCMonth() !== month || check.getUTCDate() !== d) return null;
    const result = local - tzMinutes * 60000;
    return Number.isFinite(result) ? result : null;
  }
  function parseTime(s, defaultTzHours) {
    s = String(s || '').trim();
    let m = /^(\d{1,2}) ([A-Za-z]{3}) (\d{4}) (\d{1,2}):(\d{2}):(\d{2})(?:\.(\d+))? ?([A-Za-z]{1,4})?$/.exec(s);
    if (m) {
      const tz = m[8] ? TZ[m[8].toUpperCase()] : defaultTzHours;
      if (tz == null || MON[m[2].toLowerCase()] == null) return null;
      return calendarTime(+m[3], MON[m[2].toLowerCase()], +m[1], +m[4], +m[5], +m[6], m[7], tz * 60);
    }
    m = /^(\d{4})[-/](\d{1,2})[-/](\d{1,2})[T ](\d{1,2}):(\d{2}):(\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:?\d{2})?$/.exec(s);
    if (m) {
      let tz = defaultTzHours * 60;
      if (m[8] === 'Z') tz = 0;
      else if (m[8]) {
        const g = /([+-])(\d{2}):?(\d{2})/.exec(m[8]);
        if (+g[3] > 59 || +g[2] > 14 || (+g[2] === 14 && +g[3] !== 0)) return null;
        tz = (g[1] === '-' ? -1 : 1) * (+g[2] * 60 + +g[3]);
      }
      return calendarTime(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6], m[7], tz);
    }
    return null;
  }

  function parseCsvText(text, defaultTzHours = 8) {
    const rows = splitCsv(text.replace(/^﻿/, ''));
    if (!rows.length) throw coreError('gatehouse.empty');
    const head = rows[0].map((h) => h.trim().toLowerCase());
    const idx = {};
    for (const [k, names] of Object.entries(COLS)) idx[k] = head.findIndex((h) => names.includes(h));
    for (const k of ['time', 'mmsi', 'lat', 'lon'])
      if (idx[k] < 0) throw coreError('gatehouse.column', { field: COLS[k][0], headers: rows[0].join(', ') });
    const tracks = Object.create(null), names = Object.create(null), seen = new Set();
    let skipped = 0, n = 0;
    const num = (r, k) => {
      if (idx[k] < 0 || r[idx[k]] == null) return null;
      const v = r[idx[k]].trim();
      if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(v)) return null;
      const n = Number(v); return Number.isFinite(n) ? n : null;
    };
    for (const r of rows.slice(1)) {
      const t = parseTime(r[idx.time], defaultTzHours), mmsi = String(r[idx.mmsi] || '').trim();
      const lat = num(r, 'lat'), lon = num(r, 'lon');
      if (!Number.isFinite(t) || !mmsi || lat == null || lon == null || Math.abs(lat) > 90 || Math.abs(lon) > 180) { skipped++; continue; }
      let hdg = num(r, 'hdg'), sog = num(r, 'sog'), cog = num(r, 'cog');
      if (hdg != null && (hdg < 0 || hdg >= 360)) hdg = null;
      if (sog != null && (sog < 0 || sog >= 102.3)) sog = null; // AIS 102.3 = speed unavailable.
      if (cog != null && (cog < 0 || cog >= 360)) cog = null;
      const key = mmsi + '|' + t + '|' + lat + '|' + lon;
      if (seen.has(key)) continue; // GateHouse 同一則訊息常由多個基地臺重複收錄
      seen.add(key);
      (tracks[mmsi] ||= []).push([t, lat, lon, sog, cog, hdg]);
      if (idx.name >= 0 && r[idx.name]) names[mmsi] = r[idx.name].trim();
      n++;
    }
    for (const k of Object.keys(tracks)) tracks[k].sort((a, b) => a[0] - b[0]);
    return { tracks, names, rows: n, skipped };
  }

  // 最小 ZIP 讀取（stored／deflate），取出所有 .csv
  async function unzipCsvs(buf) {
    const u8 = new Uint8Array(buf), dv = new DataView(buf.buffer || buf, u8.byteOffset, u8.byteLength);
    let eocd = -1;
    for (let i = u8.length - 22; i >= Math.max(0, u8.length - 65557); i--) if (dv.getUint32(i, true) === 0x06054b50) { eocd = i; break; }
    if (eocd < 0) throw coreError('gatehouse.zip');
    const count = dv.getUint16(eocd + 10, true);
    let p = dv.getUint32(eocd + 16, true);
    const out = [];
    for (let k = 0; k < count; k++) {
      if (dv.getUint32(p, true) !== 0x02014b50) throw coreError('gatehouse.directory');
      const method = dv.getUint16(p + 10, true), csize = dv.getUint32(p + 20, true);
      const nlen = dv.getUint16(p + 28, true), xlen = dv.getUint16(p + 30, true), clen = dv.getUint16(p + 32, true);
      const lho = dv.getUint32(p + 42, true);
      const name = new TextDecoder().decode(u8.subarray(p + 46, p + 46 + nlen));
      p += 46 + nlen + xlen + clen;
      if (!/\.csv$/i.test(name)) continue;
      const start = lho + 30 + dv.getUint16(lho + 26, true) + dv.getUint16(lho + 28, true);
      const data = u8.subarray(start, start + csize);
      let raw;
      if (method === 0) raw = data;
      else if (method === 8) raw = new Uint8Array(await new Response(new Blob([data]).stream().pipeThrough(new DecompressionStream('deflate-raw'))).arrayBuffer());
      else throw coreError('gatehouse.compression', { method });
      out.push({ name, text: new TextDecoder().decode(raw) });
    }
    return out;
  }

  function merge(results) {
    const tracks = Object.create(null), names = Object.create(null);
    let rows = 0, skipped = 0;
    for (const r of results) {
      rows += r.rows; skipped += r.skipped;
      Object.assign(names, r.names);
      for (const [m, arr] of Object.entries(r.tracks)) (tracks[m] ||= []).push(...arr);
    }
    let outliers = 0;
    for (const k of Object.keys(tracks)) {
      tracks[k].sort((a, b) => a[0] - b[0]);
      const arr = tracks[k].filter((x, i, a) => !i || x[0] !== a[i - 1][0] || x[1] !== a[i - 1][1] || x[2] !== a[i - 1][2]);
      tracks[k] = dropOutliers(arr);
      outliers += arr.length - tracks[k].length;
    }
    return { tracks, names, rows, skipped, outliers };
  }

  // 剔除明顯錯誤船位：離航跡中位數超過50公里，或無法串成合理航跡（10分鐘內相鄰保留點隱含速度超過60節）。
  // 保留能串成合理航跡的最多船位，而不是只和前一個保留點比，否則一個保留下來的錯誤點會連帶刪掉其後的正確航跡。
  function dropOutliers(arr) {
    if (arr.length < 3) return arr;
    const med = (i) => { const s = arr.map((r) => r[i]).sort((a, b) => a - b); return s[s.length >> 1]; };
    const mlat = med(1), mlon = med(2), kx = 111320 * Math.cos(mlat * Math.PI / 180);
    const dist = (a, b) => Math.hypot((a[1] - b[1]) * 111132, (a[2] - b[2]) * kx);
    const near = arr.filter((r) => dist(r, [0, mlat, mlon]) < 50000);
    const WINDOW = 600000, CHECKED = 200;
    const fits = (p, r) => dist(r, p) / Math.max(1, (r[0] - p[0]) / 1000) <= 31;
    // count[j]：以 j 結尾的最長合理航跡點數；早於 10 分鐘的點一律可接，只需記住其中最好的一個。
    const count = [], from = [];
    let older = -1, lo = 0, end = -1;
    for (let j = 0; j < near.length; j++) {
      for (; lo < j && near[j][0] - near[lo][0] >= WINDOW; lo++) if (older < 0 || count[lo] >= count[older]) older = lo;
      count[j] = older < 0 ? 1 : count[older] + 1; from[j] = older;
      for (let i = Math.max(lo, j - CHECKED); i < j; i++) {
        if (count[i] + 1 >= count[j] && fits(near[i], near[j])) { count[j] = count[i] + 1; from[j] = i; }
      }
      if (end < 0 || count[j] >= count[end]) end = j;
    }
    const out = [];
    for (let j = end; j >= 0; j = from[j]) out.push(near[j]);
    return out.reverse();
  }

  const api = { parseCsvText, parseTime, unzipCsvs, merge, splitCsv };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.AISCSV = api;
})(typeof self !== 'undefined' ? self : this);
