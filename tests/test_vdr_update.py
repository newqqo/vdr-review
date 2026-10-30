"""Update check and download with mocked fetch: nothing here reaches the network."""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "scripts" / "vdr_review" / "src"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(not NODE, reason="node is not installed")
sys.path.insert(0, str(ROOT / "scripts" / "vdr_review"))
import build as BUILD  # noqa: E402

PRELUDE = """
const path=%s;
const I=require(path+'/i18n_ui.js'),U=require(path+'/update.js'),nodeCrypto=require('node:crypto');
const sha=b=>nodeCrypto.createHash('sha256').update(b).digest('hex');
const PAGE=Buffer.from('<!doctype html><title>synthetic new version</title>');
const HTML_URL=new URL('vdr-review.html',U.MANIFEST_URL).href;
const manifest=(extra={})=>JSON.stringify({version:'1.1.0',released:'2026-01-02',html:'vdr-review.html',sha256:sha(PAGE),
 bytes:PAGE.length,notes_url:'https://github.example/releases/tag/v1.1.0',...extra});
const ok=body=>({ok:true,status:200,text:async()=>String(body),arrayBuffer:async()=>{const b=Buffer.from(body);return b.buffer.slice(b.byteOffset,b.byteOffset+b.byteLength);}});
const missing=status=>({ok:false,status,text:async()=>'',arrayBuffer:async()=>new ArrayBuffer(0)});
const timers=[];globalThis.setTimeout=(f,ms)=>{timers.push({f,ms});return timers.length;};globalThis.clearTimeout=()=>{};
const failure=fn=>{try{return {value:fn()};}catch(e){return {key:e.updateKey,params:e.updateParams};}};
function harness({protocol='file:',version='1.0.0',dirty=false,answer=true,routes={},picker}={}){
 const nodes={},calls=[],dialogs=[],anchors=[],blobs=[];
 const $=id=>nodes[id]||(nodes[id]={id,dataset:{},hidden:id!=='updateAction',disabled:false,textContent:'',href:'',classes:new Set(),
  classList:{toggle(name,on){if(on)nodes[id].classes.add(name);else nodes[id].classes.delete(name);}}});
 globalThis.location={protocol};
 globalThis.fetch=async(url,options)=>{calls.push({url,options});const route=routes[url];
  if(route===undefined)throw new TypeError('Failed to fetch');return typeof route==='function'?route(options):route;};
 globalThis.confirm=text=>{dialogs.push({text,fetchedBefore:calls.map(c=>c.url)});return answer;};
 globalThis.showSaveFilePicker=picker;
 globalThis.document={createElement:()=>({click(){anchors.push({href:this.href,download:this.download});}})};
 URL.createObjectURL=b=>{blobs.push(b);return 'blob:synthetic';};URL.revokeObjectURL=()=>{};
 U.init({$,version,isDirty:()=>dirty});
 const view=()=>{const s=$('updateStatus');return {key:s.dataset.i18n,params:JSON.parse(s.dataset.i18nParams||'{}'),text:s.textContent,
  error:s.classes.has('err'),panel:!$('updatePanel').hidden,notes:!$('updateNotes').hidden,href:$('updateNotes').href,
  download:!$('updateDownload').hidden,save:!$('updateSave').hidden,busy:$('updateAction').disabled,
  action:$('updateAction').textContent,actionKey:$('updateAction').dataset.i18n,fetched:calls.map(c=>c.url)};};
 return {press:id=>$(id).onclick(),view,calls,dialogs,anchors,blobs,nodes};
}
function savingPicker(log,name='VDR-old.html'){return async options=>{log.options=options;return {name,createWritable:async()=>({
 write:async b=>{log.bytes=Buffer.from(await b.arrayBuffer()).toString();log.type=b.type;},close:async()=>{log.closed=true;},abort:async()=>{log.aborted=true;}})};};}
""" % json.dumps(SRC.as_posix())


def scenario(body):
    # An unhandled rejection exits Node with an error, which check=True turns into a failure.
    code = PRELUDE + "\n(async()=>{\n" + body + "\n})().catch(e=>{console.error(e);process.exitCode=1;});"
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(out.stdout)


def test_version_comparison_is_numeric_and_rejects_illegal_strings():
    result = scenario("""
const compare=[['1.10.0','1.9.0'],['1.0.0','1.0.0'],['1.0.0','1.0.1'],['2.0.0','1.99.99'],['0.0.1','0.0.0']].map(p=>U.compareVersions(...p));
const bad=['','1.0','1.0.0.0','v1.0.0','1.0.0-rc.1','01.0.0','1.0.x',' 1.0.0','1.0.0\\n','99999999999999999999.0.0',1,null,{}]
 .map(v=>failure(()=>U.parseVersion(v)).key);
console.log(JSON.stringify({compare,bad,other:failure(()=>U.compareVersions('1.0.0','latest'))}));""")
    assert result["compare"] == [1, 0, -1, 1, 1]
    assert set(result["bad"]) == {"update.badVersion"}
    assert result["other"] == {"key": "update.badVersion", "params": {"version": "latest"}}


def test_manifest_validation_fails_closed():
    result = scenario("""
const good=U.parseManifest(manifest());
const fields=[{released:'soon'},{released:1},{html:'../x.html'},{html:'https://other.example/x.html'},{html:'x.js'},{html:null},
 {sha256:'ABC'},{sha256:sha(PAGE).toUpperCase()},{sha256:null},{bytes:0},{bytes:-1},{bytes:1.5},{bytes:'51'},{bytes:1e12},
 {notes_url:'javascript:alert(1)'},{notes_url:'http://github.example/x'},{notes_url:'not a url'},{notes_url:null}];
const broken=['','not json','null','[]','7','{}'];
console.log(JSON.stringify({good,fields:fields.map(f=>failure(()=>U.parseManifest(manifest(f))).key),
 broken:broken.map(t=>failure(()=>U.parseManifest(t)).key),version:failure(()=>U.parseManifest(manifest({version:'latest'}))).key}));""")
    assert result["good"]["htmlUrl"] == "https://newqqo.github.io/vdr-review/vdr-review.html"
    assert set(result["fields"]) == {"update.badManifest"}
    assert result["broken"] == ["update.badManifest"] * 5 + ["update.badVersion"]
    assert result["version"] == "update.badVersion"


@pytest.mark.parametrize("protocol, label", [("file:", "update.check"), ("https:", "update.downloadOffline")])
def test_opening_the_page_makes_no_request_and_sets_the_button_for_the_mode(protocol, label):
    result = scenario(f"""
const h=harness({{protocol:{json.dumps(protocol)}}});
await new Promise(resolve=>setImmediate(resolve));
console.log(JSON.stringify(h.view()));""")
    assert result["fetched"] == [] and result["panel"] is False
    assert result["actionKey"] == label


def test_same_version_reports_latest_without_download_controls():
    result = scenario("""
const h=harness({version:'1.1.0',routes:{[U.MANIFEST_URL]:ok(manifest())}});
await h.press('updateAction');
console.log(JSON.stringify({...h.view(),options:h.calls[0].options}));""")
    assert result["key"] == "update.latest" and result["params"] == {"current": "1.1.0", "latest": "1.1.0"}
    assert result["notes"] is False and result["download"] is False and result["error"] is False
    assert result["fetched"] == ["https://newqqo.github.io/vdr-review/version.json"]
    assert result["options"]["cache"] == "no-store" and result["options"]["credentials"] == "omit"


def test_copy_newer_than_published_is_not_offered_a_downgrade():
    result = scenario("""
const h=harness({version:'2.0.0',routes:{[U.MANIFEST_URL]:ok(manifest())}});
await h.press('updateAction');
console.log(JSON.stringify(h.view()));""")
    assert result["key"] == "update.latest" and result["download"] is False


def test_newer_version_shows_details_and_downloads_only_on_request():
    result = scenario("""
const h=harness({routes:{[U.MANIFEST_URL]:ok(manifest())}});
await h.press('updateAction');
console.log(JSON.stringify(h.view()));""")
    assert result["key"] == "update.available"
    assert result["params"] == {"version": "1.1.0", "date": "2026-01-02", "current": "1.0.0"}
    assert "v1.1.0" in result["text"] and "2026-01-02" in result["text"]
    assert result["notes"] is True and result["href"] == "https://github.example/releases/tag/v1.1.0"
    assert result["download"] is True and result["save"] is False
    assert result["fetched"] == ["https://newqqo.github.io/vdr-review/version.json"]


def test_verified_download_waits_for_a_second_click_then_saves_over_the_chosen_file():
    result = scenario("""
const log={};let opened=0;const picker=savingPicker(log);
const h=harness({picker:o=>{opened++;return picker(o);},routes:{[U.MANIFEST_URL]:ok(manifest()),[HTML_URL]:ok(PAGE)}});
await h.press('updateAction');await h.press('updateDownload');
const verified={...h.view(),opened};
await h.press('updateSave');
console.log(JSON.stringify({verified,saved:h.view(),log,page:PAGE.toString()}));""")
    assert result["verified"]["key"] == "update.verified" and result["verified"]["save"] is True
    assert result["verified"]["download"] is False and result["verified"]["opened"] == 0
    log = result["log"]
    assert log["options"]["suggestedName"] == "VDR核聽工具.html"
    assert log["bytes"] == result["page"] and log["closed"] is True and log["type"] == "text/html"
    assert result["saved"]["key"] == "update.saved" and result["saved"]["params"] == {"name": "VDR-old.html"}
    assert result["saved"]["save"] is False and result["saved"]["error"] is False


def test_without_a_file_picker_the_verified_page_goes_to_a_blob_download():
    result = scenario("""
const h=harness({routes:{[U.MANIFEST_URL]:ok(manifest()),[HTML_URL]:ok(PAGE)}});
await h.press('updateAction');await h.press('updateDownload');await h.press('updateSave');
console.log(JSON.stringify({view:h.view(),anchors:h.anchors,blob:Buffer.from(await h.blobs[0].arrayBuffer()).toString(),page:PAGE.toString()}));""")
    assert result["anchors"] == [{"href": "blob:synthetic", "download": "VDR核聽工具.html"}]
    assert result["blob"] == result["page"]
    assert result["view"]["key"] == "update.downloaded" and result["view"]["save"] is False


def test_policy_blocked_picker_falls_back_to_a_blob_download():
    result = scenario("""
const h=harness({picker:async()=>{throw Object.assign(new Error('blocked'),{name:'SecurityError'});},routes:{[U.MANIFEST_URL]:ok(manifest()),[HTML_URL]:ok(PAGE)}});
await h.press('updateAction');await h.press('updateDownload');await h.press('updateSave');
console.log(JSON.stringify({view:h.view(),anchors:h.anchors.length}));""")
    assert result["anchors"] == 1 and result["view"]["key"] == "update.downloaded"


@pytest.mark.parametrize("tamper", ["{sha256:'0'.repeat(64)}", "{bytes:PAGE.length+1}"])
def test_hash_or_size_mismatch_is_rejected_and_nothing_can_be_saved(tamper):
    result = scenario(f"""
let opened=0;
const h=harness({{picker:async()=>{{opened++;throw new Error('must not open');}},routes:{{[U.MANIFEST_URL]:ok(manifest({tamper})),[HTML_URL]:ok(PAGE)}}}});
await h.press('updateAction');await h.press('updateDownload');
console.log(JSON.stringify({{...h.view(),opened,anchors:h.anchors.length}}));""")
    assert result["key"] == "update.hashMismatch" and result["error"] is True
    assert result["save"] is False and result["opened"] == 0 and result["anchors"] == 0
    assert result["download"] is True and result["busy"] is False


def test_network_and_format_failures_give_a_clear_message_and_recover():
    result = scenario("""
const outcome=async routes=>{const h=harness({routes});await h.press('updateAction');const v=h.view();
 return {key:v.key,params:v.params,error:v.error,download:v.download,busy:v.busy};};
const results={offline:await outcome({}),
 missing:await outcome({[U.MANIFEST_URL]:missing(404)}),
 blocked:await outcome({[U.MANIFEST_URL]:async()=>{throw new TypeError('CORS');}}),
 json:await outcome({[U.MANIFEST_URL]:ok('<html>not json</html>')}),
 version:await outcome({[U.MANIFEST_URL]:ok(manifest({version:'latest'}))})};
const h=harness({routes:{[U.MANIFEST_URL]:missing(503)}});
await h.press('updateAction');const before=h.view().key;
h.calls.length=0;globalThis.fetch=async()=>ok(manifest());
await h.press('updateAction');
console.log(JSON.stringify({results,before,after:h.view().key,error:h.view().error}));""")
    results = result["results"]
    assert results["offline"]["key"] == "update.unreachable"
    assert results["blocked"]["key"] == "update.unreachable"
    assert results["missing"]["key"] == "update.httpError" and results["missing"]["params"] == {"status": 404}
    assert results["json"]["key"] == "update.badManifest"
    assert results["version"]["key"] == "update.badVersion" and results["version"]["params"] == {"version": "latest"}
    assert all(item["error"] and not item["download"] and not item["busy"] for item in results.values())
    assert result["before"] == "update.httpError" and result["after"] == "update.available" and result["error"] is False


def test_html_download_failures_keep_the_retry_button():
    result = scenario("""
const outcome=async route=>{const h=harness({routes:{[U.MANIFEST_URL]:ok(manifest()),[HTML_URL]:route}});
 await h.press('updateAction');await h.press('updateDownload');const v=h.view();return {key:v.key,download:v.download,save:v.save};};
console.log(JSON.stringify({missing:await outcome(missing(404)),offline:await outcome(async()=>{throw new TypeError('offline');})}));""")
    assert result["missing"] == {"key": "update.httpError", "download": True, "save": False}
    assert result["offline"] == {"key": "update.unreachable", "download": True, "save": False}


def test_stalled_request_times_out_as_unreachable():
    result = scenario("""
const stalled=options=>new Promise((_,reject)=>options.signal.addEventListener('abort',()=>reject(new DOMException('stop','AbortError'))));
const h=harness({routes:{[U.MANIFEST_URL]:stalled}});
const pending=h.press('updateAction');
await new Promise(resolve=>setImmediate(resolve));
const during=h.view();
timers.find(t=>t.ms>=60000).f();
await pending;
console.log(JSON.stringify({during:{key:during.key,busy:during.busy},after:h.view().key,busy:h.view().busy}));""")
    assert result["during"] == {"key": "update.checking", "busy": True}
    assert result["after"] == "update.unreachable" and result["busy"] is False


def test_missing_web_crypto_stops_before_downloading():
    result = scenario("""
const h=harness({routes:{[U.MANIFEST_URL]:ok(manifest()),[HTML_URL]:ok(PAGE)}});
await h.press('updateAction');
Object.defineProperty(globalThis,'crypto',{value:undefined,configurable:true});
await h.press('updateDownload');
console.log(JSON.stringify(h.view()));""")
    assert result["key"] == "update.noCrypto" and result["error"] is True and result["save"] is False
    assert result["fetched"] == ["https://newqqo.github.io/vdr-review/version.json"]


def test_unsaved_work_prompts_before_any_download_and_declining_stops_it():
    result = scenario("""
const routes={[U.MANIFEST_URL]:ok(manifest()),[HTML_URL]:ok(PAGE)};
const declined=harness({dirty:true,answer:false,routes});
await declined.press('updateAction');await declined.press('updateDownload');
const first={dialogs:declined.dialogs,view:declined.view()};
const accepted=harness({dirty:true,answer:true,routes});
await accepted.press('updateAction');await accepted.press('updateDownload');
const clean=harness({dirty:false,routes});
await clean.press('updateAction');await clean.press('updateDownload');
console.log(JSON.stringify({first,accepted:{dialogs:accepted.dialogs.length,key:accepted.view().key},clean:clean.dialogs.length,manifestUrl:U.MANIFEST_URL,prompt:I.t('update.dirtyConfirm')}));""")
    manifest_url = result["manifestUrl"]
    assert len(result["first"]["dialogs"]) == 1
    assert result["first"]["dialogs"][0]["text"] == result["prompt"]
    assert result["first"]["dialogs"][0]["fetchedBefore"] == [manifest_url]
    assert result["first"]["view"]["fetched"] == [manifest_url]
    assert result["first"]["view"]["key"] == "update.available" and result["first"]["view"]["download"] is True
    assert result["accepted"] == {"dialogs": 1, "key": "update.verified"}
    assert result["clean"] == 0


def test_cancelling_the_picker_changes_nothing_and_save_can_be_retried():
    result = scenario("""
const log={};let attempts=0;const picker=savingPicker(log);
const h=harness({picker:o=>{attempts++;if(attempts===1)throw Object.assign(new Error('cancel'),{name:'AbortError'});return picker(o);},
 routes:{[U.MANIFEST_URL]:ok(manifest()),[HTML_URL]:ok(PAGE)}});
await h.press('updateAction');await h.press('updateDownload');
await h.press('updateSave');const cancelled=h.view();
await h.press('updateSave');
console.log(JSON.stringify({cancelled:{key:cancelled.key,save:cancelled.save,error:cancelled.error},saved:h.view().key,log,page:PAGE.toString()}));""")
    assert result["cancelled"] == {"key": "update.saveCancelled", "save": True, "error": False}
    assert result["saved"] == "update.saved" and result["log"]["bytes"] == result["page"]


def test_failed_write_aborts_warns_and_can_be_retried():
    result = scenario("""
const log={};let attempts=0;
const h=harness({picker:async()=>({name:'old.html',createWritable:async()=>({write:async()=>{attempts++;if(attempts===1)throw new Error('disk full');},
 close:async()=>{log.closed=true;},abort:async()=>{log.aborted=true;}})}),routes:{[U.MANIFEST_URL]:ok(manifest()),[HTML_URL]:ok(PAGE)}});
await h.press('updateAction');await h.press('updateDownload');
await h.press('updateSave');const failed=h.view();
await h.press('updateSave');
console.log(JSON.stringify({failed:{key:failed.key,params:failed.params,save:failed.save,error:failed.error},log,saved:h.view().key}));""")
    assert result["failed"] == {"key": "update.saveFailed", "params": {"reason": "disk full"}, "save": True, "error": True}
    assert result["log"]["aborted"] is True and result["saved"] == "update.saved"


def test_online_page_offers_the_offline_copy_through_the_same_verified_flow():
    result = scenario("""
const log={};
const h=harness({protocol:'https:',version:'1.1.0',dirty:true,picker:savingPicker(log),routes:{[U.MANIFEST_URL]:ok(manifest()),[HTML_URL]:ok(PAGE)}});
const label=h.view().action;
await h.press('updateAction');const verified=h.view();
await h.press('updateSave');
console.log(JSON.stringify({label,expected:I.t('update.downloadOffline'),verified,dialogs:h.dialogs.length,saved:h.view().key,log,page:PAGE.toString()}));""")
    assert result["label"] == result["expected"] and result["dialogs"] == 1
    assert result["verified"]["key"] == "update.verified" and result["verified"]["save"] is True
    assert result["verified"]["notes"] is False and result["verified"]["download"] is False
    assert result["verified"]["fetched"] == ["https://newqqo.github.io/vdr-review/version.json",
                                             "https://newqqo.github.io/vdr-review/vdr-review.html"]
    assert result["saved"] == "update.saved" and result["log"]["bytes"] == result["page"]


def test_online_page_rejects_a_tampered_download():
    result = scenario("""
let opened=0;
const h=harness({protocol:'https:',picker:async()=>{opened++;},routes:{[U.MANIFEST_URL]:ok(manifest({sha256:'0'.repeat(64)})),[HTML_URL]:ok(PAGE)}});
await h.press('updateAction');
console.log(JSON.stringify({...h.view(),opened}));""")
    assert result["key"] == "update.hashMismatch" and result["save"] is False and result["opened"] == 0


def test_repeated_clicks_while_busy_do_not_start_a_second_request():
    result = scenario("""
const h=harness({routes:{[U.MANIFEST_URL]:ok(manifest())}});
const first=h.press('updateAction'),second=h.press('updateAction');
await Promise.all([first,second]);
console.log(JSON.stringify(h.view()));""")
    assert len(result["fetched"]) == 1 and result["key"] == "update.available"


def test_status_stays_bound_so_a_language_switch_retranslates_it():
    result = scenario("""
const h=harness({routes:{[U.MANIFEST_URL]:ok(manifest())}});
await h.press('updateAction');
const status=h.nodes.updateStatus,texts={};
for(const locale of ['zh-TW','en','ja','zh-CN']){I.setLocale(locale);texts[locale]=I.t(status.dataset.i18n,JSON.parse(status.dataset.i18nParams));}
I.setLocale('zh-TW');
console.log(JSON.stringify(texts));""")
    assert len(set(result.values())) == 4
    assert all("v1.1.0" in text and "2026-01-02" in text and "v1.0.0" in text for text in result.values())


def test_real_site_output_is_accepted_and_a_changed_byte_is_rejected(tmp_path):
    site = tmp_path / "_site"
    assert BUILD.main(["--site", str(site)]) == 0
    result = scenario(f"""
const fs=require('node:fs'),site={json.dumps(site.as_posix())};
const text=fs.readFileSync(site+'/version.json','utf8'),page=fs.readFileSync(site+'/vdr-review.html');
const manifest=U.parseManifest(text);
globalThis.fetch=async()=>ok(page);
const verified=await U.downloadVerified(manifest);
const changed=Buffer.from(page);changed[100]^=1;
globalThis.fetch=async()=>ok(changed);
const rejected=await U.downloadVerified(manifest).catch(e=>e.updateKey);
console.log(JSON.stringify({{same:Buffer.compare(Buffer.from(verified),page)===0,rejected,htmlUrl:manifest.htmlUrl}}));""")
    assert result == {"same": True, "rejected": "update.hashMismatch",
                      "htmlUrl": "https://newqqo.github.io/vdr-review/vdr-review.html"}


def test_update_source_is_defined_once_and_only_update_js_touches_the_network():
    sources = {path.name: path.read_text(encoding="utf-8") for path in SRC.glob("*") if path.suffix in {".js", ".html"}}
    assert sum(text.count("newqqo.github.io") for text in sources.values()) == 1
    assert sources["update.js"].count("root.fetch(") == 1
    network = re.compile(r"\bfetch\(|XMLHttpRequest|sendBeacon|WebSocket|EventSource|importScripts")
    assert {name for name, text in sources.items() if network.search(text)} == {"update.js"}
    leaflet = (ROOT / "scripts/vdr_review/vendor/leaflet/leaflet.js").read_text(encoding="utf-8")
    assert not re.search(r"\bfetch\(|sendBeacon|WebSocket|EventSource", leaflet)


def test_downloaded_content_is_never_executed_or_injected():
    source = (SRC / "update.js").read_text(encoding="utf-8")
    for forbidden in ("eval(", "new Function", "innerHTML", "outerHTML", "insertAdjacentHTML", "document.write",
                      "srcdoc", "createContextualFragment", "importScripts", "location.href", "location.assign",
                      "location.replace", "window.open"):
        assert forbidden not in source, forbidden


def test_update_module_is_wired_into_the_page_and_every_string_exists():
    source = (SRC / "update.js").read_text(encoding="utf-8")
    page = (SRC / "app.html").read_text(encoding="utf-8")
    for element in set(re.findall(r"\$\('(update[A-Za-z]+)'\)", source)):
        assert page.count(f'id="{element}"') == 1, element
    assert BUILD.MAIN_MODULES.count("update.js") == 1
    assert BUILD.MAIN_MODULES.index("update.js") < BUILD.MAIN_MODULES.index("app.js")
    app = (SRC / "app.js").read_text(encoding="utf-8")
    line = next(line for line in app.splitlines() if "VDRUpdate.init(" in line)
    assert line.startswith("VDRUpdate.init(") and app.index(line) < app.index("async function startMain(")
    assert "BUILD_INFO.version" in line and "activeEditor?.dirty()" in line
    keys = sorted(set(re.findall(r"'(update\.[A-Za-z]+)'", source + page)) | set(re.findall(r'"(update\.[A-Za-z]+)"', page)))
    assert len(keys) >= 20
    result = scenario(f"""
const keys={json.dumps(keys)};
console.log(JSON.stringify(keys.filter(k=>!['zh-TW','en','ja','zh-CN'].every(l=>{{I.setLocale(l);return I.t(k)!==k;}}))));""")
    assert result == []
