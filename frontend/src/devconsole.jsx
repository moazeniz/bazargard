import React,{useEffect,useState} from 'react';
import {Terminal,X,Download,Copy,PanelBottom,PanelLeft} from 'lucide-react';
import {copyText} from './features.jsx';
import './devconsole.css';

// Developer console (internal testing). The server answers 404 unless DEV_CONSOLE=1, so in
// the public build this renders nothing and registers no shortcut.
const API=new URL('api/dev/',document.baseURI).pathname,OPEN='hamrah-dev-console',STATE='hamrah-dev-console-state';
async function dev(path){const r=await fetch(API+path,{credentials:'same-origin'});let data=null;try{data=await r.json()}catch{}return {status:r.status,data}}
let statusRequest=null;
const devStatus=()=>statusRequest||(statusRequest=dev('status').then(r=>r.status===200?r.data:null).catch(()=>null));
const PREF='hamrah-dev-console-on';
const saved=()=>{try{return localStorage.getItem(PREF)==='1'}catch{return false}};
// Entry points (only when the server enables the console): sidebar switch (default), desktop rail
// item ('rail') and mobile top-bar icon ('icon'). Each is an on/off toggle; the choice is remembered.
export function DevMenuItem({onOpen,variant}){
 const [on,setOn]=useState(false),[active,setActive]=useState(saved);
 useEffect(()=>{devStatus().then(s=>setOn(!!s));const sync=e=>setActive(!!e.detail);window.addEventListener(STATE,sync);return()=>window.removeEventListener(STATE,sync)},[]);
 if(!on)return null;
 const click=()=>{onOpen?.();window.dispatchEvent(new CustomEvent(OPEN,{detail:'toggle'}))};
 const label='کنسول توسعه‌دهنده: '+(active?'روشن':'خاموش');
 if(variant==='rail')return <button className={'rail-item'+(active?' active':'')} role="switch" aria-checked={active} aria-label={label} title={label+' (Ctrl+`)'} onClick={click}><Terminal size={20}/><span>کنسول</span><i className={'devc-dot'+(active?' on':'')}/></button>;
 if(variant==='icon')return <button className={'icon-btn'+(active?' devc-on':'')} role="switch" aria-checked={active} aria-label={label} title={label} onClick={click}><Terminal size={19}/></button>;
 return <button className="devc-switch-row" role="switch" aria-checked={active} onClick={click}><Terminal size={17}/>کنسول توسعه‌دهنده<span className={'devc-switch'+(active?' on':'')} aria-hidden="true"><i/></span><small>{active?'روشن':'خاموش'}</small></button>}

const FILTERS=[['all','همه'],['model','مدل'],['tool','ابزار'],['guard','نگهبان'],['queue','صف و تلاش'],['external','سرویس بیرونی'],['error','خطا']];
const KIND={model:'مدل',tool:'ابزار',guard:'نگهبان',queue:'صف',external:'بیرونی',run:'اجرا'};
const STATUS={completed:'کامل',running:'در حال اجرا',queued:'در صف',retry_queued:'در صف تلاش مجدد',failed:'ناموفق',cancelled:'لغو شد',interrupted:'قطع شد'};
const ms=v=>v==null?'':v<1000?Math.round(v)+'ms':(v/1000).toFixed(v<10000?2:1)+'s';
const num=v=>Number(v||0).toLocaleString('en-US');
const usd=v=>'$'+Number(v).toFixed(v<0.01?5:4);
const isError=e=>e.level==='warn'||e.level==='error'||!!e.error||(e.event==='llm.call'&&e.status!==200)||e.event==='guard.block';
const matches=(e,f)=>f==='all'||(f==='error'?isError(e):e.kind===f);
const pairs=o=>o&&typeof o==='object'?Object.entries(o):[];
const brief=v=>{const s=typeof v==='string'?v:JSON.stringify(v);return s.length>60?s.slice(0,60)+'…':s};
function download(name,value){const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000)}

function describe(e){
 const r=e.result||{},a=e.args||{};
 switch(e.event){
  case 'llm.call':return {title:[e.provider,e.model].filter(Boolean).join(' · '),sub:[e.purpose,ms(e.duration_ms),e.status!==200&&'HTTP '+e.status,e.stop&&'stop: '+e.stop].filter(Boolean).join(' · '),
   chips:['in '+num(e.tokens_in),'out '+num(e.tokens_out),e.tokens_cache_read&&'cache read '+num(e.tokens_cache_read),e.tokens_cache_write&&'cache write '+num(e.tokens_cache_write),e.reasoning_tokens&&'reasoning '+num(e.reasoning_tokens),e.cost!=null?'cost '+usd(e.cost):e.cost_estimate!=null?'≈ '+usd(e.cost_estimate):null,e.nested&&'درون ابزار'].filter(Boolean)};
  case 'llm.fallback':return {title:'جایگزینی خودکار',sub:`${e.model||e.provider} ← ${e.fallback_model||e.fallback_provider} (${e.error_type||'?'})`};
  case 'llm.error':return {title:'خطای مدل',sub:[e.provider,e.model,e.status&&'HTTP '+e.status,e.error].filter(Boolean).join(' · ')};
  case 'tool.call':return {title:(e.label||e.tool)+(e.label?' ('+e.tool+')':''),sub:[ms(e.duration_ms),r.hits!=null&&`${num(r.hits)} نتیجه`+(r.matching_offers!=null?` از ${num(r.matching_offers)}`:''),e.error||r.error].filter(Boolean).join(' · '),
   chips:[a.query&&'«'+a.query+'»',...(Array.isArray(a.queries)?a.queries.map(q=>'«'+(q.query||q)+'»'):[]),...(a.query||a.queries?[]:pairs(a).slice(0,4).map(([k,v])=>k+': '+brief(v))),...pairs(r.filters).map(([k,v])=>k+': '+(typeof v==='object'?JSON.stringify(v):v)),...pairs(r.corrected_terms).map(([k,v])=>'اصلاح: '+k+' → '+v),...(r.relaxed_terms||[]).map(t=>'حذف قید: '+t),...(r.inferred_categories||[]).slice(0,3).map(c=>'دسته: '+(c.name||c))].filter(Boolean).slice(0,14),
   top:(r.top||[]).map(o=>[o.persian_title||o.product_id,o.shop_name,o.effective_price_toman!=null&&num(o.effective_price_toman)+' تومان',o.product_id].filter(Boolean).join(' · '))};
  case 'guard.pass':case 'guard.block':return {title:'نگهبان: '+(e.verdict||'?'),sub:['اقدام: '+(e.action||'?'),e.score!=null&&'امتیاز '+e.score,e.sanitised&&'متن پاک‌سازی شد'].filter(Boolean).join(' · '),chips:[...(Array.isArray(e.flags)?e.flags:[]),e.why].filter(Boolean)};
  case 'external.call':return {title:'سرویس بیرونی: '+(e.upstream||'?'),sub:[e.msg,ms(e.duration_ms),e.attempt&&`تلاش ${e.attempt}/${e.max_attempts||'?'}`,e.backoff_ms&&'انتظار '+ms(e.backoff_ms),e.circuit&&'مدار '+e.circuit].filter(Boolean).join(' · ')};
  case 'chat.retry_queued':return {title:'پیام در صف تلاش مجدد',sub:e.msg};
  case 'chat.early_finish':return {title:'پایان زودهنگام',sub:e.msg};
  case 'run.start':return {title:'پیام کاربر'+(e.mode?' ('+e.mode+')':''),sub:e.message};
  default:return {title:e.event.startsWith('job.')?'صف: '+e.event.slice(4)+(e.queue?' ('+e.queue+')':''):e.event,sub:[e.msg,e.attempt&&`تلاش ${e.attempt}/${e.max_attempts||'?'}`,e.backoff_ms&&'انتظار '+ms(e.backoff_ms),ms(e.duration_ms),e.error].filter(Boolean).join(' · ')};
 }
}

function Json({v,depth=0}){
 if(v===null||typeof v!=='object')return <span className={'j-'+(v===null?'null':typeof v)}>{JSON.stringify(v)}</span>;
 const items=Array.isArray(v)?v.map((x,i)=>[i,x]):Object.entries(v);
 if(!items.length)return <span className="j-null">{Array.isArray(v)?'[]':'{}'}</span>;
 return <details className="j" open={depth<1}><summary>{Array.isArray(v)?`[${items.length}]`:`{${items.length}}`}</summary><div className="j-body">{items.map(([k,x])=><div key={k}><span className="j-key">{k}</span>: <Json v={x} depth={depth+1}/></div>)}</div></details>;
}

function Breakdown({t}){const total=Math.max(1,t.model_ms+t.tool_ms+t.other_ms);return <div className="devc-breakdown"><div className="devc-bar" role="img" aria-label={`مدل ${ms(t.model_ms)}، ابزار ${ms(t.tool_ms)}، سایر ${ms(t.other_ms)}`}>{[['m',t.model_ms],['t',t.tool_ms],['o',t.other_ms]].map(([k,v])=>v>0?<i key={k} className={k} style={{width:(100*v/total)+'%'}}/>:null)}</div><div className="devc-legend"><span><i className="m"/>مدل {ms(t.model_ms)}</span><span><i className="t"/>ابزار {ms(t.tool_ms)}</span><span><i className="o"/>سایر {ms(t.other_ms)}</span>{t.nested_model_ms?<span className="muted">(مدلِ درون ابزار {ms(t.nested_model_ms)})</span>:null}</div></div>}

function Totals({t,hours}){
 const cost=t.cost_reported?usd(t.cost_reported):t.cost_estimate?'≈ '+usd(t.cost_estimate):'—';
 return <section className="devc-totals" aria-label="جمع این گفت‌وگو"><div className="devc-tiles">{[['اجرا',num(t.runs)],['فراخوان مدل',num(t.model_calls)+(t.model_errors?` (${num(t.model_errors)} خطا)`:'')],['ابزار',num(t.tool_calls)+(t.tool_errors?` (${num(t.tool_errors)} خطا)`:'')],['توکن ورودی تازه/خروجی',num(t.tokens_in)+' / '+num(t.tokens_out)],['کش خواندن/نوشتن',num(t.tokens_cache_read)+' / '+num(t.tokens_cache_write)],['هزینه',cost],['جایگزینی',num(t.fallbacks)],['زمان کل',ms(t.wall_ms)]].map(([k,v])=><div key={k}><span>{k}</span><b dir="ltr">{v}</b></div>)}</div><Breakdown t={t}/><p className="devc-note">داده‌ها {num(hours)} ساعت نگه داشته می‌شوند. هزینه «≈» تخمین با قیمت فهرست است.</p></section>;
}

function TraceEvent({e,onCopy}){
 const [json,setJson]=useState(false);const d=describe(e);
 return <li className={'devc-ev k-'+e.kind+(isError(e)?' err':'')}><span className="devc-t" dir="ltr" title={'پایان: +'+(ms(e.t_ms)||'0ms')}>+{ms(e.start_ms??e.t_ms)||'0ms'}</span><span className="devc-kind">{KIND[e.kind]||e.kind}</span><div className="devc-body">
  <div className="devc-title" dir="auto">{d.title}</div>{d.sub?<div className="devc-sub" dir="auto">{d.sub}</div>:null}
  {d.chips?.length?<div className="devc-chips">{d.chips.map((c,i)=><span key={i} dir="auto">{c}</span>)}</div>:null}
  {d.top?.length?<ol className="devc-top">{d.top.map((x,i)=><li key={i} dir="auto">{x}</li>)}</ol>:null}
  <div className="devc-actions"><button onClick={()=>setJson(v=>!v)} aria-expanded={json}>{json?'بستن JSON':'JSON'}</button><button onClick={()=>onCopy(e)}><Copy size={12}/>کپی</button></div>
  {json?<div className="devc-json" dir="ltr"><Json v={e}/></div>:null}
 </div></li>;
}

function Run({run,filter,first,onCopy}){
 const events=run.events.filter(e=>matches(e,filter)),t=run.totals;
 return <details className={'devc-run s-'+run.status} open={first}><summary><span className="devc-status">{STATUS[run.status]||run.status}</span><span className="devc-when" dir="ltr">{new Date(run.created*1000).toLocaleTimeString('fa-IR')}</span><span className="devc-msg" dir="auto">{run.message||run.run_id.slice(0,8)}</span><span className="devc-meta" dir="ltr">{ms(t.wall_ms)} · {num(t.tokens_in+t.tokens_out)} tok</span></summary>
  <div className="devc-run-body"><div className="devc-run-actions"><code dir="ltr">{run.run_id.slice(0,12)}…</code><button onClick={()=>onCopy(run)}><Copy size={12}/>کپی اجرا</button><button onClick={()=>download('bazargard-run-'+run.run_id.slice(0,10)+'.json',run)}><Download size={12}/>خروجی JSON</button></div>
   <Breakdown t={t}/>
   {events.length?<ol className="devc-events">{events.map((e,i)=><TraceEvent key={i} e={e} onCopy={onCopy}/>)}</ol>:<p className="devc-empty">{run.events.length?'رویدادی با این فیلتر نیست.':'رویدادی ثبت نشده است.'}</p>}
  </div></details>;
}

export function DevConsole({sid,live}){
 const [status,setStatus]=useState(null),[open,setOpen]=useState(saved),[data,setData]=useState(null),[error,setError]=useState(''),[filter,setFilter]=useState('all'),[note,setNote]=useState('');
 const [dock,setDock]=useState(()=>{try{return localStorage.getItem('hamrah-dev-dock')==='bottom'?'bottom':'side'}catch{return 'side'}});
 useEffect(()=>{devStatus().then(setStatus)},[]);
 useEffect(()=>{if(!status)return;const toggle=()=>setOpen(o=>!o),key=e=>{if(e.ctrlKey&&(e.code==='Backquote'||e.key==='`')){e.preventDefault();toggle()}};
  window.addEventListener(OPEN,toggle);window.addEventListener('keydown',key);return()=>{window.removeEventListener(OPEN,toggle);window.removeEventListener('keydown',key)}},[status]);
 useEffect(()=>{if(!status)return;try{localStorage.setItem(PREF,open?'1':'0')}catch{}window.dispatchEvent(new CustomEvent(STATE,{detail:open}))},[open,status]);
 useEffect(()=>{if(!status||!open||!sid)return;let stop=false,timer;
  const load=async()=>{const r=await dev('traces?session_id='+encodeURIComponent(sid)).catch(()=>({status:0}));if(stop)return;
   if(r.status===200){setData(r.data);setError('')}else{setError(r.status===404?'ردگیری این گفت‌وگو در دسترس نیست.':'دریافت ردگیری انجام نشد؛ دوباره تلاش می‌شود.')}
   timer=setTimeout(load,live||r.data?.runs?.some(x=>x.live)?1200:5000)};
  load();return()=>{stop=true;clearTimeout(timer)}},[status,open,sid,live]);
 useEffect(()=>{if(!note)return;const t=setTimeout(()=>setNote(''),1600);return()=>clearTimeout(t)},[note]);
 if(!status||!open)return null;
 const copy=async v=>{try{await copyText(JSON.stringify(v,null,2));setNote('کپی شد')}catch{setNote('کپی انجام نشد')}};
 const switchDock=()=>{const next=dock==='side'?'bottom':'side';setDock(next);try{localStorage.setItem('hamrah-dev-dock',next)}catch{}};
 const all=data?.runs?.flatMap(r=>r.events)||[];
 return <aside className={'devc devc-'+dock} dir="rtl" aria-label="کنسول توسعه‌دهنده">
  <header className="devc-head"><Terminal size={16}/><b>کنسول توسعه‌دهنده</b>{live||data?.runs?.some(r=>r.live)?<span className="devc-live">زنده</span>:null}<span className="devc-grow"/>
   {data?<button title="خروجی JSON همه اجراها" aria-label="خروجی JSON همه اجراها" onClick={()=>download('bazargard-session-'+(sid||'').slice(0,8)+'.json',data)}><Download size={16}/></button>:null}
   <button className="devc-dock" title={dock==='side'?'نمایش در پایین صفحه':'نمایش در کنار صفحه'} aria-label="تغییر جای کنسول" onClick={switchDock}>{dock==='side'?<PanelBottom size={16}/>:<PanelLeft size={16}/>}</button>
   <button title="خاموش کردن کنسول (Ctrl+`)" aria-label="خاموش کردن کنسول" onClick={()=>setOpen(false)}><X size={16}/></button></header>
  <div className="devc-filters">{FILTERS.map(([k,label])=><button key={k} className={filter===k?'on':''} aria-pressed={filter===k} onClick={()=>setFilter(k)}>{label}<span>{num(all.filter(e=>matches(e,k)).length)}</span></button>)}</div>
  <div className="devc-scroll">
   {error?<p className="devc-error" role="alert">{error}</p>:null}
   {data?<Totals t={data.totals} hours={data.retention_hours}/>:<p className="devc-empty">در حال دریافت…</p>}
   {data?.runs?.length?data.runs.map((run,i)=><Run key={run.run_id} run={run} filter={filter} first={i===0} onCopy={copy}/>):data?<p className="devc-empty">هنوز اجرایی در این گفت‌وگو ثبت نشده؛ پیامی بفرستید.</p>:null}
  </div>
  {note?<div className="devc-note-toast" role="status">{note}</div>:null}
 </aside>;
}
