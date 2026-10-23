"""«تازه‌سازی قیمت»: keeping dataset prices honest when sellers do not update them.

Five parts, all on one global (cross-user) store in memory.db:
1. seller link   – a signed, expiring, no-login link per shop; the seller confirms, changes or
                   withdraws prices in one tap (price_basis=merchant_link, audited).
2. refresh queue – who to ask first: price age × shopper demand × price uncertainty × planned visit.
3. crowd reports – a shopper's price counts globally only when several agree; never above a seller update.
4. web check     – for shops with a real website, read the current price from the shop's own page
                   (price_basis=web_verified, source link kept).
5. estimate      – when nothing is fresh: «برآورد امروز» range with a confidence label, never the seller's price.
"""
import base64,hashlib,hmac,json,math,os,re,secrets,sqlite3,statistics,threading,time
import obs
from catalog import ClosingConnection,normalize

VALID={'merchant_link':7*86400,'web_verified':3*86400,'crowd':3*86400}   # how long an update replaces the dataset price
RANK={'merchant_link':3,'web_verified':2,'crowd':1}
LINK_TTL=7*86400;STALE_DAYS=30;ESTIMATE_DAYS=21
CHANNEL_LABEL={'website':'سایت فروشگاه','instagram':'دایرکت اینستاگرام','whatsapp':'واتس‌اپ','telegram':'تلگرام','bale':'بله','sms':'پیامک'}

def monthly_drift():
    try:return min(.2,max(0.0,float(os.getenv('PRICE_DRIFT_MONTHLY','0.03'))))
    except ValueError:return .03
def age_days(stamp,now=None):
    if not stamp:return None
    try:
        from datetime import datetime
        return max(0.0,((now or time.time())-datetime.fromisoformat(str(stamp)).timestamp())/86400)
    except (ValueError,TypeError):return None
def fa(n):return f'{int(round(n)):,}'.replace(',','٬').translate(str.maketrans('0123456789','۰۱۲۳۴۵۶۷۸۹'))

def estimate(price,age,siblings=()):
    """Today's likely price range for a stale listing; None when the listing is recent enough."""
    if not price or age is None or age<ESTIMATE_DAYS:return None
    drift=(1+monthly_drift())**(age/30)
    recent=sorted(p for p,a in siblings if p and a is not None and a<STALE_DAYS)
    base=price*drift
    if len(recent)>=2:
        ref=statistics.median(recent);gap=abs(ref-base)/base
        low,high=min(base,ref)*.97,max(base,ref)*1.03
        confidence='high' if gap<.08 and len(recent)>=3 else 'medium' if gap<.2 else 'low'
        basis=f'تغییر قیمت عمومی از زمان ثبت و قیمت تازهٔ {fa(len(recent))} فروشندهٔ دیگر همین کالا'
    else:
        low,high=price*(1+monthly_drift()/2)**(age/30),base*1.06;confidence='low'
        basis='فقط تغییر قیمت عمومی از زمان ثبت؛ قیمت تازه‌ای از فروشندهٔ دیگر نبود'
    low,high=round(low,-3),round(max(high,low),-3)
    label={'high':'اطمینان بالا','medium':'اطمینان متوسط','low':'اطمینان کم'}[confidence]
    return {'low':low,'high':high,'confidence':confidence,'confidence_label':label,'basis':basis,'age_days':round(age),
            'text':f'برآورد امروز {fa(low)} تا {fa(high)} تومان ({label}) — قیمت فروشنده نیست'}


class Freshness:
    def __init__(self,path,catalog=None,base_url=''):
        self.path=str(path);self.cat=catalog;self.base_url=base_url;self.lock=threading.Lock();self._prices=None;self._at=0.0
        with self.conn() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS price_updates(id INTEGER PRIMARY KEY,shop_id TEXT,product_id TEXT,offer_id TEXT,price REAL,available INTEGER,basis TEXT,source_url TEXT,actor TEXT,created REAL,details TEXT DEFAULT '{}');
            CREATE INDEX IF NOT EXISTS price_updates_pair ON price_updates(shop_id,product_id,created);
            CREATE TABLE IF NOT EXISTS price_demand(shop_id TEXT,product_id TEXT,offer_id TEXT,kind TEXT,created REAL);
            CREATE INDEX IF NOT EXISTS price_demand_time ON price_demand(created);
            CREATE TABLE IF NOT EXISTS price_asks(id INTEGER PRIMARY KEY,shop_id TEXT,token_hash TEXT,channel TEXT,mode TEXT,created REAL,opened REAL,answered REAL);
            CREATE TABLE IF NOT EXISTS crowd_reports(id INTEGER PRIMARY KEY,shop_id TEXT,product_id TEXT,offer_id TEXT,reporter TEXT,price REAL,created REAL);
            CREATE TABLE IF NOT EXISTS freshness_meta(key TEXT PRIMARY KEY,value TEXT);''')
            row=c.execute("select value from freshness_meta where key='secret'").fetchone()
            if not row:c.execute("insert into freshness_meta values('secret',?)",(secrets.token_hex(32),));row=c.execute("select value from freshness_meta where key='secret'").fetchone()
        self.secret=bytes.fromhex(row[0])
    def conn(self):
        c=sqlite3.connect(self.path,timeout=20,factory=ClosingConnection);c.row_factory=sqlite3.Row;return c

    # ------------------------------------------------------------ global prices
    def prices(self):
        """Latest still-valid update per (shop, product); a seller update outranks web and crowd."""
        now=time.time()
        if self._prices is not None and now-self._at<5:return self._prices
        out={}
        with self.conn() as c:
            for r in c.execute('select * from price_updates where created>? order by created',(now-max(VALID.values()),)):
                r=dict(r)
                if now-r['created']>VALID.get(r['basis'],0):continue
                key=(r['shop_id'],r['product_id']);old=out.get(key)
                if old is None or RANK[r['basis']]>=RANK[old['basis']] or now-old['created']>VALID[old['basis']]:out[key]=r
        self._prices,self._at=out,now;return out
    def latest(self,shop,product):return self.prices().get((shop,product))
    def record(self,shop,product,offer,price,available,basis,actor,source_url=None,details=None):
        with self.lock,self.conn() as c:
            c.execute('insert into price_updates(shop_id,product_id,offer_id,price,available,basis,source_url,actor,created,details) values(?,?,?,?,?,?,?,?,?,?)',
                      (shop,product,offer,price,None if available is None else int(bool(available)),basis,source_url,actor,time.time(),json.dumps(details or {},ensure_ascii=False)))
        self._prices=None
        obs.log('price.update','info',basis,shop_id=shop,product_id=product,basis=basis,changed=price is not None)

    def apply(self,d):
        """Overlay a valid global update on a hydrated offer (session reports already won)."""
        if d.get('price_basis') not in (None,'dataset'):return d
        g=self.latest(d['shop_id'],d['product_id'])
        if not g:return d
        if g['price']:d['effective_price_toman']=g['price']
        d['price_basis']=g['basis'];d['price_confirmed_at']=g['created'];d['price_source_url']=g.get('source_url')
        d['price_confirmed_today']=time.time()-g['created']<86400
        if g['available'] is not None and d.get('availability')=='unknown':d['availability']=g['basis']+('_available' if g['available'] else '_unavailable')
        if g['available']==0:d['seller_withdrawn']=True
        return d

    def annotate(self,offers):
        """Freshness label and, for stale unconfirmed listings, a «برآورد امروز» range."""
        if not offers or len(offers)>80 or not self.cat:return offers
        pids=list({o['product_id'] for o in offers})
        with self.cat.conn() as c:
            rows=c.execute('select product_id,price_toman,last_price_changed_at from offers where product_id in ('+','.join('?'*len(pids))+')',pids).fetchall()
        sib={}
        for r in rows:sib.setdefault(r['product_id'],[]).append((r['price_toman'],age_days(r['last_price_changed_at'])))
        for o in offers:
            age=o.get('price_age_days')
            if o.get('price_confirmed_at'):
                o['freshness_label']='قیمت تأییدشده امروز' if o.get('price_confirmed_today') else 'قیمت تأییدشده '+fa((time.time()-o['price_confirmed_at'])/86400)+' روز پیش'
                o.pop('price_estimate',None);continue
            o['price_stale']=bool(age is not None and age>=STALE_DAYS)
            others=[(p,a) for p,a in sib.get(o['product_id'],[]) if p!=o.get('price_toman')]
            est=estimate(o.get('price_toman'),age,others) if o.get('price_basis','dataset')=='dataset' else None
            if est:o['price_estimate']=est
        return offers

    # ------------------------------------------------------------ demand
    def demand(self,offers,kind,limit=12):
        rows=[(o.get('shop_id'),o.get('product_id'),o.get('offer_id'),kind,time.time()) for o in (offers or [])[:limit] if o.get('shop_id')]
        if not rows:return
        try:
            with self.conn() as c:c.executemany('insert into price_demand values(?,?,?,?,?)',rows)
        except sqlite3.Error:pass

    # ------------------------------------------------------------ queue
    def queue(self,limit=30,days=7):
        """Offers to refresh first: age × demand × uncertainty × planned visit."""
        since=time.time()-days*86400
        with self.conn() as c:
            demand=c.execute("select offer_id,shop_id,product_id,sum(kind='search') s,sum(kind='plan') p,sum(kind='compare') k,sum(kind='view') v,max(created) last from price_demand where created>? and offer_id is not null group by offer_id order by last desc limit 300",(since,)).fetchall()
            planned={r[0] for r in c.execute("select distinct shop_id from price_demand where created>? and kind='visit'",(time.time()-86400,))}
            asks={}
            for r in c.execute('select shop_id,max(created) at,max(answered) answered,group_concat(distinct mode) modes from price_asks group by shop_id'):asks[r['shop_id']]=dict(r)
        ids=[r['offer_id'] for r in demand]
        offers={}
        for i in range(0,len(ids),30):
            for o in self.cat.search('',{},offer_ids=ids[i:i+30],full=True).get('offers',[]):offers[o['offer_id']]=o
        self.annotate(list(offers.values()))
        pids=list({o['product_id'] for o in offers.values()})
        med={}
        if pids:
            with self.cat.conn() as c:
                for pid in pids:
                    ps=[r[0] for r in c.execute('select price_toman from offers where product_id=? and price_toman>0',(pid,))]
                    if len(ps)>=2:med[pid]=statistics.median(ps)
        out=[]
        for r in demand:
            o=offers.get(r['offer_id'])
            if not o:continue
            age=o.get('price_age_days') or 0
            confirmed=bool(o.get('price_confirmed_at'))
            age_f=.15 if confirmed else min(1.0,.1+age/90)
            dem=1+math.log1p(r['s'])+2*r['p']+1.5*r['k']+.5*r['v']
            m=med.get(o['product_id']);unc=min(1.0,abs(o['effective_price_toman']-m)/m) if m else .5
            visit=2.0 if o['shop_id'] in planned or r['p'] else 1.0
            score=round(age_f*dem*(1+unc)*visit*10,1)
            shop=self.cat.shop(o['shop_id']);ask=asks.get(o['shop_id']) or {}
            out.append({'offer_id':o['offer_id'],'shop_id':o['shop_id'],'shop_name':o['shop_name'],'product_id':o['product_id'],'title':o['persian_title'],
                        'price_toman':o['effective_price_toman'],'price_basis':o['price_basis'],'price_age_days':round(age),'freshness_label':o.get('freshness_label'),
                        'estimate':o.get('price_estimate'),'score':score,'factors':{'age':round(age_f,2),'demand':round(dem,2),'uncertainty':round(unc,2),'visit':visit},
                        'demand':{'searches':r['s'],'plans':r['p'],'comparisons':r['k'],'views':r['v']},'real_channels':self.real_channels(shop),
                        'asked_at':ask.get('at'),'answered_at':ask.get('answered'),'ask_modes':ask.get('modes')})
        out.sort(key=lambda x:-x['score']);return out[:limit]

    def stats(self):
        now=time.time()
        with self.conn() as c:
            by=dict(c.execute('select basis,count(*) from price_updates where created>? group by basis',(now-7*86400,)).fetchall())
            today=c.execute('select count(distinct shop_id||product_id) from price_updates where created>?',(now-86400,)).fetchone()[0]
            asks=c.execute('select count(*),sum(answered is not null),sum(opened is not null) from price_asks where created>?',(now-7*86400,)).fetchone()
            crowd=c.execute('select count(*) from crowd_reports where created>?',(now-7*86400,)).fetchone()[0]
        return {'updates_7d':by,'confirmed_today':today,'asks_7d':asks[0] or 0,'asks_opened_7d':asks[2] or 0,'asks_answered_7d':asks[1] or 0,'crowd_reports_7d':crowd,
                'valid_hours':{k:v//3600 for k,v in VALID.items()},'monthly_drift':monthly_drift()}

    # ------------------------------------------------------------ seller link
    def token(self,shop_id,ttl=LINK_TTL):
        body=base64.urlsafe_b64encode(json.dumps({'s':shop_id,'e':int(time.time()+ttl)},separators=(',',':')).encode()).decode().rstrip('=')
        return body+'.'+base64.urlsafe_b64encode(hmac.new(self.secret,body.encode(),hashlib.sha256).digest()[:18]).decode()
    def verify(self,token):
        try:
            body,sig=str(token).split('.',1)
            good=base64.urlsafe_b64encode(hmac.new(self.secret,body.encode(),hashlib.sha256).digest()[:18]).decode()
            if not hmac.compare_digest(sig,good):raise ValueError
            data=json.loads(base64.urlsafe_b64decode(body+'='*(-len(body)%4)))
        except (ValueError,TypeError,json.JSONDecodeError):raise ValueError('لینک معتبر نیست')
        if data['e']<time.time():raise ValueError('اعتبار این لینک تمام شده است؛ از فروشگاه‌یار لینک تازه بخواهید')
        return data['s']
    @staticmethod
    def thash(token):return hashlib.sha256(str(token).encode()).hexdigest()[:16]

    def real_channels(self,shop):
        out=[]
        if (shop or {}).get('website_url'):out.append({'kind':'website','label':CHANNEL_LABEL['website'],'url':shop['website_url']})
        ig=(shop or {}).get('instagram')
        if isinstance(ig,str):
            try:ig=json.loads(ig)
            except ValueError:ig=None
        if isinstance(ig,dict) and ig.get('profile_url'):out.append({'kind':'instagram','label':CHANNEL_LABEL['instagram'],'url':ig['profile_url']})
        return out

    def ask(self,shop_id,base_url,mode='manual',channel=None):
        """Create the shop's one-tap link and the message to send with it."""
        shop=self.cat.shop(shop_id)
        if not shop:raise ValueError('فروشگاه پیدا نشد')
        tok=self.token(shop_id);url=base_url.rstrip('/')+'/?shop='+tok
        items=self.shop_offers(shop_id,limit=15)
        names='، '.join('«'+o['title'][:40]+'»' for o in items[:3])
        msg=(f'سلام، وقت بخیر. چند مشتری در بازارگرد دنبال {names}{" و …" if len(items)>3 else ""} از فروشگاه شما هستند. '
             f'لطفاً با یک لمس قیمت‌ها را تأیید یا اصلاح کنید (بدون ثبت‌نام): {url}')
        from urllib.parse import quote
        share=[{'kind':'whatsapp','label':CHANNEL_LABEL['whatsapp'],'url':'https://wa.me/?text='+quote(msg)},
               {'kind':'telegram','label':CHANNEL_LABEL['telegram'],'url':'https://t.me/share/url?url='+quote(url)+'&text='+quote(msg)},
               {'kind':'sms','label':CHANNEL_LABEL['sms'],'url':'sms:?&body='+quote(msg)}]
        with self.conn() as c:c.execute('insert into price_asks(shop_id,token_hash,channel,mode,created) values(?,?,?,?,?)',(shop_id,self.thash(tok),channel,mode,time.time()))
        obs.log('price.ask','info',mode,shop_id=shop_id,mode=mode,items=len(items))
        return {'shop_id':shop_id,'shop_name':shop['name'],'url':url,'token':tok,'message':msg,'items':len(items),'real_channels':self.real_channels(shop),'share':share,
                'expires_in_days':LINK_TTL//86400,'mode':mode}

    def auto_ask(self,base_url,top=5):
        """Ask the shops behind the top queue items: real channels get a ready one-tap message, the rest a simulated send."""
        sent=[];seen=set()
        for item in self.queue(limit=40):
            if item['shop_id'] in seen or (item['asked_at'] and time.time()-item['asked_at']<6*3600):continue
            seen.add(item['shop_id'])
            mode='prepared_real_channel' if item['real_channels'] else 'simulated_send'
            a=self.ask(item['shop_id'],base_url,mode,item['real_channels'][0]['kind'] if item['real_channels'] else 'sms')
            sent.append({k:a[k] for k in ('shop_id','shop_name','url','items','mode','real_channels')})
            if len(sent)>=top:break
        return sent

    def shop_offers(self,shop_id,limit=15):
        """Offers worth refreshing for one shop: demanded first (plans, comparisons, searches), then the stalest."""
        since=time.time()-7*86400
        with self.conn() as c:
            dem={r[0]:(r[1],r[2]) for r in c.execute("select offer_id,sum(kind in ('plan','compare'))*3+count(*),max(kind='plan') from price_demand where shop_id=? and created>? and offer_id is not null group by offer_id",(shop_id,since))}
        with self.cat.conn() as c:
            rows=[dict(r) for r in c.execute('select o.offer_id,o.product_id,o.price_toman,o.last_price_changed_at,p.persian_title,p.image_urls from offers o join products p on p.product_id=o.product_id where o.shop_id=? and o.price_toman>0',(shop_id,))]
        out=[]
        for r in rows:
            age=age_days(r['last_price_changed_at']);d=dem.get(r['offer_id'])
            g=self.latest(shop_id,r['product_id'])
            reason='در برنامهٔ خرید مشتری' if d and d[1] else 'مشتریان دنبالش هستند' if d else 'قیمت ثبت‌شده قدیمی است' if age and age>=STALE_DAYS else 'بررسی دوره‌ای'
            try:img=(json.loads(r['image_urls'] or '[]') or [None])[0]
            except ValueError:img=None
            out.append({'offer_id':r['offer_id'],'product_id':r['product_id'],'title':r['persian_title'],'image':img,'price_toman':g['price'] if g and g['price'] else r['price_toman'],
                        'dataset_price_toman':r['price_toman'],'price_age_days':round(age) if age is not None else None,'reason':reason,
                        'confirmed_at':g['created'] if g else None,'confirmed_basis':g['basis'] if g else None,'withdrawn':bool(g and g['available']==0),
                        'priority':(d[0] if d else 0)*10+min(age or 0,365)/30})
        out.sort(key=lambda x:-x['priority']);return out[:limit]

    def shop_page(self,token):
        shop_id=self.verify(token);shop=self.cat.shop(shop_id)
        with self.conn() as c:c.execute('update price_asks set opened=coalesce(opened,?) where token_hash=?',(time.time(),self.thash(token)))
        return {'shop':{'name':shop['name'],'address':shop.get('address')},'offers':self.shop_offers(shop_id),'expires_note':'این لینک مخصوص همین فروشگاه است و بدون ثبت‌نام کار می‌کند.'}

    def shop_update(self,token,items,actor_hint=''):
        shop_id=self.verify(token);valid={o['offer_id']:o for o in self.shop_offers(shop_id,limit=400)}
        done=[]
        for it in items[:60]:
            o=valid.get(it.get('offer_id'))
            if not o:raise ValueError('این کالا متعلق به این فروشگاه نیست')
            action=it.get('action')
            if action=='same':price,avail=o['dataset_price_toman'] if not o['confirmed_basis'] else o['price_toman'],True
            elif action=='price':
                price=float(it.get('price') or 0);avail=True
                if not .2*o['dataset_price_toman']<=price<=5*o['dataset_price_toman']:raise ValueError('قیمت واردشده با این کالا جور نیست؛ لطفاً دوباره بررسی کنید')
            elif action=='unavailable':price,avail=None,False
            else:raise ValueError('عمل نامعتبر')
            self.record(shop_id,o['product_id'],o['offer_id'],price,avail,'merchant_link','shop_link:'+self.thash(token),details={'action':action,'client':obs.digest(actor_hint) if actor_hint else None})
            done.append({'offer_id':o['offer_id'],'action':action,'price':price})
        with self.conn() as c:c.execute('update price_asks set answered=? where token_hash=?',(time.time(),self.thash(token)))
        return {'updated':done,'shop_offers':self.shop_offers(shop_id)}

    # ------------------------------------------------------------ crowd
    def crowd(self,reporter,shop,product,offer,price,window=72*3600,agree=.05):
        """A shopper's report becomes a global price only when ≥2 different shoppers agree within 5%."""
        if not price or price<=0:return None
        now=time.time()
        with self.conn() as c:
            c.execute('insert into crowd_reports(shop_id,product_id,offer_id,reporter,price,created) values(?,?,?,?,?,?)',(shop,product,offer,obs.digest(reporter,16),price,now))
            rows=c.execute('select reporter,price from crowd_reports where shop_id=? and product_id=? and created>? order by created desc',(shop,product,now-window)).fetchall()
        latest={}
        for r in rows:latest.setdefault(r['reporter'],r['price'])
        if len(latest)<2:return {'status':'pending','reporters':len(latest)}
        med=statistics.median(latest.values());agreeing=[p for p in latest.values() if abs(p-med)/med<=agree]
        if len(agreeing)<2:return {'status':'disagree','reporters':len(latest)}
        g=self.latest(shop,product)
        if g and g['basis']=='merchant_link':return {'status':'merchant_update_wins','reporters':len(latest)}
        self.record(shop,product,offer,round(statistics.median(agreeing),-3),True,'crowd','crowd:'+str(len(agreeing)),details={'reporters':len(agreeing)})
        return {'status':'accepted','reporters':len(agreeing)}

    # ------------------------------------------------------------ web check (real-identity shops)
    async def web_check(self,offer_id,llm_module=None):
        import llm as L
        L=llm_module or L
        o=next(iter(self.cat.search('',{},offer_ids=[offer_id],full=True).get('offers',[])),None)
        if not o:raise ValueError('آفر پیدا نشد')
        shop=self.cat.shop(o['shop_id']);site=(shop or {}).get('website_url')
        if not site:return {'status':'no_website','note':'این فروشگاه سایت ثبت‌شده ندارد؛ از لینک یک‌لمسی استفاده کنید.'}
        from urllib.parse import urlsplit
        host=(urlsplit(site).hostname or '').removeprefix('www.')
        prompt=(f'Find the current price of exactly this product on the shop website {host}: «{o["persian_title"]}». '
                f'Search only {host}. Reply with JSON only: {{"url": product page URL on {host}, "title": product title as written on that page, '
                f'"price_toman": current price in Toman as a number or null, "in_stock": true/false/null, "quote": the exact price text from the page}}.')
        r=await L.web_search(prompt,purpose='price_refresh')
        data=L.parse_json(r.get('text') or '') or {}
        url=str(data.get('url') or '');cited={c.get('url') for c in r.get('citations') or []}|{s.get('url') if isinstance(s,dict) else s for s in r.get('sources') or []}
        verdict=self.verify_web(o,host,url,data,cited)
        if verdict['ok']:
            self.record(o['shop_id'],o['product_id'],o['offer_id'],verdict['price'],data.get('in_stock') if isinstance(data.get('in_stock'),bool) else None,'web_verified','web_check',source_url=url,details={'quote':str(data.get('quote') or '')[:200],'title':str(data.get('title') or '')[:200]})
        return verdict|{'url':url or None,'host':host}

    @staticmethod
    def verify_web(o,host,url,data,cited=()):
        from urllib.parse import urlsplit
        page=(urlsplit(url).hostname or '').removeprefix('www.') if url else ''
        if not page or not (page==host or page.endswith('.'+host)):return {'ok':False,'status':'not_on_shop_site'}
        if cited and url not in cited and not any(str(c).startswith(url.split('?')[0]) for c in cited if c):return {'ok':False,'status':'url_not_cited'}
        title=normalize(str(data.get('title') or ''));want=normalize(o['persian_title'])
        codes=[t for t in re.findall(r'[a-z0-9]+',want) if re.search(r'\d',t) and len(t)>=2]
        words=[t for t in want.split() if len(t)>=3]
        if codes and not all(c in title for c in codes):return {'ok':False,'status':'different_model'}
        if not codes and (not words or sum(w in title for w in words)/len(words)<.6):return {'ok':False,'status':'different_product'}
        try:price=float(data.get('price_toman'))
        except (TypeError,ValueError):return {'ok':False,'status':'no_price_on_page'}
        if not .2*o['price_toman']<=price<=5*o['price_toman']:return {'ok':False,'status':'implausible_price'}
        return {'ok':True,'status':'verified','price':round(price)}
