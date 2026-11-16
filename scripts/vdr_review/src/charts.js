// 海圖圖層：讀取使用者自選的 Shapefile ZIP（.shp＋.dbf，經緯度座標），在本機轉成地圖上的點、線、面。
// 圖資只在這台電腦讀取，不放進工具、不上傳；僅供參考，不是航行用海圖。
(function (root) {
  'use strict';
  const node = typeof module !== 'undefined' && module.exports;
  const I18N = node ? require('./i18n_core.js') : root.VDRI18n;
  const ZIP = node ? require('./gatehouse.js') : root.AISCSV;
  const ui = (key, params) => I18N.t('core.' + key, params);
  function coreError(key, params = {}) {
    const error = new Error(ui(key, params));
    error.i18nKey = 'core.' + key; error.i18nParams = { ...params };
    return error;
  }

  const MAX_POINTS = 3000000; // total vertices kept for drawing; a whole national chart set is far below this
  const KINDS = { light: 'lights', lights: 'lights', wrecks: 'wrecks', wreck: 'wrecks', depcnt: 'depth', depare: 'depth', channel: 'channels', fairwy: 'channels' };
  const GEOMETRY = { 1: 'point', 11: 'point', 21: 'point', 8: 'point', 18: 'point', 28: 'point',
                     3: 'line', 13: 'line', 23: 'line', 5: 'polygon', 15: 'polygon', 25: 'polygon' };

  // Shapefile main file: a 100-byte header, then records of (number, length) big-endian and the shape little-endian.
  function readShp(u8, name) {
    const dv = new DataView(u8.buffer, u8.byteOffset, u8.byteLength);
    if (u8.length < 100 || dv.getInt32(0, false) !== 9994) throw coreError('charts.shp', { name });
    const type = dv.getInt32(32, true), geometry = GEOMETRY[type];
    if (!geometry && type !== 0) throw coreError('charts.shapeType', { name, type });
    const shapes = [];
    let p = 100, points = 0;
    const coord = (at) => {
      const lon = dv.getFloat64(at, true), lat = dv.getFloat64(at + 8, true);
      // Projected metres (for example TWD97 TM2) are far outside these ranges.
      if (!(Math.abs(lat) <= 90 && Math.abs(lon) <= 180)) throw coreError('charts.projection', { name });
      return [lat, lon];
    };
    while (p + 8 <= u8.length) {
      const length = dv.getInt32(p + 4, false) * 2, body = p + 8, end = body + length;
      if (length < 4 || end > u8.length) throw coreError('charts.shp', { name });
      const shape = dv.getInt32(body, true);
      let g = null, n = 0;
      if (shape === 1 || shape === 11 || shape === 21) { if (length >= 20) { g = coord(body + 4); n = 1; } }
      else if (shape === 8 || shape === 18 || shape === 28) {
        n = dv.getInt32(body + 36, true);
        if (n < 0 || body + 40 + n * 16 > end) throw coreError('charts.shp', { name });
        g = []; for (let i = 0; i < n; i++) g.push(coord(body + 40 + i * 16));
      } else if (shape !== 0) {
        const parts = dv.getInt32(body + 36, true), at = body + 44 + parts * 4;
        n = dv.getInt32(body + 40, true);
        if (parts < 0 || n < 0 || at + n * 16 > end) throw coreError('charts.shp', { name });
        g = [];
        for (let k = 0; k < parts; k++) {
          const a = dv.getInt32(body + 44 + k * 4, true), b = k + 1 < parts ? dv.getInt32(body + 48 + k * 4, true) : n;
          if (a < 0 || b > n || a > b) throw coreError('charts.shp', { name });
          const ring = []; for (let i = a; i < b; i++) ring.push(coord(at + i * 16));
          if (ring.length) g.push(ring);
        }
      }
      points += n;
      if (points > MAX_POINTS) throw coreError('charts.tooLarge', { name });
      shapes.push(g);
      p = end;
    }
    return { geometry: geometry || 'point', shapes, points };
  }

  function decoderFor(cpg, sample) {
    const label = String(cpg || '').trim().toLowerCase().replace(/^cp/, '');
    const name = { 'utf-8': 'utf-8', utf8: 'utf-8', 65001: 'utf-8', 950: 'big5', big5: 'big5', 936: 'gbk', gbk: 'gbk', gb2312: 'gbk', 932: 'shift_jis' }[label];
    if (name) return new TextDecoder(name);
    if (label) { try { return new TextDecoder(label); } catch (_) { /* unknown label: guess below */ } }
    // No code page: use UTF-8 when the text is valid UTF-8, otherwise Big5 (common in older Taiwanese exports).
    try { new TextDecoder('utf-8', { fatal: true }).decode(sample); return new TextDecoder('utf-8'); } catch (_) { return new TextDecoder('big5'); }
  }

  // dBASE table: one row per shape, in the same order. Deleted rows stay as null so the order still matches.
  function readDbf(u8, cpg, name) {
    const dv = new DataView(u8.buffer, u8.byteOffset, u8.byteLength);
    if (u8.length < 33) throw coreError('charts.dbf', { name });
    const count = dv.getUint32(4, true), header = dv.getUint16(8, true), size = dv.getUint16(10, true);
    const fields = [];
    for (let p = 32; p + 32 <= header && u8[p] !== 0x0D; p += 32) {
      fields.push({ name: new TextDecoder('ascii').decode(u8.subarray(p, p + 11)).replace(/\0.*$/, ''), type: String.fromCharCode(u8[p + 11]), length: u8[p + 16] });
    }
    if (header + count * size > u8.length + 1 || fields.reduce((a, f) => a + f.length, 1) > size) throw coreError('charts.dbf', { name });
    const decode = decoderFor(cpg, u8.subarray(header, Math.min(u8.length, header + size * 50))), rows = [];
    for (let r = 0; r < count; r++) {
      const at = header + r * size;
      if (u8[at] === 0x2A) { rows.push(null); continue; }
      const row = Object.create(null);
      let q = at + 1;
      for (const f of fields) {
        const text = decode.decode(u8.subarray(q, q + f.length)).replace(/\0/g, '').trim(); q += f.length;
        if (!text) continue;
        if (f.type === 'N' || f.type === 'F') { const v = Number(text); row[f.name] = Number.isFinite(v) ? v : text; }
        else row[f.name] = text;
      }
      rows.push(row);
    }
    return rows;
  }

  // Data date from the file name: 7 digits are a Republic of China date (1150918 = 2026-09-18), 8 digits Gregorian.
  function dataDate(stem) {
    const m = /_(\d{7,8})$/.exec(stem);
    if (!m) return null;
    const s = m[1], y = s.length === 7 ? +s.slice(0, 3) + 1911 : +s.slice(0, 4), md = s.slice(-4);
    const iso = `${y}-${md.slice(0, 2)}-${md.slice(2)}`, t = Date.parse(iso + 'T00:00:00Z');
    return Number.isFinite(t) && new Date(t).toISOString().slice(0, 10) === iso ? iso : null;
  }

  function kindOf(stem) {
    const base = stem.split('/').pop().split('_')[0].toLowerCase();
    return KINDS[base] || 'other';
  }

  async function parseChartZip(buf, fileName = '') {
    const entries = await ZIP.unzipEntries(buf, (n) => /\.(shp|dbf|cpg|prj|kml|kmz)$/i.test(n));
    const byStem = new Map();
    for (const e of entries) {
      const m = /^(.*)\.([a-z]+)$/i.exec(e.name), key = m[1].toLowerCase();
      if (!byStem.has(key)) byStem.set(key, { stem: m[1] });
      byStem.get(key)[m[2].toLowerCase()] = e.bytes;
    }
    const groups = [...byStem.values()].filter((g) => g.shp);
    if (!groups.length) throw coreError([...byStem.values()].some((g) => g.kml || g.kmz) ? 'charts.useShp' : 'charts.noShapefile', { name: fileName });
    const layers = [];
    for (const g of groups.sort((a, b) => a.stem.localeCompare(b.stem))) {
      const name = g.stem.split('/').pop();
      if (g.prj && /PROJCS\[/i.test(new TextDecoder().decode(g.prj))) throw coreError('charts.projection', { name });
      const shp = readShp(g.shp, name);
      const rows = g.dbf ? readDbf(g.dbf, g.cpg && new TextDecoder().decode(g.cpg), name) : [];
      const features = [];
      shp.shapes.forEach((shape, i) => { if (shape && rows[i] !== null) features.push({ g: shape, p: rows[i] || {} }); });
      layers.push({ id: fileName + '/' + g.stem, name, kind: kindOf(g.stem), geometry: shp.geometry,
                    features, points: shp.points, date: dataDate(name) });
    }
    return layers;
  }

  const api = { parseChartZip, readShp, readDbf, dataDate, kindOf };
  if (node) module.exports = api;
  else root.VDRCharts = api;
})(typeof self !== 'undefined' ? self : this);
