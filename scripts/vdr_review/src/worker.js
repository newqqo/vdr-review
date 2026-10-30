// 背景解碼：把一個聲道在時段內的各分鐘檔解碼、依檔名時間放到正確位置（缺漏處補靜音），並計算音量包絡。
self.onmessage = (e) => {
  const { ch, rate, codeSize, total, parts } = e.data;
  if (e.data.locale && self.VDRI18n) {
    try { self.VDRI18n.setLocale(e.data.locale); } catch (_) { /* locale errors must not change decoding */ }
  }
  const pcm = new Int16Array(total);
  let done = 0;
  for (const p of parts) {
    const s = G726.decode(new Uint8Array(p.payload), codeSize);
    const from = Math.max(0, -p.off), to = Math.min(s.length, total - p.off);
    if (to > from) pcm.set(s.subarray(from, to), p.off + from);
    if (++done % 4 === 0) self.postMessage({ type: 'prog', ch, done, n: parts.length });
  }
  const env = DSP.envelope(pcm, rate);
  self.postMessage({ type: 'done', ch, pcm: pcm.buffer, env: env.buffer }, [pcm.buffer, env.buffer]);
};
