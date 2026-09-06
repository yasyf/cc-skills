// @requires AI_GLYPH,AI_STATUS,AI_TOOLS_READ,HTML,ID_RE,REF,aiEntry,aiHandle,aiKind,aiMark,palItems,plain,$,AI_RETRY_MSG,aiAdopt,aiFail,aiOpen,aiStartRun,el,esc,mdToHtml,scrub
// @defines palAbortRun,palAll,palAsk,palCited,palClose,palFinish,palHandoff,palHandoffRow,palHits,palIx,palList,palLive,palOpen,palRender,palRun,palSchedule,palScore,palSearch,palStep,palTimer,palWire
function palScore(item,q){
 const hay=(item.label+" "+(item.rid||"")+" "+(item.sub||"")).toLowerCase();
 const i=hay.indexOf(q);
 if(i<0)return -1;
 return (item.label.toLowerCase().startsWith(q)?0:1)+(i>item.label.length?1:0)+i/400;
}
let palList=[],palIx=0,palAll=null,palHits=[],palTimer=0,palLive=null;
function palRender(){
 const box=$("#palList");box.innerHTML="";
 palList.forEach((it,ix)=>{
  if(it.kind==="divider"){box.append(el("div","pal-row pal-div",esc(it.label)));return}
  const row=el("div","pal-row"+(ix===palIx?" on":""),
   `<span class="pal-g">${esc(AI_GLYPH[it.kind]||"›")}</span>`+
   (it.dot?`<i class="pal-dot ${esc(it.dot)}"></i>`:"")+
   `<span class="pal-l">${esc(it.label)}</span>`+
   (it.rid?`<span class="pal-id">${esc(it.rid)}</span>`:"")+
   `<span class="pal-s">${esc((it.sub||"").slice(0,90))}</span>`);
  row.onclick=()=>palRun(ix);
  box.append(row);
 });
 const on=box.querySelector(".pal-row.on");
 if(on)on.scrollIntoView({block:"nearest"});
}
function palStep(dir){
 const n=palList.length;
 if(!n)return;
 let ix=palIx;
 do{ix=Math.min(n-1,Math.max(0,ix+dir))}while(palList[ix].kind==="divider"&&ix>0&&ix<n-1);
 if(palList[ix].kind!=="divider")palIx=ix;
 palRender();
}
function palSearch(raw){
 const all=palAll||(palAll=palItems());
 const s=String(raw||"");
 const mode=s[0]===">"?"cmd":s[0]==="?"&&HTML.dataset.ai?"ai":"";
 const ask=(mode?s.slice(1):s).trim();
 const q=ask.toLowerCase();
 let pool=mode==="cmd"?all.filter(i=>i.kind==="cmd"):mode==="ai"?all.filter(i=>i.kind==="ai"):all;
 if(q)pool=pool.map(i=>({i,sc:palScore(i,q)})).filter(x=>x.sc>=0).sort((a,b)=>a.sc-b.sc).map(x=>x.i);
 else if(!mode)pool=pool.filter(i=>i.kind==="section"||i.kind==="ai").slice(0,12);
 palAbortRun();
 palList=pool.slice(0,40);
 palHits=palList.slice(0,6);
 if(q&&mode!=="cmd"&&HTML.dataset.ai){
  palList.push({kind:"ai",label:"Ask the doc for “"+ask+"”",sub:pool.length?"Ask instead":"No exact match — ask instead",stay:true,ask:true,run:()=>palAsk(ask)});
  if(ask.length>=4&&(mode==="ai"||!pool.length))palSchedule(ask);
 }
 palIx=0;
 palRender();
}
function palSchedule(q){
 clearTimeout(palTimer);
 palTimer=setTimeout(()=>palAsk(q),600);
}
function palAbortRun(){
 clearTimeout(palTimer);
 palTimer=0;
 if(palLive){palLive.ctrl.abort();palLive=null}
 const host=$("#palAns");
 host.hidden=true;
 host.innerHTML="";
}
const palHandoffRow=()=>({kind:"ai",label:"Open in Ask ↵",sub:"Continue this in the panel",stay:true,run:palHandoff});
function palAsk(q){
 palAbortRun();
 const host=$("#palAns");
 host.hidden=false;
 const body=el("div","pal-ans-body");
 body.append(el("span","ai-wait","Thinking…"));
 host.append(body);
 const run=aiStartRun(q,{tools:AI_TOOLS_READ});
 palLive=run;
 const i=palList.findIndex(it=>it.ask);
 if(i>=0){palList[i]=palHandoffRow();palRender()}
 let pending=0;
 const paint=()=>{pending=0;if(run.text){body.innerHTML=mdToHtml(run.text);scrub(body)}else if(run.retrying)body.textContent=AI_RETRY_MSG};
 run.listeners.add(r=>{
  if(palLive!==r)return;
  if(!r.done){if(!pending)pending=requestAnimationFrame(paint);return}
  if(pending)cancelAnimationFrame(pending);
  pending=0;
  if(r.error){if(r.error.name!=="AbortError")aiFail(body,r.error);return}
  paint();
  if(!r.text)body.textContent=r.retried?"The model ran out of room twice; ask a narrower question.":"No answer came back.";
  palFinish(r);
 });
}
function palCited(run){
 const ids=[];
 if(ID_RE)String(run.text).replace(ID_RE,(all,pre,id)=>{ids.push(id);return all});
 return [...new Set([...run.sources,...ids])].filter(id=>REF[id]).slice(0,8);
}
function palFinish(run){
 const list=palHits.slice();
 const cited=palCited(run);
 if(cited.length){
  list.push({kind:"divider",label:"Cited"});
  cited.forEach(id=>{
   const e=aiEntry(id);
   if(!e)return;
   list.push({kind:aiKind(id),label:aiHandle(e),sub:plain(e.p||e.t||""),dot:(AI_STATUS[e.s]||["",""])[0],rid:id,run:()=>{openRef(id);aiMark(id)}});
  });
 }
 list.push(palHandoffRow());
 palList=list;
 palIx=list.length-1;
 palRender();
}
function palHandoff(){
 const run=palLive;
 if(!run)return;
 palLive=null;
 palClose();
 aiOpen();
 aiAdopt(run);
}
function palRun(ix){
 const it=palList[ix];
 if(!it||it.kind==="divider")return;
 if(!it.stay)palClose();
 it.run();
}
function palOpen(seed){
 const pal=$("#pal");
 palAll=null;
 pal.hidden=false;
 const input=$("#palIn");
 input.placeholder=HTML.dataset.ai?"Search the doc · > for commands · ? to ask":"Search the doc · > for commands";
 input.value=seed||"";
 palSearch(input.value);
 input.focus();
 input.select();
}
function palClose(){
 $("#pal").hidden=true;
 palAbortRun();
}
function palWire(){
 const input=$("#palIn");
 input.oninput=()=>palSearch(input.value);
 input.onkeydown=e=>{
  if(e.key==="ArrowDown"){e.preventDefault();palStep(1)}
  else if(e.key==="ArrowUp"){e.preventDefault();palStep(-1)}
  else if(e.key==="Enter"){e.preventDefault();palRun(palIx)}
  else if(e.key==="Escape"){e.preventDefault();palClose()}
 };
 $("#pal").addEventListener("pointerdown",e=>{if(e.target.id==="pal")palClose()});
}
