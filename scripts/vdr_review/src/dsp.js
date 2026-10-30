// 音訊工具：WAV 檔頭、語音頻段音量包絡（供波形顯示與依波形拆句）、有聲段偵測、船體幾何。
(function (root) {
  'use strict';
  const ENV_RATE = 20; // 包絡每秒20格（50毫秒）

  function wavHeader(samples, rate) {
    const h = new DataView(new ArrayBuffer(44));
    const w = (o, s) => { for (let k = 0; k < 4; k++) h.setUint8(o + k, s.charCodeAt(k)); };
    w(0, 'RIFF'); h.setUint32(4, 36 + samples * 2, true); w(8, 'WAVE'); w(12, 'fmt ');
    h.setUint32(16, 16, true); h.setUint16(20, 1, true); h.setUint16(22, 1, true); h.setUint32(24, rate, true);
    h.setUint32(28, rate * 2, true); h.setUint16(32, 2, true); h.setUint16(34, 16, true); w(36, 'data'); h.setUint32(40, samples * 2, true);
    return new Uint8Array(h.buffer);
  }

  function biquad(x, rate, type, f0) { // RBJ cookbook，Q=0.7071，就地濾波
    const w0 = 2 * Math.PI * f0 / rate, cs = Math.cos(w0), al = Math.sin(w0) / (2 * Math.SQRT1_2);
    let b0, b1, b2;
    if (type === 'hp') { b0 = (1 + cs) / 2; b1 = -(1 + cs); b2 = b0; } else { b0 = (1 - cs) / 2; b1 = 1 - cs; b2 = b0; }
    const a0 = 1 + al, a1 = -2 * cs, a2 = 1 - al;
    let x1 = 0, x2 = 0, y1 = 0, y2 = 0;
    for (let i = 0; i < x.length; i++) {
      const v = x[i], y = (b0 * v + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2) / a0;
      x2 = x1; x1 = v; y2 = y1; y1 = y; x[i] = y;
    }
  }

  // 300–3400 Hz 能量相對於20秒滾動第15百分位底噪的分貝數，映射為0–255（(dB−底噪−2)/22）
  function envelope(pcm, rate) {
    const hop = Math.round(rate / ENV_RATE), n = Math.floor(pcm.length / hop);
    const x = new Float32Array(pcm.length);
    for (let i = 0; i < pcm.length; i++) x[i] = pcm[i] / 32768;
    biquad(x, rate, 'hp', 300); biquad(x, rate, 'hp', 300);
    const lp = Math.min(3400, rate / 2 - 300);
    biquad(x, rate, 'lp', lp); biquad(x, rate, 'lp', lp);
    const db = new Float32Array(n);
    for (let k = 0; k < n; k++) {
      let e = 0;
      for (let i = k * hop, j = i + hop; i < j; i++) e += x[i] * x[i];
      db[k] = 10 * Math.log10(e / hop + 1e-10);
    }
    const W = 10 * ENV_RATE, step = ENV_RATE, floorAt = [];
    for (let k = 0; k < n + step; k += step) {
      const s = db.slice(Math.max(0, k - W), Math.min(n, k + W)).sort();
      floorAt.push(s.length ? s[Math.floor(s.length * 0.15)] : -100);
    }
    const out = new Uint8Array(n);
    for (let k = 0; k < n; k++) {
      const a = Math.floor(k / step), f = k / step - a;
      const fl = floorAt[a] * (1 - f) + (floorAt[Math.min(a + 1, floorAt.length - 1)]) * f;
      const v = (db[k] - fl - 2) / 22;
      out[k] = v <= 0 ? 0 : v >= 1 ? 255 : Math.round(v * 255);
    }
    return out;
  }

  // 有聲段（例：VHF 開口）：包絡超過門檻，已知空白小於 gap 秒合併，短於 minDur 捨棄。
  // known 可省略以保留舊行為；提供時，未分析／缺資料格一律斷開，不能當成安靜跨過。
  function segments(env, th = 64, gap = 1.5, minDur = 0.5, known) {
    const segs = [];
    let st = -1, last = -1;
    for (let k = 0; k <= env.length; k++) {
      const unknown = k < env.length && known != null && !known[k];
      const on = k < env.length && !unknown && env[k] > th;
      if (on) { if (st < 0) st = k; last = k; }
      else if (st >= 0 && (unknown || k === env.length || (k - last) / ENV_RATE > gap)) {
        if ((last + 1 - st) / ENV_RATE >= minDur) segs.push([st / ENV_RATE, (last + 1) / ENV_RATE]);
        st = -1;
      }
    }
    return segs;
  }

  // ---- 船體幾何（公尺；x 東、y 北；艏向自北順時針）----
  function hullXY(x0, y0, hdg, s) {
    const A = s.A, B = s.B, C = s.C, D = s.D, L = A + B, h = hdg * Math.PI / 180;
    const pts = [[-B, -C], [A - 0.12 * L, -C], [A, (D - C) / 2], [A - 0.12 * L, D], [-B, D]];
    return pts.map(([f, st]) => [x0 + f * Math.sin(h) + st * Math.cos(h), y0 + f * Math.cos(h) - st * Math.sin(h)]);
  }
  function ptSeg(p, a, b) {
    const vx = b[0] - a[0], vy = b[1] - a[1], L2 = vx * vx + vy * vy;
    const t = L2 ? Math.max(0, Math.min(1, ((p[0] - a[0]) * vx + (p[1] - a[1]) * vy) / L2)) : 0;
    return Math.hypot(p[0] - a[0] - t * vx, p[1] - a[1] - t * vy);
  }
  const orient = (a, b, c) => (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
  function inside(p, poly) {
    let c = false;
    for (let k = 0; k < poly.length; k++) {
      const [x1, y1] = poly[k], [x2, y2] = poly[(k + 1) % poly.length];
      if ((y1 > p[1]) !== (y2 > p[1]) && p[0] < (x2 - x1) * (p[1] - y1) / (y2 - y1) + x1) c = !c;
    }
    return c;
  }
  function polyDist(P, Q) {
    if (P.some((p) => inside(p, Q)) || Q.some((q) => inside(q, P))) return 0;
    let m = Infinity;
    for (let i = 0; i < P.length; i++) for (let j = 0; j < Q.length; j++) {
      const p = P[i], q = P[(i + 1) % P.length], a = Q[j], b = Q[(j + 1) % Q.length];
      if (orient(p, q, a) * orient(p, q, b) < 0 && orient(a, b, p) * orient(a, b, q) < 0) return 0;
      m = Math.min(m, ptSeg(p, a, b), ptSeg(q, a, b), ptSeg(a, p, q), ptSeg(b, p, q));
    }
    return m;
  }

  // 依時間內插 [t,lat,lon,...] 序列；前後兩點相隔超過 maxGap 毫秒則不內插
  function interp(arr, t, maxGap) {
    if (!arr || !arr.length || t < arr[0][0] || t > arr[arr.length - 1][0]) return null;
    let lo = 0, hi = arr.length - 1;
    while (hi - lo > 1) { const m = (lo + hi) >> 1; if (arr[m][0] <= t) lo = m; else hi = m; }
    const a = arr[lo], b = arr[hi];
    if (b[0] - a[0] > maxGap) return null;
    const w = b[0] === a[0] ? 0 : (t - a[0]) / (b[0] - a[0]);
    return [a[1] + w * (b[1] - a[1]), a[2] + w * (b[2] - a[2]), w < 0.5 ? a : b];
  }

  const api = { ENV_RATE, wavHeader, envelope, segments, hullXY, polyDist, interp };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.DSP = api;
})(typeof self !== 'undefined' ? self : this);
