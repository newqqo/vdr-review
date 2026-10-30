// Update check and offline download. Connects only when the user presses a button.
// A downloaded page is hash-verified and saved to a file; it never runs in this page.
(function (root) {
'use strict';
const I = typeof module !== 'undefined' && module.exports ? require('./i18n.js') : root.VDRI18n;
const MANIFEST_URL = 'https://newqqo.github.io/vdr-review/version.json';
const SAVE_NAME = 'VDR核聽工具.html';
const MAX_BYTES = 64 * 1024 * 1024;
const TIMEOUT_MS = 120000;
const SEMVER = /^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$/;

function fail(key, params = {}) { return Object.assign(new Error(key), { updateKey: key, updateParams: params }); }

function parseVersion(value) {
  const match = typeof value === 'string' ? SEMVER.exec(value) : null;
  const parts = match ? match.slice(1).map(Number) : [];
  if (!match || !parts.every(Number.isSafeInteger)) throw fail('update.badVersion', { version: String(value).slice(0, 40) });
  return parts;
}
function compareVersions(a, b) {
  const x = parseVersion(a), y = parseVersion(b);
  for (let i = 0; i < 3; i++) if (x[i] !== y[i]) return x[i] < y[i] ? -1 : 1;
  return 0;
}
function isHttps(value) {
  try { return typeof value === 'string' && new URL(value).protocol === 'https:'; } catch (_) { return false; }
}
function parseManifest(text) {
  let data;
  try { data = JSON.parse(text); } catch (_) { throw fail('update.badManifest'); }
  if (!data || typeof data !== 'object' || Array.isArray(data)) throw fail('update.badManifest');
  parseVersion(data.version);
  const { released, html, sha256, bytes, notes_url: notesUrl } = data;
  const valid = typeof released === 'string' && /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(released)
    // A bare file name keeps the download beside the manifest, on the same origin.
    && typeof html === 'string' && /^[A-Za-z0-9._-]+\.html$/.test(html)
    && typeof sha256 === 'string' && /^[0-9a-f]{64}$/.test(sha256)
    && Number.isSafeInteger(bytes) && bytes > 0 && bytes <= MAX_BYTES && isHttps(notesUrl);
  if (!valid) throw fail('update.badManifest');
  return { version: data.version, released, htmlUrl: new URL(html, MANIFEST_URL).href, sha256, bytes, notesUrl };
}
async function fetchBody(url, read) {
  const controller = new AbortController(), timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  let response;
  try {
    response = await root.fetch(url, { cache: 'no-store', credentials: 'omit', referrerPolicy: 'no-referrer', signal: controller.signal });
    if (response.ok) return await read(response);
  } catch (_) {
    throw fail('update.unreachable');
  } finally { clearTimeout(timer); }
  throw fail('update.httpError', { status: response.status });
}
async function readManifest() {
  return parseManifest(await fetchBody(MANIFEST_URL, response => response.text()));
}
async function downloadVerified(manifest) {
  const subtle = root.crypto && root.crypto.subtle;
  if (!subtle || typeof subtle.digest !== 'function') throw fail('update.noCrypto');
  const buffer = await fetchBody(manifest.htmlUrl, response => response.arrayBuffer());
  const digest = new Uint8Array(await subtle.digest('SHA-256', buffer));
  const hex = Array.from(digest, x => x.toString(16).padStart(2, '0')).join('');
  if (buffer.byteLength !== manifest.bytes || hex !== manifest.sha256) throw fail('update.hashMismatch');
  return new Uint8Array(buffer);
}
async function saveBytes(bytes) {
  const blob = new Blob([bytes], { type: 'text/html' });
  // No awaits before the picker: browser activation belongs to the user's click.
  if (typeof root.showSaveFilePicker === 'function') {
    let handle = null;
    try {
      handle = await root.showSaveFilePicker({ suggestedName: SAVE_NAME, types: [{ description: I.t('update.fileType'), accept: { 'text/html': ['.html'] } }] });
    } catch (e) {
      if (e && e.name === 'AbortError') return { cancelled: true };
      if (!e || (e.name !== 'SecurityError' && e.name !== 'NotAllowedError')) throw e;
    }
    if (handle) {
      let writer;
      try {
        writer = await handle.createWritable();
        await writer.write(blob); await writer.close();
        return { saved: true, name: handle.name || SAVE_NAME };
      } catch (e) {
        if (writer && typeof writer.abort === 'function') { try { await writer.abort(); } catch (_) { /* retain the original write error */ } }
        throw e;
      }
    }
  }
  const a = root.document.createElement('a'), url = URL.createObjectURL(blob);
  a.href = url; a.download = SAVE_NAME;
  try { a.click(); } finally { setTimeout(() => URL.revokeObjectURL(url), 10000); }
  return { downloaded: true };
}

function init({ $, version, isDirty }) {
  const action = $('updateAction'), panel = $('updatePanel'), status = $('updateStatus');
  const notes = $('updateNotes'), download = $('updateDownload'), save = $('updateSave');
  const offline = root.location.protocol === 'file:', label = offline ? 'update.check' : 'update.downloadOffline';
  let manifest = null, verified = null, busy = false;
  action.dataset.i18n = label; action.textContent = I.t(label);

  function say(key, params = {}, isError = false) {
    panel.hidden = false;
    status.dataset.i18n = key; status.dataset.i18nParams = JSON.stringify(params);
    status.textContent = I.t(key, params);
    status.classList.toggle('err', isError);
  }
  function setBusy(value) {
    busy = value;
    for (const button of [action, download, save]) button.disabled = value;
  }
  // Every failure stays in this panel: playback and the transcript are never involved.
  async function run(work) {
    if (busy) return;
    setBusy(true);
    try { await work(); }
    catch (e) {
      if (e && e.updateKey) say(e.updateKey, e.updateParams, true);
      else say('update.unexpected', { reason: String((e && e.message) || e) }, true);
    } finally { setBusy(false); }
  }
  async function check() {
    manifest = null; verified = null;
    notes.hidden = download.hidden = save.hidden = true;
    say('update.checking');
    const latest = await readManifest();
    if (compareVersions(latest.version, version) <= 0) { say('update.latest', { current: version, latest: latest.version }); return; }
    manifest = latest;
    notes.href = latest.notesUrl; notes.hidden = false; download.hidden = false;
    say('update.available', { version: latest.version, date: latest.released, current: version });
  }
  async function fetchVerified() {
    if (isDirty() && !root.confirm(I.t('update.dirtyConfirm'))) return;
    verified = null; save.hidden = true;
    if (!offline) { say('update.checking'); manifest = await readManifest(); }
    say('update.downloading', { version: manifest.version });
    verified = await downloadVerified(manifest);
    // Verify first, then ask for a click: the picker may truncate the file it opens,
    // and a slow download would outlast the activation of the first click.
    download.hidden = true; save.hidden = false;
    say('update.verified', { version: manifest.version });
  }
  async function saveVerified() {
    const result = await saveBytes(verified).catch(e => { throw fail('update.saveFailed', { reason: String((e && e.message) || e) }); });
    if (result.cancelled) { say('update.saveCancelled'); return; }
    verified = null; save.hidden = true;
    if (result.saved) say('update.saved', { name: result.name }); else say('update.downloaded');
  }
  action.onclick = () => run(offline ? check : fetchVerified);
  download.onclick = () => run(fetchVerified);
  save.onclick = () => run(saveVerified);
}

const api = { MANIFEST_URL, SAVE_NAME, parseVersion, compareVersions, parseManifest, readManifest, downloadVerified, saveBytes, init };
if (typeof module !== 'undefined' && module.exports) module.exports = api; else root.VDRUpdate = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
