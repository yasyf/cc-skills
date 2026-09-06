// @requires 
// @defines $,URL_ATTR,el,esc,fmt,railLink,reduced,safeUrl,scrub
const $=s=>document.querySelector(s);
const el=(t,c,h)=>{const e=document.createElement(t);if(c)e.className=c;if(h!=null)e.innerHTML=h;return e};
const fmt=ms=> ms>=1000?(ms/1000).toFixed(ms>=10000?0:1)+"s" : ms>=1?(ms%1?ms.toFixed(1):ms)+"ms" : (ms*1000)+"µs";
const esc=s=>String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
const railLink=id=>document.querySelector(`#rail a[href="#${id}"]`);
const safeUrl=u=>{const m=/^([a-z][a-z0-9+.\-]*):/i.exec(String(u).replace(/[\s\u0000-\u001f]/g,""));return !m||/^https?$/i.test(m[1])};
const URL_ATTR=/^(href|src|xlink:href)$/i;
const reduced=()=>matchMedia("(prefers-reduced-motion: reduce)").matches;
function scrub(root){
 root.querySelectorAll("*").forEach(n=>{
  [...n.attributes].forEach(a=>{
   if(/^on/i.test(a.name))n.removeAttribute(a.name);
   else if(URL_ATTR.test(a.name)&&!safeUrl(a.value))n.removeAttribute(a.name);
  });
 });
}
