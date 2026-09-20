// @requires KIND_LABEL,REF,TERM_BY,plain,statusOf,themeLabel,unveil,$,GH,GH_FILE,GH_PENDING,GH_REF,GH_STATE_LABEL,GH_TOKEN,LINK_KIND_LABEL,REPO,el,esc,ghFileKey,ghFileLoad,relTime
// @defines TIP,TIP_DELAY,TIP_SEL,TIP_WATCH,armTip,cardHtml,ghCardHtml,ghFileCardHtml,hideTip,placeTip,showTip,tipArmed,tipCorridor,tipDodge,tipFor,tipInCorridor,tipKeys,tipLeave,tipPt,tipRaf,tipSign,tipSkelRow,tipTimer,tipTrack,tipTrackSoon
const TIP=el("div");TIP.id="tip";TIP.hidden=true;TIP.setAttribute("role","tooltip");document.body.append(TIP);
const TIP_SEL="[data-tip],[data-ref],[data-term],[data-gh],[data-ghfile]";
const TIP_DELAY=150;
let tipFor=null,tipKeys=false,tipTimer=0,tipArmed=null,tipCorridor=null,tipPt={x:0,y:0},tipRaf=0,tipDodge=0;
const TIP_WATCH=new IntersectionObserver(es=>{if(es.some(e=>e.target===tipFor&&!e.isIntersecting))hideTip()});
function hideTip(){
 clearTimeout(tipTimer);
 tipArmed=null;tipCorridor=null;
 if(tipFor)TIP_WATCH.unobserve(tipFor);
 tipFor=null;
 TIP.hidden=true;TIP.classList.remove("card","file","in");
}
const tipSkelRow=n=>`<span class="tsrc-l"><span class="tsrc-n"></span><span class="tskel">${" ".repeat(8+(n*13)%36)}</span></span>`;
function ghFileCardHtml(node){
 const path=node.dataset.ghfile,from=+node.dataset.line,to=+(node.dataset.to||node.dataset.line);
 const open=`<span class="topen" data-href="${esc(node.href)}" role="button" tabindex="0">Open on GitHub →</span>`;
 const head=`<span class="tkind">${esc(REPO)} · ${esc(GH_REF)}</span><span class="ttitle">${esc(path)}</span>`;
 if(!GH_TOKEN)return head+open;
 const rec=GH_FILE[ghFileKey(path)];
 const lo=Math.max(1,from-6);
 if(!rec){
  const skel=[];
  for(let n=lo;n<=to+6;n++)skel.push(tipSkelRow(n));
  return head+`<pre class="tsrc">${skel.join("\n")}</pre>`+open;
 }
 if(rec.failed)return head+"<p>GitHub could not be reached for this file.</p>"+open;
 if(rec.status)return head+`<p>${esc(rec.status===404?"GitHub has no "+path+" at "+GH_REF+".":rec.status===403||rec.status===401?"The site's token cannot read file contents; it needs Contents: Read.":"GitHub answered "+rec.status+".")}</p>`+open;
 const lines=rec.text.split("\n");
 const hi=Math.min(lines.length,to+6);
 const rows=[];
 for(let n=lo;n<=hi;n++)rows.push(`<span class="tsrc-l${n>=from&&n<=to?" hl":""}"><span class="tsrc-n">${n}</span>${esc(lines[n-1])}</span>`);
 return head+`<pre class="tsrc">${rows.join("\n")}</pre>`+open;
}
function ghCardHtml(node){
 const key=node.dataset.gh,kind=node.dataset.kind,rec=GH[key];
 const label=node.querySelector(".lt").textContent;
 const open=`<span class="topen" data-href="${esc(node.href)}" role="button" tabindex="0">Open on GitHub →</span>`;
 if(!rec)return `<span class="tkind">${esc(LINK_KIND_LABEL[kind])} · ${esc(key)}</span><span class="ttitle">${esc(label)}</span>`+(GH_PENDING[key]?`<span class="tgh tskel">${" ".repeat(26)}</span>`:"")+open;
 const checks=rec.checks?"checks "+(rec.checks.total?rec.checks.ok+"/"+rec.checks.total+(rec.checks.state==="success"?"":" "+rec.checks.state):rec.checks.state):"";
 const bits=[LINK_KIND_LABEL[kind],GH_STATE_LABEL[rec.state]||rec.state,rec.review||"",checks].filter(Boolean);
 const by=[rec.author?"by "+rec.author:"",rec.updated?"updated "+relTime(rec.updated):""].filter(Boolean).join(", ");
 return `<span class="tkind">${esc(bits.join(" · "))}</span><span class="ttitle">${esc(rec.title||label)}</span><span class="tgh">${esc(key)}${by?" · "+esc(by):""}</span>`+open;
}
function cardHtml(node){
 if(node.dataset.gh)return ghCardHtml(node);
 if(node.dataset.ghfile)return ghFileCardHtml(node);
 const id=node.dataset.ref;
 if(id){
  const r=REF[id],[,sl]=statusOf(id);
  const bits=[KIND_LABEL[r.kind],sl,r.theme?themeLabel(r.theme):""].filter(Boolean);
  return `<span class="tkind">${esc(bits.join(" · "))}</span><span class="ttitle">${esc(plain(r.title))}</span>`+
   (r.p?`<p>${esc(plain(r.p))}</p>`:"")+
   `<span class="topen" data-open="${esc(id)}" role="button" tabindex="0">Open →</span>`;
 }
 const t=TERM_BY[node.dataset.term];
 return `<span class="ttitle">${esc(t.k)}${t.ai?' <span class="pill p-acc ai-pill">AI-defined</span>':""}</span><p>${esc(plain(t.v))}</p><span class="topen" data-open="#ground" role="button" tabindex="0">Terms →</span>`;
}
function placeTip(node,fresh){
 const r=node.getBoundingClientRect(),b=TIP.getBoundingClientRect();
 const above=r.top-b.height-8>=8;
 let top=above?r.top-b.height-8:r.bottom+8;
 if(!above&&top+b.height>innerHeight-8)top=Math.max(8,innerHeight-b.height-8);
 const anchored=Math.min(Math.max(8,r.left),innerWidth-b.width-8);
 if(fresh){
  tipDodge=0;
  const px=tipPt.x;
  if(!tipKeys&&anchored<=px&&px<=anchored+b.width){
   if(px+14+b.width<=innerWidth-8)tipDodge=px+14-anchored;
   else if(px-14-b.width>=8)tipDodge=px-14-b.width-anchored;
  }
 }
 const left=Math.min(Math.max(8,anchored+tipDodge),innerWidth-b.width-8);
 TIP.style.left=left+"px";TIP.style.top=top+"px";
}
function tipTrack(){
 if(!tipFor||TIP.hidden)return;
 placeTip(tipFor,false);
}
function tipTrackSoon(){
 if(tipRaf)return;
 tipRaf=requestAnimationFrame(()=>{tipRaf=0;tipTrack()});
}
function showTip(node,viaKeys){
 const card=node.dataset.ref||node.dataset.term||node.dataset.gh||node.dataset.ghfile;
 const t=node.getAttribute("data-tip");
 if(!card&&!t){hideTip();return}
 clearTimeout(tipTimer);
 tipArmed=null;tipCorridor=null;
 const shown=!TIP.hidden,same=tipFor===node;
 if(!same){if(tipFor)TIP_WATCH.unobserve(tipFor);tipFor=node;TIP_WATCH.observe(node)}
 tipKeys=!!viaKeys;
 TIP.classList.toggle("card",!!card);
 TIP.classList.toggle("file",!!node.dataset.ghfile);
 if(card)TIP.innerHTML=cardHtml(node);else TIP.textContent=t;
 let pending=null;
 if(node.dataset.gh&&!GH[node.dataset.gh])pending=GH_PENDING[node.dataset.gh];
 if(node.dataset.ghfile&&GH_TOKEN){
  const key=ghFileKey(node.dataset.ghfile);
  if(!GH_FILE[key])pending=ghFileLoad(node.dataset.ghfile).catch(()=>{GH_FILE[key]={failed:true}});
 }
 if(pending){const refill=()=>{if(tipFor===node)showTip(node,tipKeys)};pending.then(refill,refill)}
 if(shown){placeTip(node,!same);TIP.classList.remove("in");return}
 TIP.classList.add("in");
 TIP.hidden=false;
 requestAnimationFrame(()=>{
  if(tipFor!==node)return;
  placeTip(node,true);
  TIP.classList.remove("in");
 });
}
function armTip(node){
 if(node===tipArmed||node===tipFor)return;
 clearTimeout(tipTimer);
 tipArmed=node;tipCorridor=null;
 if(!TIP.hidden){showTip(node,false);return}
 tipTimer=setTimeout(()=>{if(tipArmed===node)showTip(node,false)},TIP_DELAY);
}
const tipSign=(p,a,b)=>(p.x-b.x)*(a.y-b.y)-(a.x-b.x)*(p.y-b.y);
function tipInCorridor(p){
 const {a,b,c}=tipCorridor;
 const d1=tipSign(p,a,b),d2=tipSign(p,b,c),d3=tipSign(p,c,a);
 return !((d1<0||d2<0||d3<0)&&(d1>0||d2>0||d3>0));
}
function tipLeave(){
 if(TIP.hidden)return;
 if(!TIP.classList.contains("card")){hideTip();return}
 if(tipCorridor)return;
 const b=TIP.getBoundingClientRect(),p={x:tipPt.x,y:tipPt.y};
 const y=Math.abs(p.y-b.top)<=Math.abs(p.y-b.bottom)?b.top:b.bottom;
 tipCorridor={a:p,b:{x:b.left-8,y},c:{x:b.right+8,y}};
 clearTimeout(tipTimer);
 tipTimer=setTimeout(()=>{if(tipCorridor&&!TIP.matches(":hover"))hideTip()},400);
}
document.addEventListener("pointermove",e=>{
 tipPt={x:e.clientX,y:e.clientY};
 if(tipCorridor&&!TIP.contains(e.target)&&!tipInCorridor(tipPt))hideTip();
},{passive:true});
document.addEventListener("pointerover",e=>{
 tipPt={x:e.clientX,y:e.clientY};
 if(TIP.contains(e.target)){clearTimeout(tipTimer);tipCorridor=null;return}
 const n=e.target.closest&&e.target.closest(TIP_SEL);
 if(n&&n===tipFor){clearTimeout(tipTimer);tipCorridor=null;return}
 if(n){armTip(n);return}
 if(tipArmed){tipArmed=null;clearTimeout(tipTimer)}
 if(TIP.hidden)return;
 if(TIP.contains(e.relatedTarget)){clearTimeout(tipTimer);tipTimer=setTimeout(hideTip,200);return}
 tipLeave();
});
document.addEventListener("pointerdown",e=>{if(!TIP.contains(e.target))hideTip()});
document.addEventListener("focusin",e=>{if(TIP.contains(e.target))return;const n=e.target.closest&&e.target.closest(TIP_SEL);n?showTip(n,true):hideTip()});
document.addEventListener("focusout",e=>{if(TIP.contains(e.relatedTarget))return;hideTip()});
document.addEventListener("keydown",e=>{if(e.key==="Escape"&&!TIP.hidden)hideTip()});
TIP.addEventListener("keydown",e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();e.target.click()}});
TIP.addEventListener("click",e=>{
 const h=e.target.closest("[data-href]");
 if(h){hideTip();open(h.dataset.href,"_blank","noopener");return}
 const o=e.target.closest("[data-open]");
 if(!o)return;
 const v=o.dataset.open;
 hideTip();
 if(v.startsWith("#")){unveil();const s=document.querySelector(v);if(s)s.scrollIntoView({block:"start"})}
 else openRef(v);
});
addEventListener("scroll",e=>{if(!TIP.contains(e.target))tipTrackSoon()},true);
addEventListener("resize",tipTrackSoon);
