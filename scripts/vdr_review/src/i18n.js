// UI-only language resources. Never translate evidence, cue text, speakers or
// project fields. Importing this module performs no DOM, storage or network work.
(function(root){
'use strict';
const DEFAULT_LOCALE='zh-TW',FALLBACK_LOCALE='en';
const own=(object,key)=>Object.prototype.hasOwnProperty.call(object,key);
const MESSAGES=Object.freeze({
 'zh-TW':Object.freeze({
  'app.title':'VDR 核聽工具',
  'action.play':'播放','action.pause':'暫停','action.resume':'繼續',
  'action.playCue':'播放本句','action.chooseCue':'先點選一句',
  'action.save':'儲存案件','action.load':'開啟進度','action.delete':'刪除',
  'action.confirmDeleteCue':'刪除這一句？','action.undo':'復原','action.addCue':'新增一句',
  'action.exportAudio':'匯出音檔','action.exportResults':'匯出調查成果',
  'playback.playing':'▶ 播放中','playback.currentPosition':'⏸ 目前位置',
  'playback.paused':'已暫停','playback.ready':'可播放','playback.buffering':'載入錄音…',
  'playback.playingCount':'{count} 句播放中','playback.error':'播放未完成：{message}',
  'playback.playingReplayLabel':'播放中，重播本句：{time}',
  'playback.playCueLabel':'播放本句：{time}',
  'review.title':'聽寫記錄','review.reviewed':'已核','review.unreviewed':'未核',
  'review.partial':'部分不明','review.unclear':'無法辨識',
  'review.unsaved':'尚未儲存','review.saved':'已儲存','review.unassignedSpeaker':'未指定講者',
  'review.timingPending':'時間待校','review.progress':'{channel}　已核 {done}／{total}',
  'workbench.title':'核聽工作區','workbench.typeLabel':'直接打字',
  'workbench.typePlaceholder':'輸入聽到的內容…','workbench.finishCue':'記下一句 ↵',
  'workbench.inputPlaceholder':'輸入內容',
  'workbench.inputTitle':'點選從這句繼續播放；已在輸入時可直接修字',
  'workbench.showAdvanced':'顯示進階工具','workbench.filterAll':'全部',
  'workbench.searchPlaceholder':'搜尋內容／講者／原文',
  'wave.title':'聲道波形','wave.activities':'活動段','wave.pending':'未分析',
  'wave.ready':'波形已處理','wave.loading':'正在建立波形…','wave.missing':'缺段',
  'wave.range':'範圍','wave.currentChannel':'目前聲道',
  'srt.title':'匯出音檔／匯入 SRT','srt.import':'匯入 SRT','srt.export':'匯出 SRT',
  'srt.chooseFile':'選擇 SRT 字幕檔','srt.alignment':'轉錄時間對照',
  'srt.audioExport':'匯出轉錄音檔','srt.exportSegment':'匯出片段',
  'srt.exportWhole':'匯出整份錄音','srt.originStart':'字幕 00:00 對應時間',
  'srt.targetChannel':'匯入聲道','srt.imported':'已匯入 {count} 句到 {channel}',
  'common.cancel':'取消','common.confirm':'確認','common.close':'關閉',
  'common.error':'錯誤','common.missing':'未提供','common.status':'狀態'
 }),
 en:Object.freeze({
  'app.title':'VDR Audio Review',
  'action.play':'Play','action.pause':'Pause','action.resume':'Resume',
  'action.playCue':'Play sentence','action.chooseCue':'Select a sentence first',
  'action.save':'Save case','action.load':'Open progress','action.delete':'Delete',
  'action.confirmDeleteCue':'Delete this sentence?','action.undo':'Undo','action.addCue':'Add sentence',
  'action.exportAudio':'Export audio','action.exportResults':'Export investigation results',
  'playback.playing':'▶ Playing','playback.currentPosition':'⏸ Current position',
  'playback.paused':'Paused','playback.ready':'Ready','playback.buffering':'Loading audio…',
  'playback.playingCount':'Playing {count} sentences','playback.error':'Playback failed: {message}',
  'playback.playingReplayLabel':'Playing, replay sentence: {time}',
  'playback.playCueLabel':'Play sentence: {time}',
  'review.title':'Transcript','review.reviewed':'Reviewed','review.unreviewed':'Unreviewed',
  'review.partial':'Partly unclear','review.unclear':'Unintelligible',
  'review.unsaved':'Unsaved','review.saved':'Saved','review.unassignedSpeaker':'Unassigned speaker',
  'review.timingPending':'Timing needs review','review.progress':'{channel}　Reviewed {done}/{total}',
  'workbench.title':'Review workbench','workbench.typeLabel':'Type what you hear',
  'workbench.typePlaceholder':'Type what you hear…','workbench.finishCue':'Finish sentence ↵',
  'workbench.inputPlaceholder':'Enter transcript',
  'workbench.inputTitle':'Click to play from this sentence; click again while typing to edit',
  'workbench.showAdvanced':'Show advanced tools','workbench.filterAll':'All',
  'workbench.searchPlaceholder':'Search transcript / speaker / original text',
  'wave.title':'Channel activity','wave.activities':'Activity segments','wave.pending':'Not analyzed',
  'wave.ready':'Waveforms processed','wave.loading':'Building waveforms…','wave.missing':'Missing segment',
  'wave.range':'Range','wave.currentChannel':'Current channel',
  'srt.title':'Export audio / Import SRT','srt.import':'Import SRT','srt.export':'Export SRT',
  'srt.chooseFile':'Choose an SRT file','srt.alignment':'Transcription time alignment',
  'srt.audioExport':'Export audio for transcription','srt.exportSegment':'Export segment',
  'srt.exportWhole':'Export full recording','srt.originStart':'Time corresponding to subtitle 00:00',
  'srt.targetChannel':'Target channel','srt.imported':'Imported {count} sentences into {channel}',
  'common.cancel':'Cancel','common.confirm':'Confirm','common.close':'Close',
  'common.error':'Error','common.missing':'Not provided','common.status':'Status'
 }),
 ja:Object.freeze({
  'app.title':'VDR 音声確認ツール',
  'action.play':'再生','action.pause':'一時停止','action.resume':'再開',
  'action.playCue':'この文を再生','action.chooseCue':'文を選択してください',
  'action.save':'案件を保存','action.load':'作業を開く','action.delete':'削除',
  'action.confirmDeleteCue':'この文を削除しますか？','action.undo':'元に戻す','action.addCue':'文を追加',
  'action.exportAudio':'音声を出力','action.exportResults':'調査成果を出力',
  'playback.playing':'▶ 再生中','playback.currentPosition':'⏸ 現在位置',
  'playback.paused':'一時停止中','playback.ready':'再生可能','playback.buffering':'音声を読み込み中…',
  'playback.playingCount':'{count} 文を再生中','playback.error':'再生できませんでした：{message}',
  'playback.playingReplayLabel':'再生中。この文をもう一度再生：{time}',
  'playback.playCueLabel':'この文を再生：{time}',
  'review.title':'文字起こし','review.reviewed':'確認済み','review.unreviewed':'未確認',
  'review.partial':'一部不明','review.unclear':'聞き取り不能',
  'review.unsaved':'未保存','review.saved':'保存済み','review.unassignedSpeaker':'話者未指定',
  'review.timingPending':'時刻の確認が必要','review.progress':'{channel}　確認済み {done}／{total}',
  'workbench.title':'音声確認画面','workbench.typeLabel':'聞こえた内容を入力',
  'workbench.typePlaceholder':'聞こえた内容を入力…','workbench.finishCue':'文を確定 ↵',
  'workbench.inputPlaceholder':'文字起こしを入力',
  'workbench.inputTitle':'クリックでこの文から再生。入力中はそのまま修正できます',
  'workbench.showAdvanced':'詳細ツールを表示','workbench.filterAll':'すべて',
  'workbench.searchPlaceholder':'内容／話者／原文を検索',
  'wave.title':'チャンネル波形','wave.activities':'音声活動区間','wave.pending':'未解析',
  'wave.ready':'波形解析済み','wave.loading':'波形を作成中…','wave.missing':'欠落区間',
  'wave.range':'表示範囲','wave.currentChannel':'現在のチャンネル',
  'srt.title':'音声出力／SRT 読み込み','srt.import':'SRT を読み込む','srt.export':'SRT を出力',
  'srt.chooseFile':'SRT 字幕ファイルを選択','srt.alignment':'文字起こし時刻の対応表',
  'srt.audioExport':'文字起こし用音声を出力','srt.exportSegment':'区間を出力',
  'srt.exportWhole':'録音全体を出力','srt.originStart':'字幕 00:00 に対応する時刻',
  'srt.targetChannel':'読み込み先チャンネル','srt.imported':'{channel} に {count} 文を読み込みました',
  'common.cancel':'キャンセル','common.confirm':'確認','common.close':'閉じる',
  'common.error':'エラー','common.missing':'未指定','common.status':'状態'
 }),
 'zh-CN':Object.freeze({
  'app.title':'VDR 核听工具',
  'action.play':'播放','action.pause':'暂停','action.resume':'继续',
  'action.playCue':'播放本句','action.chooseCue':'请先选择一句',
  'action.save':'保存案件','action.load':'打开进度','action.delete':'删除',
  'action.confirmDeleteCue':'删除这一句？','action.undo':'撤销','action.addCue':'新增一句',
  'action.exportAudio':'导出音频','action.exportResults':'导出调查成果',
  'playback.playing':'▶ 播放中','playback.currentPosition':'⏸ 当前位置',
  'playback.paused':'已暂停','playback.ready':'可播放','playback.buffering':'正在加载录音…',
  'playback.playingCount':'{count} 句播放中','playback.error':'播放未完成：{message}',
  'playback.playingReplayLabel':'播放中，重播本句：{time}',
  'playback.playCueLabel':'播放本句：{time}',
  'review.title':'听写记录','review.reviewed':'已核','review.unreviewed':'未核',
  'review.partial':'部分不明','review.unclear':'无法辨识',
  'review.unsaved':'尚未保存','review.saved':'已保存','review.unassignedSpeaker':'未指定说话人',
  'review.timingPending':'时间待校','review.progress':'{channel}　已核 {done}／{total}',
  'workbench.title':'核听工作区','workbench.typeLabel':'直接打字',
  'workbench.typePlaceholder':'输入听到的内容…','workbench.finishCue':'记下一句 ↵',
  'workbench.inputPlaceholder':'输入内容',
  'workbench.inputTitle':'点击从这句继续播放；已在输入时可直接改字',
  'workbench.showAdvanced':'显示高级工具','workbench.filterAll':'全部',
  'workbench.searchPlaceholder':'搜索内容／说话人／原文',
  'wave.title':'声道波形','wave.activities':'活动段','wave.pending':'未分析',
  'wave.ready':'波形已处理','wave.loading':'正在生成波形…','wave.missing':'缺失段',
  'wave.range':'范围','wave.currentChannel':'当前声道',
  'srt.title':'导出音频／导入 SRT','srt.import':'导入 SRT','srt.export':'导出 SRT',
  'srt.chooseFile':'选择 SRT 字幕文件','srt.alignment':'转录时间对照',
  'srt.audioExport':'导出转录音频','srt.exportSegment':'导出片段',
  'srt.exportWhole':'导出整份录音','srt.originStart':'字幕 00:00 对应时间',
  'srt.targetChannel':'导入声道','srt.imported':'已将 {count} 句导入 {channel}',
  'common.cancel':'取消','common.confirm':'确认','common.close':'关闭',
  'common.error':'错误','common.missing':'未提供','common.status':'状态'
 })
});
function localeName(value){
 if(typeof value!=='string'||!/^\s*[a-z]{2,3}(?:[-_][a-z0-9]{2,8})*\s*$/i.test(value))throw new TypeError('Invalid UI locale');
 return value.trim().replace(/_/g,'-').split('-').map((part,index)=>index===0?part.toLowerCase():
  part.length===2?part.toUpperCase():part.length===4?part[0].toUpperCase()+part.slice(1).toLowerCase():part.toLowerCase()).join('-');
}
function flatMessages(value){
 if(!value||typeof value!=='object'||Array.isArray(value)||![Object.prototype,null].includes(Object.getPrototypeOf(value)))throw new TypeError('UI messages must be a flat dictionary');
 const copy=Object.create(null);
 for(const [key,text] of Object.entries(value)){
  if(!/^[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)*$/i.test(key)||
   key.split('.').some(part=>['__proto__','prototype','constructor'].includes(part))||typeof text!=='string')throw new TypeError('Invalid UI message');
  copy[key]=text;
 }
 return copy;
}
function interpolate(text,params){
 return text.replace(/\{([a-z][a-z0-9_]*)\}/gi,(token,name)=>{
  if(!params||typeof params!=='object'||!own(params,name))return token;
  const value=params[name];return ['string','number','boolean','bigint'].includes(typeof value)?String(value):token;
 });
}
function create(options={}){
 if(!options||typeof options!=='object'||Array.isArray(options))throw new TypeError('Invalid UI language options');
 let locale=localeName(options.locale===undefined?DEFAULT_LOCALE:options.locale);
 const listeners=new Set();
 const fallback=localeName(options.fallbackLocale===undefined?FALLBACK_LOCALE:options.fallbackLocale),catalog=Object.create(null);
 for(const [name,entries] of Object.entries(MESSAGES))catalog[name]=flatMessages(entries);
 function addMessages(name,entries){const normalized=localeName(name),copy=flatMessages(entries);
  catalog[normalized]=Object.assign(Object.create(null),catalog[normalized]||{},copy);return api;}
 function lookup(key,requested){
  const names=[],add=name=>{if(!names.includes(name))names.push(name);const base=name.split('-')[0];if(!names.includes(base))names.push(base);};
  add(localeName(requested));add(fallback);add(FALLBACK_LOCALE);add(DEFAULT_LOCALE);
  for(const name of names)if(catalog[name]&&own(catalog[name],key))return catalog[name][key];
 }
 const api={
  t(key,params={},requested=locale){if(typeof key!=='string')throw new TypeError('UI message key must be a string');
   const text=lookup(key,requested);return text===undefined?key:interpolate(text,params);},
  errorText(error){
   if(error&&typeof error.i18nKey==='string'&&api.has(error.i18nKey)){
    const params={...(error.i18nParams||{})};
    for(const [name,key] of Object.entries(error.i18nParamKeys||{}))if(typeof key==='string'&&api.has(key))params[name]=api.t(key,params);
    return api.t(error.i18nKey,params);
   }
   return String(error?.message??error??'');
  },
  has(key,requested=locale){return typeof key==='string'&&lookup(key,requested)!==undefined;},
  getLocale:()=>locale,
  setLocale(name){const next=localeName(name);if(next!==locale){const previous=locale;locale=next;for(const listener of [...listeners])listener(locale,previous);}return locale;},
  onChange(listener){if(typeof listener!=='function')throw new TypeError('UI language listener must be a function');listeners.add(listener);return ()=>listeners.delete(listener);},
  getMessages(name=locale){return Object.freeze({...catalog[localeName(name)]});},
  apply(scope){
   if(!scope&&typeof document!=='undefined')scope=document;if(!scope)return 0;
   const attributes={'data-i18n':'textContent','data-i18n-title':'title','data-i18n-placeholder':'placeholder','data-i18n-aria-label':'aria-label'};
   const selector=Object.keys(attributes).map(name=>'['+name+']').join(','),nodes=[];
   if(typeof scope.matches==='function'&&scope.matches(selector))nodes.push(scope);
   if(typeof scope.querySelectorAll==='function')nodes.push(...scope.querySelectorAll(selector));
   for(const node of nodes){let params={};const raw=node.getAttribute?.('data-i18n-params');if(raw){try{params=JSON.parse(raw);}catch(e){/* retain visible placeholders on malformed bindings */}}
    for(const [attribute,target] of Object.entries(attributes)){
     const key=node.getAttribute?.(attribute);if(!key)continue;
     if(target==='textContent'){if(node.children?.length)continue;node.textContent=api.t(key,params);}
     else node.setAttribute(target,api.t(key,params));
    }
   }
   return nodes.length;
  },
  addMessages,
  availableLocales:()=>Object.keys(catalog).sort()
 };
 if(options.messages!==undefined){
  if(!options.messages||typeof options.messages!=='object'||Array.isArray(options.messages))throw new TypeError('Invalid UI locale dictionaries');
  const bundles=Object.entries(options.messages).map(([name,entries])=>[localeName(name),flatMessages(entries)]);
  for(const [name,entries] of bundles)addMessages(name,entries);
 }
 return Object.freeze(api);
}
const api=Object.freeze({...create(),create,DEFAULT_LOCALE,FALLBACK_LOCALE,messages:MESSAGES});
if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.VDRI18n=api;
})(typeof self!=='undefined'?self:this);
