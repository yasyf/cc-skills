// @requires linkIds,linkSrc,$,esc
// @defines EST_TIP,inline,mdToHtml
const EST_TIP="Estimate: derived, not measured; pending the named spike";
const inline=s=>linkSrc(linkIds(esc(s)
 .replace(/\[\^(\d+)\]/g,'<sup class="fn"><a href="#fn-$1">$1</a></sup>')
 .replace(/\[([^\]]+)\]\(([^)]+)\)/g,'<a class="xlink" href="$2" target="_blank" rel="noopener">$1</a>')
 .replace(/`([^`]+)`/g,"<code>$1</code>")
 .replace(/\*\*([^*]+)\*\*/g,"<b>$1</b>")
 .replace(/\*([^*]+)\*/g,"<i>$1</i>")
 .replace(/\(E\)/g,`<abbr class="est" data-tip="${EST_TIP}">(estimated)</abbr>`)));
function mdToHtml(md){
 const out=[];let list=null,table=null,code=null;
 const closeAll=()=>{if(list){out.push("</ul>");list=null}if(table){out.push("</tbody></table></div>");table=null}};
 for(const raw of md.split("\n")){
  if(code!==null){ if(raw.startsWith("```")){out.push("<pre>"+esc(code.join("\n"))+"</pre>");code=null} else code.push(raw); continue; }
  const l=raw.trimEnd();
  if(l.startsWith("```")){closeAll();code=[];continue}
  const h=l.match(/^(#{1,4}) (.*)/);
  if(h){closeAll();const n=h[1].length;out.push(`<h${n}>${inline(h[2])}</h${n}>`);continue}
  if(/^(-{3,}|\*{3,})$/.test(l)){closeAll();out.push("<hr>");continue}
  if(l.startsWith("|")){
   const cells=l.split("|").slice(1,-1).map(c=>c.trim());
   if(cells.every(c=>/^:?-+:?$/.test(c)))continue;
   if(!table){closeAll();out.push('<div class="twrap"><table><thead><tr>'+cells.map(c=>`<th>${inline(c)}</th>`).join("")+"</tr></thead><tbody>");table=1;continue}
   out.push("<tr>"+cells.map(c=>`<td>${inline(c)}</td>`).join("")+"</tr>");continue;
  }
  const li=l.match(/^[-*] (.*)/);
  if(li){if(table){out.push("</tbody></table></div>");table=null}if(!list){out.push("<ul>");list=1}out.push(`<li>${inline(li[1])}</li>`);continue}
  if(!l){closeAll();continue}
  closeAll();out.push(`<p>${inline(l)}</p>`);
 }
 closeAll();return out.join("\n");
}
