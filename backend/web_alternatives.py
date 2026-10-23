"""Web alternatives in the shopper's city when the catalogue has no match.

Category-agnostic: the model turns any request into requirements and
city-targeted searches, pages are found with BoxAPI web search and read
directly (Iranian shop sites are reachable from the Iranian runtime), and each
page yields listings plus store details. Every listing title, price, store
name and address must be a verbatim quote of the page; addresses are geocoded
with OpenStreetMap for the map. If too few pages are readable, web-search
answers are added as clearly labelled search summaries with their links.
Results are public leads, never qualifying catalogue offers or stock promises.
"""
import asyncio,copy,hashlib,json,re,secrets,sqlite3,time
from pathlib import Path
from urllib.parse import urlsplit,urlencode
import httpx
import llm
import obs,resilience
from jobqueue import JobQueue
from catalog import haversine
from public_research import fetch,host_in
from spec_harmonizer import ask,model_search,search_lock,quote_in,fold,text,SKIP_HOSTS,PAGE_CHARS,gateway_headers
import os

VERSION='webalt-1-'+hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:10]
MAX_PAGES=8;PER_HOST=2;SECONDS=150
USER_AGENT=os.getenv('HTTP_USER_AGENT','BazargardShoppingAssistant/4.1 (+'+(os.getenv('PUBLIC_BASE_URL') or 'https://bazargard-ai.liara.run')+')')
BLOCKED_SOURCES=SKIP_HOSTS|{'torob.com','wikipedia.org','aparat.com','namasha.com'}
CITY_CENTERS={'تهران':(35.70,51.41),'مشهد':(36.30,59.60),'اصفهان':(32.65,51.67),'شیراز':(29.61,52.53),'تبریز':(38.08,46.29),'کرج':(35.83,50.99),'قم':(34.64,50.88),'اهواز':(31.32,48.67),'رشت':(37.28,49.58),'کرمان':(30.28,57.08)}

PLAN_PROMPT='''A shopper in an Iranian city wants a product that the local catalogue does not have. Plan a web search for real shops in that city that sell matching products (any category).
Return ONLY JSON: {"product":"short Persian description","requirements":["each hard requirement from the request, Persian"],"city":"Persian city name","queries":["3 Persian web search queries"]}
Rules: city = a city named in the request, else the given city. Queries must find shop/product pages in that city with addresses or in-person sales, e.g. combine the product + key specs + city + words like خرید، فروشگاه، آدرس، حضوری، نمایندگی، بازار. One query may target the city's known market/bazaar for this category if you know it. No private details (budget numbers, names).'''

EXTRACT_PROMPT='''From ONE fetched web page, extract shop listings relevant to the shopper's request.
Return ONLY JSON: {"store":{"name":"","address":"","city":"","phone":"","in_person":"yes|no|unknown","quotes":{"name":"","address":"","in_person":""}},"listings":[{"title":"","price":"","match":"exact|close","why":"short Persian reason","quotes":{"title":"","price":""}}]}
Rules:
- listings: products on the page that satisfy the requirements (exact) or are reasonable alternatives (close). Max 6. Skip unrelated items, accessories, blog text.
- Every quote must be copied character-for-character from the page text. title/price/name/address values must come from their quotes. Empty string when the page does not state it.
- store = the seller operating this page (shop/showroom/branch). address only if the page states a physical address; in_person=yes only if the page says in-person purchase/showroom/store visit is possible.
- Never use outside knowledge, never guess prices, stock or addresses.'''


SEARCH_PROMPT='Use web search to find pages of real Iranian shops (online shops, showrooms, in-person stores) for: {q}. Answer with the best shop or product page URLs only, one per line, Iranian (.ir) shops first.'
SUMMARY_PROMPT='''Use web search to find shops in {city}, Iran that sell this product or close alternatives: {product}. Hard requirements: {req}.
Return ONLY a JSON object {{"items":[{{"url":"https shop/product page","title":"product name as listed","price":"price as listed, or empty","store":"shop name","address":"physical address in {city} if the page states one, else empty","match":"exact|close","why":"short Persian reason"}}]}} with up to 8 items, Iranian shops first.
Rules: real pages you found only; never invent prices, addresses or stock; skip accessories and unrelated products.'''


def smart_fetch(url,deadline):
 """Direct fetch (Iranian runtime); an egress gateway only when explicitly configured."""
 host=urlsplit(url).hostname or ''
 base=os.getenv('WEB_GATEWAY_URL','')
 def gateway(u,d):
  seconds=max(1.0,min(25.0,d-time.monotonic()))
  r=httpx.get(base+'/fetch',params={'url':u,'seconds':round(seconds,1),'pdf':'0'},headers=gateway_headers(),timeout=seconds+8)
  return r.json()
 order=([fetch,gateway] if host.endswith('.ir') else [gateway,fetch]) if base else [fetch]
 last={'url':url,'status':'unavailable'}
 for method in order:
  if time.monotonic()>=deadline:break
  try:doc=method(url,deadline)
  except Exception:continue
  if doc.get('status')=='readable':return doc
  last=doc
 return last


class Geocoder:
 def __init__(self,path):
  self.path=str(path)
  with sqlite3.connect(self.path) as db:db.execute('CREATE TABLE IF NOT EXISTS geo_cache(key TEXT PRIMARY KEY,created REAL,payload TEXT)')
 async def locate(self,address,city):
  key='g2|'+fold(address+'|'+city)
  with sqlite3.connect(self.path) as db:row=db.execute('select payload from geo_cache where key=?',(key,)).fetchone()
  if row:return json.loads(row[0])
  found=None
  parts=[p.strip() for p in re.split(r'[،,\-–]',address) if p.strip() and fold(p.strip()) not in (fold(city),'ایران','iran')]
  # Full address, then each part from most to least specific; bounded to the city.
  # Plate/floor/unit numbers and relative phrases ("opposite X") never locate a place alone.
  place=[p for p in parts if len(p)>=3 and not re.match(r'^(?:آدرس\s*:?\s*)?(?:پلاک|طبقه|واحد|کد\s*پستی|تلفن|جنب|روبه?روی|نبش|بعد\s+از|قبل\s+از|حد\s*فاصل|کوچه)\b',p)]
  candidates=[' '.join(parts)]+list(reversed(place))
  center=CITY_CENTERS.get(city)
  bounds={'viewbox':f'{center[1]-.45},{center[0]+.35},{center[1]+.45},{center[0]-.35}','bounded':1} if center else {}
  for n,candidate in enumerate(dict.fromkeys(candidates)):
   if n>=5:break
   async with nominatim_lock():
    try:
     async with httpx.AsyncClient(timeout=12) as client:
      r=await resilience.call('nominatim',lambda:client.get('https://nominatim.openstreetmap.org/search',params={'q':candidate+'، '+city,'format':'json','limit':1,'countrycodes':'ir',**bounds},headers={'User-Agent':USER_AGENT}),attempts=2,timeout=12)
      hits=r.json() if r.status_code==200 else []
    except (httpx.HTTPError,ValueError,resilience.CircuitOpen,asyncio.TimeoutError):hits=[]
    await asyncio.sleep(1.1)
   if hits:
    found={'lat':float(hits[0]['lat']),'lon':float(hits[0]['lon']),'precision':'address' if n==0 else 'approximate','matched':candidate};break
  with sqlite3.connect(self.path) as db:db.execute('insert or replace into geo_cache values(?,?,?)',(key,time.time(),json.dumps(found)))
  return found

_NOMINATIM={}
def nominatim_lock():
 loop=asyncio.get_running_loop()
 return _NOMINATIM.setdefault(id(loop),asyncio.Lock())


class WebAlternatives:
 def __init__(self,path,client_factory=None,fetcher=None,geocoder=None):
  self.path=str(path);self.client_factory=client_factory;self.fetcher=fetcher or smart_fetch
  self.geocoder=geocoder or Geocoder(path)
 async def ask(self,system,payload,max_tokens=2500,timeout=90):return await ask(system,payload,max_tokens,timeout,self.client_factory)

 async def search(self,query,deadline):
  links=[]
  # DuckDuckGo challenges Iranian addresses: free search only through an explicit gateway.
  for url in ('https://lite.duckduckgo.com/lite/?'+urlencode({'q':query}),'https://html.duckduckgo.com/html/?'+urlencode({'q':query})) if os.getenv('WEB_GATEWAY_URL') else ():
   if time.monotonic()>=deadline or links:break
   async with search_lock():
    try:doc=await asyncio.to_thread(self.fetcher,url,deadline)
    except Exception:doc={}
    await asyncio.sleep(1.0)
   for link in doc.get('links',[])[:80]:
    full=link if link.startswith('http') else 'https:'+link if link.startswith('//') else ''
    p=urlsplit(full)
    if host_in(p.hostname or '',{'duckduckgo.com'}):
     from urllib.parse import parse_qs
     full=parse_qs(p.query).get('uddg',[''])[0]
    if full.startswith('https://') and not host_in(urlsplit(full).hostname or '',BLOCKED_SOURCES|{'duckduckgo.com'}) and full not in links:links.append(full)
  if len(links)<3:
   for url in await model_search(query,self.client_factory,SEARCH_PROMPT):
    if not host_in(urlsplit(url).hostname or '',BLOCKED_SOURCES) and url not in links:links.append(url)
  return links

 async def run(self,query,profile,progress=lambda e:None,on_update=lambda r:None):
  started=time.monotonic();deadline=started+SECONDS
  city=text(profile.get('city')) or 'تهران'
  result={'query':query,'city':city,'version':VERSION,'status':'running','items':[],'sources':[],'stage':'plan','created':time.time(),
          'notice':'در دیتاست ترب مورد مطابقی پیدا نشد؛ این‌ها گزینه‌های جایگزین از فروشگاه‌های وب در شهر شما هستند. قیمت، موجودی و ساعت کاری را پیش از مراجعه تلفنی تأیید کنید.'}
  progress({'stage':'plan'});on_update(copy.deepcopy(result))
  plan=await self.ask(PLAN_PROMPT,{'request':query[:300],'city':city},max_tokens=900,timeout=60)
  if not isinstance(plan,dict) or not plan.get('queries'):
   # A model outage is transient: the durable queue retries the job later.
   if plan is None and llm.configured():raise resilience.Retryable('search plan unavailable')
   result.update(status='unavailable',stage='done',notice='جست‌وجوی وب در دسترس نبود؛ دوباره تلاش کنید.');on_update(result);return result
  city=text(plan.get('city'))[:30] or city
  queries=[text(q)[:160] for q in plan['queries'] if text(q)][:3]
  result.update(city=city,product=text(plan.get('product'))[:120],requirements=[text(r)[:80] for r in plan.get('requirements',[])][:8],queries=queries,stage='search')
  progress({'stage':'search','queries':queries});on_update(copy.deepcopy(result))
  found=[]
  for links in await asyncio.gather(*(self.search(q,deadline) for q in queries)):
   for url in links:
    if url not in found:found.append(url)
  # Iranian shop pages first; at most two pages per site.
  found.sort(key=lambda u:(0 if (urlsplit(u).hostname or '').endswith('.ir') else 1))
  pages=[];hosts={}
  for url in found:
   host=(urlsplit(url).hostname or '').removeprefix('www.')
   if hosts.get(host,0)>=PER_HOST:continue
   hosts[host]=hosts.get(host,0)+1;pages.append(url)
   if len(pages)>=MAX_PAGES:break
  result.update(stage='read',pages_total=len(pages),pages_done=0)
  progress({'stage':'read','pages':len(pages)});on_update(copy.deepcopy(result))
  gate=asyncio.Semaphore(4);docs={}
  async def handle(url):
   async with gate:
    try:doc=await asyncio.to_thread(self.fetcher,url,deadline)
    except Exception:doc={'url':url,'status':'unavailable'}
    docs[url]=doc
    source={'url':url,'status':doc.get('status'),'title':text(doc.get('title'))[:160],'retrieved_at':time.time(),'listings':0}
    items=[]
    if doc.get('status')=='readable':
     body=doc.get('text','')[:PAGE_CHARS]
     data=await self.ask(EXTRACT_PROMPT,{'request':query[:300],'requirements':result['requirements'],'city':city,'page':{'url':url,'title':doc.get('title',''),'text':body}},max_tokens=2500,timeout=80)
     items=await self.listings(data,doc,url,city,profile)
     source['listings']=len(items)
    seen={(fold(i['title']),i['store']['site']) for i in result['items']}
    items=[i for i in items if (fold(i['title']),i['store']['site']) not in seen]
    result['sources'].append(source);result['items'].extend(items);result['pages_done']=result.get('pages_done',0)+1
    result['items'].sort(key=rank)
    progress({'stage':'read','url':url,'listings':len(items)});on_update(copy.deepcopy(result))
  await asyncio.gather(*(handle(u) for u in pages))
  if len(result['items'])<3 and llm.configured() and deadline-time.monotonic()>25:
   result.update(stage='summary');progress({'stage':'summary'});on_update(copy.deepcopy(result))
   extra=await self.summary_items(query,result,city,profile,docs,deadline)
   seen={(fold(i['title']),i['store']['site']) for i in result['items']}
   result['items'].extend(i for i in extra if (fold(i['title']),i['store']['site']) not in seen);result['items'].sort(key=rank)
  result.update(status='completed' if result['items'] else 'empty',stage='done',elapsed_seconds=round(time.monotonic()-started))
  on_update(copy.deepcopy(result));return result

 async def summary_items(self,query,result,city,profile,docs,deadline):
  """Web-search answer when too few pages were readable. A listing whose page
  is readable must be quoted there; otherwise it is a labelled search summary."""
  try:
   found=await llm.web_search(SUMMARY_PROMPT.format(city=city,product=result.get('product') or query[:200],req='، '.join(result.get('requirements') or [])),
                              max_tool_calls=3,context='low',effort='low',max_tokens=3000,timeout=max(20,deadline-time.monotonic()),purpose='webalt_summary',client_factory=self.client_factory)
   data=llm.parse_json(found['text'])
  except (llm.LLMError,ValueError):return []
  cited={c['url'] for c in found['citations']}|set(found['sources']);out=[]
  for item in (data or {}).get('items') or [] if isinstance(data,dict) else []:
   if not isinstance(item,dict):continue
   url=text(item.get('url'));host=(urlsplit(url).hostname or '').removeprefix('www.');title=text(item.get('title'))[:200]
   if not url.startswith('https://') or host_in(host,BLOCKED_SOURCES) or not title:continue
   doc=docs.get(url)
   if doc is None and deadline-time.monotonic()>5:
    try:doc=await asyncio.to_thread(self.fetcher,url,min(deadline,time.monotonic()+12))
    except Exception:doc={'url':url,'status':'unavailable'}
    docs[url]=doc
   readable=(doc or {}).get('status')=='readable';page=(doc or {}).get('text','')+'\n'+(doc or {}).get('title','')
   if readable and not quote_in(title,page):continue
   if not readable and url not in cited:continue
   price=text(item.get('price'))[:80];address=text(item.get('address'))[:200]
   if readable:price=price if price and quote_in(price,page) else '';address=address if address and quote_in(address,page) else ''
   out.append({'title':title,'price':price,'match':'exact' if item.get('match')=='exact' else 'close','why':text(item.get('why'))[:160],
               'store':{'name':text(item.get('store'))[:80] or host,'address':address,'city':city,'phone':'','in_person':'unknown','site':host},
               'url':url,'lat':None,'lon':None,'location_precision':None,'distance_km':None,
               'maps_url':'https://www.google.com/maps/search/?'+urlencode({'api':1,'query':address+' '+city}) if address else None,
               'verification':'page_quote' if readable else 'search_summary',
               'label':None if readable else 'خلاصه جست‌وجوی وب — صفحه از این‌جا خوانده نشد؛ عنوان، قیمت و آدرس تأیید نشده‌اند',
               'quotes':{'title':title if readable else None,'price':price if readable and price else None,'address':address if readable and address else None}})
  return out[:8]

 async def listings(self,data,doc,url,city,profile):
  if not isinstance(data,dict):return []
  page=doc.get('text','')+'\n'+doc.get('title','')
  def verified(value,quote):
   value=text(value)[:200];quote=text(quote)[:300]
   return value if value and quote and quote_in(quote,page) and (quote_in(value,quote) or quote_in(value,page)) else ''
  store=data.get('store') if isinstance(data.get('store'),dict) else {}
  quotes=store.get('quotes') if isinstance(store.get('quotes'),dict) else {}
  host=(urlsplit(url).hostname or '').removeprefix('www.')
  name=verified(store.get('name'),quotes.get('name')) or host
  address=verified(store.get('address'),quotes.get('address'))
  in_person='yes' if store.get('in_person')=='yes' and quotes.get('in_person') and quote_in(quotes['in_person'],page) else 'no' if store.get('in_person')=='no' else 'unknown'
  store_city=text(store.get('city'))[:30]
  location=None
  if address:location=await self.geocoder.locate(address,store_city or city)
  distance=haversine(profile.get('lat'),profile.get('lon'),location['lat'],location['lon']) if location else None
  out=[]
  for item in (data.get('listings') or [])[:6]:
   if not isinstance(item,dict):continue
   q=item.get('quotes') if isinstance(item.get('quotes'),dict) else {}
   title=verified(item.get('title'),q.get('title'))
   if not title:continue
   out.append({'title':title,'price':verified(item.get('price'),q.get('price')),'match':'exact' if item.get('match')=='exact' else 'close','why':text(item.get('why'))[:160],
               'store':{'name':name,'address':address,'city':store_city,'phone':text(store.get('phone'))[:30] if store.get('phone') and quote_in(str(store.get('phone')),page) else '','in_person':in_person,'site':host},
               'url':url,'lat':location['lat'] if location else None,'lon':location['lon'] if location else None,'location_precision':location['precision'] if location else None,
               'distance_km':round(distance,1) if distance is not None else None,
               'maps_url':'https://www.google.com/maps/search/?'+urlencode({'api':1,'query':address+' '+(store_city or city)}) if address else None,
               'quotes':{'title':q.get('title'),'price':q.get('price') if q.get('price') and quote_in(q['price'],page) else None,'address':quotes.get('address') if address else None}})
  return out

def rank(item):
 return (item.get('verification')=='search_summary',item['match']!='exact',item['store']['in_person']!='yes',item['lat'] is None,item['distance_km'] if item['distance_km'] is not None else 999)


class WebAlternativeJobs:
 """Owner-isolated background jobs with incremental results for the UI."""
 def __init__(self,mem,agent,queue=None):
  self.mem=mem;self.agent=agent;self.tasks={};self.queue=queue or JobQueue(mem.path)
  self.queue.register('web_alternatives',self.handle,max_attempts=3,concurrency=3,timeout=400,base=20.0,cap=300.0,on_retry=self.retrying,on_dead=self.dead)
  with mem.conn() as db:
   db.execute('CREATE TABLE IF NOT EXISTS web_alt_jobs(id TEXT PRIMARY KEY,user_id TEXT,session_id TEXT,status TEXT,created REAL,updated REAL,payload TEXT)')
   # Jobs the durable queue still owns resume after a restart; the rest were interrupted.
   db.execute("""UPDATE web_alt_jobs SET status='interrupted' WHERE status IN ('queued','running','retry_queued') AND id NOT IN
                 (SELECT json_extract(payload,'$.jid') FROM job_queue WHERE kind='web_alternatives' AND state IN ('queued','running','retrying'))""")
 def owned(self,u,s,jid):
  self.mem.session(u,s)
  with self.mem.conn() as db:r=db.execute('select * from web_alt_jobs where id=? and user_id=? and session_id=?',(jid,u,s)).fetchone()
  if not r:raise ValueError('جست‌وجوی جایگزین پیدا نشد.')
  row=dict(r);data=json.loads(row.pop('payload'));return dict(row,**data)
 def write(self,jid,status=None,**fields):
  with self.mem.conn() as db:
   row=db.execute('select status,payload from web_alt_jobs where id=?',(jid,)).fetchone()
   if not row:return
   data=json.loads(row[1]);data.update(fields)
   db.execute('update web_alt_jobs set status=?,payload=?,updated=? where id=?',(status or row[0],json.dumps(data,ensure_ascii=False),time.time(),jid))
 def start(self,u,s,query):
  query=text(query)[:200]
  if len(query)<2:raise ValueError('محصول موردنظر را بنویسید.')
  self.mem.session(u,s)
  with self.mem.conn() as db:
   running=db.execute("select id,payload from web_alt_jobs where user_id=? and session_id=? and status in ('queued','running','retry_queued')",(u,s)).fetchall()
   same=next((r['id'] for r in running if json.loads(r['payload']).get('query')==query),None)
   if same:return self.owned(u,s,same)
   if len(running)>=2:raise ValueError('دو جست‌وجوی وب در جریان است؛ کمی صبر کنید.')
   jid=secrets.token_urlsafe(18);now=time.time()
   db.execute('insert into web_alt_jobs values(?,?,?,?,?,?,?)',(jid,u,s,'queued',now,now,json.dumps({'query':query,'progress':[]},ensure_ascii=False)))
  job=self.queue.enqueue('web_alternatives',{'jid':jid,'u':u,'s':s,'query':query},idem_key='webalt:'+jid)
  task=self.queue.tasks.get(job['id'])
  if task:self.tasks[jid]=task;task.add_done_callback(lambda _:self.tasks.pop(jid,None))
  return self.owned(u,s,jid)
 async def handle(self,p,job):
  await self.run(p['jid'],p['u'],p['s'],p['query']);return {'jid':p['jid']}
 def retrying(self,job,delay,error):
  self.write(job['payload']['jid'],'retry_queued',retry_at=time.time()+delay,error='جست‌وجوی وب موقتاً ممکن نشد؛ در صف تلاش مجدد است.')
 def dead(self,job,error):
  self.write(job['payload']['jid'],'failed',error='جست‌وجوی وب پس از چند تلاش کامل نشد؛ دوباره تلاش کنید.')
 async def run(self,jid,u,s,query):
  self.write(jid,'running')
  profile=self.mem.profile(u,s)
  if profile.get('lat') is None and profile.get('neighborhood'):
   # Approximate a neighborhood by the centre of its catalogue shops.
   with self.agent.cat.conn() as db:
    row=db.execute('select avg(lat),avg(lon) from shops where location_label=? and lat is not null',(profile['neighborhood'],)).fetchone()
   if row and row[0]:profile=dict(profile,lat=row[0],lon=row[1])
  # Catalogue-first: record what the Torob dataset holds for this request.
  try:
   catalogue=await asyncio.to_thread(self.agent.cat.search,query[:160],self.mem.profile(u,s),5)
   matches=len({o['product_id'] for o in catalogue.get('offers',[])})
  except Exception:matches=None
  self.write(jid,catalogue_matches=matches)
  def progress(event):
   job=self.owned(u,s,jid);self.write(jid,progress=(job.get('progress',[])+[dict(event,at=time.time())])[-40:])
  try:
   result=await WebAlternatives(Path(self.mem.path).parent/'public-research.db').run(query,profile,progress,lambda r:self.write(jid,result=dict(r,catalogue_matches=matches)))
   result['catalogue_matches']=matches
   self.write(jid,'completed',result=result)
   self.mem.save_turn(u,s,'assistant','جایگزین‌های وب در شهر شما: '+str(len(result['items']))+' مورد (سرنخ؛ پیش از مراجعه تأیید شود).',{'web_alternatives':{'job_id':jid,'query':query,'count':len(result['items'])}})
  except asyncio.CancelledError:
   # Graceful shutdown keeps the job queued for the next process.
   self.write(jid,'queued' if self.queue.stopping else 'cancelled');raise
  except Exception as error:
   obs.error('webalt.failed',error,level='warn' if resilience.retryable(error) else 'error')
   if resilience.retryable(error):self.write(jid,'retry_queued',error='جست‌وجوی وب موقتاً ممکن نشد؛ در صف تلاش مجدد است.')
   else:self.write(jid,'failed',error='جست‌وجوی وب کامل نشد؛ دوباره تلاش کنید.')
   raise
import guard
EXTRACT_PROMPT+=guard.DATA_POLICY
