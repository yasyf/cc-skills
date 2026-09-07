// @requires $
// @defines AI_CFG_KEY,AI_LOOPBACK,GH_CFG_KEY,aiEndpoint,cfgNormalise,siteCfg,siteConfig
const AI_CFG_KEY="design-doc-ai";
const GH_CFG_KEY="design-doc-github";
const AI_LOOPBACK=/^(localhost|127\.0\.0\.1|\[::1\])$/;
function aiEndpoint(raw){
 if(!raw)return null;
 let u;
 try{u=new URL(raw)}catch(e){return null}
 if(u.protocol!=="https:"&&!(u.protocol==="http:"&&AI_LOOPBACK.test(u.hostname)))return null;
 return u.href.replace(/\/+$/,"");
}
function cfgNormalise(j){
 if(!j||typeof j!=="object")return {ai:null,github:null,comments:null};
 const s=k=>typeof j[k]==="string"&&j[k].trim()?j[k].trim():null;
 let ai=null;
 if(j.disabled===true)ai={disabled:true};
 else{
  const endpoint=aiEndpoint(s("endpoint")),model=s("model"),key=s("key"),reasoning=s("reasoning");
  if(endpoint&&model&&key)ai={endpoint,model,key,...(reasoning?{reasoning}:{})};
 }
 const g=j.github;
 const token=g&&typeof g==="object"&&typeof g.token==="string"&&g.token.trim()?g.token.trim():null;
 const c=j.comments&&typeof j.comments==="object"?j.comments:null;
 const comments=c?{repo:typeof c.repo==="string"&&/^[\w.-]+\/[\w.-]+$/.test(c.repo)?c.repo:null,forbiddenTerms:Array.isArray(c.forbiddenTerms)?c.forbiddenTerms.filter(t=>typeof t==="string"&&t):[]}:null;
 return {ai,github:token?{token}:null,comments};
}
let siteCfg=null;
function siteConfig(){
 if(!siteCfg)siteCfg=(async()=>{
  const out={ai:null,github:null,comments:null};
  const take=c=>{out.ai=out.ai||c.ai;out.github=out.github||c.github;out.comments=out.comments||c.comments};
  try{const raw=localStorage.getItem(AI_CFG_KEY);if(raw)take({ai:cfgNormalise(JSON.parse(raw)).ai})}catch(e){}
  try{const raw=localStorage.getItem(GH_CFG_KEY);if(raw)take({github:cfgNormalise({github:JSON.parse(raw)}).github})}catch(e){}
  for(const path of ["../ai.json","ai.json"]){
   if(out.ai&&out.github&&out.comments)break;
   let j=null;
   try{const r=await fetch(path);if(!r.ok)continue;j=await r.json()}catch(e){continue}
   take(cfgNormalise(j));
  }
  return {ai:out.ai&&!out.ai.disabled?out.ai:null,github:out.github,comments:out.comments};
 })();
 return siteCfg;
}
