// @requires META,longDate,$,esc
// @defines GH,GH_KIND,GH_LINK,GH_PENDING,GH_REF,GH_STATE_LABEL,GH_TOKEN,LANDED,LINK_ICON,LINK_KIND_LABEL,LINK_KIND_SHORT,REPO,ghRef,ghState,itemClosed,itemState,linkChipHtml,linkChipsHtml,linkIcon,linkKey,linkLabel,linkMd,linkNorm,linksOf,relTime,srcUrl
const REPO=typeof META.repo==="string"?META.repo:"";
let GH_REF=typeof META.ref==="string"?META.ref:"HEAD";
const srcUrl=(path,a,b)=>`https://github.com/${REPO}/blob/${GH_REF}/${path}#L${a}${b?"-L"+b:""}`;
const GH_LINK=/^https:\/\/github\.com\/([\w.-]+)\/([\w.-]+)\/(pull|issues|commit)\/([A-Za-z0-9]+)\/?(?:[?#].*)?$/;
const GH_KIND={pull:"pr",issues:"issue",commit:"commit"};
function linkNorm(l){
 if(typeof l==="string")l={url:l};
 if(!l||typeof l!=="object"||typeof l.url!=="string")return null;
 const url=l.url.trim();
 let u;
 try{u=new URL(url)}catch(e){return null}
 if(u.protocol!=="https:"||!u.hostname)return null;
 const m=GH_LINK.exec(url);
 let gh=null;
 if(m){
  const kind=GH_KIND[m[3]];
  if(kind==="commit"?/^[0-9a-f]{7,40}$/.test(m[4]):/^\d+$/.test(m[4]))gh={o:m[1],r:m[2],kind,ref:m[4],key:m[1]+"/"+m[2]+(kind==="commit"?"@"+m[4].slice(0,7):"#"+m[4])};
 }
 const kind=l.kind||(gh?gh.kind:"doc");
 if(gh&&kind!==gh.kind)return null;
 const label=typeof l.label==="string"&&l.label.trim()?l.label.trim():"";
 return {url,kind,gh,label,closes:l.closes===true&&(kind==="pr"||kind==="issue")};
}
const linkKey=l=>l.gh&&l.gh.kind!=="commit"?l.gh.key:"";
const linksOf=e=>(Array.isArray(e&&e.links)?e.links:[]).map(linkNorm).filter(Boolean);
const ghRef=gh=>gh.o+"/"+gh.r+(gh.kind==="commit"?"@"+gh.ref.slice(0,7):"#"+gh.ref);
function linkLabel(l){
 if(l.label)return l.label;
 if(l.gh)return REPO===l.gh.o+"/"+l.gh.r?ghRef(l.gh).slice(REPO.length):ghRef(l.gh);
 const u=new URL(l.url);
 return u.hostname.replace(/^www\./,"")+u.pathname.replace(/\/$/,"").slice(0,24);
}
const LINK_ICON={
 pr:'<circle cx="18" cy="18" r="3"/><circle cx="6" cy="6" r="3"/><path d="M13 6h3a2 2 0 0 1 2 2v7"/><line x1="6" y1="9" x2="6" y2="21"/>',
 issue:'<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3"/>',
 commit:'<circle cx="12" cy="12" r="3"/><line x1="3" y1="12" x2="9" y2="12"/><line x1="15" y1="12" x2="21" y2="12"/>',
 doc:'<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>'
};
const LINK_KIND_LABEL={pr:"Pull request",issue:"Issue",commit:"Commit",doc:"Link"};
const LINK_KIND_SHORT={pr:"PR",issue:"issue",commit:"commit",doc:"link"};
const GH_STATE_LABEL={open:"Open",draft:"Draft",merged:"Merged",closed:"Closed",unknown:"State unknown"};
const LANDED={merged:1,closed:1};
const linkIcon=kind=>`<svg class="lico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${LINK_ICON[kind]}</svg>`;
const GH=Object.create(null),GH_PENDING=Object.create(null);
let GH_TOKEN=null;
const ghState=l=>{const k=linkKey(l);return k&&GH[k]?GH[k].state:"unknown"};
function linkChipHtml(l){
 const key=linkKey(l);
 return `<a class="lchip" data-kind="${l.kind}"${key?` data-gh="${esc(key)}"`:""} href="${esc(l.url)}" target="_blank" rel="noopener">`+
  (key?`<i class="ldot" data-state="${ghState(l)}" aria-hidden="true"></i>`:"")+linkIcon(l.kind)+
  `<span class="lt">${esc(linkLabel(l))}</span>`+(l.closes?`<span class="lcloses">closes</span>`:"")+`</a>`;
}
const linkChipsHtml=links=>links.length?`<div class="lchips">${links.map(linkChipHtml).join("")}</div>`:"";
const itemClosed=o=>o.s==="closed"||linksOf(o).some(l=>l.closes&&LANDED[ghState(l)]);
function itemState(o){
 if(itemClosed(o))return "closed";
 return linksOf(o).filter(l=>l.closes).map(ghState).find(s=>s!=="unknown")||"open";
}
function linkMd(l){
 const st=ghState(l);
 const bits=[st!=="unknown"?st:"",l.closes?"closes":""].filter(Boolean);
 return linkLabel(l)+(bits.length?" ("+bits.join(", ")+")":"");
}
function relTime(iso){
 const t=Date.parse(iso||"");
 if(!t)return "";
 const s=Math.max(0,(Date.now()-t)/1000);
 if(s<3600)return Math.max(1,Math.round(s/60))+" min ago";
 if(s<86400)return Math.round(s/3600)+" h ago";
 if(s<86400*30)return Math.round(s/86400)+" d ago";
 return longDate(iso.slice(0,10));
}
