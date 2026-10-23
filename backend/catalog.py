"""Persian-normalized catalogue retrieval and evidence-aware ranking."""
import csv, json, sqlite3, re, math, os, unicodedata, functools
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from urllib.parse import urlencode
from evidence import SellerReports

TEHRAN = ZoneInfo('Asia/Tehran')
DIGITS = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩يك', '01234567890123456789یک')

def normalize(s):
    s = unicodedata.normalize('NFKC', str(s or '')).translate(DIGITS).lower()
    return re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', ' ', s.replace('\u200c', ' '))).strip()

def parsed(s, default=None):
    try: return json.loads(s) if s and s != 'null' else default
    except (ValueError, TypeError): return default

def spec_value(key,value):
    value=normalize(value)
    if normalize(key)=='نوع اتصال':
        if value.replace(' ','') in ('باسیم','سیمی','wired'):return 'با سیم'
        if value.replace(' ','') in ('بیسیم','wireless'):return 'بی سیم'
    return value

def haversine(a,b,c,d):
    if None in (a,b,c,d): return None
    x,y=math.radians(c-a),math.radians(d-b)
    return 6371*2*math.asin(min(1, math.sqrt(math.sin(x/2)**2+math.cos(math.radians(a))*math.cos(math.radians(c))*math.sin(y/2)**2)))

def opening(hours, at=None):
    now=at or datetime.now(TEHRAN)
    day=hours.get(now.strftime('%a').lower()) if isinstance(hours,dict) else None
    t=now.strftime('%H:%M:%S')
    prev=hours.get((now-timedelta(days=1)).strftime('%a').lower(),{}) if isinstance(hours,dict) else {}
    for shift in prev.get('shifts',[]):
        a,b=shift.get('start_time',''),shift.get('end_time','')
        if prev.get('status')=='declared' and a>b and t<b:
            return {'state':'open','label':f'طبق برنامه تا {b[:5]} باز','source':'dataset'}
    if not day: return {'state':'unknown','label':'ساعت کاری نامشخص','source':'dataset'}
    if day.get('status')=='closed': return {'state':'closed','label':'طبق برنامه امروز تعطیل است','source':'dataset'}
    if day.get('status') != 'declared': return {'state':'unknown','label':'ساعت کاری نامشخص','source':'dataset'}
    t=now.strftime('%H:%M:%S'); shifts=day.get('shifts',[])
    for s in shifts:
        a,b=s.get('start_time',''),s.get('end_time','')
        if a==b: continue
        if (a<=t<b if a<b else t>=a or t<b):
            return {'state':'open','label':f'طبق برنامه تا {b[:5]} باز','source':'dataset'}
    # A previous day's overnight shift can extend into this morning.
    prev=hours.get((now-timedelta(days=1)).strftime('%a').lower(),{})
    for s in prev.get('shifts',[]):
        a,b=s.get('start_time',''),s.get('end_time','')
        if prev.get('status')=='declared' and a>b and t<b:
            return {'state':'open','label':f'طبق برنامه تا {b[:5]} باز','source':'dataset'}
    return {'state':'closed','label':'طبق برنامه بسته','source':'dataset'}

def payment_matches(options, price, category, provider=None):
    out=[]
    for p in options or []:
        # Online-only and unknown-channel credit are not promised for in-person purchases.
        if p.get('channel')!='in_person': continue
        lo,hi=p.get('min_price_toman'),p.get('max_price_toman')
        if lo and price<lo or hi and price>hi: continue
        cats=p.get('category_names') or []
        if cats and normalize(category) not in [normalize(x) for x in cats]: continue
        if provider and normalize(provider).replace(' ','') not in normalize(p.get('provider_name')).replace(' ',''): continue
        out.append(p)
    return out

ALIASES={'ماکروفر':['مایکروویو','ماکروویو'],'مایکروویو':['ماکروفر','ماکروویو'],'ماکروویو':['مایکروویو','ماکروفر'],'جاروبرقی':['جارو'],'تی وی':['تلویزیون'],'لباسشویی':['لباس شویی'],'ظرفشویی':['ظرف شویی'],'پاوربانک':['پاور بانک'],'اسپیکر':['اسپیکر','بلندگو'],'بلندگو':['اسپیکر'],'کولرگازی':['کولر'],'هندزفری':['هدفون'],'iphone':['آیفون','اپل'],'samsung':['سامسونگ'],'sneakers':['کتونی','کفش'],'laptop':['لپ تاپ'],'headphones':['هدفون'],'airpods':['ایرپاد'],'xiaomi':['شیائومی'],'گوشی':['موبایل'],'موبایل':['گوشی'],'کتانی':['کفش','کتونی'],'کتونی':['کفش','کتانی'],'ایفون':['آیفون'],'هندزفری':['هدفون'],'لبتاب':['لپ تاپ'],'sony':['سونی'],'lenovo':['لنوو'],'asus':['ایسوس'],'nokia':['نوکیا'],'apple':['اپل'],'jbl':['جی بی ال'],'jabra':['جبرا']}

ACCESSORY_CATEGORIES=('لوازم جانبی','کیف و کاور','گلس','محافظ صفحه','تاچ و ال سی دی','قطعات','لوازم یدکی','باتری گوشی','شارژر','کابل','قاب')

class ClosingConnection(sqlite3.Connection):
    """`with conn:` commits/rolls back and then closes, so connections never pile up."""
    def __exit__(self,*exc):
        try:return super().__exit__(*exc)
        finally:self.close()

class Catalog:
    @staticmethod
    def fts_match(terms):
        groups=[]
        for t in terms:
            variants=[t]+[normalize(x) for x in ALIASES.get(t,[])]
            # Prefix match: Persian suffixes (بلوتوث → بلوتوثی، گوشی → گوشی‌ها) and Latin model stems.
            groups.append('('+' OR '.join('"'+v.replace('"','')+'"'+('*' if v.isascii() or len(v)>=4 else '') for v in variants)+')')
        return ' AND '.join(groups)

    LETTERS='ابپتثجچحخدذرزژسشصضطظعغفقکگلمنوهیآئ'+'abcdefghijklmnopqrstuvwxyz'
    def correct_terms(self,terms,c):
        """'Did you mean' for rare query words: the most frequent catalogue word one edit
        away (هدفن → هدفون). Words with digits and words already in the catalogue stay."""
        try:c.execute('create virtual table if not exists temp.vocab using fts5vocab(main,product_fts,row)')
        except sqlite3.Error:return terms,[]
        out=[];fixed=[]
        for t in terms:
            if re.search(r'\d',t) or len(t)<3:out.append(t);continue
            row=c.execute('select doc from temp.vocab where term=?',(t,)).fetchone();n=row[0] if row else 0
            if n>=5:out.append(t);continue
            edits={t[:i]+t[i+1:] for i in range(len(t))}|{t[:i]+t[i+1]+t[i]+t[i+2:] for i in range(len(t)-1)}
            edits|={t[:i]+ch+t[i+1:] for i in range(len(t)) for ch in self.LETTERS}|{t[:i]+ch+t[i:] for i in range(len(t)+1) for ch in self.LETTERS}
            edits={e for e in edits if len(e)>=3 and e!=t}
            best=None
            for chunk in (list(edits)[i:i+900] for i in range(0,len(edits),900)):
                for term,doc in c.execute('select term,doc from temp.vocab where term in ('+','.join('?'*len(chunk))+')',chunk):
                    if doc>=max(50,20*(n+1)) and (best is None or doc>best[1]):best=(term,doc)
            if best:out.append(best[0]);fixed.append({'from':t,'to':best[0]})
            else:out.append(t)
        return out,fixed

    def relaxations(self,terms,c):
        """Candidate term subsets, most faithful first. Words with digits (model
        codes, sizes) and the leading product noun are always kept."""
        def count(subset):
            try:return c.execute('select count(*) from product_fts where product_fts match ?',(self.fts_match(subset),)).fetchone()[0]
            except sqlite3.Error:return 0
        # Model codes/sizes anchor the search; without one, the leading product noun does.
        coded=any(re.search(r'\d',t) for t in terms)
        def brand(t):
            try:return len(t)>=3 and bool(c.execute('select 1 from products where brand_norm like ? limit 1',('%'+t+'%',)).fetchone())
            except sqlite3.Error:return False
        brands={t for t in terms if not re.search(r'\d',t) and brand(t)}
        # A named brand is a hard requirement: never silently relaxed.
        keep=lambda i,t:bool(re.search(r'\d',t)) or t in brands or (i==0 and not coded)
        unknown=[t for i,t in enumerate(terms) if not keep(i,t) and count([t])==0]
        current=[t for t in terms if t not in unknown]
        if unknown and current:yield current,unknown
        dropped=list(unknown)
        for i in range(len(current)-1,-1,-1):
            t=current[i]
            if keep(terms.index(t),t):continue
            current=current[:i]+current[i+1:];dropped.append(t)
            if current:yield current,list(dropped)

    def __init__(self,path):
        self.path=str(path)
        from product_data import ProjectionIndex
        self.projections=ProjectionIndex(Path(path).parent/'canonical-products.db')
    def conn(self):
        c=sqlite3.connect(self.path,timeout=20,factory=ClosingConnection);c.row_factory=sqlite3.Row
        c.create_function('distance',4,haversine,deterministic=True)
        return c
    def stats(self):
        with self.conn() as c:
            return {r['key']:parsed(r['value'],r['value']) for r in c.execute('select * from metadata')}
    def categories(self,q=''):
        with self.conn() as c:
            return [dict(r) for r in c.execute('select category_name,count(*) count from products where category_norm like ? group by category_name order by count desc limit 25',('%'+normalize(q)+'%',))]
    def shop(self,sid):
        with self.conn() as c:
            r=c.execute('select * from shops where shop_id=?',(sid,)).fetchone()
        if not r: return None
        d=dict(r)
        for key in ['contacts','payment_options','weekly_hours','category_names']: d[key]=parsed(d[key], [] if key!='weekly_hours' else {})
        d['opening']=opening(d['weekly_hours'])
        d['maps_url']='https://www.google.com/maps/search/?'+urlencode({'api':1,'query':f"{d['address']} تهران"})
        return d
    def shop_offers(self,shop_id,query='',profile=None,limit=40):
        """A store's offers: those matching the shopper's query, else its cheapest listings."""
        p={k:v for k,v in (profile or {}).items() if k not in ('lat','lon','radius_km','neighborhood','budget_min_toman','budget_max_toman','open_now')}
        if query.strip():
            r=self.search(query,p,limit,full=True,shop_id=shop_id)
            if r['offers']:return r['offers']
        with self.conn() as c:ids=[x[0] for x in c.execute('select offer_id from offers where shop_id=? and price_toman>0 order by price_toman limit ?',(shop_id,int(limit)))]
        return self.search('',{},limit,offer_ids=ids,full=True)['offers'] if ids else []
    def product(self,pid):
        with self.conn() as c:
            r=c.execute('select * from products where product_id=?',(pid,)).fetchone()
        if not r: return None
        d=dict(r);d['specs']=parsed(d['specs'],[]);d['image_urls']=parsed(d['image_urls'],[])
        d['canonical']=self.projections.read(d)
        return d
    def visit_time(self, profile):
        try:
            at=datetime.fromisoformat(profile.get('visit_at',''))
            return at.replace(tzinfo=TEHRAN) if at.tzinfo is None else at.astimezone(TEHRAN)
        except (ValueError,TypeError):return datetime.now(TEHRAN)

    def hydrate_offer(self, offer, profile=None, reports=None, at=None, canonical=True):
        p=profile or {};reports=reports or SellerReports();d=dict(offer)
        for k in ['image_urls','specs','contacts','payment_options','weekly_hours']:
            if isinstance(d.get(k),str):d[k]=parsed(d[k],{} if k=='weekly_hours' else [])
        for k in ['seller_report','reported_price_toman','price_report','reported_hours','indoor','merchant_quote']:
            d.pop(k,None)
        d['availability']='unknown';d['freshness']='dataset_only';d['seller_report_expired']=False
        d['effective_price_toman']=d['price_toman'];d['price_basis']='dataset'
        latest=reports.latest(d['shop_id'],d['product_id'])
        if latest:
            d['seller_report']=latest;d['seller_report_expired']=not reports.fresh(latest)
        price=reports.price_updates().get((d['shop_id'],d['product_id']))
        if price:
            d['reported_price_toman']=price['price'];d['effective_price_toman']=price['price']
            d['price_basis']=price['source'];d['price_report']=price
        live=reports.latest(d['shop_id'],d['product_id'],[e for e in reports.actionable if e.get('available') is not None or e['status']=='unavailable'])
        if live:
            if live.get('available') is not None or live['status']=='unavailable':
                d['availability']=live['source']+('_available' if live.get('available')==1 and live['status']!='unavailable' else '_unavailable')
        quote=reports.latest(d['shop_id'],d['product_id'],[e for e in reports.real if e['source']=='merchant_link'])
        if quote:d['merchant_quote']=quote.get('details',{})
        at=at or self.visit_time(p)
        d['dataset_opening']=opening(d.get('weekly_hours',{}),at)
        d['opening']=reports.opening(d['shop_id'],d['dataset_opening'],at,TEHRAN)
        d['indoor']=reports.indoor(d['shop_id'])
        d['in_person_credit']=payment_matches(d.get('payment_options',[]),d['effective_price_toman'],d['category_name'],p.get('payment_provider'))
        credit_report=reports.latest(d['shop_id'],d['product_id'],reports.active)
        if credit_report and (credit_report.get('details') or {}).get('credit_available') is False:
            d['in_person_credit']=[]
        d['distance_km']=haversine(p.get('lat'),p.get('lon'),d.get('lat'),d.get('lon'))
        if d['distance_km'] is not None:d['distance_km']=round(d['distance_km'],2)
        from product_data import offer_claims
        # Typed spec projection is costly; full listings resolve it lazily for spec filters only.
        if canonical and not d.get('canonical'):d['canonical']=self.projections.read(d)
        d['offer_spec_claims']=offer_claims(d)
        return d

    def with_canonical(self,offers):
        for d in offers:
            if not d.get('canonical'):d['canonical']=self.projections.read(d)
        return offers

    def eligible(self,d,p,reports=None):
        price=d['effective_price_toman'];reports=reports or SellerReports()
        if price<=0:return False
        if p.get('budget_max_toman') is not None and price>p['budget_max_toman']:return False
        if p.get('budget_min_toman') is not None and price<p['budget_min_toman']:return False
        if self.brand_blocks(d,p):return False
        if p.get('lat') is not None and p.get('lon') is not None:
            if p.get('radius_km') and (d['distance_km'] is None or d['distance_km']>p['radius_km']):return False
        elif p.get('neighborhood') and not any(normalize(p['neighborhood']) in normalize(d.get(k)) for k in ('location_label','address')):return False
        if (p.get('installment_only') or p.get('payment_provider')) and not d['in_person_credit']:return False
        if p.get('open_now') and d['opening']['state']!='open':return False
        for key,value in (p.get('spec_filters') or {}).items():
            from product_data import matches
            if not matches(d,key,value):return False
        return d['shop_id'] not in reports.failed_shops() and (d['shop_id'],d['product_id']) not in reports.unavailable_pairs()

    def rank(self,found,p,sort='balanced'):
        if not found:return found
        prices=[x['effective_price_toman'] for x in found];low,high=min(prices),max(prices)
        for d in found:
            ps=(d['effective_price_toman']-low)/max(high-low,1);ds=min((d['distance_km'] or 0)/15,1)
            closed=.22 if d['opening']['state']=='closed' else .08 if d['opening']['state']=='unknown' else 0
            d['score']=round(100*(1-min(1,.5*ps+.3*ds+closed)),1)
            d['why']=['قیمت مبنای بررسی در محدوده بودجه' if p.get('budget_max_toman') else 'قیمت قابل مقایسه با سایر پیشنهادها']
            if d['price_basis']!='dataset':d['why'].append('محدودیت قیمت بر اساس آخرین گزارش معتبر؛ تأیید مستقل نیست')
            if d['distance_km'] is not None:d['why'].append(f"فاصله مستقیم {d['distance_km']} کیلومتر")
            if d['in_person_credit']:d['why'].append('اعتبار حضوری با مبلغ مبنای بررسی سازگار است')
        priority=p.get('priority') or sort
        if priority=='price':found.sort(key=lambda x:x['effective_price_toman'])
        elif priority=='distance':found.sort(key=lambda x:(x['distance_km'] is None,x['distance_km'] or 0,x['effective_price_toman']))
        else:found.sort(key=lambda x:(-x['score'],x.get('relevance',0),x['effective_price_toman']))
        # Query-match groups (the product itself before related items and spare parts) always lead.
        found.sort(key=lambda x:x.get('match_group',(0,)))
        return found

    # ---- a saved brand belongs to the product family it was asked for
    def family(self,text,brand=''):
        """Top catalogue categories of the product noun in `text` (brand words and model codes ignored)."""
        return self._family(normalize(text or ''),normalize(brand or ''))
    @functools.lru_cache(maxsize=1024)
    def _family(self,text,brand):
        skip=set(brand.split());terms=[t for t in text.split() if t not in skip and not re.search(r'\d',t)][:6]
        with self.conn() as c:
            while terms:
                try:rows=[r[0] for r in c.execute('select p.category_name,count(*) n from product_fts f join products p on p.rowid=f.rowid where product_fts match ? group by 1 order by n desc limit 25',(self.fts_match(terms),))]
                except sqlite3.Error:rows=[]
                # Categories named by the query's own words (گوشی → «گوشی موبایل») beat merely frequent ones (screen protectors).
                named=[r for r in rows if set(normalize(r).replace('،',' ').split())&set(terms)]
                if rows:return frozenset(named[:4] or rows[:3])
                terms=terms[:-1]
        return frozenset()
    @functools.lru_cache(maxsize=256)
    def _brand_categories(self,brand):
        with self.conn() as c:
            return frozenset(r[0] for r in c.execute('select category_name,count(*) n from products where brand_norm like ? or title_norm like ? group by 1 order by n desc limit 15',('%'+brand+'%',)*2))
    def brand_family(self,brand,scope=''):
        """Categories a brand preference constrains: those of the request that set it, else where the brand sells."""
        return (self.family(scope,brand) if scope else frozenset()) or self._brand_categories(normalize(brand))
    def brand_blocks(self,d,p):
        b=p.get('brand')
        if not b or normalize(b) in normalize(d.get('brand_name'))+' '+normalize(d.get('persian_title')):return False
        fam=self.brand_family(b,p.get('brand_scope') or '')
        return not fam or d.get('category_name') in fam

    def search(self,query='',profile=None,*args,**kw):
        """Catalogue search. A saved brand never silently empties a search: it is skipped for
        another product family and relaxed (and reported) when it alone leaves no offer."""
        p=dict(profile or {});brand=p.get('brand');note=None
        pids=kw.get('product_ids') or (args[2] if len(args)>2 else None)
        scoped=bool(brand and str(query or '').strip() and not pids and not kw.get('offer_ids'))
        if scoped:
            asked=self.family(query,brand)
            if asked and not asked&self.brand_family(brand,p.get('brand_scope') or ''):
                p['brand']=None;note={'key':'brand','value':brand,'reason':'other_category'}
        result=self._search(query,p,*args,**kw)
        if scoped and p.get('brand') and not result.get('offers'):
            loose=self._search(query,dict(p,brand=None),*args,**kw)
            if loose.get('offers'):result=loose;note={'key':'brand','value':brand,'reason':'no_match'}
        if note:
            result['relaxed_preferences']=[note]
            result['preference_notice']=(f'برند «{brand}» که برای درخواست دیگری گفته بودید، روی این جست‌وجو اعمال نشد.' if note['reason']=='other_category'
                                         else f'با برند «{brand}» نتیجه‌ای نبود؛ نتایج بدون شرط برند نمایش داده شده‌اند.')
        return result

    def _search(self,query='',profile=None,limit=12,exclude_shops=None,product_ids=None,sort='balanced',at=None,exclude_pairs=None,seller_events=None,offer_ids=None,discovery=False,full=False,shop_id=None):
        p=profile or {};limit=min(max(int(limit),1),3000 if full else 30);corrected=[]
        args=[]; clauses=['effective_price>0'];rank='0.0';join=''
        if full:discovery=False
        reports=SellerReports(seller_events or []);updates=reports.price_updates();cte='';price_join='';effective='o.price_toman'
        if updates:
            cte='WITH reported_prices(shop_id,product_id,price) AS (VALUES '+','.join('(?,?,?)' for _ in updates)+') '
            for (shop,pid),e in updates.items():args.extend([shop,pid,e['price']])
            price_join='LEFT JOIN reported_prices rp ON rp.shop_id=o.shop_id AND rp.product_id=o.product_id'
            effective='coalesce(rp.price,o.price_toman)'
        if shop_id:clauses.append('s.shop_id=?');args.append(shop_id)
        if offer_ids:
            clauses.append('o.offer_id in ('+','.join('?' for _ in offer_ids[:30])+')');args.extend(offer_ids[:30])
        if product_ids:
            clauses.append('p.product_id in ('+','.join('?' for _ in product_ids[:12])+')'); args.extend(product_ids[:12])
        elif query.strip():
            terms=normalize(query).split()[:10]
            with self.conn() as c:terms,corrected=self.correct_terms(terms,c)
            match=self.fts_match(terms)
            join='JOIN product_fts f ON f.rowid=p.rowid'
            clauses.append('product_fts MATCH ?');match_index=len(args);args.append(match)
            rank='bm25(product_fts,4.0,2.0,1.0,1.0)'
        elif not offer_ids:
            return {'offers':[],'message':'یک نام محصول یا دسته انتخاب کنید.','query':query}
        # Recognize primary purchase intent so cheap accessories cannot outrank the requested item.
        qnorm=normalize(query)
        if at is None and p.get('visit_at'):
            try:
                at=datetime.fromisoformat(p['visit_at'])
                at=at.replace(tzinfo=TEHRAN) if at.tzinfo is None else at.astimezone(TEHRAN)
            except (ValueError,TypeError):pass
        accessory_words=['پاکت','کیسه','فیلتر','یدکی','باتری','کیف','کاور','شارژر','گلس','محافظ','فلت','برد','قطعه','قطعات','قاب','کابل','پایه','استیکر','برچسب','تبدیل','آداپتور','اداپتور','بند','لوازم','واکس','جاکفشی']
        intended=[]
        if not product_ids and not any(w in qnorm.split() for w in accessory_words):
            if any(w in qnorm.split() for w in ['گوشی','موبایل','iphone','ایفون','آیفون']) or re.fullmatch(r'(?:galaxy )?[as][0-9]{2}(?: [a-z0-9]+)?',qnorm): intended=['گوشی موبایل']
            elif 'لپ تاپ' in qnorm or 'laptop' in qnorm: intended=['لپ تاپ و نوت بوک']
            elif any(w in qnorm.split() for w in ['هدفون','هندزفری','هدست','headphones','ایرپاد','airpods']): intended=['هدفون، هدست و هندزفری']
            elif 'تبلت' in qnorm.split(): intended=['تبلت']
            elif any(w in qnorm.split() for w in ['کفش','کتانی','کتونی','sneakers']):
                intended=['کتانی و کفش اسپرت','سایر کفش زنانه','کفش مجلسی و رسمی مردانه','کفش پاشنه بلند و مجلسی زنانه','کفش کالج مردانه','کتانی و کفش اسپرت بچگانه','کفش کالج زنانه','سایر کفش مردانه','کفش طبی مردانه','کفش طبی زنانه','کفش ایمنی و کار']
        if intended:
            clauses.append('p.category_name in ('+','.join('?' for _ in intended)+')');args.extend(intended)
        if p.get('budget_max_toman') is not None: clauses.append('effective_price<=?');args.append(float(p['budget_max_toman']))
        if p.get('budget_min_toman') is not None: clauses.append('effective_price>=?');args.append(float(p['budget_min_toman']))
        # Brands also count when named in the title: sub-brands and transliterations (QCY listed under شیائومی).
        if p.get('brand'): clauses.append('(p.brand_norm like ? or p.title_norm like ?)');args.extend(['%'+normalize(p['brand'])+'%']*2)
        if exclude_shops: clauses.append('s.shop_id not in ('+','.join('?' for _ in exclude_shops)+')');args.extend(exclude_shops)
        for shop_id,product_id in (exclude_pairs or [])[:100]:
            clauses.append('NOT (s.shop_id=? AND p.product_id=?)');args.extend([shop_id,product_id])
        lat,lon=p.get('lat'),p.get('lon');dist='NULL'
        if lat is not None and lon is not None:
            dist='distance('+str(float(lat))+','+str(float(lon))+',s.lat,s.lon)'
            if p.get('radius_km'): clauses.append(dist+'<=?');args.append(float(p['radius_km']))
        elif p.get('neighborhood'):
            clauses.append('(s.location_norm like ? or s.address_norm like ?)');args.extend(['%'+normalize(p['neighborhood'])+'%']*2)
        if p.get('installment_only') or p.get('payment_provider'):
            clauses.append("s.has_credit=1")
        sql=cte+f'''SELECT o.*,{effective} effective_price,p.persian_title,p.category_name,p.brand_name,p.model,p.image_urls,p.specs,
         s.name shop_name,s.address,s.location_label,s.lat,s.lon,s.contacts,s.payment_options,s.weekly_hours,
         {dist} distance_km,{rank} relevance
         FROM products p {join} JOIN offers o ON o.product_id=p.product_id JOIN shops s ON s.shop_id=o.shop_id {price_join}
         WHERE {' AND '.join(clauses)} ORDER BY relevance ASC,effective_price ASC LIMIT {4000 if full else 12000}'''
        if discovery and not product_ids and not offer_ids and sort=='balanced' and p.get('priority') not in ('price','distance'):
            from price_policy import sample_sql
            base=sql[len(cte):].rsplit(' ORDER BY relevance ASC,effective_price ASC LIMIT 12000',1)[0]
            sql=sample_sql(cte,base)
        else:discovery=False
        with self.conn() as c: rows=[dict(r) for r in c.execute(sql,args)]
        relaxed=[]
        if not rows and not product_ids and not offer_ids and query.strip():
            # An all-words match failed: relax generically (unknown words, then
            # descriptive words from the end). Model codes and the leading product
            # noun are never dropped; dropped words are reported to the caller.
            with self.conn() as c:
                for kept,dropped in self.relaxations(terms,c):
                    args[match_index]=self.fts_match(kept)
                    rows=[dict(r) for r in c.execute(sql,args)]
                    if rows:relaxed=dropped;break
        found=[];now=datetime.now(TEHRAN)
        for d in rows:
            for k in ['image_urls','specs','contacts','payment_options','weekly_hours']:d[k]=parsed(d[k],{} if k=='weekly_hours' else [])
            # Typed spec projections are costly: only spec filters need them here; returned offers get them below.
            d=self.hydrate_offer(d,p,reports,at,canonical=bool(p.get('spec_filters')))
            if not self.eligible(d,p,reports):continue
            try: age=max(0,(now-datetime.fromisoformat(d['last_price_changed_at'])).total_seconds()/86400)
            except (ValueError,TypeError):age=None
            d['price_age_days']=round(age,1) if age is not None else None
            d['source']={'type':'torob_dataset','offer_id':d['offer_id'],'price_timestamp':d['last_price_changed_at']}
            d['distance_km']=round(d['distance_km'],2) if d['distance_km'] is not None else None
            found.append(d)
        # Score after hard filters; distance is straight-line, never advertised as road travel time.
        self.rank(found,p,sort)
        mostly_accessories=False
        if not product_ids and not offer_ids and not any(w in qnorm.split() for w in accessory_words):
            # Accessories/spare parts sink below the requested item unless asked for.
            # Accessory categories lead with an accessory noun (پاکت جارو برقی، کارتریج پرینتر، شارژر ساعت)
            # unless the query asks for that noun; otherwise a category the query names is the product itself
            # (پاوربانک و شارژر همراه), never an accessory.
            heads=('پاکت','کابل','شارژر','کارتریج','تونر','ریبون','درام','لوازم','قطعات','کیف','کاور','قاب','گلس','محافظ','باتری','بند','فیلتر','پایه','تاچ','فلت','آی','بازر')
            head=lambda x:(normalize(x.get('category_name') or '').split() or [''])[0]
            is_acc=lambda x:(head(x) in heads and head(x) not in qnorm.split()) or cat_rank(x)[0]!=0 and (any(k in (x.get('category_name') or '') for k in ACCESSORY_CATEGORIES+('قطعات جانبی','لوازم یدکی','لامپ','خازن','پاکت','کیسه','فیلتر','یدکی')) or (normalize(x.get('persian_title') or '').split() or [''])[0] in accessory_words)
            # Products whose category is named by the query (or a variant) come first.
            words={t for t in qnorm.split() if len(t)>=3}
            words|={normalize(v) for t in list(words) for v in ALIASES.get(t,[])}
            first=[normalize(v) for v in [qnorm.split()[0]]+ALIASES.get(qnorm.split()[0],[])] if qnorm.split() else []
            def cat_rank(x):
                c=normalize(x.get('category_name') or '');hits=sum(w in c for w in words)
                return (0 if hits else 1,0 if any(c.startswith(f) for f in first) else 1,-hits,len(c))
            has_cat=any(cat_rank(x)[0]==0 for x in found)
            # More query words in the category name rank first (لامپ LED → «لامپ LED و کم مصرف»).
            for x in found:x['match_group']=(int(is_acc(x)),)+(cat_rank(x)[:3] if has_cat else (0,0,0))
            found.sort(key=lambda x:x['match_group'])
            main=[x for x in found if not is_acc(x)]
            mostly_accessories=bool(found) and len(main)<max(1,len(found)//5)
            if main and discovery:
                named=[x for x in main if has_cat and x['match_group'][1]==0]
                found=named or main
        if discovery:
            from price_policy import select
            out,policy=select(found,p)
            return {'offers':self.with_canonical(out),'matching_offers':len(found),'query':query,'filters':p,'distance_type':'straight_line_km','price_status':'dataset_not_live','inferred_categories':intended,'relaxed_terms':relaxed,'corrected_terms':corrected,'price_diversity':policy,'mostly_accessories':mostly_accessories,'candidate_limit_reached':any(r.get('pool_products',0)>600 or r.get('seller_pool_size',0)>4 for r in rows),'candidate_scope':policy['sampling'],'sampling_stats':{'sql_matching_products':max((r.get('pool_products',0) for r in rows),default=0),'sampled_products':len({r['product_id'] for r in rows}),'sampled_seller_offers':len(rows),'eligible_products':policy['eligible_sample_products']}}
        if full:
            # Every eligible offer, once per product/shop, in ranked order.
            out=[];seen=set()
            for d in found:
                key=(d['product_id'],d['shop_id'])
                if key in seen:continue
                seen.add(key);d['specs']=d['specs'][:18];out.append(d)
                if len(out)>=limit:break
            return {'offers':out,'matching_offers':len(found),'candidate_limit_reached':len(rows)>=4000,'query':query,'filters':p,'distance_type':'straight_line_km','price_status':'dataset_not_live','inferred_categories':intended,'relaxed_terms':relaxed,'corrected_terms':corrected,'mostly_accessories':mostly_accessories}
        # Diverse products and sellers instead of filling results with repeated listings.
        out=[];seen=set();per_product={};per_shop={}
        for d in found:
            key=(d['product_id'],d['shop_id'])
            if key in seen or per_product.get(d['product_id'],0)>=(limit if product_ids else 4):continue
            if not product_ids and per_shop.get(d['shop_id'],0)>=2:continue
            seen.add(key);per_product[d['product_id']]=per_product.get(d['product_id'],0)+1;per_shop[d['shop_id']]=per_shop.get(d['shop_id'],0)+1
            d['specs']=d['specs'][:18];out.append(d)
            if len(out)>=limit:break
        return {'offers':self.with_canonical(out),'matching_offers':len(found),'candidate_limit_reached':len(rows)>=12000,'query':query,'filters':p,'distance_type':'straight_line_km','price_status':'dataset_not_live','inferred_categories':intended,'relaxed_terms':relaxed,'corrected_terms':corrected,'mostly_accessories':mostly_accessories}

def import_csv(source,target):
    source=Path(source);target=Path(target);target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():raise RuntimeError('Refusing to overwrite a catalogue; choose a new path.')
    c=sqlite3.connect(target)
    c.executescript('''PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
    CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
    CREATE TABLE products(product_id TEXT PRIMARY KEY,persian_title TEXT,brand_name TEXT,category_name TEXT,model TEXT,image_urls TEXT,specs TEXT,title_norm TEXT,brand_norm TEXT,category_norm TEXT);
    CREATE VIRTUAL TABLE product_fts USING fts5(title_norm,category_norm,brand_norm,model,content='products',content_rowid='rowid',tokenize='unicode61');
    CREATE TABLE shops(shop_id TEXT PRIMARY KEY,name TEXT,address TEXT,location_label TEXT,lat REAL,lon REAL,contacts TEXT,payment_options TEXT,weekly_hours TEXT,category_names TEXT,instagram TEXT,website_url TEXT,supports_fast_delivery TEXT,location_norm TEXT,address_norm TEXT,has_credit INTEGER);
    CREATE TABLE offers(offer_id TEXT PRIMARY KEY,product_id TEXT,shop_id TEXT,price_toman REAL,seller_title TEXT,description TEXT,last_price_changed_at TEXT,warranty TEXT);
    ''')
    counts={}
    for name in ['products','shops','offers']:
        batch=[];count=0
        for r in csv.DictReader((source/(name+'.csv')).open()):
            if name=='products':
                vals=[r[k] for k in ['product_id','persian_title','brand_name','category_name','model','image_urls','specs']]+[normalize(r['persian_title']),normalize(r['brand_name']),normalize(r['category_name'])]
            elif name=='shops':
                keys=['shop_id','name','address','location_label','lat','lon','contacts','payment_options','weekly_hours','category_names','instagram','website_url','supports_fast_delivery']
                vals=[None if r[k]=='null' else r[k] for k in keys]
                vals += [normalize(r['location_label']),normalize(r['address']),int(any(x.get('channel')=='in_person' for x in parsed(r['payment_options'],[])))]
            else:vals=[None if r[k]=='null' else r[k] for k in ['offer_id','product_id','shop_id','price_toman','seller_title','description','last_price_changed_at','warranty']]
            batch.append(vals);count+=1
            if len(batch)>=2000:
                c.executemany('INSERT INTO '+name+' VALUES ('+','.join('?' for _ in vals)+')',batch);batch=[]
        if batch:c.executemany('INSERT INTO '+name+' VALUES ('+','.join('?' for _ in vals)+')',batch)
        counts[name]=count;c.commit();print(name,count,flush=True)
    c.executescript("INSERT INTO product_fts(product_fts) VALUES('rebuild'); CREATE INDEX offers_product ON offers(product_id,price_toman); CREATE INDEX offers_shop ON offers(shop_id,product_id);CREATE INDEX shops_credit ON shops(has_credit); CREATE INDEX product_category ON products(category_norm); ANALYZE;")
    counts['imported_at']=datetime.now(TEHRAN).isoformat();counts['source']='Starcoach v16 cap300 / Torob';counts['credit_shops']=c.execute('select count(*) from shops where has_credit=1').fetchone()[0]
    c.executemany('insert into metadata values(?,?)',[(k,json.dumps(v,ensure_ascii=False)) for k,v in counts.items()]);c.commit();c.close()

if __name__=='__main__':
    import sys
    import_csv(sys.argv[1],sys.argv[2])
