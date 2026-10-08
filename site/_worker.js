const H={"Content-Type":"application/json; charset=utf-8","Cache-Control":"no-store"};
const STOP=new Set("a an and are as at be been but by can for from had has have how if in into is it its may more most no not of on one or our so than that the their them then there they this those to too up us was we were what when where which who why will with you your com www home page website site get use using new best".split(" "));
export default{async fetch(request,env){
  const path=new URL(request.url).pathname;
  if(!path.startsWith("/api/seo/"))return env.ASSETS.fetch(request);
  if(request.method!=="POST")return out({ok:false,error:"仅支持 POST"},405);
  const origin=request.headers.get("Origin");
  if(origin&&new URL(origin).host!==new URL(request.url).host)return out({ok:false,error:"仅允许本站调用"},403);
  try{
    const input=await request.json();
    if(path==="/api/seo/audit")return out(await audit(input));
    if(path==="/api/seo/ideas")return out(await ideas(input,request.url,env));
    if(path==="/api/seo/site-keywords")return out(await siteKeywords(input));
    if(path==="/api/seo/history")return out(await history(input));
    return out({ok:false,error:"工具不存在"},404);
  }catch(e){return out({ok:false,error:e.message||"处理失败"},400)}
}};
function out(body,status=200){return new Response(JSON.stringify(body),{status,headers:H})}
function safeUrl(raw){
  raw=String(raw||"").trim();if(!/^https?:\/\//i.test(raw))raw="https://"+raw;
  const u=new URL(raw),h=u.hostname.toLowerCase().replace(/^\[|\]$/g,"");
  if(!["http:","https:"].includes(u.protocol))throw Error("只支持 http/https 地址");
  if(h==="localhost"||h.endsWith(".local")||h==="::1"||h==="0.0.0.0"||/^127\./.test(h)||/^10\./.test(h)||/^192\.168\./.test(h)||/^169\.254\./.test(h)||/^172\.(1[6-9]|2\d|3[01])\./.test(h)||/^f[cd][0-9a-f]{2}:/i.test(h))throw Error("不允许访问本地或内网地址");
  return u;
}
async function fetchHtml(raw){
  const u=raw instanceof URL?raw:safeUrl(raw);
  const r=await fetch(u.toString(),{redirect:"follow",headers:{Accept:"text/html,application/xhtml+xml","User-Agent":"TrendRadarSEO/1.0"},cf:{cacheTtl:900,cacheEverything:true}});
  if(!r.ok)throw Error("页面返回 HTTP "+r.status);
  if(!/html|xhtml/i.test(r.headers.get("content-type")||""))throw Error("目标不是 HTML 页面");
  if(Number(r.headers.get("content-length")||0)>2000000)throw Error("页面超过 2 MB，暂不分析");
  return{html:(await r.text()).slice(0,1000000),url:new URL(r.url),status:r.status};
}
function tags(html,name){return html.match(new RegExp("<"+name+"\\b[^>]*>","gi"))||[]}
function attr(tag,name){const m=tag.match(new RegExp("\\s"+name+"\\s*=\\s*(?:\"([^\"]*)\"|'([^']*)'|([^\\s>]+))","i"));return m?(m[1]??m[2]??m[3]??""):""}
function decode(s){return String(s||"").replace(/&#(\d+);/g,(_,n)=>String.fromCodePoint(+n)).replace(/&#x([0-9a-f]+);/gi,(_,n)=>String.fromCodePoint(parseInt(n,16))).replace(/&nbsp;/gi," ").replace(/&amp;/gi,"&").replace(/&quot;/gi,'"').replace(/&#39;|&apos;/gi,"'").replace(/&lt;/gi,"<").replace(/&gt;/gi,">")}
function clean(s){return decode(String(s||"").replace(/<script\b[\s\S]*?<\/script>/gi," ").replace(/<style\b[\s\S]*?<\/style>/gi," ").replace(/<svg\b[\s\S]*?<\/svg>/gi," ").replace(/<[^>]+>/g," ").replace(/\s+/g," ").trim())}
function contents(html,name){const a=[],re=new RegExp("<"+name+"\\b[^>]*>([\\s\\S]*?)<\\/"+name+">","gi");let m;while((m=re.exec(html)))a.push(clean(m[1]));return a}
function meta(html,key){for(const t of tags(html,"meta"))if((attr(t,"name")||attr(t,"property")).toLowerCase()===key)return clean(attr(t,"content"));return""}
function linked(html,rel){for(const t of tags(html,"link"))if(attr(t,"rel").toLowerCase().split(/\s+/).includes(rel))return attr(t,"href");return""}
function pageData(html,url){
  const title=contents(html,"title")[0]||"",description=meta(html,"description"),h1=contents(html,"h1"),h2=contents(html,"h2"),text=clean(html),images=tags(html,"img");
  let internal=0,external=0;
  for(const t of tags(html,"a"))try{const u=new URL(attr(t,"href"),url);if(/^https?:$/.test(u.protocol))u.hostname===url.hostname?internal++:external++}catch(_){}
  return{title,description,h1,h2,text,robots:meta(html,"robots"),canonical:linked(html,"canonical"),words:(text.match(/[\p{L}\p{N}]+/gu)||[]).length,images:images.length,missingAlt:images.filter(t=>!attr(t,"alt").trim()).length,internal,external,schema:(html.match(/application\/ld\+json/gi)||[]).length};
}
function check(status,item,value,advice){return{status,item,value:String(value),advice}}
async function audit(input){
  const keyword=String(input.keyword||"").trim().toLowerCase();if(!keyword)throw Error("请填写目标关键词");
  const f=await fetchHtml(input.url),d=pageData(f.html,f.url),has=v=>String(v||"").toLowerCase().includes(keyword);
  const checks=[
    check("pass","页面可访问",f.status,"保持 200 状态"),
    check(/\bnoindex\b/i.test(d.robots)?"fail":"pass","允许索引",d.robots||"未发现 noindex","移除 noindex 后再提交收录"),
    check(d.title&&d.title.length>=25&&d.title.length<=65?"pass":d.title?"warn":"fail","Title 长度",d.title?d.title.length+" 字符":"缺失","建议约 25–65 字符"),
    check(has(d.title)?"pass":"fail","Title 包含目标词",d.title||"缺失","自然加入目标关键词"),
    check(d.description&&d.description.length>=70&&d.description.length<=170?"pass":d.description?"warn":"fail","Description",d.description?d.description.length+" 字符":"缺失","建议约 70–170 字符并说明页面价值"),
    check(d.h1.length===1?"pass":d.h1.length?"warn":"fail","H1 数量",d.h1.length,"保留一个清晰主标题"),
    check(d.h1.some(has)?"pass":"warn","H1 包含目标词",d.h1.join(" | ")||"缺失","让主标题与搜索意图一致"),
    check(d.h2.length?"pass":"warn","H2 结构",d.h2.length,"用 H2 划分主要问题和答案"),
    check(d.words>=300?"pass":d.words>=120?"warn":"fail","可见正文",d.words+" 词","补齐用户完成任务所需的信息"),
    check(d.missingAlt===0?"pass":"warn","图片替代文本",d.images+" 张，"+d.missingAlt+" 张缺失","为有信息价值的图片补充 alt"),
    check(d.canonical?"pass":"warn","Canonical",d.canonical||"缺失","声明规范 URL"),
    check(d.internal?"pass":"warn","内部链接",d.internal,"加入相关页面的可抓取链接"),
    check(d.schema?"pass":"warn","结构化数据",d.schema,"仅在内容真实支持时添加 Schema")
  ];
  const points=checks.reduce((n,x)=>n+(x.status==="pass"?2:x.status==="warn"?1:0),0);
  return{ok:true,tool:"On Page SEO 体检",source:"目标页面实时 HTML",data:{summary:{observedAt:new Date().toISOString(),metricDefinition:"页面 HTML 规则检查分，不是 Google 排名分",score:Math.round(points/checks.length/2*100),url:f.url.toString(),keyword,title:d.title,description:d.description,words:d.words,internalLinks:d.internal,externalLinks:d.external},checks}};
}
function intent(k){if(/\b(buy|price|pricing|coupon|deal|order|hire|download)\b/i.test(k))return"交易";if(/\b(best|top|review|alternative|vs|compare)\b/i.test(k))return"商业调查";if(/\b(login|official|website|app)\b/i.test(k))return"导航";return"信息"}
function sleep(ms){return new Promise(resolve=>setTimeout(resolve,ms))}
async function askSuggestion(query,gl,source){
  const endpoint=source==="Google"
    ?"https://suggestqueries.google.com/complete/search?client=firefox&hl=en&gl="+encodeURIComponent(gl)+"&q="+encodeURIComponent(query)
    :"https://api.bing.com/osjson.aspx?market=en-US&query="+encodeURIComponent(query);
  let status="network";
  for(let attempt=0;attempt<2;attempt++){
    try{
      const response=await fetch(endpoint,{headers:{Accept:"application/json","User-Agent":"Mozilla/5.0 TrendRadarSEO/1.0"},cf:{cacheTtl:86400,cacheEverything:true}});
      status=response.status;
      if(response.ok){const payload=await response.json();return{items:Array.isArray(payload[1])?payload[1]:[],status}}
      if(response.status!==429&&response.status<500)break;
    }catch(e){status=e.name||"network"}
    if(attempt===0)await sleep(600);
  }
  return{items:[],status};
}
function extraQueries(seed,mode){
  if(mode==="questions")return["how to "+seed,"what is "+seed,"why "+seed];
  if(mode==="commercial")return["best "+seed,seed+" alternative",seed+" vs"];
  return[seed+" for",seed+" online"];
}
function ruleIdeas(seeds,mode,limit){
  const suffix=mode==="questions"?["how to ","what is ","why "]:mode==="commercial"?["best ","compare "," alternatives"]:[" free"," online"," tool"," for beginners"];
  const rows=[];
  for(const seed of seeds)for(const part of suffix){const keyword=(part.endsWith(" ")?part+seed:seed+part).trim().toLowerCase();rows.push({keyword,intent:intent(keyword),source:"规则生成"});if(rows.length>=limit)return rows}
  return rows;
}
async function ideas(input,requestUrl,env){
  const seeds=Array.from(new Set((input.seeds||[]).map(x=>String(x).trim()).filter(Boolean))).slice(0,3);if(!seeds.length)throw Error("至少填写一个种子词");
  const mode=["suggestions","questions","commercial"].includes(input.mode)?input.mode:"suggestions",gl=String(input.gl||"us").replace(/[^a-z]/gi,"").slice(0,2)||"us",limit=Math.min(50,Math.max(10,+input.limit||25));
  const cache=caches.default,keyUrl=new URL("/__seo_cache/ideas",requestUrl);keyUrl.searchParams.set("q",seeds.join("|"));keyUrl.searchParams.set("mode",mode);keyUrl.searchParams.set("gl",gl);keyUrl.searchParams.set("limit",String(limit));
  const key=new Request(keyUrl.toString()),saved=await cache.match(key);let stale=null;
  if(saved){stale=await saved.json();const age=Date.now()-Date.parse(stale.data.summary.observedAt);if(age<86400000){stale.data.summary.cache="24 小时缓存";return stale}}
  const failures=[],seen=new Set(),items=[],sources=new Set(),localSeeds=new Set();
  if(mode==="suggestions")try{
    const response=await env.ASSETS.fetch(new Request(new URL("/data/seo-suggestions.json",requestUrl)));
    if(response.ok){
      const database=await response.json();
      for(const seed of seeds){
        const record=database.items?.[gl+"|"+seed.toLowerCase()];
        if(!record||Date.now()-Date.parse(record.fetched_at)>604800000)continue;
        localSeeds.add(seed);sources.add("本地 Google");
        for(const raw of record.items||[]){const keyword=clean(raw).toLowerCase();if(keyword&&!seen.has(keyword)){seen.add(keyword);items.push({keyword,intent:intent(keyword),source:"本地 Google 自动补全"});if(items.length>=limit)break}}
      }
    }
  }catch(_){}
  const circuitKey=new Request(new URL("/__seo_cache/circuit/google",requestUrl).toString()),googlePaused=Boolean(await cache.match(circuitKey));
  async function collect(query){
    const order=googlePaused?["Bing"]:["Google","Bing"];
    for(const source of order){
      const result=await askSuggestion(query,gl,source);
      if(result.status===429&&source==="Google")await cache.put(circuitKey,new Response("paused",{headers:{"Cache-Control":"public,max-age=900"}}));
      if(result.items.length){
        sources.add(source);
        for(const raw of result.items){const keyword=clean(raw).toLowerCase();if(keyword&&!seen.has(keyword)){seen.add(keyword);items.push({keyword,intent:intent(keyword),source:source+" 自动补全"});if(items.length>=limit)return}}
        return;
      }
      failures.push({source,query,status:result.status});
    }
  }
  for(let i=0;i<seeds.length&&items.length<limit;i++){if(!localSeeds.has(seeds[i]))await collect(seeds[i]);if(i<seeds.length-1&&!localSeeds.has(seeds[i]))await sleep(500)}
  if(items.length<limit){const more=seeds.flatMap(seed=>extraQueries(seed,mode));for(let i=0;i<more.length&&items.length<limit;i++){await collect(more[i]);if(i<more.length-1)await sleep(500)}}
  if(!items.length&&stale){stale.data.summary.cache="过期缓存兜底";stale.data.summary.failures=failures.length;stale.data.summary.upstreamStatuses=failures;return stale}
  const finalItems=items.length?items:ruleIdeas(seeds,mode,limit),source=items.length?Array.from(sources).join(" + ")+" 自动补全":"规则生成（上游均失败）";
  const payload={ok:true,tool:"关键词拓展",source,data:{summary:{observedAt:new Date().toISOString(),metricDefinition:items.length?"自动补全候选词，不是搜索量":"规则组合，不是外部采集数据",cache:items.length?"已缓存 7 天；24 小时内直接使用":"未缓存",seeds,mode,country:gl,count:finalItems.length,failures:failures.length,upstreamStatuses:failures},items:finalItems}};
  if(items.length)await cache.put(key,new Response(JSON.stringify(payload),{headers:{"Content-Type":"application/json","Cache-Control":"public,max-age=604800"}}));
  return payload;
}
function tokens(text){return(clean(text).toLowerCase().match(/[\p{L}\p{N}][\p{L}\p{N}'-]*/gu)||[]).map(x=>x.replace(/^[-']+|[-']+$/g,"")).filter(x=>x.length>1&&!STOP.has(x))}
function addTerms(map,text,weight,page,phrases){
  const a=tokens(text).slice(0,5000),max=phrases?3:1;
  for(let n=1;n<=max;n++)for(let i=0;i<=a.length-n;i++){const term=a.slice(i,i+n).join(" ");if(!term||(n===1&&/^\d+$/.test(term)))continue;const v=map.get(term)||{score:0,pages:new Set()};v.score+=weight*(n===1?1:n);v.pages.add(page);map.set(term,v)}
}
async function sitemapUrls(base){
  try{
    let r=await fetch(new URL("/sitemap.xml",base),{headers:{"User-Agent":"TrendRadarSEO/1.0"},cf:{cacheTtl:3600,cacheEverything:true}});if(!r.ok)return[];
    let xml=(await r.text()).slice(0,500000),urls=Array.from(xml.matchAll(/<loc>\s*([^<]+)\s*<\/loc>/gi),m=>decode(m[1]));
    if(/<sitemapindex\b/i.test(xml)&&urls[0]){r=await fetch(urls[0],{cf:{cacheTtl:3600,cacheEverything:true}});if(r.ok){xml=(await r.text()).slice(0,500000);urls=Array.from(xml.matchAll(/<loc>\s*([^<]+)\s*<\/loc>/gi),m=>decode(m[1]))}}
    return urls.filter(raw=>{try{return safeUrl(raw).hostname===base.hostname}catch(_){return false}});
  }catch(_){return[]}
}
async function siteKeywords(input){
  const target=safeUrl(input.target),limit=Math.min(100,Math.max(10,+input.limit||50));let urls=[target.toString()];
  if(target.pathname==="/"||!target.pathname)urls=[target.origin+"/",...(await sitemapUrls(target))];
  urls=Array.from(new Set(urls)).slice(0,5);
  const pages=(await Promise.all(urls.map(async u=>{try{return await fetchHtml(u)}catch(_){return null}}))).filter(Boolean);if(!pages.length)throw Error("没有抓取到可分析的 HTML 页面");
  const map=new Map();for(const p of pages){const d=pageData(p.html,p.url),u=p.url.toString();addTerms(map,d.title,8,u,true);d.h1.forEach(x=>addTerms(map,x,6,u,true));d.h2.forEach(x=>addTerms(map,x,3,u,true));addTerms(map,d.text,1,u,false)}
  const items=Array.from(map,([keyword,v])=>({keyword,score:Math.round(v.score),pages:v.pages.size})).filter(x=>x.score>=2).sort((a,b)=>b.score-a.score||b.pages-a.pages||b.keyword.split(" ").length-a.keyword.split(" ").length).slice(0,limit);
  return{ok:true,tool:"站内页面出词",source:"首页/指定页与公开 sitemap",data:{summary:{observedAt:new Date().toISOString(),metricDefinition:"页面词频与标题权重，不是搜索量或排名",target:target.toString(),pagesAnalyzed:pages.length,count:items.length,note:"分数是页面出现与标题权重，不是搜索量或 Google 排名"},items,pages:pages.map(x=>x.url.toString())}};
}

function crawlDate(s){return s&&s.length>=8?s.slice(0,4)+"-"+s.slice(4,6)+"-"+s.slice(6,8):s||""}
async function history(input){
  const target=safeUrl(input.target),limit=Math.min(100,Math.max(10,+input.limit||50));
  const collections=await fetch("https://index.commoncrawl.org/collinfo.json",{cf:{cacheTtl:86400,cacheEverything:true}}).then(r=>r.ok?r.json():[]);
  const latest=collections[0];if(!latest||!latest["cdx-api"])throw Error("暂时无法读取 Common Crawl 索引");
  const api=new URL(latest["cdx-api"]);api.searchParams.set("url",target.hostname);api.searchParams.set("matchType","domain");api.searchParams.set("output","json");api.searchParams.append("filter","status:200");api.searchParams.append("filter","mime:text/html");api.searchParams.set("collapse","urlkey");api.searchParams.set("limit",String(limit));
  const response=await fetch(api,{headers:{"User-Agent":"TrendRadarSEO/1.0"},cf:{cacheTtl:86400,cacheEverything:true}});
  if(!response.ok)throw Error("Common Crawl 返回 HTTP "+response.status);
  const items=(await response.text()).trim().split("\n").filter(Boolean).slice(0,limit).map(line=>{const x=JSON.parse(line);return{url:x.url,crawledAt:crawlDate(x.timestamp),status:x.status,mime:x.mime,length:Number(x.length||0),digest:x.digest||""}});
  return{ok:true,tool:"公开网页历史",source:"Common Crawl URL Index",data:{summary:{observedAt:new Date().toISOString(),metricDefinition:"Common Crawl 收录记录，不代表 Google 收录或排名",target:target.hostname,crawl:latest.id,crawlWindow:latest.from+" — "+latest.to,count:items.length},items}};
}

if(intent("best image editor")!=="商业调查"||tokens("the useful image tool").includes("the"))throw Error("SEO helper self-check failed");