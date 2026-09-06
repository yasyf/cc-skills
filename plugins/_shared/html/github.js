// @requires GH_NOTE_HOST,META,ghRecount,$,GH,GH_PENDING,GH_REF,GH_TOKEN,REPO,el,esc,srcUrl
// @defines GH_401_KEY,GH_API,GH_FILE,GH_FILE_PENDING,GH_LANES,GH_TTL,ghApply,ghBoot,ghBranch,ghCached,ghFile,ghFileKey,ghFileLoad,ghGet,ghNote,ghPauseUntil,ghResolve,ghStore
const GH_API="https://api.github.com",GH_TTL=10*60*1000,GH_LANES=4,GH_401_KEY="gh:401";
let ghPauseUntil=0;
function ghCached(key){
 try{
  const c=JSON.parse(sessionStorage.getItem("gh:"+key)||"null");
  return c&&Date.now()-c.t<GH_TTL?c.v:null;
 }catch(e){return null}
}
function ghStore(key,v){try{sessionStorage.setItem("gh:"+key,JSON.stringify({t:Date.now(),v}))}catch(e){}}
const GH_FILE=Object.create(null),GH_FILE_PENDING=Object.create(null);
const ghFileKey=path=>path+"@"+GH_REF;
async function ghFile(path){
 const key="file:"+ghFileKey(path);
 const hit=ghCached(key);
 if(hit)return hit;
 const r=await fetch(`${GH_API}/repos/${REPO}/contents/${path.split("/").map(encodeURIComponent).join("/")}?ref=${encodeURIComponent(GH_REF)}`,{headers:{Accept:"application/vnd.github.raw+json",Authorization:"Bearer "+GH_TOKEN,"X-GitHub-Api-Version":"2022-11-28"}});
 const rec=r.ok?{text:await r.text()}:{status:r.status};
 ghStore(key,rec);
 return rec;
}
function ghFileLoad(path){
 const key=ghFileKey(path);
 if(!GH_FILE_PENDING[key])GH_FILE_PENDING[key]=ghFile(path).then(rec=>{GH_FILE[key]=rec;return rec}).finally(()=>{delete GH_FILE_PENDING[key]});
 return GH_FILE_PENDING[key];
}
async function ghBranch(){
 if(typeof META.ref==="string")return;
 const key="ref:"+REPO;
 let ref=ghCached(key);
 if(!ref){
  const repo=await ghGet(`/repos/${REPO}`);
  if(!repo)return;
  ref=repo.default_branch;
  ghStore(key,ref);
 }
 GH_REF=ref;
 document.querySelectorAll("a[data-ghfile]").forEach(a=>{a.href=srcUrl(a.dataset.ghfile,a.dataset.line,a.dataset.to)});
}
async function ghGet(path){
 if(ghPauseUntil>Date.now())await new Promise(r=>setTimeout(r,ghPauseUntil-Date.now()));
 const r=await fetch(GH_API+path,{headers:{Accept:"application/vnd.github+json",Authorization:"Bearer "+GH_TOKEN,"X-GitHub-Api-Version":"2022-11-28"}});
 if(r.status===404)return null;
 if(r.ok)return r.json();
 const e=new Error("GitHub "+r.status);
 e.status=r.status;
 if((r.status===403||r.status===429)&&r.headers.get("x-ratelimit-remaining")==="0")e.reset=(+r.headers.get("x-ratelimit-reset")||Math.ceil(Date.now()/1000)+60)*1000;
 throw e;
}
async function ghResolve(key,kind){
 const m=/^([^/]+)\/([^#]+)#(\d+)$/.exec(key),base=`/repos/${m[1]}/${m[2]}`,n=m[3];
 if(kind==="issue"){
  const is=await ghGet(`${base}/issues/${n}`);
  if(!is)return {state:"unknown"};
  return {kind,state:is.state,title:is.title,author:is.user&&is.user.login,updated:is.updated_at,closedAt:is.closed_at};
 }
 const pr=await ghGet(`${base}/pulls/${n}`);
 if(!pr)return {state:"unknown"};
 const rec={kind,state:pr.merged_at?"merged":pr.draft?"draft":pr.state,title:pr.title,author:pr.user&&pr.user.login,updated:pr.updated_at,mergedAt:pr.merged_at,closedAt:pr.closed_at};
 const [reviews,status]=await Promise.all([ghGet(`${base}/pulls/${n}/reviews?per_page=100`),ghGet(`${base}/commits/${pr.head.sha}/status`).catch(e=>{if(e.status===403)return null;throw e})]);
 if(reviews){
  const latest={};
  reviews.forEach(v=>{if(v.state!=="COMMENTED"&&v.user)latest[v.user.login]=v.state});
  const count=s=>Object.values(latest).filter(x=>x===s).length;
  const changes=count("CHANGES_REQUESTED"),approved=count("APPROVED");
  rec.review=changes?changes+" requested changes":approved?approved+" approved":"";
 }
 if(!status)rec.checks={state:"unknown"};
 else if(status.statuses.length)rec.checks={ok:status.statuses.filter(c=>c.state==="success").length,total:status.statuses.length,state:status.state};
 return rec;
}
function ghNote(text){
 const h=$(GH_NOTE_HOST+" h2");
 if(h&&!$(GH_NOTE_HOST+" .ghnote"))h.after(el("p","ghnote",esc(text)));
}
function ghApply(key){
 document.querySelectorAll(`[data-gh="${CSS.escape(key)}"] .ldot`).forEach(d=>{d.dataset.state=GH[key].state});
 ghRecount();
}
async function ghBoot(token){
 GH_TOKEN=token;
 if(REPO&&document.querySelector("[data-ghfile]"))ghBranch().catch(()=>{});
 const kinds={};
 document.querySelectorAll("[data-gh]").forEach(n=>{kinds[n.dataset.gh]=n.dataset.kind});
 const todo=[];
 Object.keys(kinds).forEach(k=>{
  const c=ghCached(k);
  if(c){GH[k]=c;ghApply(k)}else todo.push(k);
 });
 if(!todo.length||sessionStorage.getItem(GH_401_KEY))return;
 let stopped=false;
 const lane=async()=>{
  while(todo.length&&!stopped){
   const k=todo.shift();
   try{
    GH_PENDING[k]=ghResolve(k,kinds[k]);
    GH[k]=await GH_PENDING[k];
    ghStore(k,GH[k]);
    ghApply(k);
   }catch(e){
    if(e.status===401){
     stopped=true;
     try{sessionStorage.setItem(GH_401_KEY,"1")}catch(x){}
     ghNote("GitHub rejected this site's token, so the state of each linked change is off for this session.");
    }else if(e.reset){ghPauseUntil=e.reset;todo.unshift(k)}
    else{GH[k]={state:"unknown"};ghApply(k)}
   }finally{delete GH_PENDING[k]}
  }
 };
 await Promise.all(Array.from({length:GH_LANES},lane));
}
