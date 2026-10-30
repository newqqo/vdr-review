// Browser-only media helpers. No uploads, external decoder, or installed runtime.
(function (root) {
'use strict';
const I18N = typeof module !== 'undefined' && module.exports ? require('./i18n_core.js') : root.VDRI18n;
const ui = (key, params) => I18N.t('core.' + key, params);
function coreError(key, params = {}) {
  const error = new Error(ui(key, params));
  error.i18nKey = 'core.' + key; error.i18nParams = { ...params };
  return error;
}
function eventWindow(cover, day, clock, minutes, tz) {
  if (!Number.isFinite(tz) || tz < -12 * 3600 || tz > 14 * 3600) throw coreError('media.timezone');
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day || '')) throw coreError('media.dateRequired');
  const m = /^(\d{1,2})[:：](\d{2})(?::(\d{2}))?$/.exec((clock || '').trim());
  if (!m || +m[1] > 23 || +m[2] > 59 || +(m[3] || 0) > 59) throw coreError('media.clock');
  const t = ReviewState.parseDateTime(day + ' ' + m[1] + ':' + m[2] + ':' + (m[3] || '00'), tz);
  if (t == null) throw coreError('media.dateTime');
  const span = minutes * 60000;
  if (!Number.isFinite(span) || span <= 0 || span > 7200000) throw coreError('media.window');
  if (t < cover[0] || t >= cover[1]) throw coreError('media.outside');
  return [Math.max(cover[0], t - span), Math.min(cover[1], t + span)];
}
async function decodeAAC(parts, rate, total, onProgress) {
  const Ctx = root.OfflineAudioContext || root.webkitOfflineAudioContext;
  if (!Ctx) throw coreError('media.aacUnavailable');
  const ctx = new Ctx(1, 1, rate), pcm = new Int16Array(total), decoded = [];
  for (let k = 0; k < parts.length; k++) {
    const p = parts[k];
    let audio;
    try { audio = await ctx.decodeAudioData(p.payload.slice(0)); }
    catch (e) { throw coreError('media.aacDecode', { name: p.name }); }
    if (audio.numberOfChannels !== 1 || audio.sampleRate !== rate || Math.abs(audio.length - p.samples) > 1024)
      throw coreError('media.aacMismatch', { name: p.name });
    // At most one encoder-delay frame is allowed, but never silently time-stretch audio.
    const x = audio.getChannelData(0), n = Math.min(x.length, p.samples);
    const a = Math.max(0, -p.off), b = Math.min(n, total - p.off);
    for (let i = a; i < b; i++) pcm[p.off + i] = Math.round(Math.max(-1, Math.min(1, x[i])) * (x[i] < 0 ? 32768 : 32767));
    decoded.push({ file: p.name, samples: n, declared_samples: p.samples, offset: p.off, decoder_samples: audio.length });
    onProgress((k + 1) / parts.length);
  }
  return { pcm: pcm.buffer, env: DSP.envelope(pcm, rate).buffer, decoded };
}
function filter(x, rate, high, f) {
  const w = 2 * Math.PI * f / rate, cs = Math.cos(w), al = Math.sin(w) / (2 * Math.SQRT1_2);
  const b0 = (high ? 1 + cs : 1 - cs) / 2, b1 = high ? -(1 + cs) : 1 - cs;
  const a0 = 1 + al, a1 = -2 * cs, a2 = 1 - al;
  let x1 = 0, x2 = 0, y1 = 0, y2 = 0;
  for (let i = 0; i < x.length; i++) { const v = x[i], y = (b0 * v + b1 * x1 + b0 * x2 - a1 * y1 - a2 * y2) / a0; x2 = x1; x1 = v; y2 = y1; y1 = y; x[i] = y; }
}
function clipPCM(pcm, rate, start, end, enhanced) {
  const a = Math.max(0, Math.round(start * rate)), b = Math.min(pcm.length, Math.round(end * rate));
  if (!(b > a)) throw coreError('media.emptyClip');
  let out = pcm.slice(a, b), processing = { kind: 'decoded_original', sample_start: a, sample_end: b, gain: 1 };
  if (enhanced) {
    const x = Float32Array.from(out, v => v / 32768), lowpass = Math.min(3400, rate / 2 - 300);
    filter(x, rate, true, 300); filter(x, rate, true, 300); filter(x, rate, false, lowpass); filter(x, rate, false, lowpass);
    let peak = 0; for (const v of x) peak = Math.max(peak, Math.abs(v));
    const gain = peak > 0 ? Math.min(20, 0.9 / peak) : 1;
    out = Int16Array.from(x, v => Math.round(Math.max(-1, Math.min(1, v * gain)) * 32767));
    processing = { ...processing, kind: 'enhanced_listening_only', highpass_hz: 300, lowpass_hz: lowpass, filter: 'RBJ Butterworth Q=0.7071, two cascaded biquads each', gain, peak_target: 0.9, max_gain: 20 };
  }
  return { pcm: out, processing, start: a / rate, end: b / rate };
}
// This describes the existing chunked listening transform; it does not alter
// PCM or recalculate gains. Segment sample bounds are absolute in the output,
// unlike clipPCM's bounds, which are local to the input chunk.
function enhancedProcessing(rate, sourceStartUTC, totalSamples, chunks) {
  if (!Number.isSafeInteger(rate) || rate <= 0 || !Number.isFinite(sourceStartUTC) ||
      !Number.isSafeInteger(totalSamples) || totalSamples <= 0 || !Array.isArray(chunks))
    throw new TypeError('Invalid enhanced-audio processing metadata');
  let next = 0;
  const segments = chunks.map(chunk => {
    const { start, end, processing: p } = chunk;
    if (start !== next || !Number.isSafeInteger(end) || end <= start || end > totalSamples ||
        end - start > rate * 60 || !p || p.kind !== 'enhanced_listening_only' ||
        p.sample_start !== 0 || p.sample_end !== end - start || !Number.isFinite(p.gain) || p.gain <= 0)
      throw new TypeError('Invalid enhanced-audio processing segment');
    next = end;
    const { sample_start, sample_end, ...recipe } = p;
    return { ...recipe,
      output_sample_start: start, output_sample_end: end,
      output_start_s: start / rate, output_end_s: end / rate,
      source_start_utc: new Date(sourceStartUTC + start / rate * 1000).toISOString(),
      source_end_utc: new Date(sourceStartUTC + end / rate * 1000).toISOString(),
      input_sample_start: sample_start, input_sample_end: sample_end,
      filter_q: Math.SQRT1_2,
      filter_state_reset: { applies_to: 'each of the four biquad stages', x1: 0, x2: 0, y1: 0, y2: 0 } };
  });
  if (next !== totalSamples) throw new TypeError('Incomplete enhanced-audio processing segments');
  return { kind: 'enhanced_listening_only', algorithm_version: 'vdr-enhanced-listening-v1',
    sample_rate: rate, total_samples: totalSamples, segment_max_samples: rate * 60,
    input_scaling: 'int16 / 32768', filter_storage: 'Float32Array after each biquad stage sample',
    gain_policy: 'per segment: peak > 0 ? min(max_gain, peak_target / peak) : 1; peak measured after all filters',
    output_scaling: 'Math.round(clamp(filtered_sample * gain, -1, 1) * 32767)',
    segments, note: '逐60秒區塊濾波／增益；每段重設濾波狀態，邊界可能不同於整段濾波，僅供核聽' };
}
const api = { eventWindow, decodeAAC, clipPCM, enhancedProcessing };
root.VDRMedia = api;
if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
