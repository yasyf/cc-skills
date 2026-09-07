// @requires KIND_LABEL,REF,TERM_BY,plain,statusOf,themeLabel,unveil,$,GH,GH_FILE,GH_PENDING,GH_REF,GH_STATE_LABEL,GH_TOKEN,LINK_KIND_LABEL,REPO,el,esc,ghFileKey,ghFileLoad,relTime
// @defines TIP,TIP_RING,TIP_SEL,armTip,cardHtml,ghCardHtml,ghFileCardHtml,hideTip,placeTip,showTip,tipArmed,tipClosedAt,tipCorridor,tipDir,tipFor,tipInCorridor,tipLeave,tipOpenArmed,tipPt,tipSign,tipSpeed,tipSpeedNow,tipTimer
const TIP=el("div");TIP.id="tip";TIP.hidden=true;TIP.setAttribute("role","tooltip");document.body.append(TIP);
const TIP_SEL="[data-tip],[data-ref],[data-term],[data-gh],[data-ghfile]";
const TIP_RING=[];
let tipFor=null,tipTimer=0,tipArmed=null,tipClosedAt=-1e9,tipCorridor=null,tipSpeed=0,tipDir={x:0,y:0},tipPt={x:0,y:0};
function tipSpeedNow(){
 const last=TIP_RING[TIP_RING.length-1];
 return last&&performance.now()-last.t<80?tipSpeed:0;
}
function hideTip(){
 clearTimeout(tipTimer);
 tipArmed=null;tipCorridor=null;
 if(!TIP.hidden)tipClosedAt=performance.now();
 tipFor=null;TIP.hidden=true;TIP.classList.remove("card","file");
}
function ghFileCardHtml(node){
 const path=node.dataset.ghfile,from=+node.dataset.line,to=+(node.dataset.to||node.dataset.line);
 const open=`<span class="topen" data-href="${esc(node.href)}" role="button" tabindex="0">Open on GitHub →</span>`;
 const head=`<span class="tkind">${esc(REPO)} · ${esc(GH_REF)}</span><span class="ttitle">${esc(path)}</span>`;
 if(!GH_TOKEN)return head+open;
 const rec=GH_FILE[ghFileKey(path)];
 if(!rec)return head+"<p>Loading…</p>"+open;
 if(rec.status)return head+`<p>${esc(rec.status===404?"GitHub has no "+path+" at "+GH_REF+".":rec.status===403||rec.status===401?"The site's token cannot read file contents; it needs Contents: Read.":"GitHub answered "+rec.status+".")}</p>`+open;
 const lines=rec.text.split("\n");
 const lo=Math.max(1,from-6),hi=Math.min(lines.length,to+6);
 const rows=[];
 for(let n=lo;n<=hi;n++)rows.push(`<span class="tsrc-l${n>=from&&n<=to?" hl":""}"><span class="tsrc-n">${n}</span>${esc(lines[n-1])}</span>`);
 return head+`<pre class="tsrc">${rows.join("\n")}</pre>`+open;
}
function ghCardHtml(node){
 const key=node.dataset.gh,kind=node.dataset.kind,rec=GH[key];
 const label=node.querySelector(".lt").textContent;
 const open=`<span class="topen" data-href="${esc(node.href)}" role="button" tabindex="0">Open on GitHub →</span>`;
 if(!rec)return `<span class="tkind">${esc(LINK_KIND_LABEL[kind])} · ${esc(key)}</span><span class="ttitle">${esc(label)}</span>`+(GH_PENDING[key]?"<p>Loading…</p>":"")+open;
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
function placeTip(node,viaKeys){
 const r=node.getBoundingClientRect(),b=TIP.getBoundingClientRect();
 const below=(!viaKeys&&tipDir.y>0)||r.top-b.height-8<8;
 let top=below?r.bottom+8:r.top-b.height-8;
 if(below&&top+b.height>innerHeight-8)top=Math.max(8,r.top-b.height-8);
 let left=Math.min(Math.max(8,r.left),innerWidth-b.width-8);
 const px=tipPt.x;
 if(!viaKeys&&left<=px&&px<=left+b.width){
  if(px+14+b.width<=innerWidth-8)left=px+14;
  else if(px-14-b.width>=8)left=px-14-b.width;
 }
 TIP.style.left=left+"px";TIP.style.top=top+"px";
}
function showTip(node,viaKeys){
 const card=node.dataset.ref||node.dataset.term||node.dataset.gh||node.dataset.ghfile;
 const t=node.getAttribute("data-tip");
 if(!card&&!t){hideTip();return}
 clearTimeout(tipTimer);
 tipArmed=null;tipCorridor=null;tipFor=node;
 TIP.classList.toggle("card",!!card);
 TIP.classList.toggle("file",!!node.dataset.ghfile);
 if(card)TIP.innerHTML=cardHtml(node);else TIP.textContent=t;
 let pending=null;
 if(node.dataset.gh&&!GH[node.dataset.gh])pending=GH_PENDING[node.dataset.gh];
 if(node.dataset.ghfile&&GH_TOKEN&&!GH_FILE[ghFileKey(node.dataset.ghfile)])pending=ghFileLoad(node.dataset.ghfile);
 if(pending)pending.then(()=>{if(tipFor===node)showTip(node)},()=>{});
 TIP.hidden=false;
 placeTip(node,viaKeys);
}
function tipOpenArmed(node){
 if(tipArmed!==node)return;
 if(tipSpeedNow()>0.5){tipTimer=setTimeout(()=>tipOpenArmed(node),80);return}
 showTip(node,false);
}
function armTip(node){
 if(node===tipArmed)return;
 clearTimeout(tipTimer);
 tipArmed=node;tipCorridor=null;
 const delay=!TIP.hidden?40:performance.now()-tipClosedAt<1000?400:250;
 tipTimer=setTimeout(()=>tipOpenArmed(node),delay);
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
 const now=performance.now();
 TIP_RING.push({x:e.clientX,y:e.clientY,t:now});
 if(TIP_RING.length>5)TIP_RING.shift();
 tipPt={x:e.clientX,y:e.clientY};
 const a=TIP_RING[0],b=TIP_RING[TIP_RING.length-1],dt=b.t-a.t;
 if(dt>0){tipDir={x:b.x-a.x,y:b.y-a.y};tipSpeed=Math.hypot(tipDir.x,tipDir.y)/dt}
 if(tipCorridor&&!TIP.contains(e.target)&&!tipInCorridor(tipPt))hideTip();
},{passive:true});
document.addEventListener("pointerover",e=>{
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
addEventListener("scroll",hideTip,true);
addEventListener("resize",hideTip);
