"""Bounded Tehran in-person merchant leads, never replacement catalogue offers."""
import hashlib,json,re,time,http.client
from urllib.parse import urlsplit,urljoin,parse_qs,urlencode,unquote
from public_research import PublicResearch,fetch,host_in,model_identity,identity,MAX_SOURCES,TOTAL_SECONDS,CACHE_SECONDS,FAILURE_SECONDS,source_kind
from catalog import normalize,haversine
from areas import topic_for,suggestions


def public_query(query):
    model=model_identity({'persian_title':query})
    topic,_=topic_for(query)
    # Never send a full private need/budget/location message to public discovery.
    return model or topic,model,topic


def merchant_lead(doc,model,known_shops):
    text=doc.get('text','');url=doc['url'];host=urlsplit(url).hostname or ''
    if source_kind(url) in ('specialist','community'):return None,'not_merchant_source'
    shop=next((s for s in known_shops if host==s['domain'] or host.endswith('.'+s['domain'])),None)
    matched=identity({'persian_title':model,'model':model},text,doc.get('title','')) if model else {'status':'model_identity_missing','configuration':'unverified'}
    if matched['status']!='model_tokens_match':return None,'model_unproven'
    tehran=bool(re.search(r'تهران|\btehran\b',text,re.I))
    in_person=bool(re.search(r'خرید حضوری|فروش حضوری|مراجعه حضوری|فروشگاه حضوری|آدرس فروشگاه|showroom|in.store|visit our (?:shop|store)',text,re.I))
    if not tehran and not shop:return None,'tehran_unproven'
    if not in_person and not shop:return None,'in_person_unproven'
    # A public page may list several prices/variants. Keep ambiguity; do not
    # convert rial, choose a minimum, claim stock or make a qualifying offer.
    prices=list(dict.fromkeys(m[1] for m in re.finditer(r'([\d۰-۹][\d۰-۹,٬. ]{2,18})\s*تومان',text)))[:4]
    address=shop.get('address') if shop else next((p[:400] for p in text.splitlines() if 'تهران' in p and any(w in p for w in ('خیابان','پلاک','طبقه','پاساژ','بلوار','میدان'))),None)
    return {'title':doc.get('title') or host,'url':url,'source':'public_merchant_page','retrieved_at':time.time(),
            'published_at':None,'model_match':matched,'tehran_evidence':'catalogue_shop_address' if shop else 'page_mentions_tehran',
            'in_person_status':'page_claim_not_independently_verified' if in_person else 'confirm_for_this_product',
            'address':address,'lat':shop.get('lat') if shop else None,'lon':shop.get('lon') if shop else None,
            'coordinate_basis':'supplied_shop_coordinates' if shop else 'unknown','shop_id':shop.get('shop_id') if shop else None,
            'price_texts':prices,'price_status':'ambiguous_page_text' if len(prices)>1 else 'page_text_not_verified' if prices else 'unknown',
            'availability':'unknown','credit':'unknown','eligible_offer':False,
            'notice':'سرنخ عمومی برای پرس‌وجوی حضوری در تهران؛ قیمت، موجودی همین نسخه، اعتبار و هویت مستقل تأیید نشده است.'},None


class ExternalDiscovery(PublicResearch):
    def discover(self,query,known_shops=()):
        public,model,topic=public_query(query)
        key=hashlib.sha256(json.dumps(['tehran-v3',public,topic,[(s['shop_id'],s['domain'],s.get('matches_category',False)) for s in known_shops]],sort_keys=True).encode()).hexdigest()
        with self.conn() as db:row=db.execute('select expires,payload from public_research where key=?',(key,)).fetchone()
        if row and row[0]>time.time():return dict(json.loads(row[1]),cached=True)
        result={'query':public,'model':model,'topic':topic,'leads':[],'sources':[],'cached':False,'created':time.time(),
                'status':'unavailable','changes_preferences':False,'notice':'منابع بیرون از دیتاست فقط سرنخ‌اند. آفرهای واجد شرایط دیتاست در اولویت می‌مانند؛ قیود خرید تغییر نکرده‌اند.'}
        if not public or not model:
            result.update(status='needs_exact_model',notice='برای جست‌وجوی فروشنده بیرونی، کد دقیق مدل را مشخص کنید؛ سرنخ ناحیه‌ای، موجودی محصول نیست.')
            return result
        deadline=time.monotonic()+TOTAL_SECONDS
        def read(url):
            try:return self.fetcher(url,deadline)
            except TimeoutError:return {'url':url,'status':'timeout'}
            except (ValueError,OSError,http.client.HTTPException):return {'url':url,'status':'unavailable'}
        urls=[]
        for discovery_url in ['https://html.duckduckgo.com/html/?'+urlencode({'q':public+' تهران خرید حضوری فروشگاه'}),
                              'https://www.bing.com/search?'+urlencode({'q':public+' تهران خرید حضوری فروشگاه','format':'rss'})]:
            if time.monotonic()>=deadline:break
            doc=read(discovery_url)
            result['sources'].append({'url':discovery_url,'status':doc['status'],'kind':'free_discovery','retrieved_at':time.time()})
            for link in doc.get('links',[]):
                link=urljoin(discovery_url,link);p=urlsplit(link)
                if host_in(p.hostname or '',{'duckduckgo.com'}):link=parse_qs(p.query).get('uddg',[''])[0]
                p=urlsplit(link)
                if p.scheme=='https' and not host_in(p.hostname or '',{'duckduckgo.com','bing.com'}) and link not in urls:urls.append(link)
                if len(urls)>=MAX_SOURCES:break
            if urls:break
        # If free discovery is unavailable, inspect at most three supplied
        # real-identity websites in the same category. They can mention products
        # outside the sampled offers; a category alone never produces a lead.
        fallback_urls=['https://'+s['domain']+'/' for s in known_shops if s.get('matches_category')]
        urls=list(dict.fromkeys(urls+fallback_urls))[:MAX_SOURCES]
        queue=urls[:];seen=set();documents=0
        while queue and documents<MAX_SOURCES:
            if time.monotonic()>=deadline:break
            url=queue.pop(0)
            if url in seen:continue
            seen.add(url);documents+=1
            doc=read(url);reason=None
            if doc['status']=='readable':
                lead,reason=merchant_lead(doc,model,known_shops)
                if lead:
                    lead['discovered_via']='catalogue_identity_website' if url in fallback_urls else 'free_public_search_or_same_site_link'
                    result['leads'].append(lead)
                elif reason=='model_unproven':
                    # A normal same-site product link can resolve a generic
                    # homepage. It consumes the same three-document budget.
                    for link in doc.get('links',[]):
                        target=urljoin(doc['url'],link);p=urlsplit(target)
                        if p.scheme=='https' and p.hostname==urlsplit(doc['url']).hostname and target not in seen and identity({'persian_title':model,'model':model},unquote(p.path))['status']=='model_tokens_match':
                            queue.insert(0,target);break
            result['sources'].append({'url':doc['url'],'status':doc['status'],'kind':'merchant_page','reason':reason,'retrieved_at':time.time()})
        if result['leads']:result['status']='leads_only'
        now=time.time()
        with self.conn() as db:
            db.execute('insert or replace into public_research values(?,?,?,?)',(key,now,now+(CACHE_SECONDS if result['leads'] else FAILURE_SECONDS),json.dumps(result,ensure_ascii=False)))
            db.execute('delete from public_research where key in (select key from public_research order by created desc limit -1 offset 2000)')
        return result


def apply_constraints(result,profile):
    # Apply private constraints outside the public cache. An unknown outside
    # page never qualifies for budget/location/credit filters or route stops.
    import copy
    result=copy.deepcopy(result)
    for lead in result['leads']:
        distance=haversine(profile.get('lat'),profile.get('lon'),lead['lat'],lead['lon'])
        lead['distance_km']=round(distance,2) if distance is not None else None
        lead['outside_radius']=bool(distance is not None and profile.get('radius_km') is not None and distance>profile['radius_km'])
        lead['location_constraint_unverified']=bool((profile.get('radius_km') or profile.get('neighborhood')) and distance is None)
        lead['budget_constraint_unverified']=profile.get('budget_max_toman') is not None or profile.get('budget_min_toman') is not None
        lead['credit_constraint_unverified']=bool(profile.get('installment_only') or profile.get('payment_provider'))
        if lead['address']:lead['maps_url']='https://www.google.com/maps/search/?'+urlencode({'api':1,'query':lead['address']})
    return result
