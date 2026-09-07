// @requires CMT_ENTRY_RE,HTML,META,REF,aiEl,cmtDecorate,cmtNotesHost,handleOf,openNotes,$,GH_API,GH_TOKEN,el,esc,ghGet,ghPauseUntil,reduced,relTime,siteConfig
// @defines CMT_AUTO_MERGE,CMT_B32,CMT_BASE,CMT_CTX,CMT_DB,CMT_FRAME,CMT_HL,CMT_IDLE,CMT_LANES,CMT_LIVE,CMT_LOAD_ERR,CMT_MARK,CMT_ME,CMT_ME_KEY,CMT_NAME_KEY,CMT_POLL,CMT_POSTED,CMT_POSTED_AT,CMT_QUEUE,CMT_REPOLL,CMT_RETRY_CAP,CMT_SENT,CMT_SENT_KEY,CMT_STORE,CMT_STUCK_MS,CMT_VIEW,CMT_WRITE_KEY,b,cmtAnchorFromSelection,cmtAuthError,cmtAuthFailed,cmtB64,cmtBlob,cmtBoot,cmtBootOutbox,cmtChip,cmtClearWriteToken,cmtClosePop,cmtConnectHtml,cmtDb,cmtDispatch,cmtEl,cmtEntryNode,cmtEntryOf,cmtErrorText,cmtFind,cmtFlush,cmtFlushNote,cmtFlushNow,cmtFlushState,cmtFold,cmtForbidden,cmtFormHtml,cmtHead,cmtInNotes,cmtInflight,cmtItemHtml,cmtKick,cmtListHtml,cmtLoadPosted,cmtLogin,cmtMarkLabel,cmtName,cmtNewId,cmtNorm,cmtNotify,cmtOffHtml,cmtOnChange,cmtOpen,cmtOpenComposer,cmtOpenThread,cmtOpenTray,cmtOrder,cmtPaint,cmtPending,cmtPlace,cmtPop,cmtPost,cmtPullPosted,cmtQueue,cmtQuoteHtml,cmtRangeAt,cmtRefresh,cmtRegionFor,cmtRegionOf,cmtRender,cmtRepo,cmtRepoLabel,cmtResolveAnchor,cmtRetries,cmtReveal,cmtRows,cmtSchedule,cmtSend,cmtSentRead,cmtSentWrite,cmtSetName,cmtSetState,cmtSetWriteToken,cmtSite,cmtSize,cmtSlug,cmtState,cmtStuck,cmtSubs,cmtTextIndex,cmtThreadState,cmtTick,cmtTimer,cmtTotal,cmtTx,cmtUi,cmtUnhosted,cmtWireForm,cmtWriteToken,ghWrite,st
const CMT_WRITE_KEY="design-doc-github-write";
const CMT_DB="design-doc-comments",CMT_STORE="comments-outbox",CMT_IDLE=20000,CMT_RETRY_CAP=10*60*1000,CMT_BASE="main";
const CMT_B32="0123456789ABCDEFGHJKMNPQRSTVWXYZ";
const CMT_AUTO_MERGE="mutation($id:ID!){enablePullRequestAutoMerge(input:{pullRequestId:$id,mergeMethod:REBASE}){clientMutationId}}";
const cmtSubs=[];
let cmtSite=null,cmtDb=null,cmtTimer=0,cmtInflight=null,cmtAuthFailed=false,cmtRetries=0,cmtState={state:"idle"};
function cmtSlug(){
 return location.pathname.replace(/[^/]*$/,"").replace(/^\/|\/$/g,"");
}
function cmtRepo(){
 if(!cmtSite||!cmtSite.repo)return null;
 const [owner,repo]=cmtSite.repo.split("/");
 return {owner,repo};
}
function cmtWriteToken(){
 try{return localStorage.getItem(CMT_WRITE_KEY)||""}catch(e){return ""}
}
function cmtSetWriteToken(tok){
 localStorage.setItem(CMT_WRITE_KEY,tok.trim());
 cmtAuthFailed=false;
}
function cmtClearWriteToken(){
 localStorage.removeItem(CMT_WRITE_KEY);
 cmtAuthFailed=false;
}
function cmtNewId(){
 let t=Date.now(),s="";
 for(let i=0;i<10;i++){s=CMT_B32[t%32]+s;t=Math.floor(t/32)}
 crypto.getRandomValues(new Uint8Array(16)).forEach(b=>{s+=CMT_B32[b&31]});
 return s;
}
const cmtB64=s=>btoa(Array.from(new TextEncoder().encode(s),b=>String.fromCharCode(b)).join(""));
async function ghWrite(method,path,body){
 const r=await fetch(GH_API+path,{method,headers:{Accept:"application/vnd.github+json",Authorization:"Bearer "+cmtWriteToken(),"X-GitHub-Api-Version":"2022-11-28","Content-Type":"application/json"},body:body===undefined?undefined:JSON.stringify(body)});
 if(r.ok)return r.status===204?null:r.json();
 const e=new Error("GitHub "+r.status);
 e.status=r.status;
 e.rate=(r.status===403||r.status===429)&&r.headers.get("x-ratelimit-remaining")==="0";
 throw e;
}
function cmtOpen(){
 if(!cmtDb)cmtDb=new Promise((resolve,reject)=>{
  const q=indexedDB.open(CMT_DB,1);
  q.onupgradeneeded=()=>{q.result.createObjectStore(CMT_STORE,{keyPath:"id"})};
  q.onsuccess=()=>resolve(q.result);
  q.onerror=()=>reject(q.error);
 });
 return cmtDb;
}
async function cmtTx(mode,fn){
 const db=await cmtOpen();
 return new Promise((resolve,reject)=>{
  const tx=db.transaction(CMT_STORE,mode),req=fn(tx.objectStore(CMT_STORE));
  tx.oncomplete=()=>resolve(req&&req.result);
  tx.onerror=()=>reject(tx.error);
 });
}
const cmtRows=()=>cmtTx("readonly",st=>st.getAll());
async function cmtPending(){
 const slug=cmtSlug();
 return (await cmtRows()).filter(r=>r.slug===slug).map(r=>r.record);
}
function cmtOnChange(fn){cmtSubs.push(fn)}
function cmtFlushState(){return cmtState}
function cmtNotify(){cmtSubs.forEach(fn=>fn())}
function cmtSetState(s){cmtState=s;cmtNotify()}
function cmtKick(){
 clearTimeout(cmtTimer);
 cmtTimer=0;
 if(!cmtAuthFailed)cmtFlush();
}
async function cmtQueue(record){
 await cmtTx("readwrite",st=>st.add({id:record.id,slug:cmtSlug(),record}));
 clearTimeout(cmtTimer);
 cmtTimer=setTimeout(cmtKick,CMT_IDLE);
 cmtNotify();
}
const cmtAuthError=e=>e.status===401||(e.status===403&&!e.rate);
const cmtErrorText=e=>cmtAuthError(e)?"GitHub rejected your token: it is invalid or lacks permission to write comments to this repository.":e.rate?"GitHub rate limit reached; your comments stay queued and go out later.":e.status?`GitHub returned ${e.status}; your comments stay queued.`:"GitHub could not be reached; your comments stay queued.";
async function cmtSend(slug,recs){
 const {owner,repo}=cmtRepo(),base=`/repos/${owner}/${repo}`;
 const head=await ghWrite("GET",`${base}/git/ref/heads/${CMT_BASE}`);
 const branch=`comments/${slug}/${cmtNewId()}`;
 await ghWrite("POST",`${base}/git/refs`,{ref:"refs/heads/"+branch,sha:head.object.sha});
 for(const r of recs)await ghWrite("PUT",`${base}/contents/${slug}/comments/${r.id}.json`,{message:`comments: ${slug} ${r.id}`,content:cmtB64(JSON.stringify(r,null,2)+"\n"),branch});
 const pr=await ghWrite("POST",`${base}/pulls`,{title:`comments: ${slug} (${recs.length})`,head:branch,base:CMT_BASE,body:`${recs.length} reader comment${recs.length===1?"":"s"} on ${slug}, queued from the rendered doc.`});
 let autoMerge="";
 try{
  const g=await ghWrite("POST","/graphql",{query:CMT_AUTO_MERGE,variables:{id:pr.node_id}});
  if(g.errors)autoMerge=g.errors[0].message;
 }catch(e){autoMerge=e.message}
 await cmtTx("readwrite",st=>{recs.forEach(r=>st.delete(r.id))});
 return {prUrl:pr.html_url,...(autoMerge?{error:"Auto-merge was not enabled on the pull request; the repository enables it on its own."}:{})};
}
async function cmtFlushNow(){
 clearTimeout(cmtTimer);
 cmtTimer=0;
 const rows=await cmtRows();
 if(!rows.length)return null;
 if(!cmtRepo()){cmtSetState({state:"error",error:"Commenting is unavailable on this build."});return null}
 if(!cmtWriteToken()){cmtSetState({state:"error",error:"Add a GitHub token to send your queued comments."});return null}
 const bySlug=Object.create(null);
 rows.forEach(r=>{(bySlug[r.slug]=bySlug[r.slug]||[]).push(r.record)});
 cmtSetState({state:"flushing"});
 let out=null;
 try{
  for(const slug of Object.keys(bySlug))out=await cmtSend(slug,bySlug[slug]);
 }catch(e){
  cmtAuthFailed=cmtAuthError(e);
  if(!cmtAuthFailed)cmtTimer=setTimeout(cmtKick,Math.min(CMT_IDLE*2**cmtRetries++,CMT_RETRY_CAP));
  cmtSetState({state:"error",error:cmtErrorText(e)});
  return null;
 }
 cmtRetries=0;
 cmtSetState({state:"sent",...out});
 return {prUrl:out.prUrl};
}
function cmtFlush(){
 if(!cmtInflight)cmtInflight=cmtFlushNow().finally(()=>{cmtInflight=null});
 return cmtInflight;
}
async function cmtBootOutbox(){
 cmtSite=(await siteConfig()).comments;
 if(cmtWriteToken())await cmtFlush();
 else if((await cmtRows()).length)cmtNotify();
}
function cmtForbidden(text){
 const low=String(text).toLowerCase();
 return (cmtSite?cmtSite.forbiddenTerms:[]).filter(t=>low.includes(String(t).toLowerCase()));
}
document.addEventListener("visibilitychange",()=>{if(document.visibilityState==="hidden"&&cmtTimer)cmtKick()});
const CMT_LANES=6,CMT_CTX=32,CMT_STUCK_MS=30*60*1000,CMT_REPOLL=30*1000;
const CMT_NAME_KEY="design-doc-comment-name",CMT_ME_KEY="cmt:me",CMT_SENT_KEY="design-doc-comment-sent";
let CMT_POSTED=[],CMT_QUEUE=[],CMT_SENT=[],CMT_MARK=Object.create(null),CMT_VIEW=[];
let CMT_LIVE=false,CMT_LOAD_ERR="",CMT_ME="",CMT_FRAME=0,CMT_POLL=0,CMT_POSTED_AT=0;
let CMT_HL=null;
try{CMT_HL=new Highlight();CSS.highlights.set("cmt",CMT_HL)}catch(e){}
const cmtRepoLabel=()=>{const r=cmtRepo();return r?r.owner+"/"+r.repo:""};
const cmtName=()=>{try{return localStorage.getItem(CMT_NAME_KEY)||""}catch(e){return ""}};
const cmtSetName=n=>{try{localStorage.setItem(CMT_NAME_KEY,n)}catch(e){}};
const cmtSentRead=()=>{try{const v=JSON.parse(localStorage.getItem(CMT_SENT_KEY)||"[]");return Array.isArray(v)?v:[]}catch(e){return []}};
const cmtSentWrite=list=>{try{localStorage.setItem(CMT_SENT_KEY,JSON.stringify(list))}catch(e){}};
async function cmtLogin(){
 const tok=cmtWriteToken();
 if(!tok){CMT_ME="";return ""}
 if(CMT_ME)return CMT_ME;
 try{CMT_ME=sessionStorage.getItem(CMT_ME_KEY)||""}catch(e){}
 if(CMT_ME)return CMT_ME;
 const r=await fetch(GH_API+"/user",{headers:{Accept:"application/vnd.github+json",Authorization:"Bearer "+tok,"X-GitHub-Api-Version":"2022-11-28"}});
 CMT_ME=r.ok?(await r.json()).login:"";
 try{sessionStorage.setItem(CMT_ME_KEY,CMT_ME)}catch(e){}
 return CMT_ME;
}
async function cmtBlob(f){
 const key="cmtblob:"+f.sha;
 let raw="";
 try{raw=sessionStorage.getItem(key)||""}catch(e){}
 if(!raw){
  const r=await fetch(f.download_url);
  if(!r.ok)throw new Error("comment "+f.name+" would not load ("+r.status+")");
  raw=await r.text();
  try{sessionStorage.setItem(key,raw)}catch(e){}
 }
 return JSON.parse(raw);
}
async function cmtLoadPosted(){
 const repo=cmtRepo();
 if(!repo)return [];
 const dir=cmtSlug().split("/").map(encodeURIComponent).join("/")+"/comments";
 const list=await ghGet(`/repos/${repo.owner}/${repo.repo}/contents/${dir}?ref=main`);
 if(!Array.isArray(list))return [];
 const files=list.filter(f=>f.type==="file"&&f.name.endsWith(".json"));
 const out=[];
 let next=0;
 const lane=async()=>{while(next<files.length)out.push(await cmtBlob(files[next++]))};
 await Promise.all(Array.from({length:CMT_LANES},lane));
 return out;
}
function cmtPullPosted(){
 CMT_POSTED_AT=Date.now();
 return cmtLoadPosted().then(rs=>{CMT_POSTED=rs;CMT_LOAD_ERR=""}).catch(e=>{if(e.reset)ghPauseUntil=e.reset;CMT_LOAD_ERR=String(e&&e.message||e)});
}
const cmtTick=()=>(Date.now()-CMT_POSTED_AT<CMT_REPOLL?Promise.resolve():cmtPullPosted()).then(cmtRefresh);
const cmtEl=n=>n&&(n.nodeType===1?n:n.parentElement);
function cmtEntryNode(n){
 for(let e=cmtEl(n);e;e=e.parentElement){
  const m=e.id&&CMT_ENTRY_RE.exec(e.id);
  if(m&&REF[m[1]])return {id:m[1],node:e};
 }
 return null;
}
const cmtEntryOf=n=>{const h=cmtEntryNode(n);return h?h.id:""};
const cmtInNotes=a=>a.kind==="quote"&&a.scope==="notes";
const cmtUnhosted=a=>cmtInNotes(a)&&!cmtNotesHost();
function cmtRegionOf(n){
 const e=cmtEl(n);
 if(!e)return null;
 const entry=cmtEntryNode(e);
 if(entry)return {scope:"entry",entry:entry.id,root:entry.node};
 const summary=$("#summaryHost");
 if(summary&&summary.contains(e))return {scope:"summary",root:summary};
 const notes=cmtNotesHost();
 if(notes&&notes.contains(e))return {scope:"notes",root:notes};
 return null;
}
function cmtRegionFor(a,root){
 const host=root||document;
 if(a.scope==="entry")return a.entry?aiEl(a.entry):null;
 if(a.scope==="summary")return host.querySelector("#summaryHost");
 if(a.scope==="notes")return cmtNotesHost();
 return null;
}
function cmtAnchorFromSelection(){
 const sel=getSelection();
 if(!sel||!sel.rangeCount)return null;
 const r=sel.getRangeAt(0),exact=r.toString(),entry=cmtEntryOf(r.commonAncestorContainer);
 if(!exact.trim())return entry?{kind:"entry",id:entry}:null;
 const region=cmtRegionOf(r.commonAncestorContainer);
 if(!region)return null;
 const root=region.root;
 const pre=document.createRange();
 pre.setStart(root,0);
 pre.setEnd(r.startContainer,r.startOffset);
 const post=document.createRange();
 post.setStart(r.endContainer,r.endOffset);
 post.setEnd(root,root.childNodes.length);
 const a={kind:"quote",scope:region.scope,prefix:pre.toString().slice(-CMT_CTX),exact,suffix:post.toString().slice(0,CMT_CTX)};
 if(region.entry)a.entry=region.entry;
 return a;
}
function cmtTextIndex(root){
 const walk=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);
 const nodes=[],starts=[];
 let text="";
 for(let n;(n=walk.nextNode());){nodes.push(n);starts.push(text.length);text+=n.data}
 return {nodes,starts,text};
}
function cmtNorm(text){
 let out="",last=" ";
 const map=[];
 for(let i=0;i<text.length;i++){
  const c=/\s/.test(text[i])?" ":text[i];
  if(c===" "&&last===" ")continue;
  out+=c;map.push(i);last=c;
 }
 return {out,map};
}
function cmtRangeAt(ix,start,end){
 const r=document.createRange();
 const at=(off,which)=>{
  let i=ix.starts.length-1;
  while(i>0&&ix.starts[i]>off)i--;
  r[which](ix.nodes[i],off-ix.starts[i]);
 };
 at(start,"setStart");
 at(end,"setEnd");
 return r;
}
function cmtFind(scope,a){
 const ix=cmtTextIndex(scope);
 if(!ix.nodes.length)return null;
 const want=a.prefix+a.exact+a.suffix;
 const raw=ix.text.indexOf(want);
 if(raw>=0)return cmtRangeAt(ix,raw+a.prefix.length,raw+a.prefix.length+a.exact.length);
 const hay=cmtNorm(ix.text),w=cmtNorm(want);
 const at=hay.out.indexOf(w.out);
 if(at<0)return null;
 const from=a.prefix.length,to=from+a.exact.length;
 let i=0,j=w.out.length-1;
 while(i<w.out.length&&w.map[i]<from)i++;
 while(j>=0&&w.map[j]>=to)j--;
 if(i>j)return null;
 return cmtRangeAt(ix,hay.map[at+i],hay.map[at+j]+1);
}
function cmtResolveAnchor(a,root){
 if(!a)return null;
 if(a.kind==="entry")return aiEl(a.id);
 const region=cmtRegionFor(a,root);
 return region?cmtFind(region,a):null;
}
const cmtOrder=(a,b)=>a.createdAt===b.createdAt?(a.id<b.id?-1:1):(a.createdAt<b.createdAt?-1:1);
function cmtFold(records){
 const sorted=[...records].sort(cmtOrder);
 const by=Object.create(null),idx=Object.create(null),next=Object.create(null),resolved=Object.create(null),nodes=Object.create(null),roots=[],edits=new Set();
 sorted.forEach((r,i)=>{by[r.id]=r;idx[r.id]=i});
 sorted.forEach((r,i)=>{if(r.supersedes&&idx[r.supersedes]<i){next[r.supersedes]=r.id;edits.add(r.id)}});
 sorted.forEach(r=>{if(r.resolves)resolved[r.resolves]=true});
 const latest=id=>{let r=by[id];while(next[r.id])r=by[next[r.id]];return r};
 sorted.forEach(r=>{
  if(edits.has(r.id)||(r.resolves&&!r.body))return;
  const h=latest(r.id);
  nodes[r.id]={id:r.id,rev:h.rev,author:h.author||{login:""},createdAt:r.createdAt,body:h.body||"",anchor:r.anchor,edited:h.id!==r.id,gone:!String(h.body||"").trim(),resolved:!!resolved[r.id],replies:[]};
 });
 sorted.forEach(r=>{
  const n=nodes[r.id];
  if(!n)return;
  const p=r.parent&&nodes[r.parent];
  if(p)p.replies.push(n);else roots.push(n);
 });
 const prune=n=>{n.replies=n.replies.filter(prune);return !n.gone||!!n.replies.length};
 return roots.filter(prune);
}
const cmtSize=n=>1+n.replies.reduce((s,r)=>s+cmtSize(r),0);
const cmtTotal=()=>CMT_VIEW.reduce((s,t)=>s+cmtSize(t),0);
const cmtStuck=m=>!!m&&m.kind==="sent"&&Date.now()-m.at>CMT_STUCK_MS;
function cmtThreadState(t){
 if(!t.mark)return t.resolved?"resolved":"";
 return cmtFlushState().state==="error"||cmtStuck(t.mark)?"error":"pending";
}
function cmtChip(t){
 const b=el("button","cmt-chip",`<i class="cdot" aria-hidden="true"></i>${cmtSize(t)}`);
 b.type="button";
 b.dataset.state=cmtThreadState(t);
 b.setAttribute("aria-label",cmtSize(t)+" comment(s) here");
 b.onclick=e=>{e.stopPropagation();cmtOpenThread(t,b.getBoundingClientRect())};
 return b;
}
function cmtRender(){
 const layer=$("#cmtLayer");
 layer.textContent="";
 const mark=n=>{
  n.mark=CMT_MARK[n.id]||null;
  const deep=n.replies.map(mark).filter(Boolean);
  return n.mark||deep[0]||null;
 };
 CMT_VIEW=cmtFold(CMT_POSTED.concat(CMT_QUEUE,CMT_SENT.map(s=>s.rec)));
 CMT_VIEW.forEach(t=>{t.mark=mark(t);t.at=cmtResolveAnchor(t.anchor,document.body);t.lost=!t.at&&!cmtUnhosted(t.anchor)});
 layer.hidden=innerWidth<1100||!CMT_VIEW.length;
 if(!layer.hidden)CMT_VIEW.forEach(t=>{if(t.at&&!cmtInNotes(t.anchor)){t.chip=cmtChip(t);layer.append(t.chip)}});
 const btn=$("#cmtBtn");
 if(btn){
  const detached=CMT_VIEW.filter(t=>t.lost).length;
  btn.textContent="Comments"+(CMT_VIEW.length?" · "+cmtTotal():"");
  btn.classList.toggle("on",CMT_VIEW.some(t=>t.mark));
  btn.title=detached?detached+" thread(s) no longer match the text they were left on":"";
 }
 cmtPlace();
 cmtPaint();
}
function cmtPlace(){
 const main=$("#main"),layer=$("#cmtLayer");
 if(!main||layer.hidden)return;
 const box=main.getBoundingClientRect();
 let last=-1e4;
 CMT_VIEW.filter(t=>t.chip).map(t=>({t,r:t.at.getBoundingClientRect()}))
  .sort((a,b)=>a.r.top-b.r.top)
  .forEach(({t,r})=>{
   const top=Math.max(r.top,last+26);
   last=top;
   const w=t.chip.offsetWidth||58;
   t.chip.style.left=Math.min(box.right-w-4,innerWidth-w-10)+"px";
   t.chip.style.top=top+"px";
   t.chip.hidden=top<48||top>innerHeight-24;
  });
}
function cmtPaint(){
 if(!CMT_HL)return;
 CMT_HL.clear();
 CMT_VIEW.forEach(t=>{if(t.at&&t.anchor.kind==="quote")CMT_HL.add(t.at)});
}
function cmtSchedule(){
 if(CMT_FRAME)return;
 CMT_FRAME=requestAnimationFrame(()=>{CMT_FRAME=0;cmtDecorate();cmtRender()});
}
async function cmtRefresh(){
 CMT_QUEUE=await cmtPending();
 const posted=new Set(CMT_POSTED.map(r=>r.id)),queued=new Set(CMT_QUEUE.map(r=>r.id));
 CMT_SENT=cmtSentRead().filter(s=>!posted.has(s.rec.id)&&!queued.has(s.rec.id));
 cmtSentWrite(cmtSentRead().filter(s=>!posted.has(s.rec.id)));
 CMT_MARK=Object.create(null);
 CMT_QUEUE.forEach(r=>{CMT_MARK[r.id]={kind:"queued",at:Date.now(),prUrl:""}});
 CMT_SENT.forEach(s=>{CMT_MARK[s.rec.id]={kind:"sent",at:s.at,prUrl:s.prUrl||""}});
 cmtDecorate();
 cmtRender();
 const live=CMT_QUEUE.length||CMT_SENT.length;
 if(live&&!CMT_POLL)CMT_POLL=setInterval(cmtTick,4000);
 if(!live&&CMT_POLL){clearInterval(CMT_POLL);CMT_POLL=0}
}
function cmtOffHtml(){
 return `<p class="cmt-note">Commenting is unavailable on this build: no comments repository is configured for it, which is what a locally served copy looks like. Nothing you write here could be sent, so the page does not offer a box to write it in.</p>`;
}
function cmtFlushNote(mark){
 const st=cmtFlushState();
 if(st.state==="error")return `<p class="cmt-note bad">Your comments have not been sent: ${esc(st.error||"the pull request failed to open")}. They are still queued in this browser; fix the token below and they go out on the next try.</p>`;
 if(st.state==="flushing")return `<p class="cmt-note">Sending to ${esc(cmtRepoLabel())}&hellip;</p>`;
 if(cmtStuck(mark))return `<p class="cmt-note bad">This was sent ${esc(relTime(new Date(mark.at).toISOString()))} and has still not landed on the document${mark.prUrl?`; <a href="${esc(mark.prUrl)}" target="_blank" rel="noopener">its pull request</a> has not merged`:""}. Its checks may have rejected it. It stays here until it merges.</p>`;
 if(mark&&mark.kind==="sent")return `<p class="cmt-note good">Sent${mark.prUrl?` as <a href="${esc(mark.prUrl)}" target="_blank" rel="noopener">a pull request</a>`:""}, and it appears on the document once that merges.</p>`;
 if(mark&&!cmtWriteToken())return `<p class="cmt-note">Queued in this browser and not sent: comments need a GitHub token. Connect one and they go out.</p>`;
 return "";
}
function cmtMarkLabel(m){
 if(!m)return "";
 if(cmtStuck(m))return "sent, not merged";
 return m.kind==="sent"?"sent, waiting to merge":"not sent yet";
}
function cmtItemHtml(n,reply){
 const when=relTime(n.createdAt),label=cmtMarkLabel(n.mark);
 return `<div class="cmt-item${reply?" reply":""}">`+
  `<div class="cmt-by"><b>${esc(n.author.login||"someone")}</b>${when?`<span>${esc(when)}</span>`:""}`+
  (n.edited?`<span>edited</span>`:"")+(label?`<span>${esc(label)}</span>`:"")+(n.resolved?`<span>resolved</span>`:"")+`</div>`+
  `<p class="cmt-body${n.gone?" gone":""}">${n.gone?"This comment was withdrawn.":esc(n.body)}</p>`+
  `</div>`+n.replies.map(r=>cmtItemHtml(r,true)).join("");
}
function cmtQuoteHtml(a){
 if(a.kind!=="quote")return "";
 return `<p class="cmt-quote">${esc(a.exact.trim().slice(0,240))}</p>`;
}
function cmtFormHtml(label){
 return `<div class="cmt-form" data-form>`+
  (CMT_ME?"":`<input type="text" data-who value="${esc(cmtName())}" placeholder="Your name, so people know who asked" autocomplete="name">`)+
  `<textarea data-body rows="3" placeholder="${esc(label)}"></textarea>`+
  `<p class="cmt-note" data-forbid hidden></p>`+
  `<div class="cmt-row"><button class="cmt-send" type="button" data-post>Post</button>`+
  `<span class="end">${CMT_ME?"as "+esc(CMT_ME):esc(cmtRepoLabel())}</span></div></div>`;
}
function cmtWireForm(pop,anchor,parent){
 const form=pop.querySelector("[data-form]");
 if(!form)return;
 const box=form.querySelector("[data-body]"),who=form.querySelector("[data-who]"),send=form.querySelector("[data-post]"),warn=form.querySelector("[data-forbid]");
 const grade=()=>{
  const hits=box.value.trim()?cmtForbidden(box.value):[];
  warn.hidden=!hits.length;
  warn.textContent=hits.length?"This comment names "+hits.join(", ")+", and every comment lands in a file in this repository. Post it anyway if you meant to.":"";
  send.textContent=hits.length?"Post anyway":"Post";
  send.disabled=!box.value.trim()||(!!who&&!who.value.trim());
 };
 box.addEventListener("input",grade);
 if(who)who.addEventListener("input",grade);
 box.addEventListener("keydown",e=>{if((e.metaKey||e.ctrlKey)&&e.key==="Enter")send.click()});
 send.onclick=async()=>{
  if(who)cmtSetName(who.value.trim());
  send.disabled=true;
  await cmtPost(anchor,box.value.trim(),parent,"");
  cmtClosePop();
 };
 grade();
 setTimeout(()=>box.focus(),0);
}
function cmtPop(html,rect){
 cmtClosePop();
 const pop=el("div","cmt-pop",html);
 pop.id="cmtPop";
 pop.setAttribute("role","dialog");
 document.body.append(pop);
 const w=pop.offsetWidth,h=pop.offsetHeight;
 pop.style.left=Math.min(Math.max(10,rect.left-w+rect.width),innerWidth-w-10)+"px";
 pop.style.top=(rect.bottom+h+12>innerHeight?Math.max(10,rect.top-h-8):rect.bottom+8)+"px";
 return pop;
}
function cmtClosePop(){
 const pop=$("#cmtPop");
 if(pop)pop.remove();
}
async function cmtReveal(t){
 if(!t.at&&cmtUnhosted(t.anchor)){await openNotes();t.at=cmtResolveAnchor(t.anchor,document.body)}
 if(!t.at)return;
 const host=t.at.nodeType===1?t.at:cmtEl(t.at.commonAncestorContainer);
 for(let e=host;e;e=e.parentElement)if(e.tagName==="DETAILS")e.open=true;
 const r=t.at.getBoundingClientRect(),notes=cmtNotesHost();
 if(notes&&notes.contains(host))notes.scrollTop+=r.top-notes.getBoundingClientRect().top-80;
 else scrollTo({top:scrollY+r.top-180,behavior:reduced()?"auto":"smooth"});
}
const cmtHead=a=>a.kind==="entry"?handleOf(a.id)||a.id:a.entry?handleOf(a.entry)||a.entry:a.scope==="summary"?"In the summary":"In the working notes";
function cmtOpenThread(t,rect){
 const where=t.lost?`<p class="cmt-note">The text this was left on is no longer in the document, so it hangs here instead.</p>`:"";
 const pop=cmtPop(`<div class="cmt-head"><b>${esc(cmtHead(t.anchor))}</b>`+
  `<button class="xbtn" type="button" data-close>Close</button></div>`+
  where+cmtFlushNote(t.mark)+cmtQuoteHtml(t.anchor)+cmtItemHtml(t,false)+
  (CMT_LIVE?`<div class="cmt-acts"><button class="xbtn" type="button" data-resolve${t.resolved?" disabled":""}>${t.resolved?"Resolved":"Mark resolved"}</button></div>`+cmtFormHtml("Reply"):cmtOffHtml()),rect);
 cmtWireForm(pop,t.anchor,t.id);
 pop.querySelector("[data-close]").onclick=cmtClosePop;
 const res=pop.querySelector("[data-resolve]");
 if(res)res.onclick=async()=>{res.disabled=true;await cmtPost(t.anchor,"",t.id,t.id);cmtClosePop()};
}
function cmtOpenComposer(anchor,rect){
 if(!anchor)return;
 const pop=cmtPop(`<div class="cmt-head"><b>${esc(cmtHead(anchor))}</b><button class="xbtn" type="button" data-close>Close</button></div>`+
  cmtQuoteHtml(anchor)+(CMT_LIVE?cmtFlushNote(null)+cmtFormHtml("What would you like the authors to know?"):cmtOffHtml()),rect);
 cmtWireForm(pop,anchor,"");
 pop.querySelector("[data-close]").onclick=cmtClosePop;
}
function cmtConnectHtml(){
 const tok=cmtWriteToken();
 return `<h4>${tok?"Connected":"Connect to comment"}</h4>`+
  `<p>Comments are written to <code>${esc(cmtRepoLabel())}</code> as a pull request that merges once its checks pass, so commenting needs a fine-grained personal access token scoped to that repository with:</p>`+
  `<ul class="cmt-perm"><li>Contents &mdash; read and write</li><li>Pull requests &mdash; read and write</li></ul>`+
  `<p>Create one at <a href="https://github.com/settings/personal-access-tokens/new" target="_blank" rel="noopener">github.com/settings/personal-access-tokens/new</a>. On an organisation repository an admin may have to approve the token before it starts working.</p>`+
  (tok?`<div class="cmt-row"><button class="xbtn" type="button" data-disconnect>Disconnect</button><span class="end">${esc(CMT_ME||"signed in")}</span></div>`
   :`<div class="cmt-form"><input type="password" data-tok placeholder="github_pat_&hellip;" autocomplete="off" spellcheck="false">`+
    `<div class="cmt-row"><button class="cmt-send" type="button" data-savetok>Save token</button><span class="end">Kept in this browser only.</span></div></div>`);
}
function cmtListHtml(threads,empty){
 if(!threads.length)return `<p>${esc(empty)}</p>`;
 return `<div class="cmt-list">`+threads.map(t=>{
  const label=cmtMarkLabel(t.mark);
  return `<button class="cmt-open" type="button" data-thread="${esc(t.id)}"><span>${esc(cmtHead(t.anchor))}</span>`+
   `<small>${cmtSize(t)}${label?" · "+esc(label):t.resolved?" · resolved":""}</small></button>`;
 }).join("")+`</div>`;
}
function cmtOpenTray(rect){
 const attached=CMT_VIEW.filter(t=>!t.lost),detached=CMT_VIEW.filter(t=>t.lost);
 const stuck=CMT_VIEW.filter(t=>cmtStuck(t.mark));
 const pop=cmtPop(`<div class="cmt-head"><b>Comments on this document</b><button class="xbtn" type="button" data-close>Close</button></div>`+
  (CMT_LOAD_ERR?`<p class="cmt-note bad">The posted comments would not load: ${esc(CMT_LOAD_ERR)}</p>`:"")+
  (CMT_LIVE?cmtFlushNote(stuck.length?stuck[0].mark:CMT_VIEW.map(t=>t.mark).find(Boolean)):cmtOffHtml())+
  `<h4>On the page</h4>`+cmtListHtml(attached,CMT_LIVE?"No comments yet. Select any passage of the summary or of a register entry, or use the mark beside an entry.":"No comments on this document.")+
  (detached.length?`<h4>Detached</h4><p>The text these were left on has changed, so they no longer sit anywhere in the document.</p>`+cmtListHtml(detached,""):"")+
  (CMT_LIVE?cmtConnectHtml():""),rect);
 pop.querySelector("[data-close]").onclick=cmtClosePop;
 pop.querySelectorAll("[data-thread]").forEach(b=>{
  b.onclick=async()=>{
   const t=CMT_VIEW.find(x=>x.id===b.dataset.thread);
   await cmtReveal(t);
   setTimeout(()=>cmtOpenThread(t,(t.chip||t.at||b).getBoundingClientRect()),reduced()?0:400);
  };
 });
 const save=pop.querySelector("[data-savetok]");
 if(save)save.onclick=async()=>{
  const tok=pop.querySelector("[data-tok]").value.trim();
  if(!tok)return;
  cmtSetWriteToken(tok);
  CMT_ME="";
  try{sessionStorage.removeItem(CMT_ME_KEY)}catch(e){}
  await cmtLogin();
  cmtClosePop();
  cmtDispatch();
 };
 const off=pop.querySelector("[data-disconnect]");
 if(off)off.onclick=()=>{
  cmtClearWriteToken();
  CMT_ME="";
  try{sessionStorage.removeItem(CMT_ME_KEY)}catch(e){}
  cmtClosePop();
  cmtRefresh();
 };
}
function cmtDispatch(){
 cmtFlush().then(r=>{
  if(!r||!r.prUrl)return;
  cmtSentWrite(cmtSentRead().map(s=>s.prUrl?s:{...s,prUrl:r.prUrl}));
 }).catch(()=>{}).then(cmtRefresh);
}
async function cmtPost(anchor,body,parent,resolves){
 const login=(await cmtLogin())||cmtName();
 const rec={id:cmtNewId(),rev:META.rev||1,author:{login},createdAt:new Date().toISOString(),anchor,body};
 if(parent)rec.parent=parent;
 if(resolves)rec.resolves=resolves;
 await cmtQueue(rec);
 cmtSentWrite(cmtSentRead().concat([{rec,at:Date.now(),prUrl:""}]));
 await cmtRefresh();
 cmtDispatch();
}
function cmtUi(){
 const layer=el("div");
 layer.id="cmtLayer";
 layer.hidden=true;
 document.body.append(layer);
 const row=el("div","toolrow");
 const btn=el("button","toolbtn","Comments");
 btn.id="cmtBtn";
 btn.type="button";
 btn.onclick=()=>cmtOpenTray(btn.getBoundingClientRect());
 row.append(btn);
 $("#rail .tools").append(row);
 document.addEventListener("pointerdown",e=>{
  if(!e.target.closest(".cmt-pop,.cmt-chip,#cmtBtn,.cmt-ask"))cmtClosePop();
 });
 document.addEventListener("keydown",e=>{if(e.key==="Escape")cmtClosePop()});
 if(!CMT_LIVE)return;
 const sel=el("button","cmt-sel","Comment");
 sel.type="button";
 sel.hidden=true;
 document.body.append(sel);
 sel.onclick=()=>{
  const a=cmtAnchorFromSelection(),r=sel.getBoundingClientRect();
  sel.hidden=true;
  cmtOpenComposer(a,r);
 };
 document.addEventListener("pointerup",e=>{
  if(e.target.closest(".cmt-sel,.cmt-pop"))return;
  setTimeout(()=>{
   const s=getSelection(),text=String(s).trim();
   if(text.length<8||!s.rangeCount||!cmtRegionOf(s.getRangeAt(0).commonAncestorContainer)){sel.hidden=true;return}
   const r=s.getRangeAt(0).getBoundingClientRect();
   sel.style.left=Math.min(Math.max(8,r.left+(HTML.dataset.ai?126:0)),innerWidth-120)+"px";
   sel.style.top=(r.top>60?r.top-38:r.bottom+10)+"px";
   sel.hidden=false;
  },10);
 });
 document.addEventListener("pointerdown",e=>{if(!e.target.closest(".cmt-sel"))sel.hidden=true});
 document.addEventListener("click",e=>{
  const b=e.target.closest&&e.target.closest("[data-cmt]");
  if(!b)return;
  e.preventDefault();
  cmtOpenComposer({kind:"entry",id:b.dataset.cmt},b.getBoundingClientRect());
 });
}
function cmtBoot(){
 siteConfig().then(cfg=>{
  if(cfg.github&&!GH_TOKEN)GH_TOKEN=cfg.github.token;
  cmtSite=cfg.comments;
  CMT_LIVE=!!cmtRepo();
  cmtUi();
  if(!CMT_LIVE){cmtRender();return}
  cmtOnChange(()=>cmtRefresh());
  addEventListener("scroll",()=>cmtPlace(),{passive:true});
  addEventListener("resize",cmtSchedule);
  new MutationObserver(cmtSchedule).observe($("#main"),{childList:true,subtree:true,attributes:true,attributeFilter:["open"]});
  new MutationObserver(cmtSchedule).observe($("#mback"),{attributes:true,attributeFilter:["class"]});
  cmtBootOutbox().then(cmtRefresh);
  cmtPullPosted().then(cmtRefresh);
 });
}
