// @requires AI,AI_TOOLS,META,SLUG,aiCtxText,aiPrompt,aiReady,$,aiDiagRender,aiTally,aiUsage
// @defines AI_CTX_BUDGET,AI_EFFORT,AI_EFFORT_LADDER,AI_LAST_TURN,AI_NO_TOOLS,AI_RATE_KEY,AI_RESULT_CAP,AI_RETRY_MSG,AI_ROLES,AI_ROUNDS,AI_RPM,AI_TOOL_BUDGET,AI_VENDOR_KEYS,aiBody,aiBusyNow,aiCacheGet,aiCacheKey,aiCached,aiClean,aiHash,aiNoteUsage,aiOnce,aiPost,aiRecent,aiRetryAfter,aiRetryNote,aiSlot,aiStoreKey,aiStream,aiWait,lowerEffort
const AI_ROUNDS=10;
const AI_RESULT_CAP=12000;
const AI_TOOL_BUDGET=40000;
const AI_CTX_BUDGET=100000;
const AI_EFFORT={chat:"high",quiz:"high",readas:"high",followups:"low"};
const AI_EFFORT_LADDER=["high","medium","low"];
const lowerEffort=e=>{const i=AI_EFFORT_LADDER.indexOf(e);return i<0?null:AI_EFFORT_LADDER[i+1]||null};
const AI_RETRY_MSG="Thinking again, with less reasoning…";
const AI_VENDOR_KEYS={reasoning_format:"noFormat",prompt_cache_key:"noCacheKey"};
const AI_RPM=30;
const AI_RATE_KEY="design-doc-ai-rate";
const AI_ROLES=[["PM","a product manager who owns the roadmap, not the code"],["SRE","an SRE who will carry the pager for this"],["Security","a security reviewer looking for what an attacker gets"],["Exec","an executive who funds it and reads three sentences"]];
function aiWait(ms,signal){
 return new Promise((resolve,reject)=>{
  const stop=()=>reject(new DOMException("aborted","AbortError"));
  if(signal&&signal.aborted)return stop();
  const onAbort=()=>{clearTimeout(timer);stop()};
  const timer=setTimeout(()=>{if(signal)signal.removeEventListener("abort",onAbort);resolve()},Math.max(0,ms));
  if(signal)signal.addEventListener("abort",onAbort,{once:true});
 });
}
function aiRecent(){
 const now=Date.now();
 let saved=[];
 try{saved=JSON.parse(localStorage.getItem(AI_RATE_KEY)||"[]")}catch(e){}
 return (Array.isArray(saved)?saved:[]).filter(t=>typeof t==="number"&&now-t<60000).sort((a,b)=>a-b);
}
async function aiSlot(signal){
 for(;;){
  const recent=aiRecent();
  if(recent.length<AI_RPM){
   recent.push(Date.now());
   try{localStorage.setItem(AI_RATE_KEY,JSON.stringify(recent.slice(-AI_RPM)))}catch(e){}
   return;
  }
  await aiWait(60000-(Date.now()-recent[0])+50,signal);
 }
}
function aiRetryAfter(header){
 if(!header)return null;
 const raw=String(header).trim();
 if(/^\d+(\.\d+)?$/.test(raw))return parseFloat(raw)*1000;
 const at=Date.parse(raw);
 return isNaN(at)?null:Math.max(0,at-Date.now());
}
async function aiPost(body,signal){
 let stripped=false;
 for(let attempt=0;;attempt++){
  await aiSlot(signal);
  const r=await fetch(AI.endpoint+"/chat/completions",{method:"POST",signal,headers:{"content-type":"application/json",authorization:"Bearer "+AI.key},body:JSON.stringify(body)});
  if(r.status===429&&attempt<3){
   const wait=aiRetryAfter(r.headers.get("retry-after"));
   await aiWait(wait==null?2000:wait,signal);
   continue;
  }
  if(!r.ok){
   const detail=await r.text().catch(()=>"");
   const blamed=Object.keys(AI_VENDOR_KEYS).filter(k=>k in body&&detail.includes(k));
   if(r.status<500&&blamed.length&&!stripped){
    stripped=true;
    blamed.forEach(k=>{delete body[k];AI[AI_VENDOR_KEYS[k]]=true});
    continue;
   }
   throw new Error("the model call came back "+r.status+(detail?": "+detail.slice(0,180):""));
  }
  return r;
 }
}
const aiClean=s=>AI.noFormat?s.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u200b-\u200f\u2060\ufeff]/g,""):s;
function aiNoteUsage(u){
 const pd=u.prompt_tokens_details||u.prompt_token_details||{};
 const cd=u.completion_tokens_details||u.completion_token_details||{};
 aiUsage={prompt:u.prompt_tokens||0,cached:pd.cached_tokens||0,completion:u.completion_tokens||0,reasoning:cd.reasoning_tokens||0};
 aiPrompt=aiUsage.prompt;
 const key="ai:"+SLUG+":tally";
 const t={calls:0,prompt:0,cached:0,completion:0,reasoning:0};
 try{Object.assign(t,JSON.parse(sessionStorage.getItem(key)||"{}"))}catch(e){}
 t.calls++;
 Object.keys(aiUsage).forEach(k=>{t[k]+=aiUsage[k]});
 try{sessionStorage.setItem(key,JSON.stringify(t))}catch(e){}
 aiTally=t;
 aiDiagRender();
}
async function aiStream(body,signal,onText,onReason){
 const r=await aiPost(Object.assign({},body,{stream:true,stream_options:{include_usage:true}}),signal);
 const reader=r.body.getReader(),dec=new TextDecoder();
 let buf="",text="",reasoning="",finish=null;
 const calls=[];
 for(;;){
  const {value,done}=await reader.read();
  if(done)break;
  buf+=dec.decode(value,{stream:true});
  let nl;
  while((nl=buf.indexOf("\n"))>=0){
   const line=buf.slice(0,nl).trim();buf=buf.slice(nl+1);
   if(!line.startsWith("data:"))continue;
   const data=line.slice(5).trim();
   if(!data||data==="[DONE]")continue;
   let j;try{j=JSON.parse(data)}catch(e){continue}
   const choice=(j.choices||[])[0];
   if(j.usage)aiNoteUsage(j.usage);
   if(!choice)continue;
   if(choice.finish_reason)finish=choice.finish_reason;
   const d=choice.delta||{};
   if(d.reasoning){reasoning+=d.reasoning;if(onReason)onReason(reasoning)}
   if(d.content){text+=d.content;if(onText)onText(aiClean(text))}
   (d.tool_calls||[]).forEach(tc=>{
    const ix=tc.index||0;
    const c=calls[ix]||(calls[ix]={id:"",name:"",args:""});
    if(tc.id)c.id=tc.id;
    if(tc.function&&tc.function.name)c.name+=tc.function.name;
    if(tc.function&&tc.function.arguments)c.args+=tc.function.arguments;
   });
  }
 }
 return {text:aiClean(text),calls:calls.filter(Boolean),reasoning,finish};
}
const aiHash=s=>{let h=2166136261;for(let i=0;i<s.length;i++){h^=s.charCodeAt(i);h=Math.imul(h,16777619)}return (h>>>0).toString(36)};
const aiCacheKey=()=>SLUG+":"+(META.rev||0)+":"+aiHash(aiCtxText||"").padStart(8,"0");
function aiBody(kind,schema){
 const body={model:AI.model,tools:AI_TOOLS,parallel_tool_calls:true,reasoning_effort:AI.reasoning||AI_EFFORT[kind]||"medium",max_completion_tokens:kind==="chat"||schema?16000:6000,temperature:schema?0:0.2};
 if(!AI.noFormat)body.reasoning_format="parsed";
 if(!AI.noCacheKey)body.prompt_cache_key=aiCacheKey();
 return body;
}
const AI_NO_TOOLS="Tools are off for this task: write the text only.";
const AI_LAST_TURN="[Every tool turn is used. Write the answer now from what you have, in full sentences with citations; no more tool calls.]";
let aiBusyNow=null;
function aiRetryNote(){if(aiBusyNow)aiBusyNow.querySelector(".ai-wait").textContent="Thinking again…"}
async function aiOnce(compose,opt){
 const o=opt||{};
 await aiReady;
 const messages=compose();
 let effort=null;
 for(;;){
  const body=aiBody(o.kind,o.schema);
  if(effort)body.reasoning_effort=effort;
  body.messages=messages.map((m,i)=>i===messages.length-1?{role:m.role,content:m.content+"\n\n"+AI_NO_TOOLS}:m);
  body.tool_choice="none";
  if(o.schema)body.response_format={type:"json_schema",json_schema:{name:o.schema.name,strict:true,schema:o.schema.schema}};
  let text,finish;
  if(o.onText&&!o.schema){
   const res=await aiStream(body,o.signal,o.onText);
   text=res.text;finish=res.finish;
  }else{
   const r=await aiPost(body,o.signal);
   const j=await r.json();
   if(j.usage)aiNoteUsage(j.usage);
   const choice=(j.choices||[])[0]||{};
   text=aiClean((choice.message||{}).content||"");
   finish=choice.finish_reason||null;
  }
  const lower=lowerEffort(body.reasoning_effort);
  if(finish==="length"&&!text&&!effort&&lower){effort=lower;aiRetryNote();continue}
  return o.schema?JSON.parse(text||"{}"):text;
 }
}
const aiStoreKey=(feature,input)=>"ai:"+SLUG+":"+(META.rev||0)+":"+feature+":"+aiHash(input);
function aiCacheGet(feature,input){
 try{const hit=localStorage.getItem(aiStoreKey(feature,input));return hit?JSON.parse(hit):null}catch(e){return null}
}
async function aiCached(feature,input,make){
 const hit=aiCacheGet(feature,input);
 if(hit)return hit;
 const out=await make();
 try{localStorage.setItem(aiStoreKey(feature,input),JSON.stringify(out))}catch(e){}
 return out;
}
