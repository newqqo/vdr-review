// Long recordings are indexed, not expanded into one PCM buffer per channel.
// Only short reads and a bounded record LRU hold decoded audio in memory.
(function (root) {
'use strict';
const I18N = typeof module !== 'undefined' && module.exports ? require('./i18n_core.js') : root.VDRI18n;
const ui = (key, params) => I18N.t('core.' + key, params);
function coreError(key, params = {}) {
  const error = new Error(ui(key, params));
  error.i18nKey = 'core.' + key; error.i18nParams = { ...params };
  return error;
}
const MAX_RANGE_SECONDS = 120;
const DEFAULT_CACHE_BYTES = 32 * 1024 * 1024;
const AAC_FRAME = 1024, STAMP_STEP_S = 0.01;

function coverage(intervals, duration) {
  const iv = intervals.map(([a, b]) => [Math.max(0, a), Math.min(duration, b)])
    .filter(([a, b]) => b > a).sort((a, b) => a[0] - b[0]);
  const gaps = [], overlaps = [];
  let end = 0;
  for (const [a, b] of iv) {
    if (a - end > 0.000001) gaps.push([end, a]);
    if (end - a > 0.000001) overlaps.push([a, Math.min(end, b)]);
    end = Math.max(end, b);
  }
  if (duration - end > 0.000001) gaps.push([end, duration]);
  return { gaps, overlaps };
}
async function digest(buffer) {
  if (root.VDRProject?.sha256) return root.VDRProject.sha256(buffer);
  if (!root.crypto?.subtle) throw coreError('stream.shaUnavailable');
  const hash = await root.crypto.subtle.digest('SHA-256', buffer);
  return Array.from(new Uint8Array(hash), b => b.toString(16).padStart(2, '0')).join('');
}

function createStore({ audio, W0, W1, decodeG726, onUpdate = () => {}, maxCacheBytes = DEFAULT_CACHE_BYTES }) {
  const duration = (W1 - W0) / 1000;
  if (!Number.isFinite(duration) || duration <= 0) throw coreError('stream.time');
  if (!Number.isSafeInteger(maxCacheBytes) || maxCacheBytes < 0) throw coreError('stream.cache');
  const channels = {}, records = {}, envOwners = {}, cache = new Map();
  let alive = true, queue = Promise.resolve(), cacheBytes = 0, peakCacheBytes = 0;
  let decodedRecords = 0, cacheHits = 0, maxReadSeconds = 0, activeDecoders = 0, peakActiveDecoders = 0;
  let envelopeRequests = 0, envelopeDecodedRecords = 0, envelopeReusedRecords = 0;
  let envelopeCancelledRequests = 0, activeEnvelopeRequests = 0;
  const envRate = root.DSP.ENV_RATE;
  for (const ch of Object.keys(audio).sort()) {
    const files = audio[ch].filter(f => f.start < W1 && f.start + f.duration * 1000 > W0)
      .slice().sort((a, b) => a.start - b.start || a.path.localeCompare(b.path));
    if (!files.length) continue;
    const { rate, codec } = files[0];
    if (!Number.isInteger(rate) || rate <= 0 || !['aac', 'g726'].includes(codec)
        || files.some(f => f.rate !== rate || f.codec !== codec)) throw coreError('stream.mixedFormat', { channel: ch });
    if (files.some(f => !Number.isFinite(f.duration) || f.duration <= 0 || f.duration > MAX_RANGE_SECONDS))
      throw coreError('stream.longRecord', { channel: ch });
    const sources = files.map(f => ({ file: f.path, bytes: f.size ?? f.file.size,
      start_utc: new Date(f.start).toISOString(), codec, sample_rate: rate,
      samples: Math.round(f.duration * rate), duration_s: f.duration, sha256: f.sha256,
      verified: false, decoded: false }));
    records[ch] = files.map((f, i) => ({ f, source: sources[i], key: ch + ':' + i,
      offset: Math.round((f.start - W0) / 1000 * rate), samples: Math.round(f.duration * rate),
      status: 'pending', receipt: null, rank: i + 1, lead: 0 }));
    // An AAC record can repeat the previous record's last whole frames so it decodes on its own; the
    // first repeated frame then decodes without history and is distorted. When the stamps (10 ms
    // resolution) put a record that many frames inside the previous one, place it right after the
    // previous record's own samples and play the repeated audio from the previous record.
    if (codec === 'aac') for (let i = 1; i < records[ch].length; i++) {
      const prev = records[ch][i - 1], r = records[ch][i], overlap = prev.offset + prev.samples - r.offset;
      const frames = Math.round(overlap / AAC_FRAME);
      if (frames >= 1 && frames <= 4 && Math.abs(overlap - frames * AAC_FRAME) <= Math.ceil(rate * STAMP_STEP_S)) {
        r.offset = prev.offset + prev.samples - frames * AAC_FRAME; r.lead = frames * AAC_FRAME;
        sources[i].lead_duplicate_samples = r.lead;
      }
    }
    channels[ch] = { rate, codec, env: new Uint8Array(Math.ceil(duration * envRate)),
      envKnown: new Uint8Array(Math.ceil(duration * envRate)), sources, decoder: [],
      qa: { gaps: [], overlaps: [], bad: [], indexedOnly: true, pending_records: files.length } };
    envOwners[ch] = new Uint32Array(channels[ch].env.length);
    refreshQA(ch);
  }
  if (!Object.keys(channels).length) throw coreError('stream.noAudio');
  function assertAlive() { if (!alive) throw coreError('stream.closed'); }
  function notify(ch) { if (alive) onUpdate(ch, channels[ch]); }
  // Repeated lead samples come from the previous record, unless that record could not be used.
  function leadOf(ch, r) { return r.lead && records[ch][r.rank - 2]?.status !== 'bad' ? r.lead : 0; }
  function refreshQA(ch) {
    const c = channels[ch], list = records[ch];
    const iv = list.filter(r => r.status !== 'bad').map(r => [(r.offset + leadOf(ch, r)) / c.rate, (r.offset + r.samples) / c.rate]);
    const qa = coverage(iv, duration), last = list[list.length - 1], tail = qa.gaps[qa.gaps.length - 1];
    // A record placed after its repeated frames can end up to one stamp step (10 ms) before the window
    // end, which was computed from its rounded stamp. That is rounding, not missing audio.
    if (last.lead && tail && duration - tail[1] < 1e-9 && tail[1] - tail[0] <= STAMP_STEP_S) qa.gaps.pop();
    Object.assign(c.qa, qa, {
      indexedOnly: list.some(r => r.status === 'pending'),
      pending_records: list.filter(r => r.status === 'pending').length,
      repeated_lead_records: list.filter(r => leadOf(ch, r)).length
    });
  }
  function updateEnvelope(ch, r, decoded) {
    const c = channels[ch], lead = leadOf(ch, r), pcm = decoded.subarray(lead), env = root.DSP.envelope(pcm, c.rate);
    // Seeks can decode records out of order. Index rank preserves the same
    // last-record-wins overlap rule even after an earlier record is re-decoded.
    const start = Math.round((r.offset + lead) / c.rate * envRate);
    for (let k = 0; k < env.length; k++) {
      const p = start + k;
      if (p >= 0 && p < c.env.length && r.rank >= envOwners[ch][p]) {
        c.env[p] = env[k]; c.envKnown[p] = 1; envOwners[ch][p] = r.rank;
      }
    }
  }
  function keep(key, pcm) {
    if (pcm.byteLength > maxCacheBytes) return;
    while (cacheBytes + pcm.byteLength > maxCacheBytes && cache.size) {
      const oldest = cache.keys().next().value;
      cacheBytes -= cache.get(oldest).byteLength; cache.delete(oldest);
    }
    cache.set(key, pcm); cacheBytes += pcm.byteLength;
    peakCacheBytes = Math.max(peakCacheBytes, cacheBytes);
  }
  async function decode(ch, r) {
    assertAlive();
    if (r.status === 'bad') return null;
    if (cache.has(r.key)) {
      const pcm = cache.get(r.key); cache.delete(r.key); cache.set(r.key, pcm); cacheHits++;
      return pcm;
    }
    const c = channels[ch], buffer = await r.f.file.arrayBuffer(); assertAlive();
    const hash = await digest(buffer); assertAlive();
    if (!r.f.sha256 || hash.toLowerCase() !== r.f.sha256.toLowerCase()) {
      throw coreError('stream.hashMismatch', { path: r.f.path });
    }
    let rec;
    try {
      rec = root.VDR.readAudioRecord(new Uint8Array(buffer), ch);
      if (rec.rate !== c.rate || rec.codec !== c.codec || (rec.codec === 'aac' && rec.start !== r.f.start)
          || Math.abs(rec.duration - r.f.duration) > 1 / c.rate) throw coreError('stream.indexMismatch');
    } catch (e) {
      r.status = 'bad'; r.source.verified = true;
      c.qa.bad.push({ file: r.f.path, start_utc: r.source.start_utc, reason: e.message });
      refreshQA(ch); notify(ch); return null;
    }
    const part = { off: 0, payload: rec.payload.slice().buffer, samples: rec.samples, name: r.f.path };
    activeDecoders++; peakActiveDecoders = Math.max(peakActiveDecoders, activeDecoders);
    let result;
    try {
      if (rec.codec === 'aac') result = await root.VDRMedia.decodeAAC([part], c.rate, rec.samples, () => {});
      else {
        if (typeof decodeG726 !== 'function') throw coreError('stream.decoder');
        result = await decodeG726(ch, { ch, rate: c.rate, codeSize: 3, total: rec.samples, parts: [part] }, () => {});
      }
    } finally { activeDecoders--; }
    assertAlive();
    const pcm = result.pcm instanceof Int16Array ? result.pcm : new Int16Array(result.pcm);
    if (pcm.length > rec.samples || pcm.length < rec.samples - 1024) throw coreError('stream.decodedLength', { path: r.f.path });
    const declared = result.decoded?.[0];
    // AAC's browser decoder can return one fewer delay frame. decodeAAC pads its
    // allocation, so retain the actual decoded sample count for coverage/copying.
    const actual = declared ? Math.min(pcm.length, declared.samples) : pcm.length;
    r.samples = actual; r.status = 'decoded'; decodedRecords++;
    r.source.verified = true; r.source.decoded = true; r.source.decoded_samples = actual;
    if (!r.receipt) {
      r.receipt = { file: r.f.path, samples: actual, declared_samples: rec.samples,
        offset: r.offset, decoder_samples: declared?.decoder_samples ?? actual };
      c.decoder.push(r.receipt); c.decoder.sort((a, b) => a.offset - b.offset || a.file.localeCompare(b.file));
    }
    updateEnvelope(ch, r, pcm.subarray(0, actual)); refreshQA(ch); notify(ch);
    keep(r.key, pcm); return pcm;
  }
  function checkSignal(signal) {
    if (signal?.aborted) { const error = coreError('stream.cancelled'); error.name = 'AbortError'; throw error; }
  }
  async function read(ch, startSec, endSec, signal) {
    assertAlive();
    checkSignal(signal);
    const c = channels[ch]; if (!c) throw coreError('stream.channel', { channel: ch });
    if (!Number.isFinite(startSec) || !Number.isFinite(endSec) || endSec <= startSec
        || endSec - startSec > MAX_RANGE_SECONDS + 1e-8) throw coreError('stream.range');
    const start = Math.max(0, Math.min(duration, startSec)), end = Math.max(0, Math.min(duration, endSec));
    const a = Math.round(start * c.rate), b = Math.round(end * c.rate);
    if (b <= a) throw coreError('media.emptyClip');
    maxReadSeconds = Math.max(maxReadSeconds, (b - a) / c.rate);
    const pcm = new Int16Array(b - a), intervals = [], receipts = [], sources = [];
    for (const r of records[ch]) {
      if (r.offset + leadOf(ch, r) >= b || r.offset + r.samples <= a) continue;
      checkSignal(signal);
      const decoded = await decode(ch, r); assertAlive(); checkSignal(signal);
      if (!decoded) continue;
      const from = Math.max(a, r.offset + leadOf(ch, r)), to = Math.min(b, r.offset + r.samples);
      if (to <= from) continue;
      pcm.set(decoded.subarray(from - r.offset, to - r.offset), from - a);
      intervals.push([(from - a) / c.rate, (to - a) / c.rate]);
      receipts.push(r.receipt); sources.push(r.source);
    }
    return { pcm, rate: c.rate, start: a / c.rate, end: b / c.rate,
      qa: { ...coverage(intervals, pcm.length / c.rate), bad: c.qa.bad.slice() }, decoder: receipts, sources };
  }
  function enqueue(task) {
    const result = queue.then(task);
    queue = result.catch(() => {}); return result;
  }
  function readRange(ch, start, end, options = {}) {
    // Serializing entire ranges also bounds simultaneous native AAC decoders and
    // avoids allocating one queued range-sized buffer for every rapid seek.
    return enqueue(() => read(ch, start, end, options.signal));
  }
  async function ensureEnvelopes(requestedChannels, startSec, endSec, options = {}) {
    assertAlive();
    const names = requestedChannels == null ? Object.keys(channels)
      : typeof requestedChannels === 'string' ? [requestedChannels] : requestedChannels;
    if (!Array.isArray(names) || names.some(ch => typeof ch !== 'string' || !channels[ch]))
      throw coreError('stream.waveChannel');
    if (!Number.isFinite(startSec) || !Number.isFinite(endSec) || endSec <= startSec)
      throw coreError('stream.waveTime');
    const selected = [...new Set(names)], start = Math.max(0, Math.min(duration, startSec));
    const end = Math.max(0, Math.min(duration, endSec)), signal = options.signal;
    if (end <= start) throw coreError('stream.waveEmpty');
    const summary = { channels: selected, start, end, records: 0,
      decodedRecords: 0, reusedRecords: 0, rejectedRecords: 0 };
    envelopeRequests++; activeEnvelopeRequests++;
    try {
      checkSignal(signal);
      // Build only small metadata references, never a range-sized PCM buffer.
      // Time-major order lets all visible tracks acquire the first minute before
      // proceeding to later minutes. Original duration includes known AAC tails.
      const visible = [];
      for (const ch of selected) for (const r of records[ch]) {
        const from = (r.f.start - W0) / 1000;
        if (from < end && from + r.f.duration > start) visible.push({ ch, r });
      }
      visible.sort((a, b) => a.r.f.start - b.r.f.start || a.ch.localeCompare(b.ch) || a.r.rank - b.r.rank);
      summary.records = visible.length;
      for (const { ch, r } of visible) {
        assertAlive(); checkSignal(signal);
        if (r.status === 'decoded') {
          // PCM eviction is unrelated to the small persistent envelope. Never
          // re-read/re-decode an already analysed record just to draw a wave.
          summary.reusedRecords++; envelopeReusedRecords++; continue;
        }
        if (r.status === 'bad') { summary.rejectedRecords++; continue; }
        // Queue one record only. readRange can enter the shared queue while this
        // record decodes or during the task yield below; no long waveform scan
        // can monopolise the decoder by enqueuing its remaining records upfront.
        const kind = await enqueue(async () => {
          assertAlive(); checkSignal(signal);
          if (r.status === 'decoded') return 'reused';
          if (r.status === 'bad') return 'rejected';
          const pcm = await decode(ch, r);
          if (pcm) envelopeDecodedRecords++;
          assertAlive(); checkSignal(signal);
          return pcm ? 'decoded' : 'rejected';
        });
        if (kind === 'decoded') summary.decodedRecords++;
        else if (kind === 'reused') { summary.reusedRecords++; envelopeReusedRecords++; }
        else summary.rejectedRecords++;
        await new Promise(resolve => root.setTimeout(resolve, 0));
      }
      assertAlive(); checkSignal(signal);
      return summary;
    } catch (e) {
      if (e.name === 'AbortError') envelopeCancelledRequests++;
      throw e; // In particular, SHA-256 mismatches must remain fatal.
    } finally { activeEnvelopeRequests--; }
  }
  return { channels, duration, W0, W1, maxRangeSeconds: MAX_RANGE_SECONDS, readRange, ensureEnvelopes,
    stats: () => ({ cacheBytes, peakCacheBytes, maxCacheBytes, decodedRecords, cacheHits,
      cacheRecords: cache.size, maxReadSeconds, activeDecoders, peakActiveDecoders,
      envelopeRequests, envelopeDecodedRecords, envelopeReusedRecords,
      envelopeCancelledRequests, activeEnvelopeRequests }),
    destroy() { alive = false; cache.clear(); cacheBytes = 0; }
  };
}

// A native <audio> plays short WAV chunks, while every consumer sees one stable
// global timeline. Seeks/channel changes invalidate stale asynchronous reads.
function createPlayer({ element, store, channel, status = () => {}, onUpdate = () => {}, chunkSeconds = 60 }) {
  if (!element || !store.channels[channel]) throw coreError('stream.playerChannel');
  if (!Number.isFinite(chunkSeconds) || chunkSeconds < 1 || chunkSeconds > MAX_RANGE_SECONDS) throw coreError('stream.bufferLength');
  const listeners = new Map(), nativeListeners = [];
  let currentChannel = channel, current = null, next = null, pending = null, generation = 0;
  let anchor = 0, desiredPlaying = false, loading = false, dead = false, error = null, nativeSeekReason = 'user';
  let preloadPromise = null, preloadToken = 0, preloadController = null, loadController = null;
  element.controls = false; element.preload = 'auto';
  const clamp = t => Math.max(0, Math.min(store.duration, Number.isFinite(+t) ? +t : 0));
  function emit(type, reason) {
    const event = { type, target: api, currentTarget: api };
    if (reason) event.reason = reason;
    for (const item of [...(listeners.get(type) || [])]) { if (item.once) api.removeEventListener(type, item.fn); item.fn.call(api, event); }
    onUpdate(api);
  }
  function globalTime() {
    return loading || !current ? anchor : clamp(current.start + (element.currentTime || 0));
  }
  function revoke(part) { if (part?.url) root.URL.revokeObjectURL(part.url); }
  function clearNext() {
    preloadToken++; preloadController?.abort(); preloadController = null;
    revoke(next); next = null; preloadPromise = null;
  }
  function fail(e) {
    if (dead) return;
    if (error === e && !desiredPlaying && !loading) return;
    anchor = globalTime(); error = e; desiredPlaying = false; loading = false;
    loadController?.abort(); clearNext(); element.pause(); revoke(current); current = null;
    status(typeof I18N.errorText === 'function' ? I18N.errorText(e) : e.message || String(e),
      e.i18nKey ? { key: e.i18nKey, params: e.i18nParams, paramKeys: e.i18nParamKeys } : null); emit('error'); emit('pause');
  }
  async function makeChunk(ch, start, signal) {
    const result = await store.readRange(ch, start, Math.min(store.duration, start + chunkSeconds), { signal });
    if (dead || signal?.aborted) return null;
    const blob = new root.Blob([root.DSP.wavHeader(result.pcm.length, result.rate), result.pcm], { type: 'audio/wav' });
    return { channel: ch, start: result.start, end: result.end, url: root.URL.createObjectURL(blob) };
  }
  function preload() {
    if (dead || loading || !current || current.end >= store.duration || preloadPromise || next) return;
    const token = ++preloadToken, ch = currentChannel, start = current.end;
    preloadController = new root.AbortController();
    preloadPromise = makeChunk(ch, start, preloadController.signal).then(part => {
      if (dead || token !== preloadToken || ch !== currentChannel || !current || current.end !== start) { revoke(part); return; }
      next = part;
    }).catch(e => { if (!dead && token === preloadToken) {
      const message = typeof I18N.errorText === 'function' ? I18N.errorText(e) : e.message;
      const params = { ...(e.i18nParams || {}), message };
      const paramKeys = e.i18nKey ? { ...e.i18nParamKeys, message: e.i18nKey } : undefined;
      status(ui('stream.nextBuffer', params), { key: 'core.stream.nextBuffer', params, paramKeys });
    } })
      .finally(() => { if (token === preloadToken) preloadPromise = null; });
  }
  function waitMetadata(url, token, signal) {
    return new Promise((resolve, reject) => {
      const loaded = () => { cleanup(); resolve(token === generation && !dead); };
      const failed = () => { cleanup(); reject(coreError('stream.wavFailed')); };
      const canceled = () => { cleanup(); resolve(false); };
      const timer = root.setTimeout(() => { cleanup(); reject(coreError('stream.timeout')); }, 30000);
      const cleanup = () => {
        root.clearTimeout(timer); element.removeEventListener('loadedmetadata', loaded);
        element.removeEventListener('error', failed); signal.removeEventListener('abort', canceled);
      };
      element.addEventListener('loadedmetadata', loaded, { once: true });
      element.addEventListener('error', failed, { once: true });
      signal.addEventListener('abort', canceled, { once: true });
      element.src = url; element.load();
    });
  }
  async function prepareAt(time, force = false, reason = 'user') {
    if (dead) throw coreError('stream.playerClosed');
    const target = clamp(time), ch = currentChannel;
    if (!force && !loading && current?.channel === ch && target >= current.start && target < current.end) {
      anchor = target; nativeSeekReason = reason; element.currentTime = target - current.start; emit('timeupdate'); emit('seeked', reason); return;
    }
    const token = ++generation; loadController?.abort(); loadController = new root.AbortController();
    const signal = loadController.signal;
    anchor = target; loading = true; error = null; nativeSeekReason = reason;
    element.pause(); emit('seeking', reason); emit('timeupdate'); status(ui('stream.preparing'), { key: 'core.stream.preparing', params: {} });
    const start = target >= store.duration ? Math.max(0, store.duration - chunkSeconds)
      : Math.floor(target / chunkSeconds) * chunkSeconds;
    // A previous chunk can end on a sample-rounded boundary; reuse a preloaded
    // chunk by its actual start instead of re-rounding the native timeline.
    let part = null;
    if (next?.channel === ch && target >= next.start && target < next.end) { part = next; next = null; }
    clearNext();
    if (!part) part = await makeChunk(ch, start, signal);
    if (dead || token !== generation) { revoke(part); return; }
    const old = current; current = part;
    let accepted;
    try { accepted = await waitMetadata(part.url, token, signal); }
    finally { revoke(old); }
    if (!accepted) return;
    element.currentTime = Math.min(part.end - part.start, Math.max(0, target - part.start));
    loading = false; anchor = target; status('', null); emit('loadedmetadata'); emit('seeked', reason); emit('timeupdate');
    if (desiredPlaying && target < store.duration) await element.play();
    if (desiredPlaying) preload();
  }
  function prepare(time = globalTime(), force = false, reason = 'user') {
    if (!arguments.length && loading && pending) return pending;
    const operation = prepareAt(time, force, reason), token = generation;
    pending = operation.catch(e => {
      if (e.name === 'AbortError' || token !== generation || dead) return;
      fail(e); throw e;
    });
    // The currentTime property setter cannot return a promise. Mark this as
    // handled while leaving the returned promise rejectable for explicit calls.
    pending.catch(() => {}); return pending;
  }
  function listen(type, fn) { element.addEventListener(type, fn); nativeListeners.push([type, fn]); }
  const api = {
    get currentTime() { return globalTime(); },
    set currentTime(t) { prepare(clamp(t)); },
    get duration() { return store.duration; },
    get paused() { return !desiredPlaying; },
    get ended() { return !desiredPlaying && globalTime() >= store.duration; },
    get seeking() { return loading; },
    get readyState() { return loading ? 0 : element.readyState; },
    get error() { return error; },
    get channel() { return currentChannel; },
    get playbackRate() { return element.playbackRate; },
    set playbackRate(v) { element.playbackRate = v; emit('ratechange'); },
    get volume() { return element.volume; }, set volume(v) { element.volume = v; },
    get muted() { return element.muted; }, set muted(v) { element.muted = !!v; },
    async play() {
      if (dead) throw coreError('stream.playerClosed');
      if (globalTime() >= store.duration) await prepare(0);
      if (!desiredPlaying) { desiredPlaying = true; emit('play'); }
      try {
        if (loading) await pending;
        else if (!current) await prepare(globalTime());
        if (desiredPlaying && !loading) await element.play();
        preload();
      } catch (e) { fail(e); throw e; }
    },
    pause() {
      anchor = globalTime(); const changed = desiredPlaying; desiredPlaying = false; element.pause();
      if (changed) emit('pause');
    },
    async setChannel(ch) {
      if (!store.channels[ch]) throw coreError('stream.channel', { channel: ch });
      if (ch === currentChannel) return pending;
      const t = globalTime(); currentChannel = ch; clearNext();
      return prepare(t, true, 'channel');
    },
    prepare,
    addEventListener(type, fn, options = {}) {
      if (!listeners.has(type)) listeners.set(type, []);
      if (!listeners.get(type).some(item => item.fn === fn)) listeners.get(type).push({ fn, once: !!options.once });
    },
    removeEventListener(type, fn) {
      if (listeners.has(type)) listeners.set(type, listeners.get(type).filter(item => item.fn !== fn));
    },
    destroy() {
      if (dead) return;
      anchor = globalTime(); dead = true; generation++; loadController?.abort(); desiredPlaying = false; loading = false;
      element.pause(); clearNext(); revoke(current); current = null;
      for (const [type, fn] of nativeListeners) element.removeEventListener(type, fn);
      element.removeAttribute('src'); element.load(); listeners.clear();
    }
  };
  listen('timeupdate', () => {
    if (dead || loading || !current) return;
    anchor = globalTime(); emit('timeupdate');
    if (desiredPlaying && current.end - anchor <= 12) preload();
  });
  listen('seeked', () => { if (!dead && !loading) emit('seeked', nativeSeekReason); });
  listen('playing', () => { if (!dead && !loading && desiredPlaying) emit('playing'); });
  listen('error', () => {
    if (dead || loading || !current) return; // Initial load errors are handled by waitMetadata.
    const code = element.error?.code;
    fail(coreError(code ? 'stream.interruptedCode' : 'stream.interrupted', { code }));
  });
  listen('ended', () => {
    if (dead || loading || !current) return;
    anchor = current.end;
    if (anchor >= store.duration) { desiredPlaying = false; emit('timeupdate'); emit('pause'); emit('ended'); return; }
    if (desiredPlaying) prepare(anchor, true, 'buffer');
  });
  api.ready = prepare(0);
  return api;
}
const api = { createStore, createPlayer, coverage, MAX_RANGE_SECONDS, DEFAULT_CACHE_BYTES };
root.VDRStream = api;
if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
