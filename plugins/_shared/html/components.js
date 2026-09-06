// @requires COMPONENTS,REF,SVGNS,clearHl,handleOf,heroInit,hlTarget,mdRef,mermaidJob,plain,refChip,setAspect,sysdSvg,wireFullSize,$,el,esc,inline,mdToHtml
// @defines DD_KINDS,DD_MD,FLOW_LEGEND,FX_CALL_GAP,FX_CALL_H2,FX_COL_H,FX_CONN,FX_GAP,FX_LEG_H,FX_PAD,FX_PADX,LN_BOX_GAP,LN_BOX_H,LN_BOX_H2,LN_BRIDGE_MIN,LN_EYE,LN_FPAD,LN_GAP,LN_MINW,LN_NARROW,LN_ONE_COL,SVG_ATTRS,SVG_CSS_BAN,SVG_TAGS,TEXT_W,TL_PILL,WI_FNS,WI_TOKEN,afterFonts,boxH,callH,callW,cmptBeforeAfter,cmptFigMd,cmptFigure,cmptFlow,cmptFrame,cmptLanes,cmptMatrix,cmptSteps,cmptTabs,cmptTimeline,cmptWhatIf,flowBy,flowLabel,flowSub,flowSvg,flowTopics,fxCanvas,fxFonts,fxLabelled,fxMarker,fxMeasure,fxMount,fxNum,fxSvg,fxText,fxUid,lanesLabel,lanesSvg,mountedComponents,safeSvg,stackH,textWidth,trimZeros,whatifEval,whatifParse,whatifTokens,wiNum,wiUnit
const WI_TOKEN=/[0-9]*\.?[0-9]+|[A-Za-z_][A-Za-z0-9_]*|[-+*/(),]|\s+/g;
const WI_FNS={min:Math.min,max:Math.max};
function whatifTokens(src){
 const out=[];let at=0,m;
 WI_TOKEN.lastIndex=0;
 while((m=WI_TOKEN.exec(src))){
  if(m.index!==at)throw new Error(`unexpected "${src[at]}" at ${at}`);
  at=m.index+m[0].length;
  if(!/^\s+$/.test(m[0]))out.push(m[0]);
 }
 if(at!==src.length)throw new Error(`unexpected "${src[at]}" at ${at}`);
 return out;
}
function whatifParse(src){
 const t=whatifTokens(String(src));
 let p=0;
 const peek=()=>t[p];
 const eat=x=>{if(t[p]!==x)throw new Error(`expected "${x}"`);p++};
 const expr=()=>{let n=term();while(peek()==="+"||peek()==="-"){const op=t[p++];n={op,l:n,r:term()}}return n};
 const term=()=>{let n=unary();while(peek()==="*"||peek()==="/"){const op=t[p++];n={op,l:n,r:unary()}}return n};
 const unary=()=>{if(peek()==="-"||peek()==="+"){const op=t[p++];return {op,l:{num:0},r:unary()}}return prim()};
 const prim=()=>{
  const x=peek();
  if(x===undefined)throw new Error("the expression ends early");
  if(x==="("){p++;const n=expr();eat(")");return n}
  if(/^[0-9.]/.test(x)){p++;return {num:parseFloat(x)}}
  if(/^[A-Za-z_]/.test(x)){
   p++;
   if(peek()!=="(")return {ref:x};
   p++;
   const args=[expr()];
   while(peek()===","){p++;args.push(expr())}
   eat(")");
   if(!WI_FNS[x])throw new Error(`unknown function ${x}()`);
   if(args.length<2)throw new Error(`${x}() takes two or more arguments`);
   return {fn:x,args};
  }
  throw new Error(`unexpected "${x}"`);
 };
 const node=expr();
 if(p!==t.length)throw new Error(`unexpected "${t[p]}"`);
 return node;
}
function whatifEval(n,vals){
 if("num" in n)return n.num;
 if("ref" in n)return vals[n.ref];
 if("fn" in n)return WI_FNS[n.fn](...n.args.map(a=>whatifEval(a,vals)));
 const a=whatifEval(n.l,vals),b=whatifEval(n.r,vals);
 return n.op==="+"?a+b:n.op==="-"?a-b:n.op==="*"?a*b:a/b;
}
const trimZeros=s=>s.indexOf(".")<0?s:s.replace(/\.?0+$/,"");
function wiNum(v,decimals){
 if(!isFinite(v))return "—";
 if(decimals!=null)return v.toFixed(decimals);
 if(Math.abs(v-Math.round(v))<1e-9)return String(Math.round(v));
 return trimZeros(v.toFixed(Math.abs(v)>=100?0:2));
}
const wiUnit=(s,unit)=>unit?s+" "+unit:s;
function cmptFrame(spec,kind){
 const box=el("div","ddc");
 box.dataset.kind=kind;
 if(spec.title)box.append(el("div","ddc-head",inline(spec.title)));
 return box;
}
const SVG_TAGS=new Set(["svg","g","defs","title","desc","path","rect","circle","ellipse","line","polyline","polygon","text","tspan","marker","use","symbol","lineargradient","radialgradient","stop","clippath","mask","pattern"]);
const SVG_ATTRS=new Set(["id","class","style","transform","d","x","y","dx","dy","x1","y1","x2","y2","cx","cy","r","rx","ry","width","height","viewbox","preserveaspectratio","fill","fill-opacity","fill-rule","stroke","stroke-width","stroke-linecap","stroke-linejoin","stroke-dasharray","stroke-dashoffset","stroke-opacity","opacity","font-family","font-size","font-weight","font-style","letter-spacing","text-anchor","dominant-baseline","points","offset","stop-color","stop-opacity","gradientunits","gradienttransform","spreadmethod","markerwidth","markerheight","refx","refy","orient","markerunits","patternunits","clip-path","mask","vector-effect","paint-order","xmlns","role","aria-label","aria-hidden"]);
const SVG_CSS_BAN=/url\s*\(|expression|@import|behavior/i;
function safeSvg(source){
 const doc=new DOMParser().parseFromString(String(source),"image/svg+xml");
 const root=doc.documentElement;
 if(!root||root.localName!=="svg"||doc.querySelector("parsererror"))return null;
 [root,...root.querySelectorAll("*")].forEach(n=>{
  if(!SVG_TAGS.has(n.localName.toLowerCase())){n.remove();return}
  [...n.attributes].forEach(a=>{
   if(a.name.toLowerCase().startsWith("xml"))return;
   const name=a.localName.toLowerCase();
   if(name==="href")return void(/^#[\w.:-]+$/.test(a.value)||n.removeAttributeNode(a));
   if(name==="style")return void(SVG_CSS_BAN.test(a.value)&&n.removeAttributeNode(a));
   if(!SVG_ATTRS.has(name))n.removeAttributeNode(a);
  });
 });
 return document.importNode(root,true);
}
function cmptFigure(fig,host,jobs){
 const f=el("figure","ddc-fig");
 if(fig.kind==="mermaid"){
  const mount=el("div","mmd-host");
  f.append(mount);
  jobs.push(mermaidJob(mount,fig.source,fig.label||plain(fig.caption||"Diagram"),null,host.closest("#hero")?heroInit:null));
 }else{
  const svg=safeSvg(fig.source);
  if(svg){
   svg.classList.add("fit");
   svg.setAttribute("role","img");
   if(fig.label)svg.setAttribute("aria-label",fig.label);
   setAspect(svg);
   f.append(svg);
  }
 }
 if(fig.caption)f.append(el("figcaption","ddc-cap",inline(fig.caption)));
 return f;
}
function cmptTabs(spec,host,jobs){
 const box=cmptFrame(spec,"dd.tabs");
 const row=el("div","ddc-tabrow");
 row.setAttribute("role","tablist");
 const panes=el("div","ddc-panes");
 const start=Math.min(Math.max(spec.default||0,0),spec.tabs.length-1);
 spec.tabs.forEach((t,i)=>{
  const b=el("button","ddc-tab"+(i===start?" on":""),esc(t.label));
  b.type="button";b.setAttribute("role","tab");b.setAttribute("aria-selected",String(i===start));
  const pane=el("div","ddc-pane"+(i===start?" on":""));
  pane.dataset.label=plain(t.label);
  if(t.md)pane.append(el("div","ddc-md",mdToHtml(t.md)));
  if(t.figure)pane.append(cmptFigure(t.figure,host,jobs));
  b.onclick=()=>{
   [...row.children].forEach((x,j)=>{x.classList.toggle("on",j===i);x.setAttribute("aria-selected",String(j===i))});
   [...panes.children].forEach((x,j)=>x.classList.toggle("on",j===i));
  };
  row.append(b);panes.append(pane);
 });
 box.append(row,panes);
 return box;
}
function cmptBeforeAfter(spec,host,jobs){
 const box=cmptFrame(spec,"dd.before-after");
 const mode=spec.mode||"toggle";
 box.dataset.mode=mode;
 const wrap=el("div","ddc-ba");
 const sides=[["before",spec.before],["after",spec.after]].map(([side,o],i)=>{
  const s=el("div","ddc-side"+(mode==="toggle"&&!i?" on":""));
  s.dataset.side=side;
  s.append(el("span","ddc-eyebrow",esc(o.label)),cmptFigure(o.figure,host,jobs));
  wrap.append(s);
  return s;
 });
 const ctl=el("div","ddc-bactl");
 if(mode==="slider"){
  wrap.style.setProperty("--x",50);
  const r=el("input","ddc-wipe");
  r.type="range";r.min=0;r.max=100;r.step=1;r.value=50;r.dataset.default=50;
  r.setAttribute("aria-label",`Wipe between ${plain(spec.before.label)} and ${plain(spec.after.label)}`);
  r.oninput=()=>wrap.style.setProperty("--x",r.value);
  ctl.append(el("span","ddc-eyebrow",esc(spec.before.label)),r,el("span","ddc-eyebrow",esc(spec.after.label)));
 }else{
  [spec.before.label,spec.after.label].forEach((label,i)=>{
   const b=el("button","ddc-tab"+(i?"":" on"),esc(label));
   b.type="button";b.setAttribute("aria-pressed",String(!i));
   b.onclick=()=>{
    [...ctl.children].forEach((x,j)=>{x.classList.toggle("on",j===i);x.setAttribute("aria-pressed",String(j===i))});
    sides.forEach((s,j)=>s.classList.toggle("on",j===i));
   };
   ctl.append(b);
  });
  ctl.classList.add("ddc-tabrow");
 }
 box.append(wrap,ctl);
 return box;
}
function cmptWhatIf(spec){
 const box=cmptFrame(spec,"dd.whatif");
 const vals=Object.create(null);
 const asts=spec.outputs.map(o=>whatifParse(o.expr));
 const inputs=el("div","ddc-inputs"),outs=el("div","ddc-outs");
 const cells=spec.outputs.map(o=>{
  const cell=el("div","ddc-out");
  if(o.tone)cell.dataset.tone=o.tone;
  const value=el("span","ddc-ov");
  cell.append(value,el("span","ddc-ol",inline(o.label)));
  outs.append(cell);
  return value;
 });
 const recompute=()=>spec.outputs.forEach((o,i)=>{cells[i].textContent=wiUnit(wiNum(whatifEval(asts[i],vals),o.decimals),o.unit)});
 spec.inputs.forEach(inp=>{
  vals[inp.id]=inp.value;
  const row=el("label","ddc-input");
  const read=el("span","ddc-iv num");
  const r=el("input","ddc-range");
  r.type="range";r.min=inp.min;r.max=inp.max;r.step=inp.step==null?1:inp.step;r.value=inp.value;r.dataset.default=inp.value;
  const show=()=>{read.textContent=wiUnit(wiNum(vals[inp.id]),inp.unit)};
  r.oninput=()=>{vals[inp.id]=parseFloat(r.value);show();recompute()};
  show();
  row.append(el("span","ddc-il",inline(inp.label)),r,read);
  inputs.append(row);
 });
 recompute();
 box.append(inputs,outs);
 if((spec.cites||[]).length)box.append(el("div","ddc-cites",spec.cites.map(x=>refChip(x)).join(" ")));
 return box;
}
function cmptSteps(spec){
 const box=cmptFrame(spec,"dd.steps");
 const ol=el("ol","ddc-steps");
 const back=el("button",null,"← Back"),next=el("button",null,"Next →");
 let at=0;
 const go=i=>{
  at=Math.min(Math.max(i,0),spec.steps.length-1);
  [...ol.children].forEach((li,j)=>li.classList.toggle("on",j===at));
  back.disabled=at===0;next.disabled=at===spec.steps.length-1;
  const svg=sysdSvg();
  if(!svg)return;
  clearHl(svg);
  const target=spec.steps[at].target;
  if(target)hlTarget(svg,target);
 };
 spec.steps.forEach((s,i)=>{
  const li=el("li","ddc-step");
  if(s.target)li.dataset.target=s.target;
  const b=el("button","ddc-stepb",`<span class="ddc-sl">${esc(s.label)}</span>`);
  b.type="button";
  b.onclick=()=>go(i);
  li.append(b,el("div","ddc-sd",mdToHtml(s.md)));
  ol.append(li);
 });
 back.type=next.type="button";
 back.onclick=()=>go(at-1);next.onclick=()=>go(at+1);
 const nav=el("div","ddc-nav");
 nav.append(back,next);
 box.append(ol,nav);
 go(0);
 return box;
}
const TL_PILL={done:"p-ok",next:"p-acc",blocked:"p-warn"};
function cmptTimeline(spec){
 const box=cmptFrame(spec,"dd.timeline");
 const ol=el("ol","ddc-tl");
 spec.phases.forEach(ph=>{
  const li=el("li","ddc-ph",`<span class="ddc-dot" aria-hidden="true"></span><span class="ddc-pl">${inline(ph.label)}</span><span class="pill ${TL_PILL[ph.state]}">${esc(ph.state)}</span>`);
  li.dataset.state=ph.state;
  if(ph.note)li.append(el("span","ddc-pn",inline(ph.note)));
  if(ph.gate)li.append(el("span","ddc-pg",`${ph.state==="blocked"?"waiting on":"gated on"} ${refChip(ph.gate)}`));
  ol.append(li);
 });
 box.append(ol);
 return box;
}
function cmptMatrix(spec){
 const box=cmptFrame(spec,"dd.matrix");
 const pick=spec.cols.findIndex(c=>c.label===spec.pick);
 const head=spec.cols.map((c,i)=>`<th${i===pick?' class="on"':""}>${inline(c.label)}${i===pick?' <span class="pill p-acc">chosen</span>':""}</th>`).join("");
 const body=spec.rows.map((r,ri)=>`<tr><th>${inline(r.label)}</th>`+
  (spec.cells[ri]||[]).map((cell,ci)=>`<td${ci===pick?' class="on"':""}${cell.tone?` data-tone="${esc(cell.tone)}"`:""}>${inline(cell.text)}</td>`).join("")+"</tr>").join("");
 const wrap=el("div","twrap",`<table class="ddc-mx"><thead><tr><th></th>${head}</tr></thead><tbody>${body}</tbody></table>`);
 box.append(wrap);
 return box;
}
const FX_PAD=10,FX_GAP=44,FX_COL_H=40,FX_PADX=14,FX_CALL_H2=44,FX_CALL_GAP=6,FX_CONN=26,FX_LEG_H=22;
const LN_EYE=24,LN_GAP=32,LN_FPAD=8,LN_BOX_H=30,LN_BOX_H2=40,LN_BOX_GAP=8,LN_MINW=120,LN_BRIDGE_MIN=64,LN_NARROW=640,LN_ONE_COL=4;
const TEXT_W=new Map();
let fxMeasure=null,fxCanvas=null,fxFonts=null,fxUid=0;
function textWidth(text,cls){
 const key=cls+"\0"+text;
 if(TEXT_W.has(key))return TEXT_W.get(key);
 if(!fxMeasure){
  fxMeasure=document.createElementNS(SVGNS,"svg");
  fxMeasure.id="flowMeasure";
  fxMeasure.setAttribute("aria-hidden","true");
  document.body.append(fxMeasure);
 }
 const t=document.createElementNS(SVGNS,"text");
 t.setAttribute("class",cls);
 t.textContent=text;
 fxMeasure.append(t);
 let w=t.getComputedTextLength();
 if(!w){
  if(!fxCanvas)fxCanvas=document.createElement("canvas").getContext("2d");
  const cs=getComputedStyle(t);
  fxCanvas.font=`${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
  w=fxCanvas.measureText(text).width;
 }
 t.remove();
 TEXT_W.set(key,w);
 return w;
}
function afterFonts(fn){
 if(document.fonts.status==="loaded")return;
 if(!fxFonts)fxFonts=document.fonts.ready.then(()=>{fxFonts=null;TEXT_W.clear()});
 fxFonts.then(fn);
}
const fxNum=v=>String(Math.round(v*10)/10);
const fxText=(cls,x,y,text,anchor)=>`<text class="${cls}" x="${fxNum(x)}" y="${fxNum(y)}"${anchor?` text-anchor="${anchor}"`:""}>${esc(text)}</text>`;
const fxLabelled=(cls,cx,y,h,title,sub)=>sub
 ?fxText("fx-t",cx,y+17,title,"middle")+fxText(cls,cx,y+h-12,sub,"middle")
 :fxText("fx-t",cx,y+h/2+4.5,title,"middle");
function fxMarker(id){
 return `<defs><marker id="${id}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path class="fx-head" d="M0 0L10 5L0 10z"/></marker></defs>`;
}
function fxSvg(kind,w,h,body,label){
 const svg=el("div",null,`<svg viewBox="0 0 ${fxNum(w)} ${fxNum(h)}" role="img">${body}</svg>`).firstElementChild;
 svg.classList.add("fit",kind);
 svg.setAttribute("aria-label",label);
 setAspect(svg);
 return svg;
}
function fxMount(box,draw,wire){
 let svg=draw();
 box.append(svg);
 if(wire)wire();
 const redraw=()=>{const next=draw();svg.replaceWith(next);svg=next;if(wire)wire()};
 afterFonts(redraw);
 return redraw;
}
const flowBy=c=>c.by?(REF[c.by]?handleOf(c.by):plain(c.by)):c.status==="fixed"?"this design":"a later design";
const flowSub=c=>(c.status==="fixed"?"Fixed by ":"Left for ")+flowBy(c);
const FLOW_LEGEND=["Solid: this design fixes it","Dashed: left for a later design"];
const callH=()=>FX_CALL_H2;
const callW=c=>Math.max(textWidth(c.title,"fx-t"),textWidth(flowSub(c),"fx-s"))+2*FX_PADX;
const stackH=list=>list.reduce((h,c,i)=>h+callH(c)+(i?FX_CALL_GAP:0),0);
function flowLabel(spec){
 if(spec.title)return plain(spec.title);
 const cols=spec.columns,fixed=spec.callouts.filter(c=>c.status==="fixed").length;
 return `${cols.length} columns, ${plain(cols[0].label)} to ${plain(cols[cols.length-1].label)}, with ${spec.callouts.length} callouts: ${fixed} fixed by this design, ${spec.callouts.length-fixed} left for later.`;
}
function flowSvg(spec){
 const cols=spec.columns.map(c=>({...c,above:[],below:[]}));
 const byId=Object.fromEntries(cols.map(c=>[c.id,c]));
 spec.callouts.forEach(c=>{
  const col=byId[c.col];
  col[c.side||(col.above.length<3?"above":"below")].push(c);
 });
 let x=FX_PAD,aboveH=0,belowH=0;
 cols.forEach(col=>{
  col.boxW=Math.max(50,textWidth(col.label,"fx-t")+2*FX_PADX,col.sub?textWidth(col.sub,"fx-s")+2*FX_PADX:0);
  col.w=Math.max(col.boxW,...col.above.map(callW),...col.below.map(callW))+8;
  col.cx=x+col.w/2;
  x+=col.w+FX_GAP;
  aboveH=Math.max(aboveH,stackH(col.above));
  belowH=Math.max(belowH,stackH(col.below));
 });
 let w=x-FX_GAP+FX_PAD;
 const colY=FX_PAD+aboveH+(aboveH?FX_CONN:0),colMid=colY+FX_COL_H/2;
 let h=colY+FX_COL_H+(belowH?FX_CONN+belowH:0)+FX_PAD;
 w=Math.max(w,textWidth(FLOW_LEGEND[0],"fx-n")+textWidth(FLOW_LEGEND[1],"fx-n")+3*FX_PAD);
 const marker=`fx-arr-${++fxUid}`;
 const parts=[fxMarker(marker)];
 const callout=(c,y)=>{
  const cw=callW(c),ch=callH(c),col=byId[c.col];
  return `<g class="fx-call" data-status="${c.status}"${c.topic?` data-topic="${esc(plain(c.topic).toLowerCase())}"`:""}><rect x="${fxNum(col.cx-cw/2)}" y="${fxNum(y)}" width="${fxNum(cw)}" height="${ch}" rx="6"/>${fxLabelled("fx-s",col.cx,y,ch,c.title,flowSub(c))}</g>`;
 };
 cols.forEach((col,i)=>{
  if(spec.arrows!==false&&i<cols.length-1){
   const next=cols[i+1];
   parts.push(`<path class="fx-arrow" d="M${fxNum(col.cx+col.boxW/2)} ${fxNum(colMid)}L${fxNum(next.cx-next.boxW/2)} ${fxNum(colMid)}" marker-end="url(#${marker})"/>`);
  }
  parts.push(`<rect class="fx-col" x="${fxNum(col.cx-col.boxW/2)}" y="${colY}" width="${fxNum(col.boxW)}" height="${FX_COL_H}" rx="8"/>`,fxLabelled("fx-s",col.cx,colY,FX_COL_H,col.label,col.sub));
  if(col.above.length){
   parts.push(`<path class="fx-conn" d="M${fxNum(col.cx)} ${fxNum(colY-FX_CONN)}L${fxNum(col.cx)} ${colY}"/>`);
   let y=colY-FX_CONN;
   col.above.forEach(c=>{y-=callH(c);parts.push(callout(c,y));y-=FX_CALL_GAP});
  }
  if(col.below.length){
   parts.push(`<path class="fx-conn" d="M${fxNum(col.cx)} ${colY+FX_COL_H}L${fxNum(col.cx)} ${colY+FX_COL_H+FX_CONN}"/>`);
   let y=colY+FX_COL_H+FX_CONN;
   col.below.forEach(c=>{parts.push(callout(c,y));y+=callH(c)+FX_CALL_GAP});
  }
 });
 parts.push(fxText("fx-n",FX_PAD,h+FX_LEG_H-8,FLOW_LEGEND[0]),fxText("fx-n",w-FX_PAD,h+FX_LEG_H-8,FLOW_LEGEND[1],"end"));
 h+=FX_LEG_H;
 return fxSvg("ddc-flow",w,h,parts.join(""),flowLabel(spec));
}
function flowTopics(box,host){
 const panel=host.closest(".xs-panel");
 if(!panel)return;
 const rows=[...panel.querySelectorAll(".xs-row")].map(row=>{
  const topic=row.querySelector(".xs-topic");
  return [row,topic?plain(topic.textContent).toLowerCase():""];
 });
 const rowsFor=topic=>rows.filter(([,t])=>t===topic).map(([row])=>row);
 const callsFor=topic=>[...box.querySelectorAll(`.fx-call[data-topic="${CSS.escape(topic)}"]`)];
 box.querySelectorAll(".fx-call[data-topic]").forEach(g=>{
  const topic=g.dataset.topic;
  g.addEventListener("pointerenter",()=>{callsFor(topic).forEach(c=>c.classList.add("on"));rowsFor(topic).forEach(r=>r.classList.add("fx-on"))});
  g.addEventListener("pointerleave",()=>{callsFor(topic).forEach(c=>c.classList.remove("on"));rowsFor(topic).forEach(r=>r.classList.remove("fx-on"))});
 });
 rows.forEach(([row,topic])=>{
  if(row.dataset.fxWired||!topic)return;
  row.dataset.fxWired="1";
  row.addEventListener("pointerenter",()=>callsFor(topic).forEach(c=>c.classList.add("on")));
  row.addEventListener("pointerleave",()=>callsFor(topic).forEach(c=>c.classList.remove("on")));
 });
}
function cmptFlow(spec,host){
 const box=cmptFrame(spec,"dd.flow");
 fxMount(box,()=>flowSvg(spec),()=>flowTopics(box,host));
 const btn=el("button","fullbtn","View full size");
 btn.hidden=true;
 box.append(btn);
 requestAnimationFrame(wireFullSize(box,btn,flowLabel(spec)));
 return box;
}
const boxH=b=>b.sub?LN_BOX_H2:LN_BOX_H;
function lanesLabel(spec){
 if(spec.title)return plain(spec.title);
 return spec.lanes.map(l=>`${plain(l.label)}: ${l.boxes.map(b=>plain(b.title)).join(", ")}.`).join(" ");
}
function lanesSvg(spec,stacked){
 const lanes=spec.lanes.map((l,i)=>{
  const cols=l.boxes.length>LN_ONE_COL?2:1;
  const rows=[];
  l.boxes.forEach((b,j)=>{(rows[Math.floor(j/cols)]||(rows[Math.floor(j/cols)]=[])).push(b)});
  const rowH=rows.map(r=>Math.max(...r.map(boxH)));
  return {
   ...l,cols,rows,rowH,
   tone:l.tone||(i?"after":"before"),
   boxW:Math.max(LN_MINW,...l.boxes.map(b=>Math.max(textWidth(b.title,"fx-t"),b.sub?textWidth(b.sub,"fx-s"):0)))+2*FX_PADX,
   stackH:rowH.reduce((s,h,j)=>s+h+(j?LN_BOX_GAP:0),0),
  };
 });
 const frameH=Math.max(...lanes.map(l=>l.stackH))+2*LN_FPAD;
 const bridge=spec.bridge?plain(spec.bridge):null;
 const bridgeW=bridge?textWidth(bridge,"fx-n"):0;
 lanes.forEach(l=>{l.innerW=l.cols*l.boxW+(l.cols-1)*LN_BOX_GAP});
 const sharedW=Math.max(...lanes.map(l=>l.innerW));
 lanes.forEach(l=>{if(stacked)l.innerW=sharedW;l.frameW=l.innerW+2*LN_FPAD});
 const gap=stacked?(bridge?40:20):(bridge?Math.max(LN_BRIDGE_MIN,bridgeW+24):LN_GAP);
 let w,h;
 if(stacked){
  lanes[0].x=lanes[1].x=FX_PAD;
  lanes[0].y=FX_PAD+LN_EYE;
  lanes[1].y=lanes[0].y+frameH+gap+LN_EYE;
  w=lanes[0].frameW+2*FX_PAD;
  if(bridge)w=Math.max(w,FX_PAD+lanes[0].frameW/2+8+bridgeW+FX_PAD);
  h=lanes[1].y+frameH+FX_PAD;
 }else{
  lanes[0].x=FX_PAD;
  lanes[1].x=FX_PAD+lanes[0].frameW+gap;
  lanes[0].y=lanes[1].y=FX_PAD+LN_EYE;
  w=lanes[1].x+lanes[1].frameW+FX_PAD;
  h=FX_PAD+LN_EYE+frameH+FX_PAD;
 }
 const marker=`fx-arr-${++fxUid}`;
 const parts=[fxMarker(marker)];
 lanes.forEach(l=>{
  parts.push(`<g class="fx-lane" data-tone="${l.tone}">`,fxText("fx-e",l.x,l.y-8,plain(l.label).toUpperCase()),`<rect class="fx-frame" x="${fxNum(l.x)}" y="${fxNum(l.y)}" width="${fxNum(l.frameW)}" height="${fxNum(frameH)}" rx="10"/>`);
  const boxW=(l.innerW-(l.cols-1)*LN_BOX_GAP)/l.cols;
  let y=l.y+LN_FPAD;
  l.rows.forEach((row,r)=>{
   row.forEach((b,c)=>{
    const x=l.x+LN_FPAD+c*(boxW+LN_BOX_GAP);
    parts.push(`<g class="fx-box"${b.status?` data-status="${b.status}"`:""}><rect x="${fxNum(x)}" y="${fxNum(y)}" width="${fxNum(boxW)}" height="${l.rowH[r]}" rx="6"/>${fxLabelled("fx-s",x+boxW/2,y,l.rowH[r],b.title,b.sub)}</g>`);
   });
   y+=l.rowH[r]+LN_BOX_GAP;
  });
  parts.push("</g>");
 });
 if(bridge){
  if(stacked){
   const cx=FX_PAD+lanes[0].frameW/2,y1=lanes[0].y+frameH,y2=lanes[1].y-LN_EYE;
   parts.push(`<path class="fx-bridge" d="M${fxNum(cx)} ${fxNum(y1+4)}L${fxNum(cx)} ${fxNum(y2-4)}" marker-end="url(#${marker})"/>`,fxText("fx-n",cx+8,(y1+y2)/2+3,bridge));
  }else{
   const x1=lanes[0].x+lanes[0].frameW,x2=lanes[1].x,my=lanes[0].y+frameH/2;
   parts.push(`<path class="fx-bridge" d="M${fxNum(x1+4)} ${fxNum(my)}L${fxNum(x2-4)} ${fxNum(my)}" marker-end="url(#${marker})"/>`,fxText("fx-n",(x1+x2)/2,my-7,bridge,"middle"));
  }
 }
 return fxSvg("ddc-lanes",w,h,parts.join(""),lanesLabel(spec));
}
function cmptLanes(spec,host){
 const box=cmptFrame(spec,"dd.lanes");
 let stacked=host.clientWidth<LN_NARROW;
 const redraw=fxMount(box,()=>lanesSvg(spec,stacked));
 new ResizeObserver(()=>{
  const next=host.clientWidth<LN_NARROW;
  if(next===stacked)return;
  stacked=next;
  redraw();
 }).observe(host);
 return box;
}
const cmptFigMd=f=>f.kind==="mermaid"
 ? "```mermaid\n"+f.source.trim()+"\n```\n"+(f.caption?"\n_"+plain(f.caption)+"_\n":"")
 : (f.label?plain(f.label)+"\n":"")+(f.caption?"\n_"+plain(f.caption)+"_\n":"");
function mountedComponents(){
 return [...document.querySelectorAll("[data-component]")]
  .map(h=>[h,COMPONENTS[h.dataset.component],h.dataset.component])
  .filter(([,spec])=>spec);
}
const DD_KINDS={"dd.tabs":cmptTabs,"dd.before-after":cmptBeforeAfter,"dd.whatif":cmptWhatIf,"dd.steps":cmptSteps,"dd.timeline":cmptTimeline,"dd.matrix":cmptMatrix,"dd.flow":cmptFlow,"dd.lanes":cmptLanes};
const DD_MD={
 "dd.tabs":(spec,L)=>spec.tabs.forEach(t=>{
  L.push(`_${plain(t.label)}_\n`);
  if(t.md)L.push(t.md+"\n");
  if(t.figure)L.push(cmptFigMd(t.figure));
 }),
 "dd.before-after":(spec,L)=>[spec.before,spec.after].forEach(o=>{
  L.push(`_${plain(o.label)}_\n`);
  L.push(cmptFigMd(o.figure));
 }),
 "dd.whatif":(spec,L)=>{
  const vals=Object.create(null);
  spec.inputs.forEach(i=>{vals[i.id]=i.value});
  L.push("| Input | Value |\n|---|---|");
  spec.inputs.forEach(i=>L.push(`| ${plain(i.label)} | ${wiUnit(wiNum(i.value),i.unit)} |`));
  L.push("\n| Output | At those inputs |\n|---|---|");
  spec.outputs.forEach(o=>L.push(`| ${plain(o.label)} | ${wiUnit(wiNum(whatifEval(whatifParse(o.expr),vals),o.decimals),o.unit)} |`));
  if((spec.cites||[]).length)L.push(`\n_Rests on ${spec.cites.map(mdRef).join(", ")}_`);
  L.push("");
 },
 "dd.steps":(spec,L)=>spec.steps.forEach((s,i)=>L.push(`${i+1}. **${plain(s.label)}** — ${plain(s.md)}`)),
 "dd.timeline":(spec,L)=>spec.phases.forEach(ph=>L.push(
  `- **${plain(ph.label)}** — ${ph.state}${ph.note?". "+plain(ph.note):""}${ph.gate?` _(gated on ${mdRef(ph.gate)})_`:""}`)),
 "dd.matrix":(spec,L)=>{
  L.push("| | "+spec.cols.map(c=>plain(c.label)+(c.label===spec.pick?" (chosen)":"")).join(" | ")+" |");
  L.push("|"+["---",...spec.cols.map(()=>"---")].join("|")+"|");
  spec.rows.forEach((r,ri)=>L.push(`| **${plain(r.label)}** | `+(spec.cells[ri]||[]).map(c=>plain(c.text)).join(" | ")+" |"));
  L.push("");
 },
 "dd.flow":(spec,L)=>{
  L.push("");
  spec.columns.forEach(col=>{
   L.push(`- **${plain(col.label)}**${col.sub?" — "+plain(col.sub):""}`);
   spec.callouts.filter(c=>c.col===col.id).forEach(c=>L.push(`  - ${plain(c.title)} — ${flowSub(c)}`));
  });
  L.push("\n_Solid: this design fixes it; dashed: left for a later design_");
  L.push("");
 },
 "dd.lanes":(spec,L)=>spec.lanes.forEach((l,i)=>{
  L.push(i&&spec.bridge?`_→ ${plain(spec.bridge)}_\n`:"");
  L.push(`_${plain(l.label)}_\n`);
  l.boxes.forEach(b=>L.push(`- **${plain(b.title)}**${b.sub?" — "+plain(b.sub):""}${b.status&&b.status!=="closed"?` (${b.status})`:""}`));
  L.push("");
 }),
};
