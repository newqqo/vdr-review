// 逐字稿核聽編輯面板：播放時間＝本次時段開始後的秒數（與匯出的 WAV 同基準）。
// ctx：{au, sel, channels:[...], T0（UTC秒）, LT（時區秒）, ENV:{ch:Uint8Array}, ENVR, VHF:{ch:[[s,e]]}, KEY, NAME, wav(ch)->{blob,rate}, extra:{get,set}, exportWav(ch)}
(function (root) {
'use strict';
if(typeof module!=='undefined'&&module.exports){require('./i18n_editor.js');if(typeof globalThis.VDRI18n==='undefined')globalThis.VDRI18n=require('./i18n.js');}
const tr=(key,params)=>VDRI18n.t('editor.'+key,params);
function editorError(key,params={},paramKeys={}){const error=new Error(tr(key,params));error.i18nKey='editor.'+key;error.i18nParams=params;error.i18nParamKeys=paramKeys;return error;}
const errorText=error=>VDRI18n.errorText(error);
// File writes are explicit. Browser recovery never counts as a saved case file.
function createFileSession(writer){
 let revision=0,savedRevision=0,handle=null,busy=false,generation=0;
 return {
  changed(){revision++;},dirty:()=>revision!==savedRevision,handle:()=>handle,
  replace(h=null){revision++;savedRevision=revision;handle=h;generation++;},
  async save(name,data){
   if(busy)return {saved:false,busy:true};
   busy=true;const at=revision,started=generation;
   try{const result=await writer({name,data,handle});
    if(started!==generation)return {...result,superseded:true};
    if(result.saved){handle=result.handle;if(revision===at)savedRevision=at;}
    return result;
   }finally{busy=false;}
  }
 };
}
function invalidateReviewedChanges(previous,state){
 const changed=[];
 for(const [ch,arr] of Object.entries(state)){
  const old=new Map((previous[ch]||[]).map(c=>[c.id,c]));
  for(const c of arr){const p=old.get(c.id);if(c.timing_review){
   if(p&&(p.start!==c.start||p.end!==c.end)){c.timing_origin={...c.timing_review,resolution:'time_adjusted_in_editor'};delete c.timing_review;c.st=0;changed.push(c);}
   else if(c.st!==0){c.st=0;changed.push(c);}
  }else if(p&&p.st===1&&
   ['text','spk','start','end','ovl','repOf'].some(k=>p[k]!==c[k])){c.st=0;changed.push(c);}}
 }
 return changed;
}
// SRT times are relative to the audio handed to the transcription service, not
// necessarily the current review window. Parse the whole file before any edit.
function parseSrt(text){
 if(typeof text!=='string'||text.length>20000000||text.includes('\u0000'))throw editorError('srtInvalid');
 const input=text.replace(/^\uFEFF/,'').replace(/\r\n?/g,'\n').trim();
 if(!input)throw editorError('srtEmpty');
 const blocks=input.split(/\n(?:[ \t]*\n)+/),got=[];
 if(blocks.length>200000)throw editorError('srtTooMany');
 for(let i=0;i<blocks.length;i++){
  const lines=blocks[i].trim().split('\n'),k=lines.findIndex(l=>l.includes('-->'));
  if(k<0||k>1||(k===1&&(!/^\d+$/.test(lines[0].trim())||lines[0].trim().length>1000)))throw editorError('srtBlockInvalid',{number:i+1});
  const m=/^\s*(\d+):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d+):(\d{2}):(\d{2})[,.](\d{1,3})\s*$/.exec(lines[k]);
  if(!m)throw editorError('srtTimeFormat',{number:i+1});
  const g=m.slice(1).map(Number),s=g[0]*3600+g[1]*60+g[2]+Number(m[4].padEnd(3,'0'))/1000,
   e=g[4]*3600+g[5]*60+g[6]+Number(m[8].padEnd(3,'0'))/1000;
  if(g[1]>59||g[2]>59||g[5]>59||g[6]>59||!Number.isFinite(s)||!Number.isFinite(e)||e<=s||e>3155760000)
   throw editorError('srtTimeInvalid',{number:i+1});
  const body=lines.slice(k+1).join('\n').trim();
  if(body.includes('-->')||body.length>1000000)throw editorError('srtBodyInvalid',{number:i+1});
  got.push({s,e,text:body,no:k===1?lines[0].trim():null});
 }
 return got;
}
async function readSrtFile(file){
 // Hash the original bytes, including BOM and line endings, before decoding.
 // Re-encoding File.text() would identify a different file in those cases.
 if(!file||typeof file.arrayBuffer!=='function'||file.size>20000000)throw editorError('srtInvalid');
 const bytes=await file.arrayBuffer();
 if(!(bytes instanceof ArrayBuffer)||bytes.byteLength>20000000)throw editorError('srtInvalid');
 const projectApi=typeof module!=='undefined'&&module.exports?require('./project.js'):root.VDRProject;
 const srt_sha256=await projectApi.sha256(bytes);
 return {got:parseSrt(new TextDecoder('utf-8').decode(bytes)),srt_sha256,srt_bytes:bytes.byteLength};
}
function receiptUtc(value,labelKey){
 const label=tr(labelKey),paramKeys={label:'editor.'+labelKey};
 if(typeof value!=='string')throw editorError('utcRequired',{label},paramKeys);
 const m=/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,3}))?Z$/.exec(value);
 if(!m)throw editorError('utcZRequired',{label},paramKeys);
 const [y,mo,d,h,mi,se]=m.slice(1,7).map(Number),t=Date.parse(value),v=new Date(t);
 if(y<100||!Number.isFinite(t)||v.getUTCFullYear()!==y||v.getUTCMonth()!==mo-1||v.getUTCDate()!==d||h>23||mi>59||se>59)
  throw editorError('utcInvalid',{label},paramKeys);
 return t/1000;
}
function validateTranscriptionReceipt(data,recordingId,channels){
 const object=v=>v&&typeof v==='object'&&!Array.isArray(v),hash=/^[a-f0-9]{64}$/i;
 if(!object(data)||data.kind!=='vdr-transcription-export'||data.version!==1)throw editorError('receiptUnsupported');
 if(!hash.test(recordingId||'')||!hash.test(data.recording_id||'')||data.recording_id.toLowerCase()!==recordingId.toLowerCase())
  throw editorError('receiptForeign');
 if(typeof data.channel!=='string'||! /^[AC][1-9][0-9]?$/.test(data.channel)||!Array.isArray(channels)||!channels.includes(data.channel))
  throw editorError('receiptChannelMissing');
 const start=receiptUtc(data.source_start_utc,'sourceStart'),end=receiptUtc(data.source_end_utc,'sourceEnd');
 if(end<=start)throw editorError('receiptRangeInvalid');
 if(!Number.isFinite(data.display_tz_hours)||Math.abs(data.display_tz_hours)>14)throw editorError('receiptZoneInvalid');
 const correction=data.time_correction;
 if(!object(correction)||!Number.isFinite(correction.offset_s)||Math.abs(correction.offset_s)>3600||
  typeof correction.basis!=='string'||correction.basis.length>10000||correction.basis.includes('\u0000')||(correction.offset_s&&!correction.basis.trim()))
  throw editorError('receiptCorrectionInvalid');
 if(data.corrected_start_utc!=null&&Math.abs(receiptUtc(data.corrected_start_utc,'correctedStart')-start-correction.offset_s)>0.001)
  throw editorError('receiptCorrectionMismatch');
 if(typeof data.audio_file!=='string'||!data.audio_file.trim()||data.audio_file.length>1000||/\u0000/.test(data.audio_file)||!hash.test(data.audio_sha256||''))
  throw editorError('receiptAudioMissing');
 return {kind:data.kind,version:1,recording_id:recordingId.toLowerCase(),channel:data.channel,
  source_start_utc:new Date(start*1000).toISOString(),source_end_utc:new Date(end*1000).toISOString(),
  display_tz_hours:data.display_tz_hours,corrected_start_utc:new Date((start+correction.offset_s)*1000).toISOString(),
  time_correction:{offset_s:correction.offset_s,basis:correction.basis},audio_file:data.audio_file,audio_sha256:data.audio_sha256.toLowerCase()};
}
function srtAlignment(got,options){
 const o=options||{};
 if(!Array.isArray(got)||!got.length||got.some(c=>!Number.isFinite(c.s)||!Number.isFinite(c.e)||c.s<0||c.e<=c.s))throw editorError('subtitleRangeInvalid');
 const offset=o.offsetSeconds==null?0:o.offsetSeconds;
 if(!Number.isFinite(o.t0)||!Number.isFinite(offset)||Math.abs(offset)>3600||
  !Number.isFinite(o.displayTzHours)||Math.abs(o.displayTzHours)>14||!Array.isArray(o.channels)||!o.channels.includes(o.channel))
  throw editorError('recordingSettingsInvalid');
 let zero,receipt=null;
 if(o.receipt){
  receipt=validateTranscriptionReceipt(o.receipt,o.recordingId,o.channels);
  if(receipt.channel!==o.channel)throw editorError('receiptChannelMismatch');
  zero=Date.parse(receipt.source_start_utc)/1000;
  const length=(Date.parse(receipt.source_end_utc)-Date.parse(receipt.source_start_utc))/1000;
  if(got.some(c=>c.e>length+0.01))throw editorError('srtExceedsAudio');
 }else{
  // datetime-local normalizes an exact minute to YYYY-MM-DDTHH:mm even when
  // step permits milliseconds. Only seconds may be omitted; never the date.
  const m=/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,3}))?)?$/.exec(String(o.localStart||'').trim());
  if(!m)throw editorError('localStartRequired');
  const [y,mo,d,h,mi,se]=m.slice(1,7).map(x=>Number(x||0)),ms=+(m[7]||'0').padEnd(3,'0'),t=Date.UTC(y,mo-1,d,h,mi,se,ms),v=new Date(t);
  if(y<100||v.getUTCFullYear()!==y||v.getUTCMonth()!==mo-1||v.getUTCDate()!==d||h>23||mi>59||se>59)throw editorError('localStartInvalid');
  zero=t/1000-o.displayTzHours*3600-offset;
 }
 const shift=zero-o.t0;
 if(got.some(c=>Math.abs(c.s+shift)>3155760000||Math.abs(c.e+shift)>3155760000))throw editorError('alignmentOutOfRange');
 return {shift,zero_utc:new Date(zero*1000).toISOString(),receipt,
  start:got.reduce((n,c)=>Math.min(n,c.s),Infinity)+shift,end:got.reduce((n,c)=>Math.max(n,c.e),-Infinity)+shift};
}
function countSrtOverlaps(got,existing,shift){
 const ordered=existing.map(c=>[c.start,c.end]).sort((a,b)=>a[0]-b[0]||a[1]-b[1]),ranges=[];
 for(const r of ordered){const last=ranges[ranges.length-1];if(last&&r[0]<=last[1])last[1]=Math.max(last[1],r[1]);else ranges.push(r);}
 let count=0;for(const c of got){const start=c.s+shift,end=c.e+shift;let lo=0,hi=ranges.length;
  while(lo<hi){const mid=(lo+hi)>>>1;if(ranges[mid][1]<=start)lo=mid+1;else hi=mid;}
  if(lo<ranges.length&&ranges[lo][0]<end)count++;
 }return count;
}
function waveWindow(time,span,duration){
 const end=Number.isFinite(duration)&&duration>0?duration:Infinity;
 const size=Number.isFinite(span)&&span>0?span:60,t=Math.min(end,Math.max(0,Number.isFinite(time)?time:0));
 let a=Math.max(0,t-size*0.2),b=Math.min(end,a+size);a=Math.max(0,b-size);
 return [a,b];
}
function waveTimeAt(range,clientX,left,width,duration){
 if(!range||range.length!==2||!Number.isFinite(width)||width<=0)return null;
 const value=range[0]+Math.max(0,Math.min(1,(clientX-left)/width))*(range[1]-range[0]);
 return Math.min(Number.isFinite(duration)?duration:Infinity,Math.max(0,value));
}
function activityWindow(segments,start,end,limit=40){
 const shown=[],cap=Number.isInteger(limit)&&limit>0?Math.min(200,limit):40;let total=0;
 if(!Number.isFinite(start)||!Number.isFinite(end)||end<=start)return {segments:shown,total,omitted:0};
 for(const pair of Array.isArray(segments)?segments:[]){
  if(!Array.isArray(pair)||!Number.isFinite(pair[0])||!Number.isFinite(pair[1])||pair[1]<=pair[0]||pair[1]<=start||pair[0]>=end)continue;
  total++;if(shown.length<cap)shown.push({start:pair[0],end:pair[1],visibleStart:Math.max(start,pair[0]),visibleEnd:Math.min(end,pair[1])});
 }
 return {segments:shown,total,omitted:total-shown.length};
}
function init(ctx){
const {au,sel,T0,LT,ENV,KEY}=ctx,CH=ctx.channels,ENVD={rate:ctx.ENVR},VHF=ctx.VHF||{};
const ui=VDRI18n.t;
const STATUS=index=>ui(['review.unreviewed','review.reviewed','review.partial','review.unclear'][index]), STCLS=['s0','s1','s2','s3'];
// Names in existing projects are data. Only a new blank case receives localized
// starter roles, once; switching language never renames any stored speaker.
const DEFAULT_SPK=['roleCaptain','rolePilot','roleChiefOfficer','roleSecondOfficer','roleThirdOfficer','roleHelmsman','roleBosun','roleEngine','roleVts','roleSignalStation','roleTug','roleOtherVessel','roleUnknown'].map(key=>tr(key));
const PALETTE=['#1f77b4','#d62728','#2ca02c','#9467bd','#ff7f0e','#8c564b','#e377c2','#17becf','#bcbd22','#7f7f7f','#393b79','#637939','#843c39','#7b4173'];
const $=id=>document.getElementById(id), list=$('edlist');
const fmtT=s=>{if(s==null||isNaN(s))return'';const m=Math.floor(s/60),x=s-m*60;return String(m).padStart(2,'0')+':'+x.toFixed(1).padStart(4,'0')};
const fmtFile=s=>{s=T0+s+LT;s=((s%86400)+86400)%86400;const h=Math.floor(s/3600),m=Math.floor(s%3600/60),x=Math.floor(s%60);return String(h).padStart(2,'0')+':'+String(m).padStart(2,'0')+':'+String(x).padStart(2,'0')};
const fmtDate=(s,offset=0)=>new Date((T0+s+LT+offset)*1000).toISOString().replace('T',' ').slice(0,23)+' UTC'+(LT>=0?'+':'')+(LT/3600);
const fmtT2=s=>{const m=Math.floor(s/60),x=s-m*60;return String(m).padStart(2,'0')+':'+x.toFixed(2).padStart(5,'0')};
const OFS=()=>(ctx.ofs?ctx.ofs():0),GAPS=ch=>(ctx.gaps?ctx.gaps(ch):[]);
const PENDING=ch=>(ctx.pending?ctx.pending(ch):0),KNOWN=ch=>(ctx.envKnown?ctx.envKnown(ch):null);
function rangeKnown(ch,start,end){const known=KNOWN(ch),R=ENVD.rate;if(!known)return !PENDING(ch);
 for(let k=Math.max(0,Math.floor(start*R));k<Math.min(known.length,Math.ceil(end*R));k++)if(!known[k])return false;return true;}
const fmtLT=s=>{s=T0+s+OFS()+LT;s=((s%86400)+86400)%86400;const h=Math.floor(s/3600),m=Math.floor(s%3600/60),x=Math.floor(s%60);return String(h).padStart(2,'0')+':'+String(m).padStart(2,'0')+':'+String(x).padStart(2,'0')};
const parseT=v=>{v=String(v).trim().replace(',','.');const p=v.split(':').map(Number);if(!v||p.some(isNaN))return null;return p.length===3?p[0]*3600+p[1]*60+p[2]:p.length===2?p[0]*60+p[1]:p[0]};
const srtT=s=>{const ms=Math.max(0,Math.round(s*1000)),h=Math.floor(ms/3600000),m=Math.floor(ms%3600000/60000),x=Math.floor(ms%60000/1000),r=ms%1000;return [h,m,x].map(n=>String(n).padStart(2,'0')).join(':')+','+String(r).padStart(3,'0')};
const esc=s=>String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');
let uid=1,state={},spks=DEFAULT_SPK.slice(),spkColor={};
let wvPage=null,flashUntil=0,applying=false,previousProject=null,cacheOK=true,migrationInfo=null,importHistory=[];
const files=createFileSession(args=>VDRProject.saveProject(args));
let lastState={},lastContent='',fileMessage=()=>ui('review.unsaved'),flashContent=null;
// Bind only editor-owned UI leaves. Updating a locale must never replace a cue
// textarea, commit a composer draft, or touch its selection/IME target.
const localizedText=new Map();
function ownText(node,get){const read=typeof get==='function'?get:()=>get;localizedText.set(node,read);node.textContent=read();return node;}
function staticText(node,key){return ownText(node,()=>tr(key));}
function textSpan(parent,key){const n=document.createElement('span');parent.appendChild(n);return staticText(n,key);}
const content=()=>JSON.stringify({state,spks,uid,spkColor,extra:ctx.extra.get(),import_history:importHistory});
function status(){
 $('edsave').title=tr(cacheOK?'cacheHelp':'cacheFailureHelp');
 if(Date.now()<flashUntil)return;
 $('edsave').textContent=(files.dirty()?tr('unsavedDot'):fileMessage())+(cacheOK?'':tr('cacheFailed'));
}
function project(){return VDRProject.createProject({version:4,tool:'VDR核聽工具',
 tool_build:typeof BUILD_INFO!=='undefined'?BUILD_INFO:null,recording_id:ctx.recordingId,
 t0_utc:new Date(T0*1000).toISOString(),tz_hours:LT/3600,coverage:ctx.coverage||null,
 saved_at:new Date().toISOString(),note:'播放時間＝本次時段開始後秒數；VDR檔名時間未經獨立校時',
 state,spks,uid,spkColor,extra:ctx.extra.get(),migration:migrationInfo,import_history:importHistory});}
function save(){if(applying)return;
 const reset=invalidateReviewedChanges(lastState,state),now=content();
 if(now!==lastContent){files.changed();lastContent=now;}
 lastState=JSON.parse(JSON.stringify(state));
 try{localStorage.setItem(KEY,JSON.stringify(project()));cacheOK=true;}catch(e){cacheOK=false;}
 for(const c of reset)updRow(c);if(reset.length){updTool();drawStrip();}
 status();ctx.onChange?.();
}
const shifted=ReviewState.shiftState;
let resumed=null;const oldProgress=[];
for(const L of(ctx.legacy||[])){try{const d=JSON.parse(localStorage.getItem(L.key)||'null');if(ReviewState.validProgress(d))oldProgress.push({...L,data:d})}catch(e){}}
try{const cached=JSON.parse(localStorage.getItem(KEY)||'null');
 if(cached){const d=VDRProject.validateImport(cached,ctx.recordingId),t0=Date.parse(d.t0_utc)/1000;
  state=shifted(d.state,t0-T0);spks=d.spks||spks;uid=d.uid;spkColor=d.spkColor||{};migrationInfo=d.migration||null;importHistory=d.import_history||[];
  if(d.extra)ctx.extra.set(d.extra);files.changed();
  const n=Object.values(state).reduce((a,v)=>a+v.length,0);resumed=()=>tr('recoveredCache',{count:n});}
}catch(e){resumed=()=>tr('rejectedCache');}
for(const ch of CH)if(!state[ch])state[ch]=[];
const color=s=>{if(!s)return'#b8c2c0';if(['__proto__','constructor','prototype'].includes(s))return'#7f7f7f';if(!/^#[0-9a-f]{3,8}$/i.test(spkColor[s]||'')){spkColor[s]=PALETTE[Object.keys(spkColor).length%PALETTE.length]}return spkColor[s]};
const cues=()=>state[sel.value]||(state[sel.value]=[]);
const sortCues=()=>cues().sort((a,b)=>a.start-b.start||a.end-b.end);
const find=id=>cues().find(c=>c.id===id);
let active=null,playUntil=null,boundedCueId=null,lastCur=null,composerSelected=false,playbackRowKey='';
const compose=$('edcompose');let composeEntry=null,composeIME=false,composeBlurred=false;
const SKEY=KEY+'_sugg';let SUG={};try{SUG=JSON.parse(localStorage.getItem(SKEY)||'{}')}catch(e){}
const sugOf=c=>{const m=(SUG.suggestions||{})[sel.value];return m?m[String(c.id)]:null};
// ---- 講者列 ----
function renderSpk(){$('spkbar').innerHTML=spks.map((s,i)=>'<button class="chip" data-spk="'+esc(s)+'" style="--c:'+color(s)+'" title="'+(i<9?'Alt+'+(i+1):'')+'">'+(i<9?'<b>'+(i+1)+'</b> ':'')+esc(s)+'</button>').join('')+
 '<button class="chip ghost" data-spk="" data-i18n="editor.clearSpeaker">'+esc(tr('clearSpeaker'))+'</button><input id="spkadd" data-i18n-placeholder="editor.newSpeaker" placeholder="'+esc(tr('newSpeaker'))+'" size="6" data-i18n-title="editor.newSpeakerHelp" title="'+esc(tr('newSpeakerHelp'))+'">';
 $('spkadd').onkeydown=e=>{if(e.key==='Enter'){const v=e.target.value.trim();if(v&&!spks.includes(v)){spks.push(v);color(v);save();renderSpk()}}}}
$('spkbar').addEventListener('click',e=>{const b=e.target.closest('.chip');if(!b)return;setSpk(b.dataset.spk)});
$('spkbar').addEventListener('contextmenu',e=>{const b=e.target.closest('.chip[data-spk]');if(!b||!b.dataset.spk)return;e.preventDefault();if(confirm(tr('removeSpeaker',{speaker:b.dataset.spk}))){spks=spks.filter(s=>s!==b.dataset.spk);save();renderSpk()}});
function setSpk(s){const c=find(active);if(!c){flash(()=>ui('action.chooseCue'));return}c.spk=s;save();updRow(c);drawStrip();updTool()}
// ---- 清單 ----
// Keep navigation inside the transcript scroller; following speech must not
// scroll the outer page and take the moving vessel off screen.
function scrollCueRow(row,center=false,smooth=false){
 const box=list.getBoundingClientRect(),item=row.getBoundingClientRect(),height=list.clientHeight;
 if(!height||!Number.isFinite(item.top)||!Number.isFinite(box.top))return;
 const top=box.top+(list.clientTop||0),bottom=top+height;
 if(!center&&item.top>=top&&item.bottom<=bottom)return;
 const offset=center?Math.max(0,(height-item.height)/2):(item.top<top||item.height>=height?0:height-item.height);
 let target=list.scrollTop+item.top-top-offset;
 target=Math.max(0,Math.min(Math.max(0,list.scrollHeight-height),target));
 if(typeof list.scrollTo==='function')list.scrollTo({top:target,behavior:smooth?'smooth':'auto'});else list.scrollTop=target;
}
function playbackRows(force=false){
 const t=au.currentTime||0,here=cues().filter(c=>t>=c.start&&t<c.end),ids=here.map(c=>c.id),playing=!au.paused;
 const key=VDRI18n.getLocale()+'|'+sel.value+'|'+playing+'|'+ids.join(',');if(!force&&key===playbackRowKey)return;playbackRowKey=key;
 const present=new Set(ids),byId=new Map(cues().map(c=>[c.id,c]));
 list.dataset.currentCues=ids.join(',');list.dataset.playingCues=playing?ids.join(','):'';
 for(const row of list.children){if(!row.dataset||!row.dataset.id)continue;
  const current=present.has(+row.dataset.id),on=current&&playing,c=byId.get(+row.dataset.id);
  row.classList.toggle('cur',current);row.classList.toggle('is-playing',on);row.dataset.playing=String(on);
  row.setAttribute('aria-current',current?'true':'false');
  const badge=row.querySelector('.cue-playstate');if(badge){badge.hidden=!current;badge.textContent=ui(on?'playback.playing':'playback.currentPosition');}
  const button=row.querySelector('.etime');if(button&&c){button.textContent=(on?'⏸ ':'▶ ')+fmtT(c.start);button.setAttribute('aria-label',ui(on?'playback.playingReplayLabel':'playback.playCueLabel',{time:fmtLT(c.start)}));}
 }
}
function rowHTML(c,i){const showOrig=c.orig&&c.orig!==c.text;
 return '<div class="ehd"><button class="etime" data-a="play" title="'+esc(ui('action.playCue'))+'（Ctrl+Enter）">▶ '+fmtT(c.start)+'</button><span class="elt">'+fmtLT(c.start)+'</span>'+
 '<span class="espk" style="--c:'+color(c.spk)+'"'+(c.spk?'':' data-i18n="review.unassignedSpeaker"')+'>'+esc(c.spk||ui('review.unassignedSpeaker'))+'</span><span class="est '+STCLS[c.st]+'" data-i18n="'+['review.unreviewed','review.reviewed','review.partial','review.unclear'][c.st]+'" data-i18n-title="'+['review.unreviewed','review.reviewed','review.partial','review.unclear'][c.st]+'" title="'+esc(STATUS(c.st))+'">'+esc(STATUS(c.st))+'</span><span class="cue-playstate" hidden></span>'+(c.timing_review?'<span class="eflag" data-i18n="review.timingPending" data-i18n-title="editor.timingHelp" title="'+esc(tr('timingHelp'))+'">'+esc(ui('review.timingPending'))+'</span>':'')+(c.ovl?'<span class="eflag" data-i18n="editor.overlap">'+esc(tr('overlap'))+'</span>':'')+(c.repOf?'<span class="eflag" data-i18n="editor.repeat">'+esc(tr('repeat'))+'</span>':'')+(()=>{const g=sugOf(c);if(!g||(c.spk&&!c.ovl)||g.spk===c.spk)return'';return '<button class="esug'+(g.confident?' hi':'')+'" data-a="acc" title="'+esc(tr('suggestionTitle',{score:g.score,margin:g.margin,caution:g.confident?'':tr('suggestionCaution')}))+'">'+esc(tr('suggestion',{speaker:g.spk,score:(+g.score).toFixed(2)}))+'</button>'})()+'<span class="eno">#'+(i+1)+'</span></div>'+
 '<textarea class="tx" rows="1" data-i18n-placeholder="workbench.inputPlaceholder" placeholder="'+esc(ui('workbench.inputPlaceholder'))+'" data-i18n-title="workbench.inputTitle" title="'+esc(ui('workbench.inputTitle'))+'"></textarea>'+(showOrig?'<div class="eorig"><span data-i18n="editor.original">'+esc(tr('original'))+'</span>'+esc(c.orig)+'</div>':'')}
function render(){const f=$('edflt').value,q=$('edq').value.trim();const keepTop=list.scrollTop;list.innerHTML='';
 const arr=cues();let shown=0;
 const DUR=ctx.dur||Infinity;let outside=0;
 arr.forEach((c,i)=>{if(c.end<=0||c.start>=DUR){outside++;return}if(f!=='all'&&String(c.st)!==f)return;if(q&&!((c.text||'')+(c.orig||'')+(c.spk||'')).includes(q))return;shown++;
  const d=document.createElement('div');d.className='er'+(c.ovl?' ovl':'')+(c.id===active?' act':'');d.dataset.id=c.id;d.style.setProperty('--c',color(c.spk));d.innerHTML=rowHTML(c,i);
  const ta=d.querySelector('.tx');ta.value=c.text||'';list.appendChild(d);autosize(ta)});
 if(!shown){const key=arr.length>outside?'noFilteredRecords':'noRecords';list.innerHTML='<div class="empty" data-i18n="editor.'+key+'">'+esc(tr(key))+'</div>';}
 if(outside){const n=document.createElement('div');n.className='note';n.dataset.i18n='editor.outsideRecords';n.dataset.i18nParams=JSON.stringify({count:outside});n.style.margin='4px 2px';n.textContent=tr('outsideRecords',{count:outside});list.prepend(n)}
 list.scrollTop=keepTop;const ar=list.querySelector('.er.act');if(ar)scrollCueRow(ar);
 lastCur=null;playbackRows(true);updProg();drawStrip();updTool();renderVhf();drawWave();renderComposer()}
function updRow(c){const r=list.querySelector('.er[data-id="'+c.id+'"]');if(!r)return;const i=cues().indexOf(c);const ta=r.querySelector('.tx'),v=ta.value,focused=document.activeElement===ta,ss=ta.selectionStart;
 r.className='er'+(c.ovl?' ovl':'')+(c.id===active?' act':'');r.style.setProperty('--c',color(c.spk));r.innerHTML=rowHTML(c,i);const t2=r.querySelector('.tx');t2.value=v;autosize(t2);if(focused){t2.focus();t2.setSelectionRange(ss,ss)}playbackRows(true);updProg()}
function autosize(ta){ta.style.height='auto';ta.style.height=(ta.scrollHeight+2)+'px'}
function updProg(){const a=cues(),done=a.filter(c=>c.st===1).length,unk=a.filter(c=>c.st>=2).length;$('edprog').textContent=ui('review.progress',{channel:sel.value,done,total:a.length})+(unk?tr('unknownCount',{count:unk}):'');$('edbar').style.width=(a.length?done/a.length*100:0)+'%'}
function select(id,scroll){if(composeEntry&&id!==composeEntry.id)finishCompose();composerSelected=false;active=id;wvPage=null;setTimeout(drawWave,0);for(const r of list.children)if(r.dataset)r.classList.toggle('act',+r.dataset.id===id);updTool();drawStrip();
 if(scroll){const r=list.querySelector('.er[data-id="'+id+'"]');if(r)scrollCueRow(r,true)}}
function timingLabel(){const c=find(active);$('tlabel').textContent=c?('#'+(cues().indexOf(c)+1)+'　'+fmtLT(c.start)+'～'+fmtLT(c.end)+'　'+tr('seconds',{seconds:(c.end-c.start).toFixed(1)})):'';}
function updTool(){const c=find(active);$('edtool').classList.toggle('off',!c||composerSelected);
 $('ts').value=c?fmtT2(c.start):'';$('te').value=c?fmtT2(c.end):'';timingLabel();
 document.querySelectorAll('#edtool .stb').forEach(b=>b.classList.toggle('on',!!c&&+b.dataset.st===c.st))}
function playCue(c){finishCompose();if(c.id===boundedCueId&&!au.paused&&playUntil!=null){au.pause();return}select(c.id);boundedCueId=c.id;playUntil=c.end;au.currentTime=Math.max(0,c.start-0.15);au.play()}
function playFromCue(c){finishCompose();select(c.id);playUntil=null;boundedCueId=null;au.currentTime=Math.max(0,c.start-0.15);
 try{Promise.resolve(au.play()).catch(error=>flash(()=>ui('playback.error',{message:errorText(error)})));}catch(error){flash(()=>ui('playback.error',{message:errorText(error)}));}}
function togglePlay(){if(au.paused){au.play()}else{au.pause()}}
function playIcons(){playbackRows(true);const t=$('edpp');if(t){t.dataset.i18n=au.paused?'ui.resume':'ui.pause';t.textContent=ui(t.dataset.i18n);}}
au.addEventListener('play',playIcons);au.addEventListener('pause',playIcons);
let cuePointer=null;
// Native focus follows pointerdown. Do not expand the toolbar or replace rows
// until click has reached its original target; keyboard focus still selects.
list.addEventListener('focusin',e=>{const r=e.target.closest('.er');if(r&&cuePointer?.cueId!==+r.dataset.id&&(+r.dataset.id!==active||composerSelected))select(+r.dataset.id)});
list.addEventListener('pointerdown',e=>{const text=e.target.closest('.tx'),row=e.target.closest('.er');cuePointer={cueId:row?+row.dataset.id:null,text,wasEditing:!!text&&document.activeElement===text,x:e.clientX,y:e.clientY,dragged:false};});
list.addEventListener('pointermove',e=>{if(cuePointer&&Math.abs(e.clientX-cuePointer.x)+Math.abs(e.clientY-cuePointer.y)>6)cuePointer.dragged=true;});
list.addEventListener('pointercancel',()=>{cuePointer=null;});
document.addEventListener('pointerup',()=>{const pointer=cuePointer;if(pointer)setTimeout(()=>{if(cuePointer===pointer)cuePointer=null;},0);});
document.addEventListener('pointercancel',()=>{cuePointer=null;});
list.addEventListener('click',e=>{const pointer=cuePointer;cuePointer=null;const r=e.target.closest('.er');if(!r)return;const c=find(+r.dataset.id);if(!c)return;
 if(+r.dataset.id!==active||composerSelected)select(c.id);
 if(e.target.closest('[data-a=play]')){playCue(c);return;}if(e.target.closest('[data-a=acc]')){acceptSug(c);return;}
 const text=e.target.closest('.tx');
 if(e.target.closest('button,input,select,a')||pointer?.wasEditing||pointer?.dragged||e.detail>1||e.shiftKey||e.ctrlKey||e.metaKey||e.altKey||
  (text&&text.selectionStart!==text.selectionEnd))return;
 playFromCue(c);
});
function acceptSug(c){const g=sugOf(c);if(!g){flash(()=>tr('noSuggestion'));return}c.spk=g.spk;save();updRow(c);drawStrip()}
list.addEventListener('input',e=>{if(!e.target.classList.contains('tx'))return;const c=find(+e.target.closest('.er').dataset.id);c.text=e.target.value;autosize(e.target);save()});
// ---- 工具列 ----
const UNDO=[];function snap(){UNDO.push({ch:sel.value,data:JSON.stringify(cues()),active});if(UNDO.length>40)UNDO.shift()}
function undo(){finishCompose();const u=UNDO.pop();if(!u){flash(()=>tr('nothingUndo'));return}if(u.ch!==sel.value){sel.value=u.ch;sel.dispatchEvent(new Event('change'))}state[u.ch]=JSON.parse(u.data);active=u.active;composerSelected=false;save();render();if(active)select(active,true);flash(()=>tr('undone'))}
const BRK=/[\s。．.，,、？?！!；;：:]/;
function snapPos(txt,pos){if(pos<=0||pos>=txt.length)return pos;if(BRK.test(txt[pos-1])||BRK.test(txt[pos]))return pos;for(let d=1;d<=8;d++){if(pos+d<=txt.length&&BRK.test(txt[pos+d-1]))return pos+d;if(pos-d>0&&BRK.test(txt[pos-d-1]))return pos-d}return pos}
function splitTime(c){const now=au.currentTime;if(now>c.start+0.05&&now<c.end-0.05)return now;return null}
// 摘錄與整段匯出共用來源／時間／處理參數清單，不產生無來源的音檔。
async function clipWav(c,enh){flash(()=>tr('clipBusy'));
 try{const start=Math.max(0,c.start-0.3),end=Math.min(ctx.dur||Infinity,c.end+0.3);
  if(end<=start)throw editorError('clipOutside');
  await ctx.exportClip(sel.value,start,end,enh);
  flash(()=>tr(enh?'clipEnhanced':'clipOriginal'));
 }catch(err){flash(()=>tr('clipFailed',{message:errorText(err)}));}}
function act(a){finishCompose();const c=find(active);if(!c&&a!=='add'){flash(()=>ui('action.chooseCue'));return}if(a==='clip'||a==='clipE'){clipWav(c,a==='clipE');return}const arr=cues(),i=arr.indexOf(c),now=au.currentTime;
 if((c?.timing_review&&['split','dup','merge','mergeP','auto','rep','trim','extract','wsplit'].includes(a))||
    (a==='merge'&&arr[i+1]?.timing_review)||(a==='mergeP'&&arr[i-1]?.timing_review)){
  flash(()=>tr('timingFirst'));return;}
 if(a==='wsplit'&&!rangeKnown(sel.value,c.start-0.6,c.end+0.8)){flash(()=>tr('waveSplitPending'));return;}
 if(['setS','setE','split','dup','merge','mergeP','del','add','auto','rep','trim','extract','wsplit'].includes(a))snap();
 if(a==='play')playCue(c);
 if(a==='setS'){c.start=now;if(c.end<c.start)c.end=c.start+1;sortCues();render()}
 if(a==='setE'){c.end=Math.max(now,c.start+0.1);render()}
 setTimeout(drawWave,0);
 if(a==='split'){const ta=list.querySelector('.er[data-id="'+c.id+'"] .tx');const txt=c.text||'';let pos=ta?ta.selectionStart:null;if(pos==null)pos=Math.floor(txt.length/2);pos=snapPos(txt,pos);if(pos<=0||pos>=txt.length){UNDO.pop();flash(()=>tr('splitCaret'));return}const t=splitTime(c);if(t==null){UNDO.pop();au.pause();au.currentTime=c.start+(c.end-c.start)*(pos/txt.length);drawWave();flash(()=>tr('splitSuggested'));return}const n={id:uid++,start:t,end:c.end,spk:c.spk,text:txt.slice(pos).trim(),orig:c.orig,origNo:c.origNo,st:0,ovl:c.ovl};c.text=txt.slice(0,pos).trim();c.end=t;arr.splice(i+1,0,n);render();select(n.id,true)}
 if(a==='auto'){const txt=c.text||'';const parts=txt.match(/[^。．？?！!]+[。．？?！!]*|[^。．？?！!]+$/g)||[];const ps=parts.map(x=>x.trim()).filter(Boolean);if(ps.length<2){UNDO.pop();flash(()=>tr('noPunctuation'));return}const tot=ps.reduce((a,x)=>a+x.length,0);let t=c.start;const news=ps.map((x,k)=>{const e=k===ps.length-1?c.end:t+(c.end-c.start)*x.length/tot;const o={id:k?uid++:c.id,start:t,end:e,spk:k?'':c.spk,text:x,orig:c.orig,origNo:c.origNo,st:0,ovl:c.ovl};t=e;return o});arr.splice(i,1,...news);render();select(c.id,true);flash(()=>tr('splitPunctuation',{count:ps.length}))}
 if(a==='rep'){const dur=Math.max(0.8,c.end-c.start);let t0;if(now>c.start+0.1&&now<c.end){t0=now;c.end=now}else if(now>=c.end&&now<=c.end+4){t0=now}else{t0=c.end}const n={id:uid++,start:t0,end:t0+dur,spk:'',text:c.text||'',orig:'',origNo:null,st:0,ovl:false,repOf:c.id};arr.splice(i+1,0,n);render();select(n.id,true);flash(()=>tr('readbackAdded'))}
 if(a==='trim'){if(!rangeKnown(sel.value,c.start,c.end)){UNDO.pop();flash(()=>tr('trimPending'));return}const env=ENV[sel.value],R=ENVD.rate;let f=-1,l=-1;for(let k=Math.floor(c.start*R);k<=Math.ceil(c.end*R);k++){if(env&&env[k]>64){if(f<0)f=k;l=k}}if(f<0){UNDO.pop();flash(()=>tr('trimNoActivity'));return}c.start=Math.max(c.start,f/R-0.15);c.end=Math.min(c.end,l/R+0.3);sortCues();render();select(c.id);flash(()=>tr('trimDone'))}
 if(a==='extract'){const ta=list.querySelector('.er[data-id="'+c.id+'"] .tx');const txt=c.text||'';let s0=ta?ta.selectionStart:0,s1=ta?ta.selectionEnd:0;if(s0===s1){s1=s0;s0=0}const piece=txt.slice(s0,s1).trim();if(!piece){UNDO.pop();flash(()=>tr('extractSelect'));return}let t0=now,t1;const nxt=arr.filter(o=>o!==c&&o.start>t0).map(o=>o.start);const lim=Math.min(...nxt.concat([t0+2.5]),c.start>t0?c.start:Infinity);t1=Math.max(t0+0.4,Math.min(t0+1.5,lim));c.text=(txt.slice(0,s0)+txt.slice(s1)).replace(/^[\s。，、]+/,'').trim();if(c.start<t1&&now<c.start+0.05&&s0===0)c.start=Math.min(c.end-0.1,Math.max(c.start,t1));const n={id:uid++,start:t0,end:t1,spk:'',text:piece,orig:c.orig,origNo:c.origNo,st:0,ovl:false};arr.push(n);sortCues();render();select(n.id,true);flash(()=>tr('extractDone'))}
 if(a==='wsplit'){const env=ENV[sel.value],R=ENVD.rate;if(!env){UNDO.pop();return}const nb=arr.filter(o=>o!==c),prevEnd=Math.max(c.start-0.6,...nb.filter(o=>o.end<=c.start+0.05).map(o=>o.end)),nextStart=Math.min(c.end+0.8,...nb.filter(o=>o.start>=c.end-0.05).map(o=>o.start));const k0=Math.max(0,Math.floor(prevEnd*R)),k1=Math.min(env.length-1,Math.ceil(nextStart*R)),TH=64,GAP=Math.round(0.15*R),MINL=Math.round(0.2*R);const segs=[];let st=-1,last=-1;for(let k=k0;k<=k1;k++){if(env[k]>TH){if(st<0)st=k;last=k}else if(st>=0&&k-last>GAP){if(last-st+1>=MINL)segs.push([st,last]);st=-1}}if(st>=0&&last-st+1>=MINL)segs.push([st,last]);if(segs.length<2){UNDO.pop();flash(()=>tr('waveNotSplit',{count:segs.length}));return}const parts=((c.text||'').match(/[^。．？?！!]+[。．？?！!]*/g)||[]).map(x=>x.trim()).filter(Boolean);const news=segs.map(([a0,b0],k)=>({id:k?uid++:c.id,start:Math.max(0,a0/R-0.1),end:(b0+1)/R+0.15,spk:k?'':c.spk,text:'',orig:c.orig,origNo:c.origNo,st:0,ovl:false}));parts.forEach((x,k)=>{const o=news[Math.min(k,news.length-1)];o.text=(o.text?o.text+' ':'')+x});const i0=arr.indexOf(c);arr.splice(i0,1,...news);sortCues();render();select(c.id,true);flash(()=>tr('waveSplitDone',{count:segs.length,parts:parts.length,warning:parts.length!==segs.length?tr('splitMismatch'):''}))}
 if(a==='mergeP'){if(i<=0){UNDO.pop();return}const p=arr[i-1];p.text=((p.text||'')+' '+(c.text||'')).trim();p.end=Math.max(p.end,c.end);if(c.orig&&c.orig!==p.orig)p.orig=(p.orig?p.orig+' ｜ ':'')+c.orig;arr.splice(i,1);active=p.id;render();select(p.id,true)}
 if(a==='dup'){c.ovl=true;const n={id:uid++,start:c.start,end:c.end,spk:'',text:'',orig:c.orig,origNo:c.origNo,st:0,ovl:true};arr.splice(i+1,0,n);render();select(n.id,true);const t=list.querySelector('.er[data-id="'+n.id+'"] .tx');if(t)t.focus()}
 if(a==='merge'){if(i>=arr.length-1)return;const n=arr[i+1];c.text=((c.text||'')+' '+(n.text||'')).trim();c.end=Math.max(c.end,n.end);if(n.orig&&n.orig!==c.orig)c.orig=(c.orig?c.orig+' ｜ ':'')+n.orig;arr.splice(i+1,1);render()}
 if(a==='del'){if(!confirm(ui('action.confirmDeleteCue')))return;arr.splice(i,1);active=null;render()}
 if(a==='add'){const t=au.currentTime||0;const n={id:uid++,start:t,end:t+2,spk:'',text:'',orig:'',origNo:null,st:0,ovl:false};arr.push(n);sortCues();render();select(n.id,true);const ta=list.querySelector('.er[data-id="'+n.id+'"] .tx');if(ta)ta.focus()}
 save()}
$('edtool').addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;if(b.dataset.st!=null){const c=find(active);if(!c)return;if(c.timing_review&&+b.dataset.st!==0){flash(()=>tr('adjustTiming'));return;}c.st=+b.dataset.st;save();updRow(c);updTool();drawStrip();return}act(b.dataset.a)});
$('edadd').onclick=()=>act('add');$('edundo').onclick=undo;$('edpp').onclick=togglePlay;
// Direct entry is already a real, channel-bound cue while typing. There is no
// separate unsaved draft that could be omitted by save/export or a channel jump.
function composeCue(){return composeEntry?(state[composeEntry.ch]||[]).find(c=>c.id===composeEntry.id):null;}
function composeEnd(c){const end=composeEntry.last>c.start?composeEntry.last:c.start+2;
 return Math.min(ctx.dur||Infinity,Math.max(c.start+0.1,end));}
function renderComposer(){const c=composeCue(),ch=c?composeEntry.ch:sel.value,start=c?c.start:Math.max(0,au.currentTime||0),end=c?composeEnd(c):null;
 const box=$('edcomposer');box.dataset.channel=ch;box.dataset.start=String(start);box.dataset.end=end==null?'':String(end);
 $('edcomposeTime').textContent=ch+' · '+fmtLT(start)+(end==null?'':' → '+fmtLT(end));
 $('edcomposeTime').title=tr(c?'composeRange':'composeAnchor')+fmtDate(start,OFS())+(end==null?'':' ～ '+fmtDate(end,OFS()));
 $('edcomposeAdd').disabled=!compose.value.trim()||composeIME;}
function finishCompose(refresh=true){const c=composeCue();if(!composeEntry)return;
 if(c)c.end=composeEnd(c);composeEntry=null;compose.value='';composeBlurred=false;
 save();if(refresh)render();else{
  const text=c&&list.querySelector('.er[data-id="'+c.id+'"] .tx');if(text&&text.value!==c.text){text.value=c.text;autosize(text);}
  updProg();drawStrip();drawWave();renderComposer();}}
function inputCompose(){let c=composeCue();
 if(!c){if(!compose.value.trim()){renderComposer();return;}
  const dur=ctx.dur||Infinity,t=Math.max(0,Math.min(Math.max(0,dur-0.1),au.currentTime||0));
  snap();c={id:uid++,start:t,end:Math.min(dur,t+2),spk:'',text:compose.value,orig:'',origNo:null,st:0,ovl:false};
  const ch=sel.value;(state[ch]||(state[ch]=[])).push(c);sortCues();composeEntry={ch,id:c.id,last:au.currentTime||0};active=c.id;composerSelected=true;playUntil=null;
  save();render();
 }else{composeEntry.last=Math.max(c.start,au.currentTime||0);c.text=compose.value;c.end=composeEnd(c);save();
  // Keep native pointer/IME targets alive when composition ends on blur.
  const text=list.querySelector('.er[data-id="'+c.id+'"] .tx');if(text&&text.value!==c.text){text.value=c.text;autosize(text);}renderComposer();}}
compose.addEventListener('input',inputCompose);
compose.addEventListener('compositionstart',()=>{composeIME=true;composeBlurred=false;renderComposer();});
compose.addEventListener('compositionend',()=>{composeIME=false;inputCompose();if(composeBlurred)finishCompose(!cuePointer?.cueId);});
compose.addEventListener('blur',()=>{if(composeIME){composeBlurred=true;return;}finishCompose(!cuePointer?.cueId);});
compose.addEventListener('keydown',e=>{if(composeIME||e.isComposing||e.keyCode===229)return;
 if(e.key==='Enter'&&!e.shiftKey&&!e.altKey){e.preventDefault();e.stopPropagation();finishCompose();}});
$('edcomposeAdd').onclick=()=>{if(!composeIME)finishCompose();compose.focus();};
for(const id of ['ts','te'])$(id).addEventListener('change',e=>{const c=find(active);if(!c)return;const v=parseT(e.target.value);if(v==null||!Number.isFinite(v)||(id==='ts'?v>=c.end:v<=c.start)){flash(()=>tr('invalidRange'));updTool();return}
 snap();if(id==='ts')c.start=v;else c.end=v;sortCues();save();render()});
function nextRow(d){const vis=[...list.querySelectorAll('.er')];if(!vis.length)return;let i=vis.findIndex(r=>+r.dataset.id===active);i=Math.max(0,Math.min(vis.length-1,(i<0?0:i+d)));const id=+vis[i].dataset.id;select(id,true);const c=find(id);au.currentTime=Math.max(0,c.start-0.15)}
// ---- 時間軸（播放位置前後30秒） ----
const cv=$('edstrip'),cx=cv.getContext('2d');let stripHits=[];
function hatch(g,x0,x1,H){if(x1<=x0)return;g.save();g.beginPath();g.rect(x0,0,x1-x0,H);g.clip();g.fillStyle='rgba(120,120,120,.12)';g.fillRect(x0,0,x1-x0,H);g.strokeStyle='rgba(120,120,120,.45)';g.lineWidth=2;for(let x=x0-H;x<x1;x+=12){g.beginPath();g.moveTo(x,H);g.lineTo(x+H,0);g.stroke()}g.restore()}
function unknownWave(g,ch,a,b,W,H){const known=KNOWN(ch),R=ENVD.rate;if(!known||b<=a)return;let start=-1,labeled=false;
 const k0=Math.max(0,Math.floor(a*R)),k1=Math.min(known.length,Math.ceil(b*R));
 for(let k=k0;k<=k1;k++){if(k<k1&&!known[k]){if(start<0)start=k;continue;}if(start<0)continue;
  const x0=Math.max(0,(start/R-a)/(b-a)*W),x1=Math.min(W,(k/R-a)/(b-a)*W);start=-1;
  if(x1>x0){g.fillStyle='rgba(145,158,153,.25)';g.fillRect(x0,0,x1-x0,H);
   if(!labeled&&x1-x0>150){g.font='20px sans-serif';g.fillStyle='#66756f';g.fillText(tr('notAnalyzed'),x0+6,H/2+7);labeled=true;}}
 }}
function drawStrip(){const W=cv.width=cv.clientWidth*2,H=cv.height=cv.clientHeight*2;cx.clearRect(0,0,W,H);const t=au.currentTime||0,a=t-30,b=t+30,X=v=>(v-a)/(b-a)*W;
 cx.fillStyle='#f1f4f3';cx.fillRect(0,0,W,H);for(const [s,e] of GAPS(sel.value)){if(e<a||s>b)continue;hatch(cx,X(s),X(e),H)}cx.font='20px sans-serif';cx.fillStyle='#8a9a97';
 for(let s=Math.ceil(a/10)*10;s<=b;s+=10){cx.fillRect(X(s),0,1,H);cx.fillText(fmtLT(s).slice(3),X(s)+3,H-6)}
 let activityBands=0;for(const [s,e] of (VHF[sel.value]||[])){if(e<=a||s>=b)continue;const x0=Math.max(0,X(s)),x1=Math.min(W,X(e));if(x1<=x0)continue;
  cx.fillStyle='rgba(39,122,123,.18)';cx.fillRect(x0,0,x1-x0,H);activityBands++;}
 cv.dataset.activityChannel=sel.value;cv.dataset.activityCount=String(activityBands);cv.dataset.activityStart=String(a);cv.dataset.activityEnd=String(b);
 const lanes=[];stripHits=[];const LH=(H-30)/3;
 for(const c of cues()){if(c.end<a||c.start>b)continue;let L=0;while(lanes[L]!=null&&lanes[L]>c.start)L++;lanes[L]=c.end;if(L>2)L=2;
  const x0=X(c.start),x1=Math.max(X(c.end),x0+4),y=4+L*LH;cx.fillStyle=color(c.spk);cx.globalAlpha=c.st===1?0.95:0.6;cx.fillRect(x0,y,x1-x0,LH-6);cx.globalAlpha=1;
  if(c.id===active){cx.strokeStyle='#000';cx.lineWidth=3;cx.strokeRect(x0,y,x1-x0,LH-6)}
  if(x1-x0>60){cx.fillStyle='#fff';cx.fillText((c.spk||'?')+' '+(c.text||'').slice(0,12),x0+4,y+LH-14)}stripHits.push([x0/W,x1/W,y/H,(y+LH-6)/H,c.id])}
 unknownWave(cx,sel.value,a,b,W,H);cx.fillStyle='#d0021b';cx.fillRect(W/2-1,0,3,H)}
cv.addEventListener('click',e=>{const b=cv.getBoundingClientRect(),fx=(e.clientX-b.left)/b.width,fy=(e.clientY-b.top)/b.height;
 const h=stripHits.find(([x0,x1,y0,y1])=>fx>=x0&&fx<=x1&&fy>=y0&&fy<=y1);if(h){select(h[4],true);const c=find(h[4]);au.currentTime=Math.max(0,c.start-0.15)}else{au.currentTime=Math.max(0,(au.currentTime||0)-30+fx*60)}drawStrip()});
// ---- 各聲道同步波形：共用時間窗與游標，只播放所選的一軌 ----
const firstWave=$('edwave'),waveRows=$('edwaveRows'),waveScale=$('edwaveScale'),waveStatus=$('edwaveStatus'),waveRangeLabel=$('edwaveRange');
let wvRange=null,wvDrag=null,wvMoved=false,waveLastTime=null,waveRequestKey=null,waveRequestId=0,waveRequestBusy=false,waveRequestError='';
if(!['30','60','120','300'].includes(waveScale.value))waveScale.value='60';
waveRows.innerHTML='';
const waveTracks=CH.map((ch,index)=>{
 const row=document.createElement('div');row.className='wave-track';row.dataset.channel=ch;
 const label=document.createElement('button');label.type='button';label.className='wave-label';label.dataset.channel=ch;
 const configured=typeof ctx.channelLabels==='function'?ctx.channelLabels(ch):(ctx.channelLabels||{})[ch];
 label.textContent=configured||ch;label.title=tr('switchChannelHelp',{channel:ch});
 const canvas=index===0?firstWave:document.createElement('canvas');if(index)canvas.id='edwave_'+ch;
 canvas.className='wave-canvas';canvas.dataset.channel=ch;canvas.title=tr('waveHelp',{channel:ch});
 if(canvas.setAttribute)canvas.setAttribute('aria-label',tr('waveAria',{channel:ch}));
 row.appendChild(label);row.appendChild(canvas);waveRows.appendChild(row);
 return {ch,row,label,canvas,g:canvas.getContext('2d')};
});
function waveStatusText(){
 waveStatus.className=waveRequestError?'err':'';
 waveStatus.textContent=waveRequestError?tr('waveFailed',{message:errorText(waveRequestError)}):
  waveRequestBusy?ui('wave.loading'):ui(typeof ctx.requestWaveforms==='function'?'wave.ready':'wave.pending');
 waveStatus.title=tr('waveScope',{count:CH.length});
}
function requestVisibleWaveforms(a,b){
 const key=CH.join(',')+':'+a.toFixed(3)+':'+b.toFixed(3);
 if(key===waveRequestKey)return;waveRequestKey=key;
 if(typeof ctx.requestWaveforms!=='function'){waveStatusText();return;}
 const request=++waveRequestId;waveRequestBusy=true;waveRequestError='';waveStatusText();
 let operation;try{operation=ctx.requestWaveforms(CH.slice(),a,b);}catch(error){operation=Promise.reject(error);}
 Promise.resolve(operation).then(()=>{
  if(request!==waveRequestId)return;waveRequestBusy=false;waveRequestError='';waveStatusText();drawWave();
 },error=>{
  if(request!==waveRequestId)return;waveRequestBusy=false;
  if(error&&error.name==='AbortError'){waveStatusText();return;}
  waveRequestError=error;waveStatusText();
 });
}
function updateWaveWindow(){
 const t=au.currentTime||0,span=+waveScale.value||60,c=find(active),newPage=!wvPage;
 if(!wvPage){
  if(c){const a=Math.max(0,c.start-2),end=Math.min(ctx.dur||Infinity,Math.max(c.end+2,a+5));wvPage=[a,end];}
  else wvPage=waveWindow(t,span,ctx.dur);
 }
 if(!newPage&&!wvDrag&&waveLastTime!==t&&(t<wvPage[0]||t>wvPage[1]-Math.min(0.2,(wvPage[1]-wvPage[0])/20))){
  // A selected sentence stays framed while replaying it; general playback and
  // manual seeks advance one shared page, never a separate page per channel.
  if(!(playUntil!=null&&c&&t>=c.start-0.2&&t<=c.end+0.2))wvPage=waveWindow(t,span,ctx.dur);
 }
 if(wvPage[1]<=wvPage[0])wvPage=waveWindow(t,span,ctx.dur);
 wvRange=wvPage.slice();waveLastTime=t;waveRangeLabel.textContent=fmtDate(wvRange[0],OFS())+' ～ '+fmtDate(wvRange[1],OFS());
 waveRows.dataset.start=String(wvRange[0]);waveRows.dataset.end=String(wvRange[1]);
}
function drawWaveTrack(track,a,b){
 const {ch,canvas,g,row,label}=track,W=canvas.width=Math.max(2,canvas.clientWidth*2),H=canvas.height=Math.max(2,canvas.clientHeight*2);
 const isActive=ch===sel.value,env=ENV[ch],known=KNOWN(ch),R=ENVD.rate,c=isActive?find(active):null,X=t=>(t-a)/(b-a)*W;
 row.classList.toggle('is-active',isActive);row.dataset.active=String(isActive);
 if(label.setAttribute)label.setAttribute('aria-pressed',String(isActive));
 canvas.dataset.start=String(a);canvas.dataset.end=String(b);canvas.dataset.cursor=String(au.currentTime||0);
 g.clearRect(0,0,W,H);g.fillStyle=isActive?'#edf7f5':'#f8faf9';g.fillRect(0,0,W,H);
 // Unknown first, then known gaps: a missing record must never be disguised as
 // a pending analysis or an apparently silent flat waveform.
 unknownWave(g,ch,a,b,W,H);
 const gaps=GAPS(ch).filter(([s,e])=>e>a&&s<b);row.dataset.gaps=String(gaps.length);
 for(const [s,e] of gaps){const x0=Math.max(0,X(s)),x1=Math.min(W,X(e));g.fillStyle='#eef0ef';g.fillRect(x0,0,x1-x0,H);hatch(g,x0,x1,H);
  if(x1-x0>55){g.fillStyle='#69716e';g.font='18px sans-serif';g.fillText(ui('wave.missing'),x0+4,H-8);}}
 const step=b-a>150?60:b-a>60?20:b-a>15?10:b-a>7?2:1;
 g.font='18px ui-monospace,Consolas,monospace';
 for(let s=Math.ceil((T0+a+LT+OFS())/step)*step-T0-LT-OFS();s<=b;s+=step){
  const x=X(s);g.fillStyle='#dde5e1';g.fillRect(x,0,1,H);g.fillStyle='#73827a';g.fillText(fmtLT(s),Math.max(2,Math.min(W-145,x+3)),18);}
 if(c&&c.end>a&&c.start<b){g.fillStyle='rgba(39,122,123,.12)';g.fillRect(Math.max(0,X(c.start)),20,Math.min(W,X(c.end))-Math.max(0,X(c.start)),Math.max(0,H-20));}
 const k0=Math.max(0,Math.floor(a*R)),k1=Math.max(k0,Math.ceil(b*R));let unknown=0,analyzed=0;
 g.fillStyle=isActive?'#287e77':'#6b8e89';
 for(let k=k0;k<k1;k++){
  if(known?!known[k]:(!env||PENDING(ch)>0)){unknown++;continue;}analyzed++;
  if(!env||k>=env.length)continue;
  const value=env[k]/255,x=X(k/R),width=Math.max(1,W/((b-a)*R)-0.5),height=value*H*0.31;
  g.fillRect(x,H*0.60-height,width,Math.max(1,height*2));
 }
 row.dataset.unknown=String(unknown);row.dataset.analyzed=String(analyzed);
 for(const cue of state[ch]||[]){if(cue.end<=a||cue.start>=b)continue;g.fillStyle=color(cue.spk);g.globalAlpha=cue.id===active&&isActive?0.9:0.45;
  g.fillRect(Math.max(0,X(cue.start)),H-6,Math.min(W,X(cue.end))-Math.max(0,X(cue.start)),5);g.globalAlpha=1;}
 if(c&&c.end>=a&&c.start<=b){g.fillStyle='#1c716c';g.font='18px ui-monospace,Consolas,monospace';
  for(const [time,text] of [[c.start,tr('start')],[c.end,tr('end')]]){const x=X(time);if(x<0||x>W)continue;g.fillRect(x-1,20,3,H-20);g.fillText(text+fmtT(time),Math.max(2,Math.min(W-145,x+4)),H-10);}}
 const cursor=au.currentTime||0;if(cursor>=a&&cursor<=b){g.fillStyle='#c1272d';g.fillRect(X(cursor)-1,0,3,H);}
}
function drawWave(){
 updateWaveWindow();for(const track of waveTracks)drawWaveTrack(track,wvRange[0],wvRange[1]);
 requestVisibleWaveforms(wvRange[0],wvRange[1]);waveStatusText();renderVhf();
}
function waveTime(track,e){const bounds=track.canvas.getBoundingClientRect();return waveTimeAt(wvRange,e.clientX,bounds.left,bounds.width,ctx.dur);}
function nearWaveBoundary(track,e){
 const c=find(active);if(track.ch!==sel.value||!c||!wvRange)return null;
 const bb=track.canvas.getBoundingClientRect(),px=t=>bb.left+(t-wvRange[0])/(wvRange[1]-wvRange[0])*bb.width;
 if(Math.abs(e.clientX-px(c.start))<=8)return 's';if(Math.abs(e.clientX-px(c.end))<=8)return 'e';return null;
}
function switchWaveChannel(track,target=null){
 // The player's channel setter and seek are generation-guarded. Dispatch the
 // canonical change before seeking, so rapid clicks cannot restore an old time.
 if(wvRange)wvPage=wvRange.slice();playUntil=null;
 if(track.ch!==sel.value){sel.value=track.ch;sel.dispatchEvent(new Event('change'));}
 if(target!=null)au.currentTime=target;
 drawStrip();drawWave();
}
for(const track of waveTracks){
 track.label.onclick=()=>switchWaveChannel(track);
 track.canvas.addEventListener('mousedown',e=>{drawWave();const edge=nearWaveBoundary(track,e);if(!edge)return;
  e.preventDefault();snap();wvDrag={edge,ch:track.ch,track};wvMoved=false;});
 track.canvas.addEventListener('mousemove',e=>{if(!wvDrag)track.canvas.style.cursor=nearWaveBoundary(track,e)?'ew-resize':'crosshair';});
 track.canvas.addEventListener('click',e=>{if(!wvRange||wvMoved){wvMoved=false;return;}switchWaveChannel(track,waveTime(track,e));});
}
window.addEventListener('mousemove',e=>{
 if(!wvDrag)return;const c=find(active);if(!c||wvDrag.ch!==sel.value)return;
 const time=waveTime(wvDrag.track,e);if(time==null)return;wvMoved=true;
 if(wvDrag.edge==='s')c.start=Math.min(time,c.end-0.1);else c.end=Math.max(time,c.start+0.1);
 updTool();drawWave();
});
window.addEventListener('mouseup',()=>{
 if(!wvDrag)return;const c=find(active);wvDrag=null;if(!wvMoved){UNDO.pop();return;}
 if(!c)return;sortCues();save();updRow(c);drawStrip();updTool();drawWave();flash(()=>tr('timingAdjusted'));
});
waveScale.onchange=()=>{wvPage=waveWindow(au.currentTime||0,+waveScale.value,ctx.dur);drawWave();};
// A collapsed details element has zero-size canvases; redraw after reopening.
$('waveDetails').addEventListener('toggle',()=>{if($('waveDetails').open)drawWave();});
// ---- 目前聲道的聲音活動段（相對指標，不是確定發話或講者判定） ----
let activityRenderKey='';
function renderVhf(){
 const box=$('vhfseg'),ch=sel.value,pending=PENDING(ch),known=KNOWN(ch),R=ENVD.rate,
  [start,end]=wvRange||waveWindow(au.currentTime||0,+waveScale.value||60,ctx.dur),
  visible=activityWindow(VHF[ch],start,end,40),gaps=GAPS(ch).filter(([s,e])=>e>start&&s<end);
 let unknown=0,analyzed=0;const k0=Math.max(0,Math.floor(start*R)),k1=Math.ceil(end*R);
 for(let k=k0;k<k1;k++){
  if(gaps.some(([s,e])=>e>k/R&&s<(k+1)/R))continue;
  const isUnknown=known?!known[k]:(pending>0||!ENV[ch]);if(isUnknown)unknown++;else analyzed++;
 }
 const key=VDRI18n.getLocale()+'|'+ch+'|'+start+'|'+end+'|'+OFS()+'|'+pending+'|'+unknown+'|'+analyzed+'|'+gaps.length+'|'+visible.total+'|'+visible.segments.map(s=>s.start+','+s.end).join(';');
 if(key===activityRenderKey)return;activityRenderKey=key;box.style.display='';
 box.dataset.channel=ch;box.dataset.start=String(start);box.dataset.end=String(end);box.dataset.total=String(visible.total);
 box.dataset.shown=String(visible.segments.length);box.dataset.pending=String(pending);box.dataset.unknown=String(unknown);box.dataset.analyzed=String(analyzed);box.dataset.gaps=String(gaps.length);
 const chips=visible.segments.map(s=>'<button class="chip ghost" data-channel="'+esc(ch)+'" data-t="'+s.visibleStart+'" data-start="'+s.start+'" data-end="'+s.end+'" title="'+esc(tr('activityChipHelp'))+'">'+fmtLT(s.visibleStart)+'（'+esc(tr('seconds',{seconds:Math.max(1,Math.round(s.visibleEnd-s.visibleStart))}))+'）</button>').join('');
 box.innerHTML='<span class="activity-scope" title="'+esc(tr('activityTitle',{channel:ch,start:fmtLT(start),end:fmtLT(end)}))+'">'+esc(tr('activityLabel',{channel:ch}))+'</span>'+
  (chips||'<span class="note" title="'+esc(tr(analyzed?'activityNoThresholdHelp':unknown?'activityUnknownHelp':'activityEmptyHelp'))+'">'+esc(tr(analyzed?'activityBelow':unknown?'activityWaiting':'activityEmpty'))+'</span>')+
  (visible.omitted?'<span class="note" title="'+esc(tr('activityOmittedHelp',{count:visible.omitted}))+'">'+esc(tr('activityOmitted',{count:visible.omitted}))+'</span>':'')+
  (unknown?'<span class="note" title="'+esc(tr('activityPendingHelp'))+'">'+esc(ui('wave.pending'))+'</span>':pending?'<span class="note" title="'+esc(tr('activityOtherPendingHelp'))+'">'+esc(tr('activityOtherPending'))+'</span>':'')+
  (gaps.length?'<span class="note warn" title="'+esc(tr('activityGapsHelp',{count:gaps.length}))+'">'+esc(tr('activityGaps',{count:gaps.length}))+'</span>':'');
}
$('vhfseg').addEventListener('click',async e=>{const b=e.target.closest('[data-t]');if(!b||(b.dataset.channel&&b.dataset.channel!==sel.value))return;
 const time=+b.dataset.t;if(!Number.isFinite(time))return;
 try{playUntil=null;au.currentTime=Math.max(0,time-0.5);await au.play();}
 catch(error){flash(()=>tr('activityPlayFailed',{message:errorText(error)}));}});
// ---- 其他 ----
function flash(m){flashContent=typeof m==='function'?m:()=>m;flashUntil=Date.now()+6000;$('edsave').textContent=flashContent();setTimeout(status,6100)}
$('edflt').onchange=render;$('edq').oninput=render;
$('edrate').onchange=e=>{au.playbackRate=+e.target.value};
au.addEventListener('loadedmetadata',()=>{au.playbackRate=+$('edrate').value});
sel.addEventListener('change',()=>{finishCompose();active=null;composerSelected=false;playUntil=null;render()});
au.addEventListener('timeupdate',()=>{const t=au.currentTime;
 const entry=composeEntry;
 // The virtual player emits timeupdate before seeked for an in-chunk seek.
 // Defer only normal playback progress so seeked can commit the pre-seek end.
 if(entry&&!au.paused)Promise.resolve().then(()=>{if(composeEntry===entry){entry.last=t;renderComposer();}});
 if(playUntil!=null&&t>=playUntil){if($('edloop').checked){const c=find(boundedCueId);if(c){playUntil=c.end;au.currentTime=Math.max(0,c.start-0.15);return}}au.pause();playUntil=null;boundedCueId=null}
 const c=cues().find(x=>t>=x.start&&t<x.end);const id=c?c.id:null;if(id!==lastCur){lastCur=id;
  if(id&&!au.paused&&$('edfollow').checked&&document.activeElement.tagName!=='TEXTAREA'){const r=list.querySelector('.er[data-id="'+id+'"]');if(r)scrollCueRow(r,true,true)}}
 playbackRows();
 drawStrip();drawWave();renderComposer()});
function finishComposeOnSeek(e){if(!e||e.reason!=='buffer')finishCompose();}
au.addEventListener('seeking',finishComposeOnSeek);
au.addEventListener('seeked',e=>{finishComposeOnSeek(e);playbackRows();drawStrip();drawWave();renderComposer()});window.addEventListener('resize',()=>{drawStrip();drawWave();});
document.addEventListener('keydown',e=>{
 cuePointer=null;
 if(e.isComposing||e.keyCode===229||composeIME)return;
 if(e.target&&typeof e.target.closest==='function'&&e.target.closest('#srtImportPanel, #projectImportPanel'))return;
 if(e.key==='Escape'){e.preventDefault();togglePlay();return}
 if(e.ctrlKey&&e.code==='Space'){e.preventDefault();togglePlay();return}
 if(e.target===compose&&!(e.altKey&&(e.code==='KeyP'||e.key.toLowerCase()==='p')))return;
 if(e.ctrlKey&&e.key==='Enter'){e.preventDefault();const c=find(active);if(c)playCue(c);return}
 if(!e.altKey)return;
 // Physical shortcut keys stay stable across keyboard layouts. The fallback
 // keeps older browsers and synthetic/accessibility key events working.
 const shortcut=/^Key[A-Z]$/.test(e.code||'')?e.code.slice(3).toLowerCase():e.key.toLowerCase();
 const digit=/^Digit[1-9]$/.test(e.code||'')?e.code.slice(5):e.key;
 if(e.key==='ArrowLeft'||e.key==='ArrowRight'){e.preventDefault();au.currentTime=Math.max(0,au.currentTime+(e.key==='ArrowLeft'?-2:2))}
 else if(e.key==='ArrowUp'||e.key==='ArrowDown'){e.preventDefault();nextRow(e.key==='ArrowUp'?-1:1)}
 else if(/^[1-9]$/.test(digit)){e.preventDefault();const s=spks[+digit-1];if(s!=null)setSpk(s)}
 else if(e.key==='Enter'){e.preventDefault();const c=find(active);if(c){if(c.timing_review){flash(()=>tr('adjustTiming'));return;}c.st=1;save();updRow(c);nextRow(1)}}
 else if(shortcut==='x'){e.preventDefault();act('split')}
 else if(shortcut==='c'){e.preventDefault();act('dup')}
 else if(shortcut==='a'){e.preventDefault();const c=find(active);if(c)acceptSug(c)}
 else if(shortcut==='z'){e.preventDefault();undo()}
 else if(shortcut==='m'){e.preventDefault();act('extract')}
 else if(shortcut==='w'){e.preventDefault();act('wsplit')}
 else if(e.code==='BracketLeft'||e.key==='['){e.preventDefault();act('setS')}
 else if(e.code==='BracketRight'||e.key===']'){e.preventDefault();act('setE')}
 else if(shortcut==='p'){e.preventDefault();togglePlay()}
 else if(shortcut==='r'){e.preventDefault();act('rep')}
 else if(e.key==='Delete'){e.preventDefault();act('del')}
 else if(shortcut==='s'){e.preventDefault();act(e.shiftKey?'clipE':'clip')}});
function dlBlob(name,blob){const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),10000)}
const dl=(name,text,type)=>dlBlob(name,new Blob(['﻿'+text],{type}));
function srtText(){return ReviewState.clipCues(cues(),ctx.dur||Infinity).map((c,i)=>(i+1)+'\n'+srtT(c.start)+' --> '+srtT(c.end)+'\n'+(c.spk?c.spk+'：':'')+(c.text||'')+(c.timing_review?'〔'+ui('review.timingPending')+'〕':'')+(c.st===2?'〔'+ui('review.partial')+'〕':c.st===3?'〔'+ui('review.unclear')+'〕':'')+'\n').join('\n');}
function csvText(){const q=v=>{let s=String(v??'');if(/^[\s]*[=+@-]/.test(s))s="'"+s;return '"'+s.replace(/"/g,'""')+'"';},o=OFS(),ov=(ch,c)=>GAPS(ch).some(([s,e])=>e>c.start&&s<c.end);
 const headers=tr('csvHeaders').split('|');headers.splice(5,0,o?tr('correctedHeader',{offset:(o>0?'+':'')+o.toFixed(1)}):tr('uncorrectedHeader'));const rows=[headers];
 for(const ch of Object.keys(state).sort())(state[ch]||[]).forEach((c,i)=>{
  const outside=c.start<0||c.end>(ctx.dur||Infinity)||!CH.includes(ch),absent=c.end<=0||c.start>=(ctx.dur||Infinity)||!CH.includes(ch),pending=PENDING(ch),qa=absent?tr('qaNotLoaded'):
   (pending?tr(ov(ch,c)?'qaIndexGap':'qaIndexClear')+tr('qaPending'):tr(ov(ch,c)?'qaGap':'qaClear'))+(outside?tr('qaOutside'):'');
  rows.push([ch,i+1,fmtT2(c.start),fmtT2(c.end),fmtDate(c.start),o?fmtDate(c.start,o):'',c.spk,c.text,STATUS(c.st),c.ovl?tr('yes'):'',qa+(c.timing_review?tr('qaTiming'):''),c.orig]);
 });
 return rows.map(r=>r.map(q).join(',')).join('\r\n');}
const projectName=()=>ctx.NAME+tr('projectSuffix');
$('exSrt').onclick=()=>{finishCompose();dl(ctx.NAME+'_'+sel.value+tr('srtSuffix'),srtText(),'text/plain');};
$('exCsv').onclick=()=>{finishCompose();dl(ctx.NAME+tr('csvSuffix'),csvText(),'text/csv');};
$('saveP').onclick=async()=>{finishCompose();save();
 try{const result=await files.save(projectName(),project());
  if(result.superseded)flash(()=>tr('saveSuperseded'));
  else if(result.saved){const name=result.handle.name||projectName();fileMessage=()=>tr('savedFile',{name});flash(()=>files.dirty()?tr('savedEarlier'):tr('savedSameFile',{message:fileMessage()}));}
  else if(result.cancelled)flash(()=>tr('saveCancelled'));
  else if(result.downloaded)flash(()=>tr('saveDownload'));
  else if(result.busy)flash(()=>tr('saveBusy'));
 }catch(e){flash(()=>tr('saveFailed',{message:errorText(e)}));}
 status();};
$('exBundle').onclick=async()=>{finishCompose();save();const button=$('exBundle');button.disabled=true;flash(()=>tr('bundleBusy'));
 try{const d=project(),ch=sel.value,csv=csvText(),srt=srtText(),entries=await ctx.bundleEntries(ch);
  const readme=tr('bundleNotes');
  const blob=await VDRProject.zipEntries([
   {name:projectName(),data:JSON.stringify(d,null,1)},
   {name:tr('bundleCsv'),data:'\uFEFF'+csv},
   {name:ch+tr('srtSuffix'),data:'\uFEFF'+srt},
   {name:tr('bundleReadme'),data:'\uFEFF'+readme},...entries]);
  dlBlob(ctx.NAME+tr('bundleSuffix'),blob);flash(()=>tr('bundleDone'));
 }catch(e){flash(()=>tr('bundleFailed',{message:errorText(e)}));}finally{button.disabled=false;}};
// Validate every field and the source binding before touching the current case.
function prepareImport(d,allowLegacy=false){const valid=VDRProject.validateImport(d,ctx.recordingId,{allowLegacy});
 const shift=Date.parse(valid.t0_utc)/1000-T0;
 return {...valid,state:shifted(valid.state,shift),spks:valid.spks||DEFAULT_SPK.slice(),spkColor:valid.spkColor||{},
  t0_utc:new Date(T0*1000).toISOString()};}
const recover=document.createElement('button');staticText(recover,'recover');recover.hidden=true;recover.className='ghost';
$('loadP').insertAdjacentElement('afterend',recover);
function replaceProject(d,handle=null,keepPrevious=true){
 finishCompose();composerSelected=false;
 const before=project();applying=true;
 try{state=d.state;spks=d.spks;uid=d.uid;spkColor=d.spkColor;migrationInfo=d.migration||null;importHistory=d.import_history||[];ctx.extra.set(d.extra||ctx.defaultExtra);}
 catch(e){state=before.state;spks=before.spks;uid=before.uid;spkColor=before.spkColor;migrationInfo=before.migration||null;importHistory=before.import_history||[];
  try{ctx.extra.set(before.extra);}catch(_){}throw e;
 }finally{applying=false;}
 if(keepPrevious){previousProject=before;recover.hidden=false;}
 for(const ch of CH)if(!state[ch])state[ch]=[];
 active=null;UNDO.length=0;SUG={};files.replace(handle);fileMessage=()=>tr('loadedFile');
 renderSpk();render();lastState=JSON.parse(JSON.stringify(state));lastContent=content();save();
}
recover.onclick=()=>{if(!previousProject)return;
 if(!confirm(tr('recoverConfirm')))return;
 try{const d=prepareImport(previousProject);replaceProject(d,null,true);files.changed();status();flash(()=>tr('recovered'));}catch(e){alert(tr('recoverFailed',{message:errorText(e)}));}};
// Import is a preview, never a replacement merely because a file was selected.
const projectPanel=document.createElement('section');projectPanel.id='projectImportPanel';projectPanel.hidden=true;
Object.assign(projectPanel.style,{padding:'12px',margin:'8px 12px',border:'1px solid #a9c6c4',borderRadius:'8px',background:'#f5faf9',maxHeight:'45%',overflow:'auto',flexShrink:'0'});
$('edpanel').querySelector('.edhead').insertAdjacentElement('afterend',projectPanel);
const projectNode=(tag,id,key,parent=projectPanel)=>{const n=document.createElement(tag);if(id)n.id=id;if(key)staticText(n,key);parent.appendChild(n);return n;};
const projectTitle=projectNode('h3','','importTitle');projectTitle.style.margin='0 0 6px';
const projectFilename=projectNode('div','projectImportFilename','');projectFilename.style.overflowWrap='anywhere';
const projectLegacy=projectNode('div','projectLegacy','');projectLegacy.hidden=true;
projectNode('div','','legacyHelp',projectLegacy);
const projectDateLabel=projectNode('label','','',projectLegacy);textSpan(projectDateLabel,'legacyDate');
const projectDate=projectNode('input','projectLegacyDate','',projectDateLabel);projectDate.type='date';
const projectLegacyTime=projectNode('div','projectLegacyTime','',projectLegacy);
const projectCheckLabel=projectNode('label','','',projectLegacy),projectCheck=projectNode('input','projectLegacyConfirm','',projectCheckLabel);projectCheck.type='checkbox';
textSpan(projectCheckLabel,'legacyConfirmed');
const projectZeroLabel=projectNode('label','projectLegacyZeroLabel','',projectLegacy);projectZeroLabel.hidden=true;projectZeroLabel.style.display='block';
const projectZero=projectNode('input','projectLegacyZero','',projectZeroLabel);projectZero.type='checkbox';
const projectZeroText=projectNode('span','','',projectZeroLabel);projectZeroLabel.title=tr('legacyZeroHelp');
const projectPrepare=projectNode('button','projectLegacyPrepare','prepare',projectLegacy);projectPrepare.type='button';
const projectSummary=projectNode('div','projectImportSummary','');projectSummary.setAttribute('role','status');
const projectConflicts=projectNode('details','projectImportConflicts','');projectConflicts.hidden=true;
projectNode('summary','','conflictsTitle',projectConflicts);
const projectConflictList=projectNode('div','projectImportConflictList','',projectConflicts);Object.assign(projectConflictList.style,{maxHeight:'200px',overflow:'auto',whiteSpace:'pre-wrap'});
const projectActions=projectNode('div','','');projectActions.className='bar';
const projectMerge=projectNode('button','projectImportMerge','mergeKeep',projectActions);projectMerge.type='button';projectMerge.className='pbtn';
const projectCancel=projectNode('button','projectImportCancel','',projectActions);ownText(projectCancel,()=>ui('common.cancel'));projectCancel.type='button';
const projectOther=projectNode('details','',''),projectOtherSummary=projectNode('summary','','otherMethods',projectOther);
projectOtherSummary.className='note';
const projectReplace=projectNode('button','projectImportReplace','replace',projectOther);projectReplace.type='button';
let pendingProject=null,projectImportGeneration=0;
function closeProjectImport(){projectImportGeneration++;pendingProject=null;projectPanel.hidden=true;}
projectCancel.onclick=closeProjectImport;
function showProjectPreview(){
 const pending=pendingProject;if(!pending||!pending.data)return;
 const merged=VDRProject.mergeProjects(project(),pending.data);pending.preview=merged;pending.current=content();
 const s=merged.stats;ownText(projectSummary,()=>tr('importStats',{added:s.added,duplicates:s.duplicates,conflicts:s.conflicts?tr('importConflicts',{count:s.conflicts}):''}));
 projectMerge.disabled=false;projectReplace.disabled=false;projectConflicts.hidden=!s.conflicts;projectConflicts.open=false;for(const node of projectConflictList.children)localizedText.delete(node);projectConflictList.replaceChildren();
 for(const conflict of merged.conflicts.slice(0,100)){
  const arr=merged.project.state[conflict.channel]||[],current=arr.find(c=>c.id===conflict.currentId),incoming=arr.find(c=>c.id===conflict.incomingId);
  const row=document.createElement('div');row.style.margin='6px 0';
  ownText(row,()=>tr('conflictRow',{channel:conflict.channel,time:fmtDate(conflict.start,OFS()),current:current?.text||'',incoming:incoming?.text||''}));projectConflictList.appendChild(row);
 }
 if(merged.conflicts.length>100)ownText(projectNode('div','','',projectConflictList),()=>tr('moreConflicts',{count:merged.conflicts.length-100}));
}
function legacyTimePreview(){
 if(!pendingProject||pendingProject.raw.version!==2)return;
 const seconds=pendingProject.raw.t0_utc_s,day=projectDate.value,base=Date.parse(day+'T00:00:00Z');
 ownText(projectLegacyTime,()=>Number.isFinite(base)&&Number.isFinite(seconds)?tr('legacyStart',{time:new Date((base/1000+seconds+LT)*1000).toISOString().replace('T',' ').slice(0,23)+' UTC'+(LT>=0?'+':'')+(LT/3600)}):tr('chooseDate'));
}
projectDate.onchange=()=>{if(pendingProject){pendingProject.data=null;pendingProject.preview=null;}projectMerge.disabled=true;projectReplace.disabled=true;projectCheck.checked=false;staticText(projectSummary,'recheckDate');projectConflicts.hidden=true;legacyTimePreview();};
projectCheck.onchange=()=>{if(!projectCheck.checked&&pendingProject){pendingProject.data=null;pendingProject.preview=null;projectMerge.disabled=true;projectReplace.disabled=true;staticText(projectSummary,'confirmSourceDate');projectConflicts.hidden=true;}};
projectZero.onchange=()=>{if(pendingProject){pendingProject.data=null;pendingProject.preview=null;projectMerge.disabled=true;projectReplace.disabled=true;staticText(projectSummary,'recheckPreview');projectConflicts.hidden=true;}};
projectPrepare.onclick=()=>{
 if(!pendingProject||!projectCheck.checked){staticText(projectSummary,'checkFirst');return;}
 try{pendingProject.data=VDRProject.migrateLegacyV2(pendingProject.raw,ctx.recordingId,projectDate.value,{allowZeroDuration:projectZero.checked});showProjectPreview();}
 catch(e){pendingProject.data=null;projectMerge.disabled=true;projectReplace.disabled=true;ownText(projectSummary,()=>errorText(e));}
};
projectMerge.onclick=()=>{
 if(!pendingProject?.data)return;
 try{finishCompose();if(pendingProject.current!==content()){showProjectPreview();const previousSummary=localizedText.get(projectSummary);ownText(projectSummary,()=>previousSummary()+tr('previewChanged'));return;}
  const merged=VDRProject.mergeProjects(project(),pendingProject.data);
  if(merged.stats.added){const currentHandle=files.handle();replaceProject(merged.project,currentHandle,true);files.changed();status();}
  closeProjectImport();flash(()=>merged.stats.added?tr('mergeDone',{count:merged.stats.added,overlaps:merged.stats.conflicts?tr('mergeBoth'):''}):tr('mergeNoNew'));
 }catch(e){ownText(projectSummary,()=>tr('mergeFailed',{message:errorText(e)}));}
};
projectReplace.onclick=()=>{
 if(!pendingProject?.data)return;
 if(!confirm(tr('replaceConfirm',{unsaved:files.dirty()?tr('replaceUnsaved'):''})))return;
 // A converted v2/v3 file must not become the save target; saving asks for a new v4 master.
 try{const pending=pendingProject,legacy=pending.raw.version!==4;replaceProject(prepareImport(pending.data),legacy?null:pending.handle,true);if(legacy)files.changed();closeProjectImport();status();flash(()=>tr('progressLoaded'));}
 catch(e){ownText(projectSummary,()=>tr('loadFailed',{message:errorText(e)}));}
};
async function importFile(f,handle=null){
 closeProjectImport();const generation=projectImportGeneration;
 try{if(f.size>20*1024*1024)throw editorError('projectTooLarge');
  const text=await f.text();if(generation!==projectImportGeneration)return;
  if(text.length>20*1024*1024)throw editorError('projectTooLarge');
  const raw=JSON.parse(text.replace(/^\uFEFF/,''));let allowLegacy=false;
  if(raw.version===3){if(!confirm(tr('legacyV3Confirm')))return;allowLegacy=true;}
  const d=raw.version===2?null:VDRProject.validateImport(raw,ctx.recordingId,{allowLegacy});
  pendingProject={raw,data:d,handle};projectFilename.textContent=f.name;projectLegacy.hidden=raw.version!==2;projectCheck.checked=false;
  projectMerge.disabled=true;projectReplace.disabled=true;projectConflicts.hidden=true;projectOther.open=false;ownText(projectSummary,'');
  if(raw.version===2){const zeroCount=Object.values(raw.state||{}).reduce((n,arr)=>n+(Array.isArray(arr)?arr.filter(c=>c&&Number.isFinite(c.start)&&c.start===c.end).length:0),0);
   projectZero.checked=false;projectZeroLabel.hidden=!zeroCount;projectZeroLabel.style.display=zeroCount?'block':'none';ownText(projectZeroText,()=>tr('legacyZeroCount',{count:zeroCount}));
   projectDate.value=new Date(T0*1000).toISOString().slice(0,10);legacyTimePreview();}
  else showProjectPreview();
  projectPanel.hidden=false;projectPanel.scrollIntoView({block:'nearest'});
 }catch(e){alert(tr('masterNotLoaded',{message:errorText(e)}));}
}
$('loadP').onclick=async()=>{
 finishCompose();
 if(typeof window.showOpenFilePicker!=='function'){$('imJson').click();return;}
 try{const [handle]=await window.showOpenFilePicker({multiple:false,types:[{description:tr('masterDescription'),accept:{'application/json':['.json']}}]});await importFile(await handle.getFile(),handle);}
 catch(e){if(e.name!=='AbortError')alert(tr('masterOpenFailed',{message:errorText(e)}));}
};
$('imSrtBtn').onclick=()=>$('imSrt').click();$('imSugBtn').onclick=()=>$('imSug').click();
for(const m of document.querySelectorAll('details.menu'))m.addEventListener('click',e=>{if(e.target.closest('button'))m.open=false});
document.addEventListener('click',e=>{for(const m of document.querySelectorAll('details.menu[open]'))if(!m.contains(e.target))m.open=false});
$('imJson').onchange=e=>{const f=e.target.files[0];e.target.value='';if(f)return importFile(f);};
// The importer is inline so colleagues can see the channel, complete date and
// audio name together. A receipt stores raw source time: never apply OFS twice.
const srtPanel=document.createElement('section');srtPanel.id='srtImportPanel';srtPanel.hidden=true;
Object.assign(srtPanel.style,{flexBasis:'100%',width:'100%',boxSizing:'border-box',padding:'14px',margin:'8px 0',border:'1px solid #a9c6c4',borderRadius:'8px',background:'#f5faf9',textAlign:'left'});
const srtNode=(tag,id,text,parent=srtPanel)=>{const n=document.createElement(tag);if(id)n.id=id;if(text)n.textContent=text;parent.appendChild(n);return n;};
const srtTitle=srtNode('h3','srtImportTitle','');ownText(srtTitle,()=>ui('srt.import'));srtTitle.style.margin='0 0 8px';
staticText(srtNode('p','',''),'srtIntro');
const channelLabel=srtNode('label','','');textSpan(channelLabel,'srtChannelLabel');
const srtChannel=srtNode('select','srtImportChannel','',channelLabel);
for(const ch of CH){const op=srtNode('option','',ch,srtChannel);op.value=ch;}
const originLabel=srtNode('label','','');textSpan(originLabel,'srtOriginLabel');
const srtOrigin=srtNode('select','srtImportOrigin','',originLabel);
originLabel.style.display='inline-block';
const receiptLabel=srtNode('label','','');textSpan(receiptLabel,'srtReceiptLabel');receiptLabel.style.display='block';receiptLabel.style.marginTop='10px';
const srtReceiptFile=srtNode('input','srtReceiptFile','',receiptLabel);srtReceiptFile.type='file';srtReceiptFile.accept='.json,application/json';
const manualRow=srtNode('label','srtManualRow','');textSpan(manualRow,'srtManualLabel');manualRow.style.display='block';manualRow.style.marginTop='10px';
const srtLocalStart=srtNode('input','srtLocalStart','',manualRow);srtLocalStart.type='datetime-local';srtLocalStart.step='0.001';
const zoneText=srtNode('span','','',manualRow);ownText(zoneText,()=>tr('srtZone',{zone:'UTC'+(LT>=0?'+':'')+(LT/3600)}));
const srtPreview=srtNode('p','srtImportPreview','');srtPreview.style.whiteSpace='pre-line';
const srtError=srtNode('p','srtImportError','');srtError.style.color='#b42318';srtError.setAttribute&&srtError.setAttribute('role','alert');
const srtApply=srtNode('button','srtImportApply',''),srtCancel=srtNode('button','srtImportCancel','');staticText(srtApply,'srtApply');ownText(srtCancel,()=>ui('common.cancel'));
srtApply.type=srtCancel.type='button';srtCancel.className='ghost';srtCancel.style.marginLeft='8px';
$('imSrtBtn').insertAdjacentElement('afterend',srtPanel);
let pendingSrt=null,srtChoices=[],attachedReceipts=[],srtReadId=0,srtReceiptReadId=0;
const receiptLabelText=r=>tr('receiptOption',{file:r.audio_file,time:new Date((Date.parse(r.source_start_utc)/1000+OFS()+LT)*1000).toISOString().replace('T',' ').slice(0,23)});
function selectedReceipt(){const m=/^receipt:(\d+)$/.exec(srtOrigin.value);return m?srtChoices[+m[1]]||null:null;}
function alignment(){if(!pendingSrt)throw editorError('srtSelectFirst');
 if(srtOrigin.value!=='manual'&&!selectedReceipt())throw editorError('receiptReselect');
 return srtAlignment(pendingSrt.got,{receipt:selectedReceipt(),recordingId:ctx.recordingId,channels:CH,channel:srtChannel.value,
  t0:T0,displayTzHours:LT/3600,offsetSeconds:OFS(),localStart:srtLocalStart.value});}
function previewSrt(){ownText(srtError,'');manualRow.hidden=srtOrigin.value!=='manual';manualRow.style.display=manualRow.hidden?'none':'block';srtLocalStart.disabled=manualRow.hidden;
 try{const a=alignment(),r=a.receipt,existing=state[srtChannel.value]||[],overlaps=countSrtOverlaps(pendingSrt.got,existing,a.shift),
   outside=pendingSrt.got.filter(c=>c.s+a.shift<0||c.e+a.shift>(ctx.dur||Infinity)).length;
  const subtitleFile=pendingSrt.file,subtitleCount=pendingSrt.got.length,targetChannel=srtChannel.value;
  ownText(srtPreview,()=>tr('srtPreviewFile',{file:subtitleFile,count:subtitleCount,channel:targetChannel})+
   (r?tr('srtPreviewReceipt',{file:r.audio_file,hash:r.audio_sha256}):tr('srtManualWarning'))+
   tr('srtPreviewRange',{zero:fmtDate(a.shift,OFS()),start:fmtDate(a.start,OFS()),end:fmtDate(a.end,OFS())})+
   (existing.length?tr('srtExisting',{count:existing.length,overlaps}):'')+(outside?tr('srtOutside',{count:outside}):'')+tr('srtVerify'));
  srtApply.disabled=false;
 }catch(e){ownText(srtPreview,'');ownText(srtError,()=>errorText(e));srtApply.disabled=true;}}
function refreshSrtOrigins(preferred=null){
 const saved=typeof ctx.exports==='function'?ctx.exports():[],all=[...(Array.isArray(saved)?saved:[]),...attachedReceipts];srtChoices=[];
 for(const value of all){try{const r=validateTranscriptionReceipt(value,ctx.recordingId,CH);
   if(r.channel===srtChannel.value&&!srtChoices.some(p=>p.audio_sha256===r.audio_sha256&&p.source_start_utc===r.source_start_utc))srtChoices.push(r);
  }catch(_){/* Foreign or obsolete descriptors never become alignment choices. */}}
 srtChoices.reverse();srtOrigin.innerHTML='';
 const op=srtNode('option','',tr('manualOrigin'),srtOrigin);op.value='manual';
 srtChoices.forEach((r,i)=>{const item=srtNode('option','',receiptLabelText(r),srtOrigin);item.value='receipt:'+i;});
 let index=preferred?srtChoices.findIndex(r=>r.audio_sha256===preferred.audio_sha256&&r.source_start_utc===preferred.source_start_utc):-1;
 if(index<0&&pendingSrt){const stem=s=>String(s).replace(/^.*[\\/]/,'').replace(/\.[^.]+$/,'').toLowerCase(),wanted=stem(pendingSrt.file);
  index=srtChoices.findIndex(r=>stem(r.audio_file)===wanted);}
 srtOrigin.value=index>=0?'receipt:'+index:srtChoices.length?'receipt:0':'manual';previewSrt();
}
srtChannel.onchange=()=>refreshSrtOrigins();srtOrigin.onchange=previewSrt;srtLocalStart.oninput=previewSrt;srtLocalStart.onchange=previewSrt;
srtCancel.onclick=()=>{srtReadId++;pendingSrt=null;srtPanel.hidden=true;};
$('imSrt').onchange=async e=>{const f=e.target.files[0];e.target.value='';if(!f)return;const read=++srtReadId;pendingSrt=null;srtPanel.hidden=true;
 try{const source=await readSrtFile(f);if(read!==srtReadId)return;
  pendingSrt={file:String(f.name||tr('srtFilename')).slice(0,1000),...source};srtChannel.value=sel.value;
  srtLocalStart.value=new Date((T0+LT+OFS())*1000).toISOString().slice(0,23);srtPanel.hidden=false;
  refreshSrtOrigins();srtPanel.scrollIntoView({block:'nearest'});
 }catch(error){if(read===srtReadId)alert(tr('srtNotImported',{message:errorText(error)}));}};
srtReceiptFile.onchange=async e=>{const f=e.target.files[0];e.target.value='';if(!f||!pendingSrt)return;const read=srtReadId,receiptRead=++srtReceiptReadId;srtApply.disabled=true;
 try{if(f.size>1000000)throw editorError('receiptTooLarge');
  const data=JSON.parse((await f.text()).replace(/^\uFEFF/,'')),receipt=validateTranscriptionReceipt(data,ctx.recordingId,CH);
  if(read!==srtReadId||receiptRead!==srtReceiptReadId||!pendingSrt)return;
  if(receipt.channel!==srtChannel.value)throw editorError('receiptWrongChannel',{receipt:receipt.channel,selected:srtChannel.value});
  attachedReceipts.push(receipt);refreshSrtOrigins(receipt);
 }catch(error){if(read===srtReadId&&receiptRead===srtReceiptReadId){ownText(srtError,()=>tr('receiptNotUsed',{message:errorText(error)}));srtApply.disabled=true;}}};
srtApply.onclick=()=>{
 try{const a=alignment(),ch=srtChannel.value,got=pendingSrt.got,arr=state[ch]||[],overlaps=countSrtOverlaps(got,arr,a.shift);
  if(!Number.isSafeInteger(uid+got.length))throw editorError('idOverflow');
  if(arr.length&&!confirm(tr('srtAppendConfirm',{channel:ch,existing:arr.length,overlaps,count:got.length})))return;
  const origin={file:pendingSrt.file,srt_sha256:pendingSrt.srt_sha256,srt_bytes:pendingSrt.srt_bytes,
   imported_at_utc:new Date().toISOString(),method:a.receipt?'export_receipt':'manual_local_time',zero_source_utc:a.zero_utc,
   display_tz_hours:LT/3600,import_time_correction:{offset_s:OFS(),basis:(ctx.extra.get()||{}).time_offset_basis||''}};
  if(a.receipt){origin.audio_file=a.receipt.audio_file;origin.audio_sha256=a.receipt.audio_sha256;}
  const additions=got.map((c,i)=>({id:uid+i,start:c.s+a.shift,end:c.e+a.shift,spk:'',text:c.text,orig:c.text,origNo:c.no,st:0,ovl:false,srt_source:origin}));
  // Source/format/alignment checks and confirmation have all completed before
  // changing a channel, recording an undo point or allocating permanent IDs.
  UNDO.push({ch,data:JSON.stringify(arr),active:ch===sel.value?active:null});if(UNDO.length>40)UNDO.shift();
  state[ch]=arr.concat(additions).sort((x,y)=>x.start-y.start||x.end-y.end);uid+=got.length;
  if(sel.value!==ch){sel.value=ch;sel.dispatchEvent(new Event('change'));}active=null;
  save();render();pendingSrt=null;srtPanel.hidden=true;flash(()=>tr('srtDone',{count:got.length,channel:ch}));
 }catch(error){ownText(srtError,()=>tr('srtPending',{message:errorText(error)}));srtApply.disabled=true;}};
$('imSug').onchange=e=>{const f=e.target.files[0];e.target.value='';if(!f)return;f.text().then(t=>{const d=JSON.parse(t.replace(/^﻿/,''));if(!d.suggestions){alert(tr('notSuggestions'));return}SUG=d;try{localStorage.setItem(SKEY,JSON.stringify(d))}catch(_){}const n=Object.values(d.suggestions).reduce((a,m)=>a+Object.keys(m).length,0);flash(()=>tr('suggestionsImported',{count:n,method:d.method}));render()})};
$('edreset').onclick=()=>{finishCompose();if(!cues().length){flash(()=>tr('channelEmpty'));return}if(confirm(tr('clearConfirm',{channel:sel.value}))){snap();state[sel.value]=[];active=null;render();save()}};
$('exWav').onclick=async()=>{const button=$('exWav');button.disabled=true;flash(()=>tr('audioBusy'));
 try{await ctx.exportWav(sel.value);flash(()=>tr('audioDone'));}
 catch(error){flash(()=>tr('audioFailed',{message:errorText(error)}));}finally{button.disabled=false;}};
renderSpk();render();lastState=JSON.parse(JSON.stringify(state));lastContent=content();status();if(resumed)flash(resumed);
window.addEventListener('beforeunload',e=>{finishCompose();if(files.dirty()){e.preventDefault();e.returnValue=tr('unloadUnsaved');}});
const oldBox=$('legacyProgress');oldBox.hidden=oldProgress.length===0;
if(oldProgress.length){
 const picker=$('legacyChoice');picker.innerHTML='';
 oldProgress.sort((a,b)=>a.t0-b.t0).forEach((L,i)=>{
  const op=document.createElement('option');op.value=i;
  ownText(op,()=>tr('legacyOption',{time:new Date((L.t0+LT)*1000).toISOString().replace('T',' ').slice(0,19),count:Object.values(L.data.state).reduce((n,a)=>n+a.length,0)}));
  picker.appendChild(op);
 });
 $('legacyApply').onclick=()=>{
  if(!confirm(tr('legacyCacheConfirm')))return;
  const L=oldProgress[+picker.value],d=L.data;
  try{const x=JSON.parse(localStorage.getItem(L.key+':extra')||'null');
   const next=prepareImport({...d,version:3,t0_utc:new Date(L.t0*1000).toISOString(),tz_hours:LT/3600,
    spks:d.spks||DEFAULT_SPK.slice(),spkColor:d.spkColor||{},extra:x||ctx.defaultExtra},true);
   replaceProject(next,null,true);files.changed();status();flash(()=>tr('legacyCacheLoaded'));
  }catch(e){alert(tr('legacyCacheInvalid',{message:errorText(e)}));}
 };
}
function refreshLanguage(){
 // Never call render/updRow/finishCompose here: native text and IME targets,
 // input values, selection, scroll and case revisions must remain untouched.
 for(const [node,read] of localizedText)node.textContent=read();
 VDRI18n.apply(list);VDRI18n.apply($('spkbar'));
 const byId=new Map(cues().map(c=>[c.id,c]));
 for(const row of list.children){const c=byId.get(+row.dataset?.id);if(!c)continue;
  const play=row.querySelector('.etime');if(play)play.title=ui('action.playCue')+'（Ctrl+Enter）';
  const suggestion=row.querySelector('.esug'),g=sugOf(c);
  if(suggestion&&g){suggestion.textContent=tr('suggestion',{speaker:g.spk,score:(+g.score).toFixed(2)});
   suggestion.title=tr('suggestionTitle',{score:g.score,margin:g.margin,caution:g.confident?'':tr('suggestionCaution')});}
 }
 for(const track of waveTracks){
  const configured=typeof ctx.channelLabels==='function'?ctx.channelLabels(track.ch):(ctx.channelLabels||{})[track.ch];
  track.label.textContent=configured||track.ch;track.label.title=tr('switchChannelHelp',{channel:track.ch});
  track.canvas.title=tr('waveHelp',{channel:track.ch});track.canvas.setAttribute('aria-label',tr('waveAria',{channel:track.ch}));
 }
 projectZeroLabel.title=tr('legacyZeroHelp');
 for(const option of srtOrigin.children){const m=/^receipt:(\d+)$/.exec(option.value);
  if(option.value==='manual')option.textContent=tr('manualOrigin');else if(m&&srtChoices[+m[1]])option.textContent=receiptLabelText(srtChoices[+m[1]]);}
 timingLabel();updProg();renderComposer();playIcons();drawStrip();drawWave();status();
 if(Date.now()<flashUntil&&flashContent)$('edsave').textContent=flashContent();
}
const disposeI18n=VDRI18n.onChange(refreshLanguage);
return {state:()=>state,render,changed:save,dirty:files.dirty,disposeI18n,refreshMedia(){drawStrip();drawWave();renderVhf();}};
}
const api={init,createFileSession,invalidateReviewedChanges,parseSrt,readSrtFile,validateTranscriptionReceipt,srtAlignment,countSrtOverlaps,waveWindow,waveTimeAt,activityWindow};
if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.Editor=api;
})(typeof self!=='undefined'?self:this);
