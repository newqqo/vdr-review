// Standalone project persistence and hand-off archives. No network or runtime dependency.
(function (root) {
'use strict';
const I18N = typeof module !== 'undefined' && module.exports ? require('./i18n_core.js') : root.VDRI18n;
const ui = (key, params) => I18N.t('core.' + key, params);
function label(key, params = {}) { return { key: 'core.' + key, params }; }
function coreError(key, params = {}) {
  const values = { ...params }, field = values.label;
  let paramKeys;
  if (field && typeof field === 'object') {
    Object.assign(values, field.params, { label: I18N.t(field.key, field.params) });
    paramKeys = { label: field.key };
  }
  const error = new Error(ui(key, values));
  error.i18nKey = 'core.' + key; error.i18nParams = values;
  if (paramKeys) error.i18nParamKeys = paramKeys;
  return error;
}
const HASH = /^[0-9a-f]{64}$/i, CHANNEL = /^[AC][1-9][0-9]?$/;
const FORBIDDEN = new Set(['__proto__', 'constructor', 'prototype']);
const own = (o, k) => Object.prototype.hasOwnProperty.call(o, k);
function fail(key, params) { throw coreError(key, params); }
function record(value, label) {
  if (!value || typeof value !== 'object' || Array.isArray(value) ||
      (Object.getPrototypeOf(value) !== Object.prototype && Object.getPrototypeOf(value) !== null)) fail('validation.format', { label });
  for (const k of Object.keys(value)) if (FORBIDDEN.has(k)) fail('validation.unsafe', { label });
  return value;
}
function string(value, label, max) {
  if (typeof value !== 'string' || value.length > max || /\u0000/.test(value)) fail('validation.text', { label });
  return value;
}
function finite(value, label) { if (!Number.isFinite(value)) fail('validation.number', { label }); return value; }
function positiveId(value, label) {
  if (!Number.isSafeInteger(value) || value < 1) fail('validation.id', { label });
  return value;
}
function isoTime(value) {
  string(value, label('label.recordingTime'), 40);
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,3}))?(Z|[+-]\d{2}:\d{2})$/.exec(value);
  if (!m) fail('project.fullTime');
  const [y, mo, d, h, mi, se] = m.slice(1, 7).map(Number);
  const v = new Date(Date.UTC(y, mo - 1, d, h, mi, se));
  if (y < 100 || v.getUTCFullYear() !== y || v.getUTCMonth() !== mo - 1 || v.getUTCDate() !== d ||
      h > 23 || mi > 59 || se > 59 || (m[8] !== 'Z' && (+m[8].slice(1, 3) > 23 || +m[8].slice(4) > 59))) fail('project.invalidTime');
  const t = Date.parse(value);
  if (!Number.isFinite(t)) fail('project.invalidTime');
  return new Date(t).toISOString();
}
// Copy only JSON-safe values; avoid invoking getters/toJSON or retaining caller-owned objects.
function jsonCopy(value, depth) {
  if (depth > 20) fail('project.tooDeep');
  if (value === null || typeof value === 'boolean') return value;
  if (typeof value === 'string') return string(value, label('label.projectField'), 2000000);
  if (typeof value === 'number') return finite(value, label('label.projectField'));
  if (Array.isArray(value)) {
    if (value.length > 200000) fail('project.listTooLong');
    return value.map(v => jsonCopy(v, depth + 1));
  }
  record(value, label('label.projectField'));
  const out = {};
  for (const key of Object.keys(value)) {
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    if (!descriptor || !own(descriptor, 'value')) fail('project.dynamicField');
    if (descriptor.value !== undefined) out[key] = jsonCopy(descriptor.value, depth + 1);
  }
  return out;
}
function validateExtra(value) {
  if (value == null) return value;
  const x = record(value, label('label.extra'));
  for (const k of ['events', 'other', 'time_offset_basis']) if (own(x, k)) string(x[k], label('label.extraField', { field: k }), k === 'events' ? 1000000 : 10000);
  if (own(x, 'fixed') && typeof x.fixed !== 'boolean') fail('project.fixedBoolean');
  if (own(x, 'time_offset_s')) {
    finite(x.time_offset_s, label('label.timeOffset'));
    if (Math.abs(x.time_offset_s) > 3600 || (x.time_offset_s && !String(x.time_offset_basis || '').trim())) fail('project.offsetLimit');
  }
  for (const k of ['own_dims', 'other_dims']) if (own(x, k)) {
    if (!Array.isArray(x[k]) || x[k].length !== 4 || x[k].some(v => v !== null && (!Number.isFinite(v) || v < 0 || v > 10000))) fail('project.dimensions');
  }
  return jsonCopy(x, 0);
}
function validateTimingReview(value, cue) {
  const review = record(value, label('label.timingReview'));
  const fields = ['reason', 'original_start', 'original_end', 'placeholder_seconds', 'origin_t0_utc'];
  if (Object.keys(review).length !== fields.length || fields.some(k => !own(review, k)) ||
      review.reason !== 'legacy_zero_duration' || review.placeholder_seconds !== 0.1)
    fail('project.timingFlag');
  const start = finite(review.original_start, label('label.originalStart')), end = finite(review.original_end, label('label.originalEnd'));
  if (start !== end || Math.abs(start) > 3155760000 || isoTime(review.origin_t0_utc) !== review.origin_t0_utc ||
      Math.abs((cue.end - cue.start) - review.placeholder_seconds) > 0.000001)
    fail('project.timingMismatch');
  if (cue.st !== 0) fail('project.timingUnreviewed');
}
function validateImport(data, recordingId, options) {
  record(data, label('label.project'));
  if (!HASH.test(recordingId || '')) fail('project.unverifiedImport');
  const expected = recordingId.toLowerCase(), legacy = data.version === 3;
  if (legacy && !(options && options.allowLegacy === true)) fail('project.legacyConfirmation');
  if (data.version !== 4 && !legacy) fail('project.version');
  if (!legacy && (!HASH.test(data.recording_id || '') || data.recording_id.toLowerCase() !== expected)) fail('project.sourceMismatch');
  if (legacy && own(data, 'recording_id') && (!HASH.test(data.recording_id || '') || data.recording_id.toLowerCase() !== expected)) fail('project.legacySourceMismatch');
  const t0 = isoTime(data.t0_utc);
  const source = record(data.state, label('label.channels')), state = {}, ids = new Set();
  let maxId = 0, count = 0;
  for (const ch of Object.keys(source)) {
    if (!CHANNEL.test(ch)) fail('project.channel', { channel: ch });
    const cues = source[ch];
    if (!Array.isArray(cues) || cues.length > 200000 || (count += cues.length) > 200000) fail('project.cueList');
    state[ch] = cues.map(c => {
      record(c, label('label.cue'));
      const id = positiveId(c.id, label('label.cueId'));
      if (ids.has(id)) fail('project.duplicateId');
      ids.add(id); maxId = Math.max(maxId, id);
      const start = finite(c.start, label('label.cueStart')), end = finite(c.end, label('label.cueEnd'));
      if (end <= start || Math.abs(start) > 3155760000 || Math.abs(end) > 3155760000) fail('project.cueTime');
      if (!Number.isInteger(c.st) || c.st < 0 || c.st > 3) fail('project.cueStatus');
      string(c.text, label('label.cueText'), 1000000); string(c.spk, label('label.speaker'), 1000);
      if (own(c, 'orig') && c.orig !== undefined) string(c.orig, label('label.originalSubtitle'), 1000000);
      if (own(c, 'origNo') && c.origNo != null && c.origNo !== undefined &&
          !(typeof c.origNo === 'string' && c.origNo.length <= 1000) && !Number.isSafeInteger(c.origNo)) fail('project.originalId');
      if (own(c, 'ovl') && c.ovl !== undefined && typeof c.ovl !== 'boolean') fail('project.overlapFlag');
      if (own(c, 'repOf') && c.repOf !== undefined) positiveId(c.repOf, label('label.repetitionId'));
      if (own(c, 'timing_review')) validateTimingReview(c.timing_review, c);
      return jsonCopy(c, 0);
    });
  }
  if (own(data, 'uid')) positiveId(data.uid, label('label.nextCueId'));
  if (maxId >= Number.MAX_SAFE_INTEGER) fail('project.idLimit');
  if (own(data, 'tz_hours') && (!Number.isFinite(data.tz_hours) || Math.abs(data.tz_hours) > 14)) fail('project.timezone');
  if (own(data, 'spks') && (!Array.isArray(data.spks) || data.spks.length > 1000 ||
      data.spks.some(s => typeof s !== 'string' || s.length > 1000 || s.includes('\u0000')))) fail('project.speakers');
  if (own(data, 'spkColor')) {
    record(data.spkColor, label('label.speakerColor'));
    for (const v of Object.values(data.spkColor)) if (typeof v !== 'string' || !/^#(?:[0-9a-f]{3}|[0-9a-f]{4}|[0-9a-f]{6}|[0-9a-f]{8})$/i.test(v)) fail('project.speakerColor');
  }
  if (own(data, 'extra')) validateExtra(data.extra);
  if (own(data, 'import_history')) {
    if (!Array.isArray(data.import_history) || data.import_history.length > 1000) fail('project.history');
    for (const entry of data.import_history) record(entry, label('label.importHistory'));
  }
  const out = jsonCopy(data, 0);
  out.version = 4; out.recording_id = expected; out.t0_utc = t0; out.state = state;
  out.uid = Math.max(data.uid || 1, maxId + 1);
  if (legacy) out.migration = { from_version: 3, source_binding: 'explicit_user_confirmation', note: '舊版主檔未記錄錄音指紋；來源由使用者人工確認' };
  return out;
}
function createProject(payload) {
  return validateImport(payload, payload && payload.recording_id);
}
// Version 2 stored UTC seconds within an unknown calendar day. Only an
// explicitly supplied, validated date can bind it to a source recording.
function migrateLegacyV2(data, recordingId, utcDate, options) {
  record(data, label('label.legacyProject'));
  if (data.version !== 2) fail('project.v2Only');
  if (typeof utcDate !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(utcDate)) fail('project.confirmDate');
  const midnight = isoTime(utcDate + 'T00:00:00.000Z');
  const seconds = finite(data.t0_utc_s, label('label.legacySeconds'));
  if (seconds < 0 || seconds >= 86400) fail('project.daySeconds');
  if (!HASH.test(recordingId || '')) fail('project.unverifiedMigration');
  if (own(data, 'recording_id') && (!HASH.test(data.recording_id || '') || data.recording_id.toLowerCase() !== recordingId.toLowerCase()))
    fail('project.legacySourceMismatch');
  const out = jsonCopy(data, 0);
  out.version = 4; out.recording_id = recordingId.toLowerCase();
  out.t0_utc = new Date(Date.parse(midnight) + seconds * 1000).toISOString();
  const zeroIds = [], source = record(out.state, label('label.channels'));
  for (const cues of Object.values(source)) {
    if (!Array.isArray(cues)) fail('project.cueList');
    for (const cue of cues) {
      record(cue, label('label.cue'));
      if (Number.isFinite(cue.start) && Number.isFinite(cue.end) && cue.start === cue.end) {
        if (!options || options.allowZeroDuration !== true) fail('project.zeroConfirmation');
        if (!Number.isInteger(cue.st) || cue.st < 0 || cue.st > 3) fail('project.cueStatus');
        cue.timing_review = { reason: 'legacy_zero_duration', original_start: cue.start,
          original_end: cue.end, placeholder_seconds: 0.1, origin_t0_utc: out.t0_utc };
        cue.end = cue.start + 0.1; cue.st = 0; zeroIds.push(cue.id);
      }
    }
  }
  out.migration = { from_version: 2, source_binding: 'explicit_user_confirmation',
    confirmed_utc_date: utcDate, original_t0_utc_s: seconds,
    zero_duration_cues: { count: zeroIds.length, ids: zeroIds },
    note: '舊版主檔未記錄日期與錄音指紋；UTC日期與來源由使用者人工確認' };
  return createProject(out);
}
// Stable JSON equality includes every cue field except its local identifier.
// repOf is included after remapping, so a different repetition source is not
// silently mistaken for the same investigation note.
function cueSignature(cue) {
  const stable = value => {
    if (Array.isArray(value)) return '[' + value.map(stable).join(',') + ']';
    if (value && typeof value === 'object') return '{' + Object.keys(value).sort()
      .map(k => JSON.stringify(k) + ':' + stable(value[k])).join(',') + '}';
    return JSON.stringify(value);
  };
  const fields = {};
  for (const k of Object.keys(cue)) if (k !== 'id') fields[k] = cue[k];
  return stable(fields);
}
function mergeProjects(current, incoming) {
  if (!current || current.version !== 4 || !incoming || incoming.version !== 4)
    fail('project.mergeVersion');
  const base = createProject(current), other = validateImport(incoming, base.recording_id);
  const timeShiftSeconds = (Date.parse(other.t0_utc) - Date.parse(base.t0_utc)) / 1000;
  const project = jsonCopy(base, 0), idMap = {}, duplicates = [], conflicts = [], added = [];
  const signatures = new Map(), nodes = new Map(), dependants = new Map(), ready = [];
  let nextId = base.uid, existingCount = 0;
  for (const [ch, cues] of Object.entries(base.state)) for (const cue of cues) {
    existingCount++;
    const key = ch + '|' + cueSignature(cue);
    if (!signatures.has(key)) signatures.set(key, cue.id);
  }
  for (const [ch, cues] of Object.entries(other.state)) {
    if (!own(project.state, ch)) project.state[ch] = [];
    for (const cue of cues) nodes.set(cue.id, { ch, cue });
  }
  for (const node of nodes.values()) {
    const source = node.cue.repOf;
    if (source == null) ready.push(node);
    else {
      if (!nodes.has(source)) fail('project.missingRepetition');
      if (!dependants.has(source)) dependants.set(source, []);
      dependants.get(source).push(node);
    }
  }
  // Resolve targets before their repetitions, without a recursive stack whose
  // depth would depend on an untrusted JSON file. Self/cyclic chains reject.
  for (let index = 0; index < ready.length; index++) {
    const { ch, cue } = ready[index], shifted = { ...cue,
      start: cue.start + timeShiftSeconds, end: cue.end + timeShiftSeconds };
    if (!Number.isFinite(shifted.start) || !Number.isFinite(shifted.end) || shifted.end <= shifted.start
        || Math.abs(shifted.start) > 3155760000 || Math.abs(shifted.end) > 3155760000)
      fail('project.mergedTime');
    if (own(cue, 'repOf')) shifted.repOf = idMap[cue.repOf];
    const key = ch + '|' + cueSignature(shifted);
    if (signatures.has(key)) {
      idMap[cue.id] = signatures.get(key);
      duplicates.push({ channel: ch, incomingId: cue.id, currentId: idMap[cue.id] });
    } else {
      if (nextId >= Number.MAX_SAFE_INTEGER) fail('project.idLimit');
      if (existingCount + added.length >= 200000) fail('project.tooManyCues');
      shifted.id = nextId++; idMap[cue.id] = shifted.id;
      signatures.set(key, shifted.id); project.state[ch].push(shifted);
      added.push({ channel: ch, cue: shifted, sourceIncomingId: cue.id });
    }
    for (const child of dependants.get(cue.id) || []) ready.push(child);
  }
  if (ready.length !== nodes.size) fail('project.repetitionCycle');
  // A max-end interval tree avoids scanning a six-hour current transcript for
  // every incoming cue. Report cross-file overlaps only, not incoming's own
  // deliberate overlapping speech. Excessive conflicts reject atomically.
  function intervalTree(cues) {
    const sorted = cues.slice().sort((a, b) => a.start - b.start || a.id - b.id);
    function build(a, b) {
      if (a >= b) return null;
      const m = (a + b) >>> 1, left = build(a, m), right = build(m + 1, b), cue = sorted[m];
      return { cue, left, right, maxEnd: Math.max(cue.end, left?.maxEnd ?? -Infinity, right?.maxEnd ?? -Infinity) };
    }
    return build(0, sorted.length);
  }
  const trees = new Map();
  for (const entry of added) {
    const ch = entry.channel, cue = entry.cue;
    if (!trees.has(ch)) trees.set(ch, intervalTree(base.state[ch] || []));
    const stack = [trees.get(ch)];
    while (stack.length) {
      const node = stack.pop(); if (!node || node.maxEnd <= cue.start) continue;
      const existing = node.cue;
      if (existing.start < cue.end && existing.end > cue.start) {
        if (conflicts.length >= 10000) fail('project.tooManyConflicts');
        conflicts.push({ channel: ch, currentId: existing.id, incomingId: cue.id,
          sourceIncomingId: entry.sourceIncomingId, start: Math.max(existing.start, cue.start),
          end: Math.min(existing.end, cue.end) });
      }
      if (node.left) stack.push(node.left);
      if (node.right && existing.start < cue.end) stack.push(node.right);
    }
  }
  project.uid = nextId;
  for (const cues of Object.values(project.state)) cues.sort((a, b) => a.start - b.start || a.end - b.end || a.id - b.id);
  if (own(base, 'spks') || own(other, 'spks')) project.spks = [...new Set([...(base.spks || []), ...(other.spks || [])])];
  if (own(base, 'spkColor') || own(other, 'spkColor')) {
    project.spkColor = { ...(base.spkColor || {}) };
    for (const [speaker, color] of Object.entries(other.spkColor || {})) if (!own(project.spkColor, speaker)) project.spkColor[speaker] = color;
  }
  conflicts.sort((a, b) => a.channel.localeCompare(b.channel) || a.start - b.start || a.currentId - b.currentId || a.incomingId - b.incomingId);
  if (added.length) {
    const history = project.import_history || [];
    if (history.length >= 1000) fail('project.historyLimit');
    const entry = { t0_utc: other.t0_utc, timeShiftSeconds, added: added.length,
      duplicates: duplicates.length, conflicts: conflicts.length };
    if (own(other, 'migration')) entry.migration = jsonCopy(other.migration, 0);
    project.import_history = [...history, entry];
  }
  return { project: createProject(project), stats: { added: added.length, duplicates: duplicates.length, conflicts: conflicts.length },
    conflicts, duplicates, idMap, timeShiftSeconds };
}
async function sha256(data) {
  if (!root.crypto || !root.crypto.subtle || typeof root.crypto.subtle.digest !== 'function') fail('project.shaUnavailable');
  // Large WAVs are hashed incrementally: never materialise a 4-hour Blob as one ArrayBuffer.
  if (typeof Blob !== 'undefined' && data instanceof Blob && data.size > 16 * 1024 * 1024) {
    const hash = new Sha256(), reader = data.stream().getReader();
    let chunks = 0;
    try { for (;;) { const r = await reader.read(); if (r.done) break; hash.update(r.value); if (++chunks % 8 === 0) await new Promise(resolve => setTimeout(resolve, 0)); } }
    finally { reader.releaseLock(); }
    return hash.hex();
  }
  const bytes = await bytesOf(data);
  const result = new Uint8Array(await root.crypto.subtle.digest('SHA-256', bytes));
  if (result.length !== 32) fail('project.shaFailed');
  return Array.from(result, x => x.toString(16).padStart(2, '0')).join('');
}
function safeDownloadName(value) {
  const name = typeof value === 'string' && value ? value : 'VDR案件主檔.vdr.json';
  return name.replace(/[<>:"/\\|?*\u0000-\u001f]/g, '_').replace(/[. ]+$/, '') || 'VDR案件主檔.vdr.json';
}
async function saveProject(options) {
  const data = createProject(options.data), name = safeDownloadName(options.name);
  // No awaits before the picker: browser activation belongs to the user's click.
  let handle = options.handle || null;
  if (!handle && typeof root.showSaveFilePicker === 'function') {
    try {
      handle = await root.showSaveFilePicker({ suggestedName: name, types: [{ description: ui('project.fileType'), accept: { 'application/json': ['.json'] } }] });
    } catch (e) { if (e && e.name === 'AbortError') return { saved: false, cancelled: true }; throw e; }
  }
  const blob = new Blob([JSON.stringify(data, null, 1)], { type: 'application/json;charset=utf-8' });
  if (handle) {
    if (typeof handle.createWritable !== 'function') fail('project.notWritable');
    let writer;
    try {
      writer = await handle.createWritable();
      await writer.write(blob); await writer.close();
      return { saved: true, handle, mode: 'file' };
    } catch (e) {
      if (writer && typeof writer.abort === 'function') { try { await writer.abort(); } catch (_) { /* retain the original write error */ } }
      throw e;
    }
  }
  if (!root.document || !root.URL || typeof root.URL.createObjectURL !== 'function') fail('project.saveUnavailable');
  const a = root.document.createElement('a'), url = root.URL.createObjectURL(blob);
  a.href = url; a.download = name;
  try { a.click(); } finally { root.setTimeout(() => root.URL.revokeObjectURL(url), 10000); }
  return { saved: false, downloaded: true, mode: 'download', handle: null,
    message: ui('project.downloadSent') };
}
async function bytesOf(data) {
  if (typeof data === 'string') return new TextEncoder().encode(data);
  if (data instanceof Uint8Array) return data;
  if (data instanceof ArrayBuffer) return new Uint8Array(data);
  if (typeof Blob !== 'undefined' && data instanceof Blob) return new Uint8Array(await data.arrayBuffer());
  fail('project.fileData');
}
const CRC_TABLE = (() => { const t = new Uint32Array(256); for (let n = 0; n < 256; n++) { let c = n; for (let i = 0; i < 8; i++) c = (c & 1) ? 0xedb88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; } return t; })();
function crc32(bytes) { let c = 0xffffffff; for (const b of bytes) c = CRC_TABLE[(c ^ b) & 255] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; }
const SHA_K = new Uint32Array([0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2]);
const ror = (x,n) => (x >>> n) | (x << (32-n));
class Sha256 {
  constructor() { this.h = new Uint32Array([0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19]); this.buf = new Uint8Array(64); this.w = new Uint32Array(64); this.used = 0; this.length = 0; }
  block(bytes,o) {
    const w=this.w, h=this.h;
    for(let i=0;i<16;i++){const p=o+i*4;w[i]=(bytes[p]<<24)|(bytes[p+1]<<16)|(bytes[p+2]<<8)|bytes[p+3];}
    for(let i=16;i<64;i++){const a=w[i-15],b=w[i-2];w[i]=(w[i-16]+(ror(a,7)^ror(a,18)^(a>>>3))+w[i-7]+(ror(b,17)^ror(b,19)^(b>>>10)))>>>0;}
    let [a,b,c,d,e,f,g,k]=h;
    for(let i=0;i<64;i++){const t1=(k+(ror(e,6)^ror(e,11)^ror(e,25))+((e&f)^(~e&g))+SHA_K[i]+w[i])>>>0,t2=((ror(a,2)^ror(a,13)^ror(a,22))+((a&b)^(a&c)^(b&c)))>>>0;k=g;g=f;f=e;e=(d+t1)>>>0;d=c;c=b;b=a;a=(t1+t2)>>>0;}
    const v=[a,b,c,d,e,f,g,k];for(let i=0;i<8;i++)h[i]=(h[i]+v[i])>>>0;
  }
  update(bytes) {
    this.length+=bytes.length;let o=0;
    if(this.used){const n=Math.min(64-this.used,bytes.length);this.buf.set(bytes.subarray(0,n),this.used);this.used+=n;o=n;if(this.used===64){this.block(this.buf,0);this.used=0;}}
    for(;o+64<=bytes.length;o+=64)this.block(bytes,o);
    if(o<bytes.length){this.buf.set(bytes.subarray(o),0);this.used=bytes.length-o;}
    return this;
  }
  hex() {
    const length=this.length, tail=new Uint8Array(this.used<56?64:128);tail.set(this.buf.subarray(0,this.used));tail[this.used]=128;
    const d=new DataView(tail.buffer);d.setUint32(tail.length-8,Math.floor(length/0x20000000),false);d.setUint32(tail.length-4,(length*8)>>>0,false);
    for(let i=0;i<tail.length;i+=64)this.block(tail,i);
    return Array.from(this.h,x=>x.toString(16).padStart(8,'0')).join('');
  }
}
async function blobCrc32(blob) {
  let c=0xffffffff, chunks=0;const reader=blob.stream().getReader();
  try { for(;;){const r=await reader.read();if(r.done)break;for(const b of r.value)c=CRC_TABLE[(c^b)&255]^(c>>>8);if(++chunks%8===0)await new Promise(resolve=>setTimeout(resolve,0));} }
  finally {reader.releaseLock();}
  return (c^0xffffffff)>>>0;
}
function archiveName(name) {
  string(name, label('label.archiveName'), 10000);
  if (!name || name.includes('\\') || /^[A-Za-z]:/.test(name) || name.startsWith('/') || /[\u0000-\u001f\u007f]/.test(name)) fail('project.archivePath');
  const segments = name.split('/');
  if (segments.some(s => !s || s === '.' || s === '..' || /[<>:"|?*]/.test(s) || /[. ]$/.test(s) || /^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)/i.test(s))) fail('project.archivePath');
  const bytes = new TextEncoder().encode(name);
  if (bytes.length > 65535) fail('project.archiveName');
  return bytes;
}
async function zipEntries(entries) {
  if (!Array.isArray(entries) || entries.length > 65535) fail('project.archiveList');
  const locals = [], central = [], names = new Set(); let offset = 0, centralSize = 0;
  for (const e of entries) {
    record(e, label('label.archiveEntry'));
    const name = archiveName(e.name), key = e.name.normalize('NFC').toLowerCase();
    if (names.has(key)) fail('project.archiveDuplicate', { name: e.name });
    names.add(key);
    const isBlob = typeof Blob !== 'undefined' && e.data instanceof Blob;
    const bytes = isBlob ? e.data : await bytesOf(e.data), size = isBlob ? bytes.size : bytes.byteLength, crc = isBlob ? await blobCrc32(bytes) : crc32(bytes);
    if (size >= 0xffffffff || offset + 30 + name.length + size >= 0xffffffff) fail('project.archiveCapacityHint');
    const local = new Uint8Array(30 + name.length), l = new DataView(local.buffer);
    l.setUint32(0, 0x04034b50, true); l.setUint16(4, 20, true); l.setUint16(6, 0x0800, true);
    l.setUint16(12, 33, true); // deterministic 1980-01-01, 00:00; provenance carries real times.
    l.setUint32(14, crc, true); l.setUint32(18, size, true); l.setUint32(22, size, true); l.setUint16(26, name.length, true); local.set(name, 30);
    const header = new Uint8Array(46 + name.length), c = new DataView(header.buffer);
    c.setUint32(0, 0x02014b50, true); c.setUint16(4, 20, true); c.setUint16(6, 20, true); c.setUint16(8, 0x0800, true);
    c.setUint16(14, 33, true); c.setUint32(16, crc, true); c.setUint32(20, size, true); c.setUint32(24, size, true);
    c.setUint16(28, name.length, true); c.setUint32(42, offset, true); header.set(name, 46);
    locals.push(local, bytes); central.push(header); centralSize += header.length; offset += local.length + size;
  }
  if (offset + centralSize + 22 >= 0xffffffff) fail('project.archiveCapacity');
  const end = new Uint8Array(22), v = new DataView(end.buffer);
  v.setUint32(0, 0x06054b50, true); v.setUint16(8, entries.length, true); v.setUint16(10, entries.length, true);
  v.setUint32(12, centralSize, true); v.setUint32(16, offset, true);
  return new Blob([...locals, ...central, end], { type: 'application/zip' });
}
const api = { validateImport, createProject, migrateLegacyV2, mergeProjects, saveProject, sha256, zipEntries, crc32, Sha256 };
if (typeof module !== 'undefined' && module.exports) module.exports = api; else root.VDRProject = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
