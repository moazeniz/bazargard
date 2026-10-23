"""Real buyer reviews for a catalogue product.

Persian reviews come from Digikala's public review API (only when the listing
title carries the product's exact model code). Foreign owner reviews are found
with BoxAPI web search; a quote is shown as a buyer's words only after it is
found verbatim in the fetched review page. When the page cannot be read from
here, only the model's one-line summary is shown, labelled as a web-search
summary with its link, never as a buyer quote. A summary groups recurring
pros/cons with links back to the reviews that support them, and English
excerpts are translated into Persian next to the original.
"""
import asyncio,hashlib,json,os,re,sqlite3,time
from pathlib import Path
import httpx
import llm
import resilience
from urllib.parse import urlsplit
from spec_harmonizer import ask,configured_model,squash,fold,text,quote_in,gateway_fetch,host_in,SKIP_HOSTS

VERSION='reviews-1-'+hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:10]
DK='https://api.digikala.com/v1'
UA={'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36','Accept':'application/json'}
MAX_DK_PAGES=3

SUMMARY_PROMPT='''You summarise genuine buyer reviews of ONE product for a Persian shopper.
Input: product title, Persian buyer reviews from Digikala (id, rate 1-5, text, likes) and English web excerpts (id, text, source).
Return ONLY JSON:
{"summary":"2-3 Persian sentences: overall owner sentiment and what matters most","pros":[{"text":"short Persian point","review_ids":["..."]}],"cons":[{"text":"...","review_ids":["..."]}],"seller_issues":[{"text":"delivery/seller/packaging complaints, not about the product itself","review_ids":["..."]}],"web":[{"id":"w1","kind":"owner_review|editorial|irrelevant","sentiment":"positive|negative|mixed|neutral","fa":"faithful Persian translation of the excerpt"}]}
Rules: use only what reviewers wrote; every point must cite review_ids that actually say it; a point needs at least one id. Keep 2-6 pros and cons, most frequent first. Excerpts that are spec sheets, marketing, news or a professional reviewer are "editorial"; about another product are "irrelevant". Translate owner reviews faithfully (no added claims).'''


WEB_REVIEW_PROMPT='''Use web search to find genuine owner/customer reviews of exactly this product: {title} ({name}). First identify its English brand/model name, then search retailer customer-review pages (Amazon, AliExpress, Best Buy, Walmart, Flipkart, regional retailers), user forums and Reddit threads.
Return ONLY a JSON object {{"product_en":"English model name","reviews":[{{"url":"https page containing the review","quote":"the reviewer's own words copied verbatim from that page, 1-3 sentences, original language","rating":<1-5 or null>,"summary_fa":"one faithful Persian sentence of what this reviewer says"}}]}} with up to 10 different reviewers.
Rules: only this exact product; no editorial/professional reviews, spec sheets, marketing or news; never paraphrase or translate inside quote; skip a reviewer if you cannot copy their words.'''


ANTHROPIC_REVIEW_PROMPT='Find genuine owner/customer reviews (e.g. Amazon customer reviews, Reddit threads, forums, retailer reviews) of exactly this product: {title} ({name}). Quote 6-10 different reviewers in their own words, each quote cited to its source page. No editorial or professional reviews.'


def cited_reviews(citations):
 """Reviews from verbatim provider citations (cited page text with URL)."""
 out=[];seen=set()
 for c in citations:
  quote=text(c.get('text'));url=c.get('url') or ''
  if not c.get('verbatim') or len(quote)<40 or not url.startswith('http') or squash(quote) in seen:continue
  seen.add(squash(quote))
  rate=re.search(r'Rated\s*([1-5])(?:\.0)?\s*out of\s*5|([1-5])(?:\.0)?\s*out of\s*5 stars',quote,re.I)
  date=re.search(r'\b(20\d\d-\d\d-\d\d)',quote)
  clean=re.sub(r'\b20\d\d-\d\d-\d\dT[\d:]+\b|Rated\s*[1-5](?:\.0)?\s*out of\s*5','',quote).strip()
  out.append({'rate':int(rate.group(1) or rate.group(2)) if rate else None,'date':date.group(1) if date else '','display':re.sub(r'\n{2,}','\n',clean)[:900],'id':'w'+str(len(out)+1),'source':re.sub(r'^www\.','',url.split('/')[2]),
              'url':url,'title':text(c.get('title'))[:160],'text':quote[:900],'verified':'provider_citation','lang':'en' if re.search(r'[a-zA-Z]{4}',quote) and not re.search(r'[؀-ۿ]',quote) else 'fa'})
 return out[:14]


def latin_words(value):return re.findall(r'[a-z0-9]+',fold(value))
def model_codes(title):
 """Model codes from a listing title (letters+digits, e.g. T13, A55, MWB010)."""
 return list(dict.fromkeys(w for w in latin_words(title) if len(w)>=3 and re.search(r'\d',w) and re.search(r'[a-z]',w)))
ACCESSORY_HEADS={'کاور','قاب','گلس','محافظ','کیف','شارژر','کابل','باتری','بند','پایه','استیکر','برچسب','لوازم','قطعه','فیلتر','کیس','آداپتور','اداپتور','هولدر','ابزار','پد','بالشتک','فوم','سری'}
def head(title):
 words=fold(title).split()
 return words[0] if words else ''
def best_match(products,codes,words,title=''):
 """Digikala result whose title contains every model code as a whole word: never an
 accessory made for the product (cover, case, charger...), then same product noun,
 then the most title words in common (T13 ANC beats T13 ANC2 and T13X)."""
 best=None;own=head(title);accessory=own in ACCESSORY_HEADS or 'مناسب برای' in fold(title)
 for p in products:
  name=p.get('title_fa') or '';cand=set(latin_words(name+' '+(p.get('title_en') or '')))
  if not (codes and all(c in cand for c in codes)):continue
  if not accessory and (head(name) in ACCESSORY_HEADS or 'مناسب برای' in fold(name)):continue
  # Other model codes in the candidate (ANC2, T13X) mean a different variant.
  variants=sum(1 for w in cand-set(words) if re.search(r'\d',w) and re.search(r'[a-z]',w))
  score=(head(name)==own,sum(w in cand for w in words),-variants,-len(cand-set(words)))
  if best is None or score>best[0]:best=(score,p)
 return best[1] if best else None


def model_tokens(title):
 t=fold(title)
 return [x for x in {squash(m) for m in re.findall(r'[a-z0-9][a-z0-9\-]{2,}',t)} if re.search(r'\d',x) and re.search(r'[a-z]',x) and len(x)>=4]


class UserReviews:
 def __init__(self,path,client_factory=None,http=None,fetcher=None):
  self.path=str(path);self.client_factory=client_factory;self.http=http;self.fetcher=fetcher or gateway_fetch
  with sqlite3.connect(self.path) as db:db.execute('CREATE TABLE IF NOT EXISTS user_reviews_cache(key TEXT PRIMARY KEY,created REAL,expires REAL,payload TEXT)')

 def cached(self,key):
  with sqlite3.connect(self.path) as db:row=db.execute('select payload from user_reviews_cache where key=? and expires>?',(key,time.time())).fetchone()
  return json.loads(row[0]) if row else None
 def store(self,key,value,seconds):
  with sqlite3.connect(self.path) as db:
   now=time.time();db.execute('insert or replace into user_reviews_cache values(?,?,?,?)',(key,now,now+seconds,json.dumps(value,ensure_ascii=False)))

 async def get(self,url,params=None):
  client=self.http or httpx.AsyncClient(timeout=15,headers=UA)
  try:
   # Digikala answers Iranian addresses; transient errors are retried with backoff.
   r=await resilience.call('digikala',lambda:client.get(url,params=params),attempts=3,timeout=15)
   return r.json() if r.status_code==200 else None
  except (httpx.HTTPError,ValueError,resilience.CircuitOpen,asyncio.TimeoutError):return None
  finally:
   if not self.http:await client.aclose()

 async def digikala(self,product,progress):
  # Codes come from the title; the dataset's model field is often an internal slug.
  title=text(product.get('persian_title'));codes=model_codes(title)
  if not codes:codes=model_codes(text(product.get('model')).replace('-',''))[:1] if re.search(r'\d',text(product.get('model'))) and len(text(product.get('model')))<=20 else []
  if not codes:return {'status':'no_model_code','reviews':[]}
  progress({'stage':'digikala_search'})
  # Results of both searches are pooled so the exact variant wins over near names.
  words=latin_words(title);pool={}
  for q in dict.fromkeys([title[:80],' '.join(words)[:60],' '.join(codes[:2])+' '+text(product.get('brand_name') or '')]):
   data=await self.get(DK+'/search/',{'q':q})
   for item in ((data or {}).get('data') or {}).get('products') or []:pool.setdefault(item.get('id'),item)
  found=best_match(list(pool.values()),codes,words,title)
  if not found:return {'status':'not_found','reviews':[]}
  pid=found['id'];reviews=[];total=None
  for page in range(1,MAX_DK_PAGES+1):
   progress({'stage':'digikala_reviews','page':page})
   data=await self.get(f'{DK}/rate-review/products/{pid}/',{'page':page})
   d=(data or {}).get('data') or {}
   for c in d.get('comments') or []:
    body=text(c.get('body'))
    if not body:continue
    reviews.append({'id':'d'+str(c.get('id')),'source':'digikala','rate':int(c['rate']) if str(c.get('rate') or '').isdigit() else None,'title':text(c.get('title')) if c.get('title') not in (None,'None') else '',
                    'text':body[:1200],'date':text(c.get('created_at')),'buyer':c.get('review_user_type')=='buyer' or bool(c.get('is_buyer')),
                    'likes':(c.get('reactions') or {}).get('likes',0),'dislikes':(c.get('reactions') or {}).get('dislikes',0),
                    'pros':[text(x) for x in c.get('advantages') or []][:5],'cons':[text(x) for x in c.get('disadvantages') or []][:5],'lang':'fa'})
   pager=d.get('pager') or {};total=pager.get('total_items',total)
   if page>=(pager.get('total_pages') or 1):break
  rates=[r['rate'] for r in reviews if r['rate']]
  dist={str(n):sum(1 for r in rates if r==n) for n in range(1,6)}
  return {'status':'ok' if reviews else 'empty','product_id':pid,'title':found.get('title_fa'),'url':'https://www.digikala.com/product/dkp-'+str(pid)+'/',
          'total':total or len(reviews),'average':round(sum(rates)/len(rates),2) if rates else None,'distribution':dist,'reviews':reviews}

 async def web(self,product,progress):
  """Foreign owner reviews via the provider's web search. Anthropic citations are
  verbatim page text and are used directly (citations only, never model prose);
  BoxAPI answers are verified against fetched pages, else labelled summaries."""
  empty={'reviews':[],'summaries':[]}
  if not llm.configured() or os.getenv('SPEC_MODEL_SEARCH','1')=='0':return empty
  name=text(product.get('model')) or text(product.get('persian_title'))
  progress({'stage':'web_reviews'})
  try:
   found=await llm.web_search({'anthropic':ANTHROPIC_REVIEW_PROMPT.format(title=text(product.get('persian_title')),name=name),'boxapi':WEB_REVIEW_PROMPT.format(title=text(product.get('persian_title')),name=name)},
                              max_tool_calls=5,context='medium',effort='low',max_tokens=5000,timeout=120,purpose='web_reviews',client_factory=self.client_factory)
   if found['provider']=='anthropic':return {'reviews':cited_reviews(found['citations']),'summaries':[]}
   data=llm.parse_json(found['text'])
  except (llm.LLMError,ValueError):return empty
  items=[r for r in (data or {}).get('reviews') or [] if isinstance(r,dict)][:12] if isinstance(data,dict) else []
  cited={c['url'] for c in found['citations']}|set(found['sources']);titles={c['url']:c.get('title','') for c in found['citations']}
  urls=list(dict.fromkeys(text(r.get('url')) for r in items if text(r.get('url')).startswith('https://')))[:8]
  deadline=time.monotonic()+20
  async def read(url):
   try:return url,await asyncio.to_thread(self.fetcher,url,deadline)
   except Exception:return url,{'url':url,'status':'unavailable'}
  pages=dict(await asyncio.gather(*(read(u) for u in urls)))
  out=[];summaries=[];seen=set()
  for r in items:
   url=text(r.get('url'));quote=text(r.get('quote'))[:900];page=pages.get(url) or {};host=urlsplit(url).hostname or ''
   if url not in pages or host_in(host,SKIP_HOSTS-{'reddit.com'}) or squash(quote) in seen:continue
   seen.add(squash(quote));source=re.sub(r'^www\.','',host);rate=r.get('rating') if r.get('rating') in (1,2,3,4,5) else None
   if page.get('status')=='readable':
    # Shown as a buyer's words only when the page itself contains them.
    if len(quote)<25 or not quote_in(quote,page.get('text','')):continue
    out.append({'rate':rate,'date':'','display':quote,'id':'w'+str(len(out)+1),'source':source,'url':url,'title':text(page.get('title') or titles.get(url))[:160],'text':quote,'verified':'page_quote',
                'lang':'en' if re.search(r'[a-zA-Z]{4}',quote) and not re.search(r'[؀-ۿ]',quote) else 'fa'})
   elif url in cited and text(r.get('summary_fa')):
    summaries.append({'id':'s'+str(len(summaries)+1),'source':source,'url':url,'title':text(titles.get(url))[:160],'text':text(r.get('summary_fa'))[:300],'rate':rate,'kind':'search_summary',
                      'label':'خلاصه جست‌وجوی وب — متن صفحه از این‌جا قابل بررسی نبود؛ نقل‌قول مستقیم خریدار نیست'})
  return {'reviews':out[:14],'summaries':summaries[:8]}

 async def collect(self,product,progress=lambda e:None):
  key='rv:'+hashlib.sha256(json.dumps([VERSION,configured_model(),product.get('product_id'),product.get('persian_title')]).encode()).hexdigest()
  hit=self.cached(key)
  if hit:return dict(hit,cached=True)
  dk,found=await asyncio.gather(self.digikala(product,progress),self.web(product,progress));web=found['reviews']
  progress({'stage':'summary'})
  ranked=sorted(dk['reviews'],key=lambda r:(-(r['likes']-r['dislikes']),-len(r['text'])))[:40]
  data=None
  if ranked or web:
   data=await ask(SUMMARY_PROMPT,{'product':product.get('persian_title'),'digikala':[{'id':r['id'],'rate':r['rate'],'text':r['text'][:500],'likes':r['likes'],'pros':r['pros'],'cons':r['cons']} for r in ranked],
                                 'web':[{'id':w['id'],'text':w['text'],'source':w['source']} for w in web]},max_tokens=4000,timeout=100,client_factory=self.client_factory)
  ids={r['id'] for r in dk['reviews']}|{w['id'] for w in web}
  def points(name):
   out=[]
   for p in (data or {}).get(name) or []:
    if not isinstance(p,dict):continue
    refs=[i for i in p.get('review_ids') or [] if i in ids]
    if refs and text(p.get('text')):out.append({'text':text(p['text'])[:200],'review_ids':refs[:12],'count':len(refs)})
   return sorted(out,key=lambda x:-x['count'])
  kinds={w.get('id'):w for w in (data or {}).get('web') or [] if isinstance(w,dict)}
  web_reviews=[]
  for w in web:
   k=kinds.get(w['id'],{})
   if k.get('kind')!='owner_review':continue
   web_reviews.append(dict(w,sentiment=k.get('sentiment'),fa=text(k.get('fa'))[:1200] if w['lang']=='en' else w['text']))
  result={'version':VERSION,'product_id':product.get('product_id'),'created':time.time(),'digikala':{k:v for k,v in dk.items() if k!='reviews'},
          'reviews':dk['reviews'][:60],'web_reviews':web_reviews,'web_summaries':found['summaries'],'summary':text((data or {}).get('summary'))[:800],
          'pros':points('pros'),'cons':points('cons'),'seller_issues':points('seller_issues'),
          'status':'ok' if dk['reviews'] or web_reviews or found['summaries'] else 'none',
          'notice':'نظرها نوشته خود خریداران است: از دیجی‌کالا (فارسی) و منابع خارجی (ترجمه‌شده، با متن اصلی). خلاصه را دستیار از همین نظرها ساخته است.'}
  self.store(key,result,86400 if result['status']=='ok' else 3600)
  return result
import guard
SUMMARY_PROMPT+=guard.DATA_POLICY
