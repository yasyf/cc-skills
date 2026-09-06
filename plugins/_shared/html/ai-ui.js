// @requires AI,AI_CMDS,AI_SECTION_NAMES,AI_SUGGEST,DIFF,HTML,META,REF,SLUG,TERMS,TLDR,aiAbort,aiBusy,aiCtxStats,aiEntry,aiEntryText,aiExec,aiHandle,aiKind,aiMark,aiNoteSources,aiReady,aiRenderPath,aiSec,aiSpokenCalls,aiSystem,aiTail,clearAiMarks,firstSentence,handleOf,plain,refChip,unveil,$,AI_LAST_TURN,AI_RESULT_CAP,AI_RETRY_MSG,AI_ROLES,AI_ROUNDS,AI_TOOL_BUDGET,aiBody,aiBusyNow,aiCached,aiOnce,aiStream,el,esc,inline,lowerEffort,mdToHtml,palClose,palOpen,scrub
// @defines AI_CARDS,AI_FAQ_SCHEMA,AI_FOLLOW_SCHEMA,AI_GLOSS_SCHEMA,AI_PATH_SCHEMA,AI_QUIZ_SCHEMA,AI_VERB,aiAboutCtx,aiAboutPrefix,aiAddDefs,aiAdopt,aiAsk,aiAskBtn,aiBarText,aiBubble,aiBusyNode,aiCard,aiChangedSince,aiChat,aiChatKey,aiClear,aiCmdSub,aiCmds,aiCollapse,aiCommit,aiDiagRender,aiDiagText,aiExplain,aiFail,aiFaq,aiFeature,aiFirstLine,aiFollow,aiFollowups,aiGlossary,aiGrow,aiHistory,aiLastAnswer,aiLoadChat,aiMenuAt,aiMoreMenu,aiNode,aiOpen,aiPaintAnswer,aiPickRole,aiPlaceMenu,aiPrevFocus,aiQuiz,aiReadAs,aiRenderCard,aiRenderChanged,aiRenderChips,aiRenderFaq,aiRenderGlossary,aiRenderQuiz,aiRenderReadAs,aiRenderSection,aiReviewComment,aiRowMenu,aiRun,aiSaveChat,aiScrollLog,aiSetAbout,aiSetFollow,aiSetSources,aiSetState,aiSetVerbs,aiShowChanged,aiShowFaq,aiShowGlossary,aiShowQuiz,aiShowReadAs,aiShowSection,aiSlashHide,aiSlashIx,aiSlashList,aiSlashRender,aiSlashRun,aiStartRun,aiState,aiStateKey,aiStick,aiStop,aiSugFor,aiSuggest,aiSummariseSection,aiTally,aiToggle,aiTyping,aiUsage,aiVerbs,aiWhyHere,aiWireDiffPanel,aiWireKeys,aiWirePanel,aiWireRows,aiWireSections,aiWireSelection,aiWireTools,palButton
let aiUsage=null,aiTally=null,aiFollow=null;
let aiState="bar",aiPrevFocus=null,aiStick=true,aiSugFor=null,aiAboutCtx=null,aiSlashList=[],aiSlashIx=0;
let aiChat=[];
async function aiRun(question,opt){
 const o=opt||{};
 await aiReady;
 const msgs=[{role:"system",content:aiSystem()}];
 aiHistory().forEach(m=>msgs.push(m));
 msgs.push({role:"user",content:aiTail(o)+"\n\n"+question});
 const sources=new Set(),used=[];
 let answer="",finish=null,spent=0,effort=o.effort||null,retried=false,lastTurn=false;
 for(let round=0;round<AI_ROUNDS;round++){
  const prefix=answer?answer+"\n\n":"";
  const body=aiBody("chat");
  body.messages=msgs;
  if(o.tools)body.tools=o.tools;
  if(effort)body.reasoning_effort=effort;
  if(round===AI_ROUNDS-1&&!lastTurn){lastTurn=true;msgs.push({role:"user",content:AI_LAST_TURN})}
  if(lastTurn)body.tool_choice="none";
  const res=await aiStream(body,o.signal,t=>{if(o.onText)o.onText(prefix+aiSpokenCalls(t,true).text)},o.onReason);
  finish=res.finish;
  const lower=lowerEffort(body.reasoning_effort);
  if(finish==="length"&&!res.text&&!res.calls.length&&!retried&&lower){
   retried=true;effort=lower;
   if(o.onRetry)o.onRetry(effort);
   round--;continue;
  }
  const spoken=aiSpokenCalls(res.text);
  const calls=res.calls.map(c=>({id:c.id,name:c.name.replace(/^functions\./,""),args:c.args||"{}"}))
   .concat(spoken.calls.map((c,i)=>({id:"spoken_"+round+"_"+i,name:c.name,args:JSON.stringify(c.args)})));
  if(spoken.text)answer=prefix+spoken.text;
  if(!calls.length)break;
  const results=await Promise.all(calls.map(async c=>{
   let args={};
   try{args=JSON.parse(c.args)}catch(e){return {c,out:"the arguments did not parse as JSON"}}
   used.push({name:c.name,args});
   if(o.onTool)o.onTool(c.name,args);
   aiNoteSources(c.name,args,sources);
   try{return {c,out:await aiExec(c.name,args,sources)}}
   catch(e){return {c,out:"that tool failed: "+e.message}}
  }));
  if(!res.calls.length&&spoken.text)break;
  msgs.push({role:"assistant",content:(res.reasoning?res.reasoning.slice(0,2000)+"\n\n":"")+spoken.text,tool_calls:calls.map(c=>({id:c.id,type:"function",function:{name:c.name,arguments:c.args}}))});
  results.forEach(r=>{
   const room=Math.min(AI_RESULT_CAP,AI_TOOL_BUDGET-spent);
   let out=String(r.out);
   if(room<=0)out="the tool budget for this turn is spent; answer from what you already have";
   else if(out.length>room)out=out.slice(0,room)+"\n[truncated]";
   spent+=out.length;
   msgs.push({role:"tool",tool_call_id:r.c.id,content:out});
  });
 }
 return {answer,finish,sources:[...sources],used,retried};
}

function aiChatKey(){return "ai:"+SLUG+":chat"}
function aiStateKey(){return "ai:"+SLUG+":state"}
function aiHistory(){return aiChat.filter(m=>m.role==="user"||m.role==="assistant").map(m=>({role:m.role,content:m.content}))}
function aiSaveChat(){try{sessionStorage.setItem(aiChatKey(),JSON.stringify(aiChat.slice(-12)))}catch(e){}}
function aiLoadChat(){
 try{const raw=sessionStorage.getItem(aiChatKey());if(raw)aiChat=JSON.parse(raw)||[]}catch(e){aiChat=[]}
 aiChat.forEach(m=>{
  if(m.role==="card"){aiNode(aiRenderCard(m));return}
  const box=aiBubble(m.role,m.shown||m.content,m.sources,m.about);
  if(m.verbs&&m.verbs.length)aiSetVerbs(box,m.verbs);
 });
 aiBarText(aiFirstLine(aiLastAnswer()));
 aiScrollLog(true);
}
function aiLastAnswer(){
 for(let i=aiChat.length-1;i>=0;i--)if(aiChat[i].role==="assistant")return aiChat[i].content||"";
 return "";
}
function aiFirstLine(text){
 const s=firstSentence(text||"");
 return s.length>90?s.slice(0,90)+"…":s;
}
function aiBarText(text){$("#aiBarLast").textContent=text||""}
function aiScrollLog(force){
 const log=$("#aiLog");
 if(force)aiStick=true;
 if(aiStick)log.scrollTop=log.scrollHeight;
}
function aiAboutPrefix(about){
 return (about.kind==="entry"?"About "+about.id+" “"+about.text+"”":"About this passage: “"+about.text+"”")+"\n\n";
}
function aiBubble(role,text,sources,about){
 const box=el("div","ai-msg ai-"+role);
 if(role==="user"){
  if(about)box.append(el("span","ai-about-in",esc((about.kind==="entry"?"About "+about.id+": ":"About: ")+about.text)));
  box.append(document.createTextNode(text));
 }else{
  const body=el("div","ai-body");
  if(text){body.innerHTML=mdToHtml(text);scrub(body)}
  else body.append(el("span","ai-wait","Thinking…"));
  box.append(body);
  if(sources&&sources.length)aiSetSources(box,sources);
 }
 return aiNode(box);
}
const AI_VERB={
 scroll_to:a=>"scrolled to "+a.id,
 highlight:a=>"highlighted "+a.id,
 open_entry:a=>"opened "+a.id,
 filter:a=>"filtered "+a.section,
 show_diagram:a=>a.node?"showed "+a.node+" on the diagram":"showed the diagram",
 read_entry:a=>"read "+a.id,
 read_section:a=>"read "+(AI_SECTION_NAMES[a.section]||a.section),
 list_entries:a=>"listed "+a.register,
 select_text:()=>"selected a passage",
 set_reading_path:()=>"pinned a reading path",
 search_document:a=>"searched “"+a.query+"”",
 read_round:a=>"read round "+a.round,
 read_notes:()=>"read the notes",
 read_links:a=>"read the links"+(a.id?" on "+a.id:"")
};
function aiVerbs(calls){return calls.map(c=>{const f=AI_VERB[c.name];return f?f(c.args||{}):c.name.replace(/_/g," ")})}
function aiSetVerbs(box,verbs){
 let line=box.querySelector(".ai-verbs");
 if(!verbs.length){if(line)line.remove();return}
 if(!line){line=el("p","ai-verbs");box.querySelector(".ai-body").after(line)}
 const open=line.classList.contains("open");
 line.textContent=(open?verbs:verbs.slice(0,3)).join(" · ");
 if(verbs.length>3&&!open){
  const more=el("button",null,esc(" · +"+(verbs.length-3)+" more"));
  more.type="button";
  more.onclick=()=>{line.classList.add("open");aiSetVerbs(box,verbs)};
  line.append(more);
 }
}
function aiSetSources(box,sources){
 let row=box.querySelector(".ai-src");
 const ids=[...new Set(sources)].filter(id=>REF[id]);
 if(!ids.length){if(row)row.remove();return}
 if(!row){row=el("div","ai-src");box.append(row)}
 const open=row.classList.contains("open");
 row.innerHTML="<span>Sources:</span>"+(open?ids:ids.slice(0,3)).map(refChip).join("");
 if(ids.length>3&&!open){
  const more=el("button",null,esc("+"+(ids.length-3)));
  more.type="button";
  more.onclick=()=>{row.classList.add("open");aiSetSources(box,sources)};
  row.append(more);
 }
}
function aiNode(node){$("#aiLog").append(node);aiScrollLog();return node}
function aiSetState(s){
 const was=aiState;
 aiState=s;
 $("#ai").dataset.state=s;
 document.body.classList.toggle("ai-wide",s==="wide");
 $("#aiBar").setAttribute("aria-expanded",String(s!=="bar"));
 const wide=$("#aiWide");
 wide.innerHTML=s==="wide"?"&#8676;":"&#8677;";
 wide.title=s==="wide"?"Back to the corner":"Dock to the side";
 wide.setAttribute("aria-label",wide.title);
 if(s==="bar"){
  document.querySelectorAll(".ai-menu").forEach(m=>m.remove());
  aiSlashHide();
  if(was!=="bar"){
   const back=aiPrevFocus&&aiPrevFocus.isConnected&&!$("#ai").contains(aiPrevFocus)?aiPrevFocus:$("#aiBar");
   back.focus();
  }
  aiPrevFocus=null;
  return;
 }
 try{localStorage.setItem(aiStateKey(),s)}catch(e){}
 if(was==="bar"){
  aiPrevFocus=document.activeElement;
  aiSuggest();
  $("#aiIn").focus();
  aiScrollLog(true);
 }
}
function aiOpen(seed,about){
 if(about)aiSetAbout(about);
 if(aiState==="bar"){
  let want="dock";
  try{want=localStorage.getItem(aiStateKey())||"dock"}catch(e){}
  aiSetState(want==="wide"?"wide":"dock");
 }
 const box=$("#aiIn");
 if(seed!=null){box.value=seed;aiGrow(box);aiSlashRender()}
 box.focus();
}
function aiCollapse(){aiSetState("bar")}
function aiToggle(){if(aiState==="bar")aiOpen();else aiCollapse()}
function aiGrow(box){box.style.height="auto";box.style.height=Math.min(120,box.scrollHeight)+"px"}
function aiRenderChips(list){
 const host=$("#aiSug");host.innerHTML="";
 list.forEach(([label,run,ids])=>{
  const b=el("button","ai-chip",esc(label));
  b.type="button";
  b.onclick=run;
  if(ids){b.dataset.ids=ids.join(" ");b.title="Draws on "+ids.map(handleOf).join(", ")}
  host.append(b);
 });
}
function aiSuggest(){
 if(aiChat.length||aiBusy){
  aiSugFor=null;
  aiRenderChips((aiFollow||[]).map(f=>[f.q,()=>aiAsk(f.q),f.ids]));
  return;
 }
 if(aiSugFor===aiSec)return;
 aiSugFor=aiSec;
 const meta=(META.ai&&META.ai.suggest)||{};
 const list=[];
 if(AI_SECTION_NAMES[aiSec])list.push(["TL;DR "+AI_SECTION_NAMES[aiSec],()=>aiShowSection(aiSec)]);
 [...(meta[aiSec]||[]),...(AI_SUGGEST[aiSec]||[])].slice(0,2).forEach(q=>list.push([q,()=>aiAsk(q)]));
 aiRenderChips(list);
}
function aiSetFollow(items){aiFollow=items;aiSugFor=null;aiSuggest()}
function aiSetAbout(ctx){
 aiAboutCtx=ctx||null;
 const host=$("#aiAbout");
 host.innerHTML="";
 host.classList.remove("open");
 if(!ctx){host.hidden=true;return}
 const t=el("button","ai-about-t",esc("“"+ctx.text+"”"));
 t.type="button";
 t.title="Show the whole passage";
 t.onclick=()=>host.classList.toggle("open");
 const x=el("button","ai-x","×");
 x.type="button";
 x.setAttribute("aria-label","Drop this context");
 x.onclick=()=>{aiSetAbout(null);$("#aiIn").focus()};
 host.append(el("span","ai-about-k",esc(ctx.kind==="entry"?"About "+ctx.id+":":"About:")),t,x);
 host.hidden=false;
}
function aiStop(){if(aiAbort)aiAbort.abort()}
function aiAsk(question,opt){
 const q=String(question||"").trim();
 if(!q||aiBusy)return;
 const o=opt||{};
 const about=aiAboutCtx;
 aiSetAbout(null);
 aiOpen();
 aiAdopt(aiStartRun(q,{about,effort:o.effort,commit:true}));
}
function aiStartRun(q,o){
 const run={q,about:o.about||null,ctrl:new AbortController(),text:"",calls:[],sources:[],finish:null,retrying:false,retried:false,done:false,error:null,commit:!!o.commit,committed:false,listeners:new Set()};
 const emit=()=>run.listeners.forEach(fn=>fn(run));
 clearAiMarks();
 aiBarText("Thinking…");
 run.promise=aiRun(run.about?aiAboutPrefix(run.about)+q:q,{
  effort:o.effort,
  signal:run.ctrl.signal,
  tools:o.tools,
  onText:t=>{run.text=t;emit()},
  onTool:(name,args)=>{run.calls.push({name,args});emit()},
  onRetry:()=>{run.retrying=true;aiBarText(AI_RETRY_MSG);emit()}
 }).then(res=>{run.text=res.answer||"";run.sources=res.sources||[];run.finish=res.finish;run.retried=!!res.retried},e=>{run.error=e}).then(()=>{
  run.done=true;
  if(run.commit&&!run.error)aiCommit(run);
  aiBarText(aiFirstLine(aiLastAnswer()));
  emit();
 });
 return run;
}
function aiCommit(run){
 if(run.committed)return;
 run.committed=true;
 aiChat.push(
  {role:"user",content:run.about?aiAboutPrefix(run.about)+run.q:run.q,shown:run.q,about:run.about},
  {role:"assistant",content:run.text,sources:run.sources,verbs:aiVerbs(run.calls)}
 );
 aiSaveChat();
 aiFollowups(run.q,run.text).then(aiSetFollow,()=>{});
}
function aiPaintAnswer(box,run,final){
 const body=box.querySelector(".ai-body");
 if(run.text){body.innerHTML=mdToHtml(run.text);scrub(body)}
 else if(final)body.textContent=run.error?"Stopped.":run.retried?"The model ran out of room twice; ask a narrower question.":"No answer came back.";
 else if(run.retrying)body.textContent=AI_RETRY_MSG;
 if(final&&run.finish==="length"&&run.text)body.append(el("p","ai-note-in","(cut short — ask me to continue)"));
 aiSetVerbs(box,aiVerbs(run.calls));
 if(final)aiSetSources(box,run.sources);
 aiScrollLog();
}
function aiAdopt(run){
 if(aiBusy&&aiAbort!==run.ctrl)aiStop();
 aiBusy=true;
 aiAbort=run.ctrl;
 run.commit=true;
 aiSetFollow(null);
 $("#aiSend").textContent="Stop";
 aiBubble("user",run.q,null,run.about);
 const box=aiBubble("assistant","");
 aiScrollLog(true);
 let pending=0;
 const paint=()=>{pending=0;aiPaintAnswer(box,run,false)};
 const finish=r=>{
  if(pending)cancelAnimationFrame(pending);
  pending=0;
  if(r.error&&r.error.name!=="AbortError")aiFail(box.querySelector(".ai-body"),r.error);
  else{
   aiPaintAnswer(box,r,true);
   if(!r.error)aiCommit(r);
  }
  if(aiAbort===r.ctrl){aiBusy=false;aiAbort=null;$("#aiSend").textContent="Ask"}
  aiScrollLog();
 };
 run.listeners.add(r=>{if(r.done)finish(r);else if(!pending)pending=requestAnimationFrame(paint)});
 if(run.done)finish(run);
}

async function aiExplain(entry,kind,roleIx,onText){
 const [label,role]=AI_ROLES[roleIx];
 const input=entry.id+"|"+label+"|"+(META.rev||0);
 return aiCached("explain",input,()=>aiOnce(()=>[
  {role:"system",content:aiSystem()},
  {role:"user",content:"Reader: "+role+".\n\nEntry:\n"+aiEntryText(entry,kind)+"\n\nExplain what this entry means for that reader in three sentences, from the document only. No preamble."}
 ],{kind:"explain",onText}));
}
async function aiWhyHere(entry,kind){
 return aiCached("why",entry.id+"|"+(META.rev||0),()=>aiOnce(()=>[
  {role:"system",content:aiSystem()},
  {role:"user",content:"Entry:\n"+aiEntryText(entry,kind)+"\n\nSay why this entry exists, in exactly two sentences: what it holds up, and what falls without it."}
 ],{kind:"why"}));
}
async function aiReviewComment(entry,kind,concern){
 return aiOnce(()=>[
  {role:"system",content:aiSystem()},
  {role:"user",content:"Entry:\n"+aiEntryText(entry,kind)+"\n\nThe reviewer's concern, in their words: "+concern+"\n\nDraft a review comment on this entry: three labelled paragraphs, Context, Concern, Ask. Plain sentences, no hedging, under 120 words."}
 ],{kind:"review"});
}
async function aiSummariseSection(id){
 return aiCached("section",id+"|"+(META.rev||0),()=>aiOnce(()=>[
  {role:"system",content:aiSystem()},
  {role:"user",content:"Summarise the "+AI_SECTION_NAMES[id]+" section ("+id+") as four or five bullets, each one line, from that section only. Return markdown bullets and nothing else."}
 ],{kind:"section"}));
}
async function aiChangedSince(){
 if(!DIFF)return "";
 const marks=Object.entries(DIFF.marks);
 const detail=marks.slice(0,40).map(([k,v])=>{
  const id=k.split(":")[1],e=aiEntry(id);
  return k+" "+v+(e?" — "+plain(e.t):"");
 }).join("\n");
 const notes=(DIFF.notes||[]).map(n=>"revision "+n.rev+": "+plain(n.note||"")+" "+(n.items||[]).map(plain).join("; ")).join("\n");
 return aiCached("changed",(META.rev||0)+"|"+DIFF.base.rev+"|"+marks.length,()=>aiOnce(()=>[
  {role:"system",content:aiSystem()},
  {role:"user",content:"Since revision "+DIFF.base.rev+", now revision "+(META.rev||0)+".\n\nRevision notes:\n"+notes+"\n\nWhat the diff marks:\n"+detail+"\n\nTell a returning reader what changed since the revision they last read, in one paragraph of plain prose. No bullet list."}
 ],{kind:"changed"}));
}
const AI_FAQ_SCHEMA={name:"faq",schema:{type:"object",properties:{items:{type:"array",items:{type:"object",properties:{q:{type:"string"},a:{type:"string"}},required:["q","a"],additionalProperties:false}}},required:["items"],additionalProperties:false}};
async function aiFaq(){
 return aiCached("faq",String(META.rev||0),()=>aiOnce(()=>[
  {role:"system",content:aiSystem()},
  {role:"user",content:"Write the six questions a reader of this document will ask that the document never states outright, each with the answer the document gives. Each answer is at most 40 words and cites the entry it rests on."}
 ],{kind:"faq",schema:AI_FAQ_SCHEMA}));
}
const AI_GLOSS_SCHEMA={name:"glossary",schema:{type:"object",properties:{terms:{type:"array",items:{type:"object",properties:{k:{type:"string"},v:{type:"string"}},required:["k","v"],additionalProperties:false}}},required:["terms"],additionalProperties:false}};
async function aiGlossary(){
 const known=TERMS.map(t=>t.k).join(", ");
 return aiCached("glossary",String(META.rev||0),()=>aiOnce(()=>[
  {role:"system",content:aiSystem()},
  {role:"user",content:"Already defined: "+(known||"nothing")+"\n\nFind the terms this document uses without defining, and define each in at most 20 words from the document itself. At most eight terms, none of them already defined, none of them ordinary English."}
 ],{kind:"glossary",schema:AI_GLOSS_SCHEMA}));
}
const AI_PATH_SCHEMA={name:"reading_path",schema:{type:"object",properties:{title:{type:"string"},items:{type:"array",items:{type:"object",properties:{id:{type:"string"},why:{type:"string"}},required:["id","why"],additionalProperties:false}}},required:["title","items"],additionalProperties:false}};
async function aiReadAs(roleIx){
 const [label,role]=AI_ROLES[roleIx];
 return aiCached("readas",label+"|"+(META.rev||0),()=>aiOnce(()=>[
  {role:"system",content:aiSystem()},
  {role:"user",content:"Reader: "+role+".\n\nPick the six entries of this document that reader must read, in order, with one line each on why, and title the path for them. Use ids exactly as they appear."}
 ],{kind:"readas",schema:AI_PATH_SCHEMA}));
}
const AI_QUIZ_SCHEMA={name:"quiz",schema:{type:"object",properties:{items:{type:"array",items:{type:"object",properties:{q:{type:"string"},options:{type:"array",items:{type:"string"}},answer:{type:"integer"},id:{type:"string"}},required:["q","options","answer","id"],additionalProperties:false}}},required:["items"],additionalProperties:false}};
async function aiQuiz(){
 return aiCached("quiz",String(META.rev||0),()=>aiOnce(()=>[
  {role:"system",content:aiSystem()},
  {role:"user",content:"Write five multiple-choice questions that check whether a reviewer understood this document. Four options each, answer is the index of the right one, id is the register entry the answer comes from. Questions about what was decided and what it costs, never about wording."}
 ],{kind:"quiz",schema:AI_QUIZ_SCHEMA}));
}
const AI_FOLLOW_SCHEMA={name:"followups",schema:{type:"object",properties:{items:{type:"array",items:{type:"object",properties:{q:{type:"string"},ids:{type:"array",items:{type:"string"}}},required:["q","ids"],additionalProperties:false}}},required:["items"],additionalProperties:false}};
async function aiFollowups(question,answer){
 const index=Object.keys(REF).map(id=>id+" — "+handleOf(id)+": "+plain(REF[id].title)).join("\n");
 const out=await aiCached("followups2",question+"|"+answer,()=>aiOnce(()=>[
  {role:"system",content:"You propose up to three questions a reader of a design document would ask next, after the answer they just read. Each is one short sentence the reader could type as it stands, about the document, never about you, and never the question just asked. Each question must be one the listed entries can answer: put in ids the entries it draws on, by their ids exactly as listed, and leave out any question that none of them answers."},
  {role:"user",content:"The document in brief:\n"+TLDR.map(t=>"- "+plain(t.p||t.md)).join("\n")+"\n\nSections: "+Object.values(AI_SECTION_NAMES).join(", ")+"\n\nEntries, as id — handle: title\n"+index+"\n\nQuestion: "+question+"\n\nAnswer: "+answer}
 ],{kind:"followups",schema:AI_FOLLOW_SCHEMA}));
 return (out.items||[]).filter(f=>f&&typeof f.q==="string"&&f.q.trim()&&Array.isArray(f.ids)&&f.ids.length&&f.ids.every(id=>REF[id])).slice(0,3);
}
function aiDiagText(){
 const L=[];
 if(aiCtxStats){
  const s=aiCtxStats;
  L.push("context "+s.total+" tokens · document "+s.doc+" · handles "+s.handles+" · notes "+s.notes+" · log "+s.log+(s.terms?" · terms "+s.terms:"")+(s.dropped.length?" · dropped: "+s.dropped.join(", "):""));
 }
 if(aiUsage)L.push("last call · prompt "+aiUsage.prompt+" ("+aiUsage.cached+" cached) · completion "+aiUsage.completion+" · reasoning "+aiUsage.reasoning);
 if(aiTally)L.push("this tab · "+aiTally.calls+" calls · prompt "+aiTally.prompt+" ("+aiTally.cached+" cached) · completion "+aiTally.completion);
 return L.join("\n");
}
function aiDiagRender(){$("#aiDiag").textContent=aiDiagText()}

function aiBusyNode(label){
 const box=el("div","ai-msg ai-assistant");
 box.append(el("span","ai-wait",esc(label)));
 return aiNode(box);
}
function aiFail(box,e){box.innerHTML="";box.append(el("span","ai-err",esc("Sorry — "+e.message)))}
function aiCard(title,kind){
 const card=el("section","ai-card");
 card.dataset.kind=kind;
 const head=el("div","ai-card-head",`<b>${esc(title)}</b>`);
 const x=el("button","ai-x","×");
 x.type="button";
 x.setAttribute("aria-label","Remove this card");
 x.onclick=()=>{
  const i=aiChat.indexOf(card.aiRec);
  if(i>=0){aiChat.splice(i,1);aiSaveChat()}
  card.remove();
 };
 head.append(x);
 card.append(head,el("div","ai-card-body"));
 return card;
}
const AI_CARDS={faq:aiRenderFaq,quiz:aiRenderQuiz,terms:aiRenderGlossary,readas:aiRenderReadAs,changed:aiRenderChanged,tldr:aiRenderSection};
function aiRenderCard(rec){
 const card=aiCard(rec.title,rec.kind);
 card.aiRec=rec;
 AI_CARDS[rec.kind](card.querySelector(".ai-card-body"),rec.data);
 return card;
}
async function aiFeature(label,kind,title,fetch){
 if(aiBusy)return;
 aiOpen();
 aiBusy=true;
 $("#aiSend").disabled=true;
 aiSugFor=null;
 aiSuggest();
 const busy=aiBusyNode(label+"…");
 aiBusyNow=busy;
 try{
  const data=await fetch();
  const rec={role:"card",kind,title,data};
  busy.replaceWith(aiRenderCard(rec));
  aiChat.push(rec);
  aiSaveChat();
 }catch(e){aiFail(busy,e)}
 finally{
  if(aiBusyNow===busy)aiBusyNow=null;
  aiBusy=false;
  $("#aiSend").disabled=false;
  aiSugFor=null;
  aiSuggest();
 }
 aiScrollLog();
}
function aiShowFaq(){return aiFeature("Working out the questions people ask","faq","Questions people ask",aiFaq)}
function aiRenderFaq(body,out){
 (out.items||[]).forEach(it=>body.append(el("details",null,`<summary>${esc(it.q)}</summary><p>${inline(it.a)}</p>`)));
 scrub(body);
}
function aiShowChanged(){
 if(!DIFF){aiOpen();aiNode(el("div","ai-msg ai-assistant","Pick a revision to compare against first."));return}
 const base=DIFF.base.rev;
 return aiFeature("Reading the diff","changed","What changed since r"+base,async()=>({
  text:await aiChangedSince(),
  ids:Object.keys(DIFF.marks).map(k=>k.split(":")[1]).filter(id=>REF[id]).slice(0,40)
 }));
}
function aiRenderChanged(body,d){
 body.innerHTML=mdToHtml(d.text);
 if(d.ids.length)body.append(el("details","ai-changed",`<summary>${esc(d.ids.length+" changed entries")}</summary><p>${d.ids.map(refChip).join(" ")}</p>`));
 scrub(body);
}
function aiShowQuiz(){return aiFeature("Writing five questions","quiz","Check my understanding",aiQuiz)}
function aiRenderQuiz(body,out){
 (out.items||[]).forEach((q,qi)=>{
  const wrap=el("div","ai-q",`<p><b>${esc(qi+1+". ")}</b>${esc(q.q)}</p>`);
  (q.options||[]).forEach((opt,oi)=>{
   const b=el("button","ai-opt",esc(opt));
   b.type="button";
   b.onclick=()=>{
    wrap.querySelectorAll(".ai-opt").forEach(x=>{x.disabled=true});
    b.classList.add(oi===q.answer?"ok":"bad");
    const foot=el("p","ai-qa",oi===q.answer?"Right. ":"Not quite. ");
    if(REF[q.id]){
     const a=el("a","ref","Show me");
     a.href="#";a.onclick=ev=>{ev.preventDefault();openRef(q.id);aiMark(q.id)};
     foot.append(a);
    }
    wrap.append(foot);
   };
   wrap.append(b);
  });
  body.append(wrap);
 });
}
function aiPickRole(anchor){
 aiMenuAt(anchor,AI_ROLES.map(([label,desc],ix)=>({label:"Read as "+label,sub:desc,run:()=>aiShowReadAs(ix)})));
}
function aiShowReadAs(roleIx){
 const [label]=AI_ROLES[roleIx];
 return aiFeature("Picking what "+label+" should read","readas","Read as "+label,async()=>{
  const out=await aiReadAs(roleIx);
  const d={title:out.title||"",items:out.items||[],role:label};
  aiRenderPath(d.title||"Read as "+label,d.items);
  return d;
 });
}
function aiRenderReadAs(body,d){
 body.innerHTML=(d.title?`<p><b>${esc(d.title)}</b></p>`:"")+d.items.map(x=>`<p>${REF[x.id]?refChip(x.id):esc(x.id)} — ${esc(plain(x.why||""))}</p>`).join("");
 const pin=el("button","ai-btn","Pin in the sidebar");
 pin.type="button";
 pin.onclick=()=>{aiRenderPath(d.title||"Read as "+d.role,d.items);pin.textContent="Pinned in the sidebar"};
 body.append(pin);
 scrub(body);
}
function aiAddDefs(terms){
 window.ddTerms.add(terms);
 const host=$("#defsList");
 if(!host)return;
 terms.forEach(t=>{
  if(host.querySelector(`[data-ai-term="${CSS.escape(t.k)}"]`))return;
  const card=el("div","def ai-def",`<b>${esc(t.k)} <span class="pill p-acc ai-pill">AI-defined</span></b><span>${esc(t.v)}</span>`);
  card.dataset.aiTerm=t.k;
  host.append(card);
 });
}
function aiShowGlossary(){
 return aiFeature("Looking for terms the document never defines","terms","Terms the document never defines",async()=>{
  const out=await aiGlossary();
  return (out.terms||[]).filter(t=>t&&t.k&&t.v);
 });
}
function aiRenderGlossary(body,terms){
 aiAddDefs(terms);
 body.innerHTML=terms.map(t=>`<p><b>${esc(t.k)}</b> — ${esc(t.v)}</p>`).join("");
 const copy=el("button","ai-btn","Copy as JSON");
 copy.type="button";
 copy.onclick=()=>navigator.clipboard.writeText(JSON.stringify(terms,null,1)).then(()=>{copy.textContent="Copied"});
 const go=el("button","ai-btn","Show them in Terms");
 go.type="button";
 go.onclick=()=>{const g=document.getElementById("ground");if(g){unveil();g.scrollIntoView({block:"start"})}};
 body.append(copy,go);
}
function aiShowSection(id){
 const name=AI_SECTION_NAMES[id];
 if(!name)return;
 return aiFeature("Summarising "+name,"tldr","TL;DR: "+name,async()=>({id,md:await aiSummariseSection(id)}));
}
function aiRenderSection(body,d){
 body.innerHTML=mdToHtml(d.md);
 const sec=document.getElementById(d.id);
 if(sec){
  const show=el("button","ai-btn","Show under the heading");
  show.type="button";
  show.onclick=()=>{
   const box=sec.querySelector(".ai-sub")||el("div","sub ai-sub");
   box.innerHTML=mdToHtml(d.md);
   scrub(box);
   const sub=sec.querySelector("p.sub");
   (sub&&!sub.hidden?sub:sec.querySelector("h2")).after(box);
   unveil();
   box.scrollIntoView({block:"center"});
   show.textContent="Shown under the heading";
  };
  body.append(show);
 }
 scrub(body);
}
function aiClear(){
 aiChat=[];
 $("#aiLog").querySelectorAll(".ai-msg,.ai-card").forEach(n=>n.remove());
 clearAiMarks();
 aiSaveChat();
 $("#aiPath").hidden=true;
 aiSetAbout(null);
 aiBarText("");
 aiSetFollow(null);
}

function aiPlaceMenu(menu,anchor){
 document.body.append(menu);
 const r=anchor.getBoundingClientRect();
 menu.style.left=Math.min(Math.max(8,r.left-40),innerWidth-menu.offsetWidth-8)+"px";
 menu.style.top=(r.bottom+8+menu.offsetHeight>innerHeight?Math.max(8,r.top-menu.offsetHeight-8):r.bottom+8)+"px";
 const onScroll=e=>{
  if(menu.contains(e.target))return;
  menu.remove();
  removeEventListener("scroll",onScroll,true);
 };
 addEventListener("scroll",onScroll,true);
 const first=menu.querySelector("button");
 if(first)first.focus();
}
function aiMenuAt(anchor,items){
 document.querySelectorAll(".ai-menu").forEach(m=>m.remove());
 const menu=el("div","ai-menu ai-cmds");
 menu.setAttribute("role","menu");
 items.forEach(it=>{
  const b=el("button","ai-menu-item",`<span>${esc(it.label)}</span>`+(it.sub?`<small>${esc(it.sub)}</small>`:""));
  b.type="button";
  b.setAttribute("role","menuitem");
  b.onclick=()=>{menu.remove();it.run()};
  menu.append(b);
 });
 aiPlaceMenu(menu,anchor);
 return menu;
}
const aiCmds=()=>AI_CMDS.filter(c=>!c.when||c.when());
const aiCmdSub=c=>typeof c.sub==="function"?c.sub():c.sub||"";
function aiMoreMenu(){
 const anchor=$("#aiMore");
 aiMenuAt(anchor,aiCmds().map(c=>({label:c.label,sub:aiCmdSub(c),run:()=>c.run(anchor)})));
}
function aiSlashHide(){
 const host=$("#aiSlash");
 host.hidden=true;
 host.innerHTML="";
 aiSlashList=[];
}
function aiSlashRender(){
 const m=/^\/(\w*)$/.exec($("#aiIn").value);
 if(!m){aiSlashHide();return}
 const q=m[1].toLowerCase();
 aiSlashList=aiCmds().filter(c=>c.id.startsWith(q)||c.label.toLowerCase().includes(q));
 if(!aiSlashList.length){aiSlashHide();return}
 aiSlashIx=Math.min(aiSlashIx,aiSlashList.length-1);
 const host=$("#aiSlash");
 host.innerHTML="";
 aiSlashList.forEach((c,i)=>{
  const sub=aiCmdSub(c);
  const row=el("div","ai-slash-row"+(i===aiSlashIx?" on":""),`<span class="ai-slash-k">/${esc(c.id)}</span><span class="ai-slash-l">${esc(c.label)}</span>`+(sub?`<small>${esc(sub)}</small>`:""));
  row.setAttribute("role","option");
  row.setAttribute("aria-selected",String(i===aiSlashIx));
  row.onclick=()=>aiSlashRun(c);
  host.append(row);
 });
 host.hidden=false;
}
function aiSlashRun(c){
 if(!c)return;
 const box=$("#aiIn");
 box.value="";
 aiGrow(box);
 aiSlashHide();
 c.run(box);
}

function aiRowMenu(anchor,entry,kind){
 document.querySelectorAll(".ai-menu").forEach(m=>m.remove());
 const menu=el("div","ai-menu");
 const head=el("div","ai-menu-head",`<b>${esc(aiHandle(entry))}</b><span class="idchip">${esc(entry.id)}</span>`);
 menu.append(head);
 const out=el("div","ai-menu-out");
 const roles=el("div","ai-menu-row");
 roles.append(el("span","ai-menu-label","Explain for"));
 AI_ROLES.forEach(([label],ix)=>{
  const b=el("button","ai-btn",label);
  b.type="button";
  b.onclick=async()=>{
   roles.querySelectorAll(".ai-btn").forEach(x=>x.classList.remove("on"));
   b.classList.add("on");
   out.textContent="…";
   try{
    const text=await aiExplain(entry,kind,ix,t=>{out.innerHTML=mdToHtml(t);scrub(out)});
    out.innerHTML=mdToHtml(text);scrub(out);
   }catch(e){out.textContent="Sorry — "+e.message}
  };
  roles.append(b);
 });
 menu.append(roles);
 const acts=el("div","ai-menu-row");
 const why=el("button","ai-btn","Why is this here");
 why.type="button";
 why.onclick=async()=>{
  out.textContent="…";
  try{const text=await aiWhyHere(entry,kind);out.innerHTML=mdToHtml(text);scrub(out)}
  catch(e){out.textContent="Sorry — "+e.message}
 };
 const review=el("button","ai-btn","Draft a review comment");
 review.type="button";
 review.onclick=()=>{
  out.innerHTML="";
  const ta=el("textarea","ai-ta");ta.placeholder="What worries you about this?";ta.rows=2;
  const go=el("button","ai-btn","Draft it");
  go.type="button";
  go.onclick=async()=>{
   const concern=ta.value.trim();
   if(!concern)return;
   out.textContent="…";
   try{
    const text=await aiReviewComment(entry,kind,concern);
    out.innerHTML=mdToHtml(text);scrub(out);
    const copy=el("button","ai-btn","Copy");
    copy.type="button";
    copy.onclick=()=>navigator.clipboard.writeText(text).then(()=>{copy.textContent="Copied"});
    out.append(copy);
   }catch(e){out.textContent="Sorry — "+e.message}
  };
  out.append(ta,go);ta.focus();
 };
 const ask=el("button","ai-btn","Ask about this");
 ask.type="button";
 ask.onclick=()=>{menu.remove();aiOpen(null,{kind:"entry",id:entry.id,text:aiHandle(entry)})};
 acts.append(why,review,ask);
 menu.append(acts,out);
 aiPlaceMenu(menu,anchor);
}
function aiAskBtn(id){
 return `<button class="ai-ask ai-only" type="button" data-ask="${esc(id)}" aria-label="Ask about this" title="Ask about this">✦</button>`;
}
function aiWireRows(){
 document.addEventListener("click",e=>{
  const b=e.target.closest&&e.target.closest("[data-ask]");
  if(b){
   e.preventDefault();
   const entry=aiEntry(b.dataset.ask);
   if(entry)aiRowMenu(b,entry,aiKind(b.dataset.ask));
   return;
  }
  const menu=document.querySelector(".ai-menu");
  if(!menu)return;
  const path=e.composedPath?e.composedPath():[];
  if(path.includes(menu)||menu.contains(e.target)||(e.target.closest&&e.target.closest("#aiMore")))return;
  menu.remove();
 });
}
function aiWireSelection(){
 const chip=el("button","ai-sel ai-only","Ask about this");
 chip.type="button";
 chip.hidden=true;
 document.body.append(chip);
 chip.onclick=()=>{
  const text=String(getSelection()).trim().slice(0,600);
  chip.hidden=true;
  aiOpen(null,{kind:"passage",text});
 };
 document.addEventListener("pointerup",()=>{
  setTimeout(()=>{
   if(!HTML.dataset.ai)return;
   const sel=getSelection(),text=String(sel).trim();
   if(text.length<12||!sel.rangeCount||!$("#main").contains(sel.anchorNode)||$("#ai").contains(document.activeElement)){chip.hidden=true;return}
   const r=sel.getRangeAt(0).getBoundingClientRect();
   if(aiState!=="bar"){
    const p=$("#aiPanel").getBoundingClientRect();
    if(r.right>p.left&&r.left<p.right&&r.bottom>p.top&&r.top<p.bottom){chip.hidden=true;return}
   }
   chip.style.left=Math.min(Math.max(8,r.left),innerWidth-160)+"px";
   chip.style.top=(r.top>60?r.top-38:r.bottom+10)+"px";
   chip.hidden=false;
  },10);
 });
 document.addEventListener("pointerdown",e=>{if(!e.target.closest(".ai-sel"))chip.hidden=true});
}
function aiWireSections(){
 const secs=[...document.querySelectorAll("main section")].filter(s=>!s.hidden);
 const io=new IntersectionObserver(entries=>{
  entries.forEach(en=>{
   if(!en.isIntersecting)return;
   aiSec=en.target.id;
   $("#aiCtx").textContent=AI_SECTION_NAMES[aiSec]?"§ "+AI_SECTION_NAMES[aiSec]:"";
   if(aiState!=="bar")aiSuggest();
  });
 },{rootMargin:"-25% 0px -60% 0px"});
 secs.forEach(s=>io.observe(s));
}
function aiWireDiffPanel(){
 const panel=$("#diffPanel");
 const add=()=>{
  if(panel.hidden||!DIFF||panel.querySelector(".ai-changed-btn"))return;
  const b=el("button","xbtn ai-changed-btn ai-only","What changed, in prose");
  b.type="button";
  b.onclick=aiShowChanged;
  const head=panel.querySelector("div");
  if(head)head.append(b);
 };
 new MutationObserver(add).observe(panel,{childList:true,attributes:true,attributeFilter:["hidden"]});
 add();
}
function aiWireTools(){
 const rail=document.querySelector("#rail");
 const path=el("div","ai-path ai-only");
 path.id="aiPath";
 path.hidden=true;
 rail.insertBefore(path,rail.querySelector(".spacer"));
 const ask=el("button","toolbtn ai-only","Ask the doc");
 ask.type="button";
 ask.onclick=()=>aiOpen();
 $("#toolrow").prepend(ask);
}
function aiTyping(){return /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)||document.activeElement.isContentEditable}
function aiWireKeys(){
 document.addEventListener("keydown",e=>{
  const meta=e.metaKey||e.ctrlKey;
  if(meta&&e.key.toLowerCase()==="k"){e.preventDefault();$("#pal").hidden?palOpen(""):palClose();return}
  if(e.key==="Escape"&&!$("#pal").hidden){palClose();return}
  if(!HTML.dataset.ai)return;
  if(meta&&e.key.toLowerCase()==="j"){e.preventDefault();aiToggle();return}
  if(e.key==="?"&&!aiTyping()){e.preventDefault();aiOpen();return}
  if(e.key!=="Escape"||$("#mback").classList.contains("open"))return;
  if(!$("#aiSlash").hidden){aiSlashHide();return}
  const menu=document.querySelector(".ai-menu");
  if(menu){menu.remove();return}
  clearAiMarks();
  if(aiBusy){aiStop();return}
  if(aiState!=="bar")aiCollapse();
 });
}
function aiWirePanel(){
 $("#aiBar").onclick=()=>aiOpen();
 $("#aiClose").onclick=aiCollapse;
 $("#aiWide").onclick=()=>aiSetState(aiState==="wide"?"dock":"wide");
 $("#aiMore").onclick=()=>{
  const open=document.querySelector(".ai-menu.ai-cmds");
  if(open)open.remove();else aiMoreMenu();
 };
 const log=$("#aiLog");
 log.addEventListener("scroll",()=>{aiStick=log.scrollTop+log.clientHeight>=log.scrollHeight-24});
 const box=$("#aiIn");
 box.addEventListener("input",()=>{aiGrow(box);aiSlashIx=0;aiSlashRender()});
 box.addEventListener("keydown",e=>{
  const open=!$("#aiSlash").hidden;
  if(open&&e.key==="ArrowDown"){e.preventDefault();aiSlashIx=Math.min(aiSlashList.length-1,aiSlashIx+1);aiSlashRender();return}
  if(open&&e.key==="ArrowUp"){e.preventDefault();aiSlashIx=Math.max(0,aiSlashIx-1);aiSlashRender();return}
  if(open&&(e.key==="Enter"||e.key==="Tab")){e.preventDefault();aiSlashRun(aiSlashList[aiSlashIx]);return}
  if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();$("#aiForm").requestSubmit()}
 });
 $("#aiForm").addEventListener("submit",e=>{
  e.preventDefault();
  if(aiBusy){aiStop();return}
  const q=box.value.trim();
  if(!q)return;
  box.value="";aiGrow(box);
  aiAsk(q);
 });
 $("#aiHost").textContent=AI.model+" · "+new URL(AI.endpoint).host+" · runs in your browser";
}
function palButton(){
 const row=el("div","toolrow");
 row.id="toolrow";
 const pal=el("button","toolbtn","⌘K");
 pal.title="Everything you can do here";
 pal.onclick=()=>palOpen("");
 row.append(pal);
 document.querySelector("#rail .tools").append(row);
}
