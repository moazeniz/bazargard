"""Category-agnostic comparison harmonization and web completion.

Catalogue keys differ wildly between products (87k distinct keys over 3.4k
categories), so alias tables cannot keep up. One model call plans a shared
row schema for the compared products, one call per product maps its numbered
raw specifications (and title) onto those rows, and one call per fetched web
page extracts only still-missing rows. Every value must cite a verbatim quote
that is re-checked here against the cited source; numbers must appear in the
quote (unit conversions allowed). Raw catalogue data is never modified.
Pages are found with BoxAPI web search and fetched directly (most spec sites
are reachable from Iran). When pages stay unreadable, one web-search answer
may fill a gap, but such values are labelled search_summary with their link.
"""
import asyncio,copy,hashlib,html,json,os,re,sqlite3,time
from pathlib import Path
from urllib.parse import urlencode,urlsplit,urljoin,parse_qs
import httpx
from catalog import normalize
from public_research import fetch,host_in
import guard
import llm

VERSION='harmonize-1-'+hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:10]
MISSING='در داده موجود نیست'
UNITS={'GB':'گیگابایت','kg':'کیلوگرم','g':'گرم','inch':'اینچ','Hz':'هرتز','Wh':'وات‌ساعت','mAh':'میلی‌آمپرساعت','mm':'میلی‌متر','cm':'سانتی‌متر','m':'متر','mm2':'میلی‌متر مربع',
       'V':'ولت','A':'آمپر','W':'وات','L':'لیتر','bar':'بار','ohm':'اهم','MP':'مگاپیکسل','dB':'دسی‌بل','lm':'لومن','K':'کلوین','rpm':'دور در دقیقه','ms':'میلی‌ثانیه','h':'ساعت','GHz':'گیگاهرتز','core':'هسته'}
MAX_ENTRIES=260;MAX_KEYS=450;WEB_ROUNDS=1;WEB_PAGES=6;WEB_SECONDS=90;PAGE_CHARS=22000
SKIP_HOSTS={'duckduckgo.com','bing.com','google.com','youtube.com','facebook.com','instagram.com','twitter.com','x.com','pinterest.com','tiktok.com','reddit.com','aparat.com','t.me','linkedin.com'}
PREFERRED={'gsmarena.com':1,'notebookcheck.net':1,'icecat.biz':1,'devicespecifications.com':1,'productz.com':2,'versus.com':3,'rtings.com':2,'soundguys.com':2,'displayspecifications.com':1,'laptopmedia.com':2}
MANUFACTURERS={'hp.com','lenovo.com','msi.com','asus.com','dell.com','acer.com','apple.com','samsung.com','sony.com','mi.com','xiaomi.com','lg.com','huawei.com','honor.com','microsoft.com','jbl.com','anker.com','bose.com','philips.com','bosch-home.com','delonghi.com','tefal.com','braun.com','panasonic.com','canon.com','nikon.com','hikvision.com','dahuasecurity.com','schneider-electric.com','se.com','tp-link.com','gigabyte.com','razer.com','logitech.com','realme.com','oneplus.com','nokia.com','motorola.com','oppo.com','vivo.com','pentax.com','fujifilm.com','gopro.com','dji.com','makita.com','boschtools.com','dewalt.com','grundfos.com','pedrollo.com','honda.com'}

FA_DIGITS=str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩٫٬','01234567890123456789.,')
FACTORS=(1,1000,.001,1024,1/1024,10,.1,100,.01,2.54,1/2.54,25.4,1/25.4,1e6,1e-6)

def text(value):return html.unescape(str(value if value is not None else '')).strip()
def fold(value):
 s=text(value).translate(FA_DIGITS).replace('‌',' ').replace('‏','').replace('‎','').replace('ي','ی').replace('ك','ک').replace('×','x').lower()
 return re.sub(r'\s+',' ',s).strip()
def squash(value):return re.sub(r'[\s\-_/.,:;()\[\]"\'«»]+','',fold(value))
def numbers(value):
 s=fold(value);s=re.sub(r'(?<=\d),(?=\d{3}(?!\d))','',s);s=s.replace(',','.')
 out=[]
 for m in re.findall(r'\d+(?:\.\d+)?',s):
  try:out.append(float(m))
  except ValueError:pass
 return out
def number_supported(n,quote):
 if n is None:return True
 try:n=float(n)
 except (TypeError,ValueError):return False
 for x in numbers(quote):
  for f in FACTORS:
   if abs(x*f-n)<=max(abs(n)*.012,1e-9):return True
 return False
def quote_in(quote,source):
 q=fold(quote)
 if len(q)<1:return False
 return q in fold(source) or (len(squash(quote))>=2 and squash(quote) in squash(source))

def gateway_headers():
 token=os.getenv('WEB_GATEWAY_TOKEN','')
 return {'Authorization':'Bearer '+token} if token else {}

def gateway_fetch(url,deadline):
 """Direct SSRF-guarded fetch; an egress gateway is used only when
 WEB_GATEWAY_URL is explicitly configured (optional, not needed on Liara)."""
 base=os.getenv('WEB_GATEWAY_URL','')
 seconds=max(1.0,min(25.0,deadline-time.monotonic()))
 if base:
  try:
   r=httpx.get(base+'/fetch',params={'url':url,'seconds':round(seconds,1),'pdf':'1'},headers=gateway_headers(),timeout=seconds+8)
   if r.status_code==200:return r.json()
  except (httpx.HTTPError,ValueError):pass
 return fetch(url,deadline,allow_pdf=True)

_LOCKS={}
def search_lock():
 # One lock per event loop (tests and workers may run separate loops).
 loop=asyncio.get_running_loop()
 return _LOCKS.setdefault(id(loop),asyncio.Lock())

SEARCH_PROMPT='Find official, manufacturer or specification-database pages listing the technical specifications of exactly this product: {q}. Answer with the best page URLs only, one per line, most authoritative first.'
async def model_search(query,client_factory=None,prompt=SEARCH_PROMPT):
 """Page discovery through BoxAPI web search. Returns result URLs only; the
 pages themselves are fetched and verified locally."""
 if not llm.configured() or os.getenv('SPEC_MODEL_SEARCH','1')=='0':return []
 try:found=await llm.web_search(prompt.format(q=query),max_tool_calls=2,context='low',effort='low',max_tokens=1200,timeout=60,purpose='page_search',client_factory=client_factory)
 except llm.LLMError as e:
  print('spec_model_search',e.kind,e.status,flush=True);return []
 named=re.findall(r'https://[^\s)\]>"\'`]+',found['text'])
 urls=[u.rstrip('.,;') for u in named]+[c['url'] for c in found['citations']]+[u for u in found['sources'] if u.startswith('https://')]
 return list(dict.fromkeys(u.split('#')[0].replace('?utm_source=openai','') for u in urls))[:12]

def configured_model():return os.getenv('SPEC_MODEL') or llm.model()

async def ask(system,payload,max_tokens=3000,timeout=90,client_factory=None):
 """Single JSON-returning BoxAPI call (JSON mode; keys rotate and fail over
 inside llm); None when unavailable or malformed."""
 if not llm.configured():return None
 payload=guard.scrub(payload)  # catalogue/web/review text is untrusted data
 body={'model':configured_model(),'max_tokens':max_tokens,'system':system,'messages':[{'role':'user','content':json.dumps(payload,ensure_ascii=False)}]}
 for attempt in range(2):
  try:
   data=await llm.chat(body,json_mode=True,timeout=timeout,purpose='json:'+system[:24].split('\n')[0],client_factory=client_factory)
   parsed=llm.parse_json('\n'.join(b.get('text','') for b in data.get('content',[]) if b.get('type')=='text'))
   if isinstance(parsed,dict) or attempt:return parsed
  except llm.LLMError as error:
   print('spec_model_request',error.kind,error.status,flush=True);return None
  except (ValueError,TypeError,KeyError) as error:
   print('spec_model_request_error',type(error).__name__,flush=True)
   if attempt:return None
 return None

PLAN_PROMPT='''You design the row schema of a Persian product comparison table for the given products (any category: electronics, appliances, tools, cables, clothing, cosmetics, industrial parts...).
Input: product titles, category names and every distinct raw specification key with sample values.
Return ONLY JSON: {"rows":[{"id":"snake_case_ascii","label":"Persian label","en":"short English spec name for web search","unit":null|"<unit>","core":true|false,"web":true|false}]}
Rules:
- One row per real-world concept. Merge synonymous keys written differently (Persian/English/abbreviated/with units/with category words), e.g. «حافظه RAM», «ظرفیت رم», «RAM» => one RAM row; «پردازنده», «مدل پردازنده مرکزی», «تراشه» => one processor row.
- Keep different concepts separate: installed vs maximum supported; integrated vs dedicated GPU; main vs selfie camera; product weight vs package weight (omit package weight).
- Storage: one capacity row for installed storage total plus one storage-type row. "No HDD" is not a capacity.
- Composite raw values (e.g. "15.6 inch IPS 144Hz", "ابعاد/ وزن") must still map to several rows - include those rows.
- Add the basic static specs buyers of this category normally compare even if no product has them (core=true), so they can be completed from the web. Basic = physical/static facts like screen size, resolution, RAM, storage, CPU/chipset, GPU, battery capacity, weight, dimensions, ports, wireless versions, power, capacity, material, size. NO benchmarks, temperatures, measured runtimes, scores or opinions.
- web=false for offer/seller-specific facts (color, condition, accessories in box, warranty, originality, seller notes, installed OS on a listing); web=true otherwise.
- unit: numeric rows use exactly one of: GB kg g inch Hz Wh mAh mm cm m mm2 V A W L bar ohm MP dB lm K rpm ms h GHz core. Text/boolean rows use null. Choose kg for laptops/appliances weight and g for phones/earbuds/small items.
- Order rows by buyer importance. 12-40 rows (never more than 40). Skip pure marketing text, product codes, barcodes, descriptions, price.'''

EXTRACT_PROMPT='''Map ONE product's numbered raw specifications onto the given comparison rows.
Entry 0 is the listing title. Return ONLY JSON:
{"model_name":"brand + series + model code in English, max 6 words, no specs (e.g. HP Victus 15-fa2039nw)","model_code":"most distinctive model/part code copied exactly from the entries or title (e.g. 15-FA2039NW, A13UC, 15ARP9, WH-1000XM4); empty if none","facts":[{"row":"<row id>","v":"Persian display value","n":<number in row unit or null>,"src":[<entry numbers>],"q":"verbatim excerpt copied from the cited entry value or title"}]}
Rules:
- Search ALL entries for each row, including keys with different names, composite values and the title. Do not miss a row whose value is present anywhere.
- q must be copied character-for-character from the cited entry VALUE (or title) and contain the number/word supporting v. Keep q short.
- n: required for rows with a unit; convert to the row unit (e.g. 167 g -> 0.167 when unit is kg, 1 TB -> 1024 GB). null if not a single explicit quantity.
- v: concise Persian display text (keep model names in Latin). Yes/no rows: «دارد» / «ندارد».
- One fact per row. If entries genuinely disagree (not just different wording/units), add "alt":[{"v":...,"n":...,"src":[...],"q":"..."}].
- SSD capacity plus "no HDD" is NOT a conflict. Bilingual brand repeats (e.g. «لنوو / Lenovo») are NOT a conflict.
- Never guess or use your own knowledge; only what the entries state. Omit rows not stated.'''

WEB_PROMPT='''Extract still-missing static specifications for ONE target product from ONE fetched web page.
Return ONLY JSON: {"page_product":"product the page describes","match":"exact"|"family"|"different","facts":[{"row":"<row id>","v":"Persian display value","n":<number in row unit or null>,"q":"verbatim excerpt from the page"}]}
Rules:
- match=exact only if the page is about the same model (same model code/configuration family as the target). match=family if it is a model family page with several configurations. Otherwise different and facts=[].
- For family pages, only give facts that are identical for every configuration (e.g. screen size, ports, bluetooth version); never RAM/storage/CPU/GPU/battery/weight unless the page row explicitly belongs to the target configuration (consistent with the known target facts).
- q must be copied verbatim from the page text and contain the supporting value. n in the row unit.
- Only the requested rows. No benchmarks, temperatures, measured results, reviews or opinions. No guessing.'''

SUMMARY_SEARCH_PROMPT='''Use web search to find the official/manufacturer or specification-database values of the listed specifications for exactly this product: {model} (shop listing title: {title}). Facts already known: {known}.
Return ONLY a JSON object: {{"page_product":"exact product the sources describe","facts":[{{"row":"<row id>","v":"Persian display value","n":<number in the row unit or null>,"url":"https page that states the value","q":"short verbatim excerpt copied from that page, containing the value"}}]}}
Rows (id, label, unit): {rows}
Rules: only this exact model and configuration; skip a row when sources disagree or only describe other configurations; static specifications only (no benchmarks, measured results, prices or opinions); never guess; omit rows you cannot source.'''

class Harmonizer:
 def __init__(self,path,client_factory=None,fetcher=None):
  self.path=str(path);self.client_factory=client_factory;self.fetcher=fetcher or gateway_fetch
  Path(self.path).parent.mkdir(parents=True,exist_ok=True)
  with self.conn() as db:db.execute('CREATE TABLE IF NOT EXISTS harmonize_cache(key TEXT PRIMARY KEY,created REAL,expires REAL,payload TEXT)')
 def conn(self):return sqlite3.connect(self.path,timeout=10)
 def cached(self,key):
  with self.conn() as db:row=db.execute('SELECT payload FROM harmonize_cache WHERE key=? AND expires>?',(key,time.time())).fetchone()
  return json.loads(row[0]) if row else None
 def store(self,key,value,seconds):
  with self.conn() as db:
   now=time.time();db.execute('INSERT OR REPLACE INTO harmonize_cache VALUES(?,?,?,?)',(key,now,now+seconds,json.dumps(value,ensure_ascii=False)))
   db.execute('DELETE FROM harmonize_cache WHERE key IN (SELECT key FROM harmonize_cache ORDER BY created DESC LIMIT -1 OFFSET 5000)')
 async def ask(self,system,payload,max_tokens=3000,timeout=90):return await ask(system,payload,max_tokens,timeout,self.client_factory)

 # ---- catalogue harmonization ----
 @staticmethod
 def entries(product):
  out=[{'i':0,'section':'عنوان آگهی','key':'عنوان','value':text(product.get('persian_title'))}]
  if product.get('brand_name') and product['brand_name']!='null':out.append({'i':1,'section':'مشخصات اصلی','key':'برند','value':text(product['brand_name'])})
  for spec in product.get('specs',[])[:MAX_ENTRIES]:
   value=text(spec.get('value'))
   if not value or fold(value) in ('null','نامشخص','نامعلوم','-'):continue
   out.append({'i':len(out),'section':text(spec.get('section')),'key':text(spec.get('key')),'value':value[:400]})
  return out
 @staticmethod
 def fingerprint(product):
  return hashlib.sha256(json.dumps([product.get('persian_title'),product.get('brand_name'),product.get('category_name'),product.get('specs')],ensure_ascii=False,sort_keys=True).encode()).hexdigest()

 async def plan(self,products):
  key='plan:'+hashlib.sha256(json.dumps([VERSION,configured_model(),sorted(self.fingerprint(p) for p in products)]).encode()).hexdigest()
  hit=self.cached(key)
  if hit:return hit
  keys={}
  for p in products:
   for e in self.entries(p)[1:]:
    k=keys.setdefault(normalize(e['key']),{'key':e['key'],'samples':[],'count':0});k['count']+=1
    if len(k['samples'])<2 and e['value'] not in k['samples']:k['samples'].append(e['value'][:80])
  listed=sorted(keys.values(),key=lambda k:-k['count'])[:MAX_KEYS]
  data=await self.ask(PLAN_PROMPT,{'products':[{'title':p.get('persian_title'),'category':p.get('category_name'),'brand':p.get('brand_name')} for p in products],'keys':listed},max_tokens=4000)
  rows=[];seen=set()
  for r in (data or {}).get('rows',[]) if isinstance(data,dict) else []:
   if not isinstance(r,dict):continue
   rid=re.sub(r'[^a-z0-9_]','_',str(r.get('id','')).lower())[:40].strip('_')
   label=text(r.get('label'))[:60]
   if not rid or not label or rid in seen or rid=='price':continue
   unit=r.get('unit') if r.get('unit') in UNITS else None
   seen.add(rid);rows.append({'id':rid,'label':label,'en':text(r.get('en'))[:60],'unit':unit,'core':bool(r.get('core')),'web':r.get('web') is not False})
  if len(rows)<3:return None
  rows=rows[:40]
  self.store(key,rows,30*86400);return rows

 async def extract(self,product,rows):
  key='extract:'+hashlib.sha256(json.dumps([VERSION,configured_model(),rows,self.fingerprint(product)],ensure_ascii=False).encode()).hexdigest()
  hit=self.cached(key)
  if hit:return hit
  entries=self.entries(product)
  data=await self.ask(EXTRACT_PROMPT,{'rows':[{k:r[k] for k in ('id','label','unit')} for r in rows],'entries':[str(e['i'])+'. ['+e['section']+'] '+e['key']+': '+e['value'] for e in entries]},max_tokens=5000,timeout=100)
  if not isinstance(data,dict):return None
  units={r['id']:r['unit'] for r in rows};by_index={e['i']:e for e in entries};facts={}
  def valid(f):
   if not isinstance(f,dict) or f.get('row') not in units:return None
   src=[i for i in (f.get('src') or []) if isinstance(i,int) and i in by_index]
   q=text(f.get('q'));v=text(f.get('v'))[:160]
   if not src or not q or not v:return None
   cited=[by_index[i] for i in src]
   if not any(quote_in(q,e['value']) or quote_in(q,e['key']+': '+e['value']) for e in cited):return None
   n=f.get('n') if units[f['row']] else None
   if n is not None and (not isinstance(n,(int,float)) or isinstance(n,bool) or not number_supported(n,q)):n=None
   # Every quantity shown to the buyer must be literally supported.
   if any(not number_supported(x,q) for x in numbers(v)):return None
   return {'value':v,'numeric':n,'quote':q,'raw':[{'section':e['section'],'key':e['key'],'value':e['value']} for e in cited],
           'source':'listing_title' if all(e['i']==0 for e in cited) else 'catalogue','src':src}
  for f in data.get('facts',[]) if isinstance(data.get('facts'),list) else []:
   main=valid(f)
   if not main or f['row'] in facts:continue
   alts=[a for a in (valid(dict(x,row=f['row'])) for x in (f.get('alt') or []) if isinstance(x,dict)) if a]
   alts=[a for a in alts if not same(a,main)]
   facts[f['row']]=dict(main,alternatives=alts)
  code=text(data.get('model_code'))[:40]
  if code and squash(code) not in squash(' '.join(e['key']+' '+e['value'] for e in entries)):code=''
  result={'model_name':text(data.get('model_name'))[:80],'model_code':code,'facts':facts,'used':sorted({i for f in facts.values() for i in f['src']}|{i for f in facts.values() for a in f['alternatives'] for i in a['src']})}
  self.store(key,result,30*86400);return result

 # ---- web completion ----
 async def search(self,query,deadline,tokens=()):
  links=[]
  # DuckDuckGo answers Iranian addresses with a challenge page: free search only via an explicit gateway.
  attempts=('https://lite.duckduckgo.com/lite/?'+urlencode({'q':query}),'https://html.duckduckgo.com/html/?'+urlencode({'q':query})) if os.getenv('WEB_GATEWAY_URL') else ()
  for n,url in enumerate(attempts):
   if time.monotonic()>=deadline:break
   # One search at a time across all jobs; the free endpoint throttles bursts.
   async with search_lock():
    try:doc=await asyncio.to_thread(self.fetcher,url,deadline)
    except Exception:doc={'status':'unavailable'}
    await asyncio.sleep(1.0)
   if doc.get('status')!='readable':continue
   for link in doc.get('links',[])[:60]:
    full=urljoin(url,link);p=urlsplit(full)
    if host_in(p.hostname or '',{'duckduckgo.com'}):full=parse_qs(p.query).get('uddg',[''])[0];p=urlsplit(full)
    host=p.hostname or ''
    if full.startswith('https://') and not host_in(host,SKIP_HOSTS) and full not in links:links.append(full)
   if links:break
  if not tokens or not any(t in squash(u) for t in tokens for u in links):
   for url in await model_search(query,self.client_factory):
    if not host_in(urlsplit(url).hostname or '',SKIP_HOSTS) and url not in links:links.append(url)
  def rank(u):
   host=urlsplit(u).hostname or ''
   named=bool(tokens) and any(t in squash(u) for t in tokens)
   kind=0 if host_in(host,MANUFACTURERS) else next((v for d,v in PREFERRED.items() if host_in(host,{d})),4)
   return (0 if named else 1,kind)
  ranked=sorted(links,key=rank)
  # Pages whose address names the model first; generic pages only as a last resort.
  named=[u for u in ranked if tokens and any(t in squash(u) for t in tokens)]
  return named+[u for u in ranked if u not in named][:max(0,2-len(named))]

 @staticmethod
 def window(page,labels):
  body=page.get('text','')
  if len(body)<=PAGE_CHARS:return body
  lines=body.split('\n');keep=[];size=0
  words=[w.lower() for l in labels for w in re.findall(r'[a-zA-Z]{3,}',l)]
  head=body[:5000];keep.append(head);size=len(head)
  for line in lines:
   low=line.lower()
   if (re.search(r'\d',line) and len(line)<300) or any(w in low for w in words):
    if size+len(line)>PAGE_CHARS:break
    keep.append(line);size+=len(line)+1
  return '\n'.join(keep)

 @staticmethod
 def identity_tokens(name,code=''):
  if code and len(squash(code))>=4:return [squash(code)]
  tokens=[t for t in re.findall(r'[a-z0-9]+(?:-[a-z0-9]+)*',fold(name)) if re.search(r'\d',t) and re.search(r'[a-z]',t) and len(t.replace('-',''))>=3]
  # Capacities, CPUs and GPUs are shared by many products; they never prove identity.
  tokens=[t for t in tokens if not re.fullmatch(r'\d+(?:gb|tb|mb|hz|w|wh|mah|mm|inch)|i[3579]|\d{4,5}[a-z]{0,3}|rtx\d+|gtx\d+',t.replace('-',''))]
  return [t.replace('-','') for t in tokens]

 async def complete_product(self,product,rows,cells,model_name,progress,model_code='',on_evidence=None):
  targets=[r for r in rows if r['web'] and cells.get(r['id']) is None]
  if not targets or not model_name:return {'status':'nothing_to_complete' if not targets else 'no_model','sources':[],'evidence':{}}
  key='web:'+hashlib.sha256(json.dumps([VERSION,configured_model(),model_name,sorted(r['id'] for r in targets),{k:v['value'] for k,v in cells.items() if v}],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
  hit=self.cached(key)
  if hit:
   progress({'stage':'cache','model':model_name,'accepted_fields':sorted(hit['evidence'])});return dict(hit,cached=True)
  deadline=time.monotonic()+WEB_SECONDS;evidence={};sources=[];seen=set();fetched={};tokens=self.identity_tokens(model_name,model_code)
  known={r['label']:cells[r['id']]['value'] for r in rows if cells.get(r['id'])}
  for round_number in range(1,WEB_ROUNDS+1):
   remaining=[r for r in targets if r['id'] not in evidence]
   if not remaining or time.monotonic()>=deadline or len(sources)>=WEB_PAGES:break
   query=model_name+(' specifications' if round_number==1 else ' specs '+' '.join(r['en'] or r['id'].replace('_',' ') for r in remaining[:6]))
   progress({'stage':'discovery','round':round_number,'model':model_name,'query':query,'remaining_fields':[r['label'] for r in remaining]})
   links=[u for u in await self.search(query,deadline,tokens) if u not in seen][:3]
   async def read(url):
    seen.add(url);progress({'stage':'source','round':round_number,'model':model_name,'url':url})
    try:page=await asyncio.to_thread(self.fetcher,url,deadline)
    except Exception:page={'url':url,'status':'unavailable'}
    page['retrieved_at']=time.time();fetched[url]=page;return page
   pages=await asyncio.gather(*(read(u) for u in links))
   async def evaluate(page):
    record={'url':page.get('url'),'status':page.get('status'),'title':text(page.get('title'))[:200],'retrieved_at':page['retrieved_at'],'accepted':[]}
    if page.get('status')!='readable':return record
    head=squash(page.get('title',''))+squash(page.get('text','')[:6000])+squash(page.get('url',''))
    if tokens and not any(t in head for t in tokens):record['status']='model_not_found';return record
    want=[r for r in targets if r['id'] not in evidence]
    data=await self.ask(WEB_PROMPT,{'target':{'title':product.get('persian_title'),'model':model_name,'known_facts':known},'rows':[{k:r[k] for k in ('id','label','en','unit')} for r in want],'page':{'url':page['url'],'title':page.get('title',''),'text':self.window(page,[r['en'] for r in want])}},max_tokens=2500,timeout=80)
    if not isinstance(data,dict) or data.get('match') not in ('exact','family'):record['status']='different_product' if isinstance(data,dict) else 'extraction_unavailable';return record
    record['match']=data['match'];record['page_product']=text(data.get('page_product'))[:120]
    units={r['id']:r['unit'] for r in want}
    for f in data.get('facts',[]) if isinstance(data.get('facts'),list) else []:
     if not isinstance(f,dict) or f.get('row') not in units:continue
     q=text(f.get('q'))[:300];v=text(f.get('v'))[:160]
     if not q or not v or not quote_in(q,page.get('text','')+'\n'+page.get('title','')):continue
     n=f.get('n') if units[f['row']] else None
     if n is not None and (not isinstance(n,(int,float)) or isinstance(n,bool) or not number_supported(n,q)):n=None
     if any(not number_supported(x,q) for x in numbers(v)):continue
     host=urlsplit(page['url']).hostname or ''
     item={'field':f['row'],'value':v,'numeric':n,'quote':q,'url':page['url'],'retrieved_at':page['retrieved_at'],'status':'accepted',
           'source_type':'manufacturer' if host_in(host,MANUFACTURERS) else 'spec_reference' if any(host_in(host,{d}) for d in PREFERRED) else 'public_page',
           'model_match':'مدل یکسان' if data['match']=='exact' else 'صفحه خانواده مدل؛ فقط مشخصات مشترک','variant_match':record['page_product'] or model_name,'source_title':record['title']}
     evidence.setdefault(f['row'],[]).append(item);record['accepted'].append(f['row'])
    # Stream each verified value to the open table as soon as it is accepted.
    if record['accepted'] and on_evidence:on_evidence({k:list(v) for k,v in evidence.items()})
    return record
   for record in await asyncio.gather(*(evaluate(p) for p in pages)):
    sources.append(record);progress({'stage':'evaluated','round':round_number,'model':model_name,'url':record['url'],'status':record['status'],'accepted_fields':sorted(evidence)})
  remaining=[r for r in targets if r['id'] not in evidence]
  if remaining and llm.configured() and deadline-time.monotonic()>20:
   progress({'stage':'discovery','round':'summary','model':model_name,'query':model_name,'remaining_fields':[r['label'] for r in remaining]})
   found=await self.summary_facts(product,model_name,remaining,known,tokens,fetched,deadline)
   for rid,items in found.items():evidence.setdefault(rid,[]).extend(items)
   if found and on_evidence:on_evidence({k:list(v) for k,v in evidence.items()})
   sources.append({'url':'web_search','status':'search_summary' if found else 'search_summary_empty','title':'BoxAPI web search','retrieved_at':time.time(),'accepted':sorted(found)})
   progress({'stage':'evaluated','round':'summary','model':model_name,'url':'web_search','status':'search_summary','accepted_fields':sorted(evidence)})
  result={'status':'partial' if evidence else 'unavailable','sources':sources,'evidence':evidence,'model':model_name,'targets':[r['id'] for r in targets]}
  self.store(key,result,86400 if evidence else 1800);return result

 async def summary_facts(self,product,model_name,rows,known,tokens,fetched,deadline):
  """One web-search answer for still-missing rows. A value whose cited page is
  readable must be quoted there (else dropped); when the page cannot be read
  from here the value is kept only as a labelled search summary with its link."""
  prompt=SUMMARY_SEARCH_PROMPT.format(model=model_name,title=text(product.get('persian_title'))[:160],known=json.dumps(known,ensure_ascii=False)[:1200],
                                      rows=json.dumps([[r['id'],r['label'],r['unit']] for r in rows],ensure_ascii=False))
  try:
   found=await llm.web_search(prompt,max_tool_calls=3,context='low',effort='low',max_tokens=2500,timeout=max(20,deadline-time.monotonic()),purpose='spec_summary',client_factory=self.client_factory)
   data=llm.parse_json(found['text'])
  except (llm.LLMError,ValueError):return {}
  if not isinstance(data,dict) or not isinstance(data.get('facts'),list):return {}
  product_name=text(data.get('page_product'))[:120]
  if tokens and not any(t in squash(product_name) for t in tokens):return {}
  cited={c['url'] for c in found['citations']}|set(found['sources']);units={r['id']:r['unit'] for r in rows};out={}
  for f in data['facts'][:40]:
   if not isinstance(f,dict) or f.get('row') not in units or f['row'] in out:continue
   url=text(f.get('url'));q=text(f.get('q'))[:300];v=text(f.get('v'))[:160];host=urlsplit(url).hostname or ''
   if not url.startswith('https://') or host_in(host,SKIP_HOSTS) or not q or not v:continue
   if any(not number_supported(x,q) for x in numbers(v)):continue
   n=f.get('n') if units[f['row']] else None
   if n is not None and (not isinstance(n,(int,float)) or isinstance(n,bool) or not number_supported(n,q)):n=None
   page=fetched.get(url)
   if page is None and deadline-time.monotonic()>3:
    try:page=await asyncio.to_thread(self.fetcher,url,min(deadline,time.monotonic()+12))
    except Exception:page={'url':url,'status':'unavailable'}
    page['retrieved_at']=time.time();fetched[url]=page
   readable=(page or {}).get('status')=='readable'
   if readable and not quote_in(q,page.get('text','')+'\n'+page.get('title','')):continue
   # Anthropic citations are verbatim page text: a quote inside one counts as checked.
   readable=readable or any(c.get('verbatim') and c['url']==url and quote_in(q,c.get('text','')) for c in found['citations'])
   if not readable and url not in cited:continue
   out[f['row']]=[{'field':f['row'],'value':v,'numeric':n,'quote':q,'url':url,'retrieved_at':(page or {}).get('retrieved_at',time.time()),
                   'status':'accepted' if readable else 'search_summary','verification':'page_quote' if readable else 'search_summary',
                   'source_type':'manufacturer' if host_in(host,MANUFACTURERS) else 'spec_reference' if any(host_in(host,{d}) for d in PREFERRED) else 'public_page',
                   'model_match':'مدل یکسان؛ نقل‌قول در صفحه بررسی شد' if readable else 'خلاصه جست‌وجوی وب؛ متن صفحه از این‌جا قابل بررسی نبود',
                   'variant_match':product_name or model_name,'source_title':text((page or {}).get('title'))[:200]}]
  return out

 # ---- full pipeline ----
 async def complete(self,catalog,base,ids,progress=lambda e:None,web=True,on_preview=None):
  """Catalogue table first, then web completion product by product.

  on_preview(result,stats) receives every intermediate table so the UI can
  show real progress instead of a static waiting message."""
  started=time.monotonic();products=[catalog.product(pid) for pid in base['product_ids']];pids=base['product_ids']
  targets=[pid for pid in pids if pid in ids];timing={}
  def publish(result,stage,done,web_started=None):
   if not on_preview:return
   cells=[c for r in result['rows'][1:] for c in r['cells']]
   filled=sum(c['status'] in ('recorded','conflict','web_completed') for c in cells)
   elapsed=time.monotonic()-started;now=time.monotonic()
   # Products are researched in parallel: the table is complete when the
   # slowest finishes, roughly 1.4x the first one. Never jump upwards.
   if stage=='harmonize':eta=max(20,125-elapsed)
   elif done>=len(targets):eta=0
   else:
    web_elapsed=now-(web_started or now)
    if done and 'first' not in timing:timing['first']=web_elapsed
    eta=max(10,timing['first']*1.4-web_elapsed) if 'first' in timing else max(15,80-web_elapsed)
   if 'eta' in timing and eta>timing['eta']:eta=timing['eta']
   timing['eta']=eta
   on_preview(result,{'stage':stage,'cells_total':len(cells),'cells_filled':filled,'cells_web':sum(c['status']=='web_completed' for c in cells),
                      'cells_pending':sum(bool(c.get('pending')) for c in cells),'products_total':len(targets),'products_done':done,
                      'elapsed_seconds':round(elapsed),'eta_seconds':round(max(0,eta))})
  progress({'stage':'harmonize','model':'کاتالوگ','remaining_fields':[]})
  if on_preview:on_preview(None,{'stage':'harmonize','cells_total':0,'cells_filled':0,'cells_web':0,'cells_pending':0,'products_total':len(targets),'products_done':0,'elapsed_seconds':0,'eta_seconds':125})
  rows=await self.plan(products)
  if not rows:
   print('harmonizer_unavailable','plan',flush=True);return None
  progress({'stage':'harmonize','model':'نگاشت '+str(len(rows))+' ردیف','remaining_fields':[]})
  extracted=await asyncio.gather(*(self.extract(p,rows) for p in products))
  if any(x is None for x in extracted):
   print('harmonizer_unavailable','extract',sum(x is None for x in extracted),flush=True);return None
  table={r['id']:{pid:None for pid in pids} for r in rows}
  for pid,data in zip(pids,extracted):
   for rid,fact in data['facts'].items():table[rid][pid]=fact
  web_results={}
  publish(self.assemble(base,rows,pids,products,extracted,table,web_results,ids,web,final=not web),'web' if web else 'done',0,time.monotonic())
  if web:
   gate=asyncio.Semaphore(5);web_started=time.monotonic();partial={}
   async def one(pid,product,data):
    async with gate:
     def arrived(evidence,pid=pid):
      partial[pid]={'evidence':evidence}
      publish(self.assemble(base,rows,pids,products,extracted,table,web_results,ids,web,final=False,partial=partial),'web',len(web_results),web_started)
     return pid,await self.complete_product(product,rows,{rid:table[rid][pid] for rid in table},data['model_name'] or product.get('model') or '',lambda e:progress(dict(e,product_id=pid)),data.get('model_code',''),arrived)
   tasks=[asyncio.ensure_future(one(pid,p,d)) for pid,p,d in zip(pids,products,extracted) if pid in ids]
   for finished in asyncio.as_completed(tasks):
    pid,research=await finished;web_results[pid]=research
    if len(web_results)<len(tasks):publish(self.assemble(base,rows,pids,products,extracted,table,web_results,ids,web,final=False,partial=partial),'web',len(web_results),web_started)
  return self.assemble(base,rows,pids,products,extracted,table,web_results,ids,web,final=True)

 def assemble(self,base,rows,pids,products,extracted,table,web_results,ids,web,final=True,partial=None):
  result=copy.deepcopy(base)
  out_rows=[next(r for r in base['rows'] if r['id']=='price')]
  for r in rows:
   cells=[]
   for pid in pids:
    fact=table[r['id']][pid];web_items=((web_results.get(pid) or (partial or {}).get(pid) or {}).get('evidence') or {}).get(r['id'],[])
    if fact:
     conflict=bool(fact['alternatives'])
     values=[fact]+fact['alternatives']
     cells.append({'value':' / '.join(dict.fromkeys(x['value'] for x in values)) if conflict else fact['value'],'numeric':None if conflict else fact['numeric'],
                   'status':'conflict' if conflict else 'recorded','source':fact['source'],'quote':fact['quote'],'raw':[e for x in values for e in x['raw']]})
    elif web_items:
     # Page-verified quotes outrank search summaries.
     web_items=sorted(web_items,key=lambda i:i.get('status')=='search_summary');distinct=[]
     for item in web_items:
      if not any(same(item,d) for d in distinct):distinct.append(item)
     makers=[d for d in distinct if d['source_type']=='manufacturer']
     chosen=makers[0] if len(distinct)>1 and len(makers)==1 else distinct[0]
     agreed=len(distinct)==1 or chosen is makers[0] if makers else len(distinct)==1
     cells.append({'value':chosen['value'] if agreed else ' / '.join(d['value'] for d in distinct),'numeric':chosen['numeric'] if agreed else None,
                   'status':'web_completed' if agreed else 'conflict','source':'public_spec','raw':[],
                   'research_evidence':web_items,'research_status':('search_summary' if chosen.get('status')=='search_summary' else 'accepted') if agreed else 'source_conflict'})
    elif web and r['web'] and pid in ids and pid not in web_results and not final:
     cells.append({'value':'در حال جست‌وجو در وب…','numeric':None,'status':'missing','pending':True,'source':'catalogue','raw':[]})
    else:
     cells.append({'value':MISSING,'numeric':None,'status':'missing','source':'catalogue','raw':[],
                   **({'completion_state':'unavailable','value':'در وب هم منبع معتبری پیدا نشد'} if web and r['web'] and pid in ids and web_results.get(pid) else {})})
   if all(c['status']=='missing' and not c.get('pending') for c in cells) and not r['core']:continue
   out_rows.append({'id':r['id'],'label':r['label'],'unit':r['unit'],'cells':cells,'core':r['core']})
  detailed=[]
  for pid,product,data in zip(pids,products,extracted):
   for e in self.entries(product):
    if e['i'] in data['used'] or e['i']<=1:continue
    rid='raw:'+normalize(e['section'])+':'+normalize(e['key'])
    row=next((x for x in detailed if x['id']==rid),None)
    if not row:row={'id':rid,'label':e['key'],'unit':None,'cells':{p:[] for p in pids}};detailed.append(row)
    row['cells'][pid].append(e)
  detailed_rows=[]
  for row in detailed:
   cells=[]
   for pid in pids:
    found=row['cells'][pid]
    cells.append({'value':' / '.join(dict.fromkeys(e['value'] for e in found)) if found else MISSING,'numeric':None,'status':'recorded' if found else 'missing','source':'catalogue','raw':[{'section':e['section'],'key':e['key'],'value':e['value']} for e in found]})
   detailed_rows.append({'id':row['id'],'label':row['label'],'unit':None,'cells':cells})
  charts=[]
  for row in out_rows+detailed_rows:
   row['different']=len({(c['status'],c['numeric'] if c['numeric'] is not None else fold(c['value'])) for c in row['cells']})>1
   if row['unit'] and row['unit'] in UNITS and sum(c['numeric'] is not None for c in row['cells'])>=2:
    charts.append({'id':row['id'],'label':row['label'],'unit':row['unit'],'unit_label':UNITS[row['unit']],
                   'values':[{'product_id':pid,'value':c['numeric'],'status':c['status'],'source':c['source'],'research_evidence':c.get('research_evidence',[])} for pid,c in zip(pids,row['cells'])],
                   'note':'مبدأ نمودار صفر است؛ نامشخص صفر نیست. مقدار بزرگ‌تر لزوماً بهتر نیست.'})
  price=out_rows[0]
  if price.get('unit')=='toman' and sum(c.get('numeric') is not None for c in price['cells'])>=2:
   charts.insert(0,next((c for c in base.get('charts',[]) if c['id']=='price'),None) or {'id':'price','label':price['label'],'unit':'toman','unit_label':'تومان','values':[{'product_id':pid,'value':c.get('numeric'),'status':c['status']} for pid,c in zip(pids,price['cells'])],'note':'قیمت کمترین آفر دیده‌شده در همین فهرست است.'})
  result.update(rows=out_rows,detailed_rows=detailed_rows,charts=[c for c in charts if c],harmonizer=VERSION,
                spec_research=[{'product_id':pid,'title':p.get('persian_title'),'model':d['model_name'],'research':web_results.get(pid)} for pid,p,d in zip(pids,products,extracted)],
                basic_specs_completed=sum(c['status']=='web_completed' for r in out_rows for c in r['cells']),
                harmonized_fields=sum(c['status'] in ('recorded','conflict') and c['source']!='public_spec' for r in out_rows[1:] for c in r['cells']))
  return result

def same(a,b):
 if a.get('numeric') is not None and b.get('numeric') is not None:
  return abs(float(a['numeric'])-float(b['numeric']))<=max(abs(float(a['numeric']))*.012,1e-9)
 return squash(a.get('value'))==squash(b.get('value'))
EXTRACT_PROMPT+=guard.DATA_POLICY;WEB_PROMPT+=guard.DATA_POLICY
