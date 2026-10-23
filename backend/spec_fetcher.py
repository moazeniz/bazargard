"""Dedicated bounded, multi-round public static-spec fetching.

Manufacturer-first discovery and document fallback. All values remain literal,
field-scoped evidence. Public cache contains no buyer/session/seller context.
"""
import hashlib,json,re,time,sqlite3
from pathlib import Path
from urllib.parse import urlencode,urlsplit,urljoin,parse_qs
from catalog import normalize
from product_data import resolve,scalar,FIELD_META,VERSION
from spec_schema import category,comparable
from public_research import fetch,host_in,source_kind,model_identity

MAX_ROUNDS=3;MAX_DOCUMENTS=6;SECONDS=65
FETCH_VERSION='spec-v4-'+hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:10]
MANUFACTURERS={'msi.com','microsoft.com','hp.com','hpcloud.hp.com','samsung.com','sony.com','apple.com','asus.com','lenovo.com','dell.com','jbl.com','mi.com','delonghi.com','delonghigroup.com','hikvision.com','dahuasecurity.com','schneider-electric.com','se.com','grundfos.com','cat.com','honda.com','bosch-home.com','philips.com'}
REFERENCES={'gsmarena.com','devicespecifications.com','soundguys.com','rtings.com','notebookcheck.net','icecat.biz'}
RETAILERS={'bhphotovideo.com','delonghi.ph','digikey.com','mouser.com','rs-online.com','manuals.co.uk'}
MIRRORS={'files.bbystatic.com','cdn.cnetcontent.com','doi-product-assets.s3.amazonaws.com','device.report'}
VARIANT={'ram','storage','cpu','igpu','dgpu','vram','gpu','battery','weight','dimensions','power','voltage','current','cross_section','cores','length','clothing_size','color','lens'}
NEVER_WEB={'condition','os','accessories','brand','country','model_label'}

# Public discovery leads, never facts: every request still fetches and validates.
# Generic search/linked-document discovery works independently of this seed file.
LEADS_PATH=Path(__file__).with_name('spec_source_leads.json')

def kind(url):
 host=urlsplit(url).hostname or ''
 return 'manufacturer' if host_in(host,MANUFACTURERS) else 'spec_reference' if host_in(host,REFERENCES) else 'retailer_spec' if host_in(host,RETAILERS) else 'document_mirror' if host_in(host,MIRRORS) else 'public_page'

def identity(product):
 title=str(product.get('persian_title') or '').translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹','0123456789'))
 for fa,en in [('سرفیس بوک','Surface Book'),('الیت بوک','EliteBook'),('الیتبوک','EliteBook'),('پروبوک','ProBook'),('آیفون','iPhone'),('ایفون','iPhone'),('ایر','Air'),('پرو مکس','Pro Max'),('پرو','Pro'),('مکس','Max')]:title=title.replace(fa,en)
 m=re.search(r'\biPhone\s*(Air|\d{1,2}[a-z]?(?:\s+(?:Pro\s+Max|Pro|Plus|Mini))?)\b',title,re.I)
 if m:return 'iPhone '+m[1]
 if category(product)=='laptop':
  for pattern in [r'Victus\s*15[ -]*(FA[0-9A-Z]+)',r'Cyborg\s*15\s*(A[0-9]+[A-Z]+)',r'LOQ\s*15[A-Z]+[0-9]+',r'WF[0-9]{4}[A-Z]+']:
   m=re.search(pattern,title,re.I)
   if m:return m[0].strip()
 patterns=[r'Surface\s+Book\s*(?:\(?([123])(?:st|nd|rd)?(?:\s*Gen)?\)?)?',r'(EliteBook|ProBook|Notebook)\s*(\d{3,4})\s*G\s*(\d{1,2})',r'\b(?:Galaxy\s+)?(A\d{2,3}|S\d{2,3})(?:\s*(Ultra|Plus|FE))?\b',r'(?:WH|WF)[ -]?(?:CH)?\d{3,4}[A-Z0-9]*',r'EC[ .-]?\d{3,4}(?:[.][A-Z]{1,2})?']
 for n,p in enumerate(patterns):
  m=re.search(p,title,re.I)
  if not m:continue
  if n==0:return 'Surface Book '+(m[1] or '1')
  if n==1:return ('EliteBook ' if m[1].lower()=='elitebook' else 'HP ')+m[2]+' G'+m[3]
  return m[0].strip()
 for entry in product.get('specs',[]):
  if normalize(entry.get('key')) in ('پارت نامبر','کد فنی','شماره فنی'):
   value=str(entry.get('value','')).strip()
   if re.fullmatch(r'[a-z0-9-]{5,40}',value,re.I) and normalize(value) in normalize(title):return value
 return model_identity(dict(product,persian_title=title))

def model_pattern(model):
 if re.match(r'Surface Book [123]$',model,re.I):
  n=model[-1];generation={'1':'1st','2':'2nd','3':'3rd'}[n]
  return r'\bsurface\s+book\s*(?:'+n+r'\b|\(?'+generation+r'[\W_]+gen\)?)'
 tokens=re.findall(r'[a-z]+|\d+',model.lower())
 if model.lower().startswith('hp '):tokens=tokens[1:]
 return r'(?<![a-z0-9])'+r'[\W_]*'.join(map(re.escape,tokens))+r'(?![a-z0-9])' if tokens else r'(?!)'

def matches_model(product,title,text,document_format=None):
 model=identity(product);pattern=model_pattern(model)
 # PDF title metadata is frequently empty; the document's first page heading
 # can establish its model, never an isolated mention deep in another review.
 matched=bool(model and re.search(pattern,title,re.I)) and not bool(re.search(r'\bvs\.?\b|versus|compare|best.{0,30}phones',title,re.I))
 if matched and re.match(r'iPhone \d+[a-z]?$',model,re.I) and re.search(pattern+r'\s+(?:Pro|Plus|Mini|Max)\b',title,re.I):matched=False
 if not matched and document_format=='pdf':matched=bool(model and re.search(pattern,text[:1800],re.I))
 return {'model':model,'status':'exact_family' if matched else 'mismatch_or_unproven'}

# Label-bound patterns permit HTML table/definition-list line breaks. Options
# stay candidates and cannot be guessed to be installed configuration.
RULES={
 'screen':r'(?:screen(?: size)?|display size(?: \(diagonal\))?|size|display diagonal)\s*[:：]?\s*(\d+(?:\.\d+)?\s*(?:inches|inch|in\b|["”″]))|(\d+(?:\.\d+)?-inch)(?=[^\n]{0,35}(?:display|OLED))',
 'resolution':r'(?:resolution)\s*[:：]?\s*(\d{3,4}\s*[x×*]\s*\d{3,4})',
 'camera_resolution':r'(?:effective pixels|resolution|image sensor)\s*[:：]?\s*(\d+(?:\.\d+)?\s*(?:MP|megapixels))',
 'ram':r'(?:RAM|memory(?: size)?)\s*[:：]?\s*(\d+\s*(?:GB|TB))|(\d+\s*GB)\s+RAM',
 'storage':r'(?:internal storage|storage|SSD)\s*[:：]?\s*(\d+\s*(?:GB|TB))',
 'cpu':r'(?:chipset|chip|processor|CPU(?: model)?)\s*[:：]?\s*((?:Exynos\s+\d{3,5}|(?:Qualcomm\s+)?Snapdragon\s+[\w +]{2,25}|(?:Intel\s+)?(?:Core\s+)?i[3579][ -]\d{4,5}[A-Z]{0,3}|(?:Apple\s+)?A\d{1,2}(?:\s+(?:Bionic|Pro))?|(?:Apple\s+)?M[1-9](?:\s+(?:Pro|Max|Ultra))?))',
 'ram_type':r'\b((?:LP)?DDR[345](?:X)?)\b',
 'storage_type':r'\b(SSD(?:\s+M\.2)?|eMMC|UFS\s+\d(?:\.\d)?)\b',
 'weight':r'(?:weight(?: \(approx\.?\))?)\s*[:：]?\s*(\d+(?:\.\d+)?\s*(?:kg|g)\b)|(?:weight)[^\n]{0,40}?\((\d+(?:\.\d+)?\s*(?:grams|kilograms))\)',
 'dimensions':r'(?:dimensions(?: \([^\n)]{0,25}\))?|size LxHxD)\s*[:：]?\s*(\d+(?:\.\d+)?\s*[x×]\s*\d+(?:\.\d+)?\s*[x×]\s*\d+(?:\.\d+)?\s*(?:mm|cm))',
 'bluetooth':r'\bBluetooth(?:®|™)?\s*(?:Wireless\s*|version\s*|Ver\.?\s*|v\.?\s*)?(\d(?:\.\d)?)\b',
 'wifi':r'\b((?:Wi-Fi\s*)?(?:IEEE\s*)?802\.11(?:ac|ax|be|[abgn])(?:/(?:ac|ax|be|[abgn]))*)',
 'panel':r'\b(Super AMOLED|Dynamic AMOLED(?:\s+2X)?|AMOLED|OLED|IPS|PixelSense(?:™)?)\b',
 'touch':r'(?:Touch)\s*[:：]\s*(\d+[ -]point multi-touch|Yes|No)',
 'refresh':r'(?:refresh rate\s*[:：]?\s*)?(\d{2,3}\s*Hz)\b',
 'battery_mah':r'\b(\d{3,5}\s*mAh)\b',
 'battery':r'(?:battery|capacity)\s*[:：]?\s*(\d+(?:\.\d+)?\s*Wh)\b',
 'driver':r'(?:driver(?: unit| size|s)?)\s*[:：]?\s*(\d+(?:\.\d+)?\s*mm)\b',
 'impedance':r'(?:impedance)\s*[:：]?\s*(\d+(?:\.\d+)?\s*(?:ohms?|Ω))',
 'power':r'(?:input power(?: \(W\))?|absorbed power|rated power|power consumption)\s*[:：.]*\s*(\d+(?:\.\d+)?\s*(?:kW|W))\b',
 'capacity':r'(?:water tank capacity|tank capacity)\s*[:：.]*\s*(\d+(?:\.\d+)?\s*(?:litres?|liters?|L))\b',
 'pressure':r'(?:pump pressure|pressure)\s*[:：.]*\s*(\d+(?:\.\d+)?\s*bars?)\b',
 'voltage':r'(?:rated voltage|input voltage|voltage)\s*[:：.]*\s*(\d+(?:\.\d+)?\s*V)\b',
 'current':r'(?:rated current|current rating)\s*[:：.]*\s*(\d+(?:\.\d+)?\s*A)\b',
 'cross_section':r'(?:cross.section(?:al area)?)\s*[:：]?\s*(\d+(?:\.\d+)?\s*mm[²2])',
 'length':r'(?:cable length|length)\s*[:：]?\s*(\d+(?:\.\d+)?\s*(?:mm|cm|m))\b',
 'conductor':r'(?:conductor material)\s*[:：]?\s*(Copper|Aluminium|Aluminum)',
 'lens':r'(?:lens|focal length)\s*[:：]?\s*(\d+(?:\.\d+)?\s*mm)\b',
 'ip_rating':r'\b(IP[456][0-9])\b',
 'material':r'(?:casing|body material|housing material)\s*[:：]?\s*(Magnesium|Aluminium|Aluminum|Plastic|Stainless steel)',
}

def extract(product,doc):
 text=doc.get('text','');match=matches_model(product,doc.get('title',''),text,doc.get('format'));data=resolve(product);relevant=set(data['schema']);results=[]
 if doc.get('status')!='readable':return [],match
 # Spec tables precede measured benchmarks, review prose and unrelated offers.
 text=re.split(r'\n(?:Our Tests|Similarly priced|Related products|Customer reviews)\b',text,flags=re.I)[0]
 for field,pattern in RULES.items():
  if field not in relevant or field in NEVER_WEB:continue
  found=list(re.finditer(pattern,text,re.I));values=[]
  for m in found:
   value=next(g for g in m.groups() if g is not None).strip();start=max(text.rfind('\n',0,m.start())+1,m.start()-40);end=text.find('\n',m.end());end=len(text) if end<0 else end
   context=text[start:min(end,m.end()+100)]
   # Keep a literal, contiguous bounded quote and its field association.
   quote=context[:260].strip();numeric=scalar(value.replace('-inch',' inch'),field,FIELD_META[field][1]);unit=FIELD_META[field][1]
   status='accepted';scope='shared_model'
   if match['status']!='exact_family':status='model_or_variant_unproven'
   elif doc.get('truncated'):status='partial_document'
   elif kind(doc['url'])=='public_page':status='untrusted_source'
   if field in VARIANT:
    scope='variant_statement'
    if re.search(r'\bor\b|\boptions?\b|up to|starting|depending|select|/|shipping|package|gross|maximum|review unit|our test',context,re.I):status='variant_options'
    # A single family value does not establish installed RAM/storage/CPU on a
    # laptop with unknown configuration. Confirm exact title/SKU or anchors.
    if category(product)=='laptop' and field in ('ram','storage','cpu','igpu','dgpu','vram'):
     known=data['facts'].get(field)
     if not known or known['status']=='conflict':status='configuration_unproven'
     elif unit and known['numeric']!=numeric:status='configuration_conflict'
     elif not unit and comparable(known['value']) not in comparable(value) and comparable(value) not in comparable(known['value']):status='configuration_conflict'
   if field=='lens' and len(re.findall(r'\d+(?:\.\d+)?\s*mm',context,re.I))>1:status='variant_options'
   if field=='weight' and re.search(r'shipping|package|gross|starting|incl.*(?:box|pack)',text[max(0,start-50):start]+' '+context,re.I):status='variant_options'
   if unit and numeric is None:status='unsupported_unit'
   # Never accept an explicit unit prefix such as GBps or an unrelated Hz CPU.
   if field=='refresh' and not re.search(r'display|screen|refresh|amoled|oled|ips',context,re.I):status='field_context_unproven'
   if field=='ram' and re.search(r'graphics|GPU|VRAM',context,re.I):status='field_context_unproven'
   if field=='battery_mah' and not re.search(r'battery|li.ion|capacity|mAh',context,re.I):status='field_context_unproven'
   values.append({'field':field,'value':value,'numeric':numeric,'unit':unit,'status':status,'quote':quote,'model':match['model'],'model_match':match['status'],'variant_match':scope,'source_title':doc.get('title',''),'source_type':kind(doc['url'])})
  if field=='wifi' and values:
   values=[max(values,key=lambda v:len(v['value']))]
  tokens={v['numeric'] if v['numeric'] is not None else comparable(v['value']) for v in values}
  if len(tokens)>1:
   for v in values:v['status']='ambiguous_values'
  results.extend(values[:2])
 # One short quote per accepted field; cap total source prose at 180 words.
 output=[];words=0
 for v in results:
  n=len(v['quote'].split())
  if words+n>180:continue
  output.append(v);words+=n
 return output,match

class SpecFetcher:
 def __init__(self,path,fetcher=None):
  self.path=str(path);self.fetcher=fetcher or (lambda u,d:fetch(u,d,allow_pdf=True));Path(path).parent.mkdir(parents=True,exist_ok=True)
  with self.conn() as db:db.execute('CREATE TABLE IF NOT EXISTS spec_research(key TEXT PRIMARY KEY,created REAL,expires REAL,payload TEXT)')
 def conn(self):return sqlite3.connect(self.path,timeout=10)
 def investigate(self,product,progress=lambda e:None):
  data=resolve(product);model=identity(product);wanted=[k for k in data['schema'] if k not in NEVER_WEB and (k not in data['facts'] or data['facts'][k]['typed_status']=='unsupported_unit')]
  key=hashlib.sha256(json.dumps([FETCH_VERSION,VERSION,model,product.get('persian_title'),product.get('specs')],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
  with self.conn() as db:row=db.execute('SELECT payload FROM spec_research WHERE key=? AND expires>?',(key,time.time())).fetchone()
  if row:
   result=json.loads(row[0]);result['cached']=True;progress({'stage':'cache','model':model,'accepted_fields':result['accepted_fields']});return result
  deadline=time.monotonic()+SECONDS;sources=[];attempts=[];seen=set();accepted=set();queue=[]
  if LEADS_PATH.exists():queue=json.loads(LEADS_PATH.read_text()).get(normalize(model),[])
  if re.fullmatch(r'LC1D[0-9A-Z]{3,12}',model,re.I):queue.insert(0,'https://www.se.com/ww/en/product/download-pdf/'+model.upper())
  def read(url):
   try:return self.fetcher(url,deadline)
   except Exception as e:return {'url':url,'status':'timeout' if isinstance(e,TimeoutError) else 'unavailable'}
  for round_number in range(1,MAX_ROUNDS+1):
   gaps=[k for k in wanted if k not in accepted]
   if not model or not gaps or time.monotonic()>=deadline or len(sources)>=MAX_DOCUMENTS:break
   query=model+' specifications '+ ' '.join(gaps[:5] if round_number==1 else gaps[(round_number-2)*3:(round_number-2)*3+4])
   progress({'stage':'discovery','round':round_number,'model':model,'remaining_fields':gaps,'query':query})
   search_url='https://html.duckduckgo.com/html/?'+urlencode({'q':query})
   search=read(search_url);attempts.append({'round':round_number,'url':search_url,'status':search.get('status')})
   links=search.get('links',[])
   if not links and time.monotonic()<deadline:
    search_url='https://www.bing.com/search?'+urlencode({'q':query,'format':'rss'});search=read(search_url);attempts.append({'round':round_number,'url':search_url,'status':search.get('status')});links=search.get('links',[])
   for link in links[:30]:
    link=urljoin(search_url,link);p=urlsplit(link)
    if host_in(p.hostname or '',{'duckduckgo.com'}):link=parse_qs(p.query).get('uddg',[''])[0]
    if link.startswith('https://') and kind(link)!='public_page':queue.append(link)
   queue=list(dict.fromkeys(queue));queue.sort(key=lambda u:{'manufacturer':0,'spec_reference':1,'document_mirror':2,'retailer_spec':3}.get(kind(u),4))
   for url in list(queue):
    if url in seen:continue
    if len(sources)>=min(MAX_DOCUMENTS,round_number*2) or time.monotonic()>=deadline:break
    seen.add(url);progress({'stage':'source','round':round_number,'model':model,'url':url})
    doc=read(url);doc['retrieved_at']=time.time();doc['kind']=kind(doc['url'])
    facts,match=extract(product,doc);accepted.update(f['field'] for f in facts if f['status']=='accepted')
    # Follow advertised specification/datasheet links within remaining bounds.
    for link in doc.get('links',[]):
     full=urljoin(doc['url'],link)
     if re.search(r'\.pdf(?:\?|$)|specification|specs',full,re.I) and kind(full)!='public_page' and full not in seen and (matches_model(product,full.replace('-',' ').replace('_',' '),'')['status']=='exact_family' or (full.lower().endswith('.pdf') and urlsplit(full).hostname==urlsplit(doc['url']).hostname)):queue.append(full)
    doc.pop('text',None);doc.pop('links',None);doc.update(basic_specs=facts,match=match)
    sources.append(doc);progress({'stage':'evaluated','round':round_number,'model':model,'url':url,'status':doc['status'],'accepted_fields':sorted(accepted),'remaining_fields':[k for k in wanted if k not in accepted]})
  result={'product_id':product['product_id'],'model':model,'created':time.time(),'cached':False,'sources':sources,'attempts':attempts,'target_missing_fields':wanted,'accepted_fields':sorted(accepted),'remaining_fields':[k for k in wanted if k not in accepted],'status':'partial' if accepted else 'unavailable','limits':{'rounds':MAX_ROUNDS,'documents':MAX_DOCUMENTS,'seconds':SECONDS,'max_bytes':3000000},'notice':'مشخصات تبلیغ‌شده با شاهد منبع؛ موجودی یا وضعیت همین دستگاه فروشنده نیست.'}
  with self.conn() as db:
   now=time.time();db.execute('INSERT OR REPLACE INTO spec_research VALUES(?,?,?,?)',(key,now,now+(86400 if accepted else 900),json.dumps(result,ensure_ascii=False)));db.execute('DELETE FROM spec_research WHERE key IN (SELECT key FROM spec_research ORDER BY created DESC LIMIT -1 OFFSET 2000)')
  return result
