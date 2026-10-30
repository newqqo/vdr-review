// 可測試的時間、字幕視窗與舊暫存選擇規則；不讀寫瀏覽器或案件資料。
(function(root){
'use strict';
function parseDateTime(s,tz){
 const m=/^(\d{4})[-/](\d{1,2})[-/](\d{1,2})[ T](\d{1,2}):(\d{2})(?::(\d{2})(?:\.(\d{1,3}))?)?$/.exec(String(s).trim());
 if(!m)return null;
 const [y,mo,d,h,mi,se]=m.slice(1,7).map(x=>+(x||0)),ms=Number((m[7]||'0').padEnd(3,'0'));
 const t=Date.UTC(y,mo-1,d,h,mi,se,ms),v=new Date(t);
 if(v.getUTCFullYear()!==y||v.getUTCMonth()!==mo-1||v.getUTCDate()!==d||h>23||mi>59||se>59)return null;
 return t-tz*1000;
}
function clipCues(cues,dur){
 return cues.filter(c=>Number.isFinite(c.start)&&Number.isFinite(c.end)&&c.end>0&&c.start<dur&&c.end>c.start)
  .map(c=>Object.assign({},c,{start:Math.max(0,c.start),end:Math.min(dur,c.end)}))
  .filter(c=>Math.round(c.end*1000)>Math.round(c.start*1000)).sort((a,b)=>a.start-b.start||a.end-b.end);
}
function validProgress(d){
 return !!d&&(d.t0==null||Number.isFinite(d.t0))&&!!d.state&&typeof d.state==='object'&&!Array.isArray(d.state)&&
 Object.values(d.state).every(a=>Array.isArray(a)&&a.every(c=>c&&Number.isFinite(c.start)&&Number.isFinite(c.end)&&c.end>c.start));
}
function autoLegacy(items){return items.length===1?items[0]:null;}
function shiftState(state,shift){
 const out={};for(const [ch,arr] of Object.entries(state||{}))out[ch]=arr.map(c=>({...c,start:c.start+shift,end:c.end+shift}));
 return out;
}
const api={parseDateTime,clipCues,validProgress,autoLegacy,shiftState};
if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.ReviewState=api;
})(typeof self!=='undefined'?self:this);
