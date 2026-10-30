/*
 * G.726 ADPCM 解碼（3-bit／24 kbit/s 為主，亦支援 2–5 bit），big-endian 位元排列。
 * 移植自 FFmpeg libavcodec/g726.c（Copyright (c) 2004 Roman Shaposhnik），
 * 依 GNU LGPL 2.1 或更新版本授權；本檔亦以相同授權提供。
 * 移植目的：與既有 ffmpeg「-f g726 -code_size N」解碼結果逐樣本一致（含 int16 截位行為）。
 */
(function (root) {
  'use strict';
  const MIN16 = -32768;
  const TABLES = {
    2: { iq: [116, 365, 365, 116], W: [-22, 439, 439, -22], F: [0, 7, 7, 0] },
    3: { iq: [MIN16, 135, 273, 373, 373, 273, 135, MIN16], W: [-4, 30, 137, 582, 582, 137, 30, -4], F: [0, 1, 2, 7, 7, 2, 1, 0] },
    4: { iq: [MIN16, 4, 135, 213, 273, 323, 373, 425, 425, 373, 323, 273, 213, 135, 4, MIN16],
         W: [-12, 18, 41, 64, 112, 198, 355, 1122, 1122, 355, 198, 112, 64, 41, 18, -12],
         F: [0, 0, 0, 1, 1, 1, 3, 7, 7, 3, 1, 1, 1, 0, 0, 0] },
    5: { iq: [MIN16, -66, 28, 104, 169, 224, 274, 318, 358, 395, 429, 459, 488, 514, 539, 566,
              566, 539, 514, 488, 459, 429, 395, 358, 318, 274, 224, 169, 104, 28, -66, MIN16],
         W: [14, 14, 24, 39, 40, 41, 58, 100, 141, 179, 219, 280, 358, 440, 529, 696,
             696, 529, 440, 358, 280, 219, 179, 141, 100, 58, 41, 40, 39, 24, 14, 14],
         F: [0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 2, 3, 4, 5, 6, 6, 6, 6, 5, 4, 3, 2, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0] },
  };
  const s16 = (v) => (v << 16) >> 16;
  const log2_16 = (v) => { v &= 0xFFFF; let n = 0; while (v > 1) { v >>= 1; n++; } return n; };
  const clip = (v, lo, hi) => (v < lo ? lo : v > hi ? hi : v);
  const sgn = (v) => (v < 0 ? -1 : 1);

  function i2f(i, f) {
    f.sign = i < 0 ? 1 : 0;
    if (f.sign) i = -i;
    f.exp = (log2_16(i) + (i ? 1 : 0)) & 0xFF;
    f.mant = (i ? (i << 6) >> f.exp : 32) & 0xFF;
    return f;
  }

  function mult(f1, f2) {
    const exp = f1.exp + f2.exp;
    let res = ((f1.mant * f2.mant) + 0x30) >> 4;
    res = exp > 19 ? res << (exp - 19) : res >> (19 - exp);
    return s16((f1.sign ^ f2.sign) ? -res : res);
  }

  function newContext(codeSize) {
    const fl = () => ({ sign: 0, exp: 0, mant: 32 });
    return { cs: codeSize, t: TABLES[codeSize], sr: [fl(), fl()], dq: [fl(), fl(), fl(), fl(), fl(), fl()],
             a: [0, 0], b: [0, 0, 0, 0, 0, 0], pk: [1, 1], ap: 0, yu: 544, yl: 34816, dms: 0, dml: 0, td: 0,
             se: 0, sez: 0, y: 544 };
  }

  function inverseQuant(c, i) {
    const dql = c.t.iq[i] + (c.y >> 2);
    const dex = (dql >> 7) & 0xF, dqt = (1 << 7) + (dql & 0x7F);
    return s16(dql < 0 ? 0 : ((dqt << dex) >> 7));
  }

  const tmp = { sign: 0, exp: 0, mant: 0 };
  function decodeOne(c, I) {
    const Isig = I >> (c.cs - 1);
    let dq = inverseQuant(c, I);
    const ylint = c.yl >> 15, ylfrac = (c.yl >> 10) & 0x1F;
    const thr2 = ylint > 9 ? 0x1F << 10 : (0x20 + ylfrac) << ylint;
    const tr = c.td === 1 && dq > ((3 * thr2) >> 2);
    if (Isig) dq = -dq;
    const re = s16(c.se + dq);
    const pk0 = (c.sez + dq) ? sgn(c.sez + dq) : 0;
    const dq0 = dq ? sgn(dq) : 0;
    if (tr) {
      c.a[0] = c.a[1] = 0;
      for (let i = 0; i < 6; i++) c.b[i] = 0;
    } else {
      const fa1 = clip((-c.a[0] * c.pk[0] * pk0) >> 5, -256, 255);
      c.a[1] += 128 * pk0 * c.pk[1] + fa1 - (c.a[1] >> 7);
      c.a[1] = clip(c.a[1], -12288, 12288);
      c.a[0] += 64 * 3 * pk0 * c.pk[0] - (c.a[0] >> 8);
      c.a[0] = clip(c.a[0], -(15360 - c.a[1]), 15360 - c.a[1]);
      for (let i = 0; i < 6; i++) c.b[i] += 128 * dq0 * sgn(-c.dq[i].sign) - (c.b[i] >> 8);
    }
    c.pk[1] = c.pk[0];
    c.pk[0] = pk0 ? pk0 : 1;
    const sr1 = c.sr[1]; c.sr[1] = c.sr[0]; c.sr[0] = i2f(re, sr1);
    const last = c.dq[5];
    for (let i = 5; i > 0; i--) c.dq[i] = c.dq[i - 1];
    c.dq[0] = i2f(dq, last);
    c.dq[0].sign = Isig;
    c.td = c.a[1] < -11776 ? 1 : 0;
    const F = c.t.F[I];
    c.dms += (F << 4) + ((-c.dms) >> 5);
    c.dml += (F << 4) + ((-c.dml) >> 7);
    if (tr) c.ap = 256;
    else {
      c.ap += (-c.ap) >> 4;
      if (c.y <= 1535 || c.td || Math.abs((c.dms << 2) - c.dml) >= (c.dml >> 3)) c.ap += 0x20;
    }
    c.yu = clip(c.y + c.t.W[I] + ((-c.y) >> 5), 544, 5120);
    c.yl += c.yu + ((-c.yl) >> 6);
    const al = c.ap >= 256 ? 1 << 6 : c.ap >> 2;
    c.y = (c.yl + (c.yu - (c.yl >> 6)) * al) >> 6;
    let se = 0;
    for (let i = 0; i < 6; i++) se += mult(i2f(c.b[i] >> 2, tmp), c.dq[i]);
    c.sez = se >> 1;
    for (let i = 0; i < 2; i++) se += mult(i2f(c.a[i] >> 2, tmp), c.sr[i]);
    c.se = se >> 1;
    return s16(clip(re * 4, -0xFFFF, 0xFFFF));
  }

  // bytes：Uint8Array；碼字依 big-endian（高位先）連續排列。每次呼叫重設解碼器（JCY 每分鐘檔各自獨立）。
  function decode(bytes, codeSize) {
    const cs = codeSize || 3;
    const n = Math.floor(bytes.length * 8 / cs);
    const out = new Int16Array(n);
    const c = newContext(cs);
    const mask = (1 << cs) - 1;
    let acc = 0, bits = 0, p = 0;
    for (let k = 0; k < n; k++) {
      while (bits < cs) { acc = ((acc << 8) | bytes[p++]) & 0xFFFF; bits += 8; }
      bits -= cs;
      out[k] = decodeOne(c, (acc >> bits) & mask);
    }
    return out;
  }

  const api = { decode };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.G726 = api;
})(typeof self !== 'undefined' ? self : this);
