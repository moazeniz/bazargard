"""Reproducible benchmark of بازارگرد on the Torob catalogue.

  python bench/bench.py build      # check labels against the catalogue, record ground truth
  python bench/bench.py retrieval  # catalogue search + ranking, no LLM
  python bench/bench.py e2e URL    # agent end-to-end on a small subset (real model calls)
  python bench/bench.py dataset    # dataset facts
  python bench/bench.py report     # docs/BENCHMARK.md from bench/results/*.json
Environment: CATALOG (default data/catalog.db).
"""
import asyncio,json,os,re,sqlite3,statistics,sys,time
from datetime import datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent;sys.path.insert(0,os.getenv('BENCH_BACKEND',str(ROOT/'backend')));sys.path.insert(0,str(ROOT/'bench'))
TAG=os.getenv('BENCH_TAG','')  # e.g. -baseline: results of an older build
from spec import QUERIES,E2E
RESULTS=ROOT/'bench'/'results';RESULTS.mkdir(parents=True,exist_ok=True)
CATALOG=os.getenv('CATALOG',str(ROOT/'data'/'catalog.db'))
ACCESSORY_WORDS=('کیف و کاور','محافظ','گلس','قاب','لوازم جانبی','لوازم یدکی','قطعات','شارژر','کابل','باتری گوشی','تاچ و ال سی دی','فلت و برد','پاکت جارو',
                 'کارتریج','تونر','درام پرینتر','ریبون','پایه نگهدارنده','آی سی گوشی','دوربین گوشی','بازر و بلندگو','فن خنک کننده','بند ')
FA=str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩','01234567890123456789')

def load():return json.loads((RESULTS/'queries.json').read_text())['queries']
def save(name,data):(RESULTS/name).write_text(json.dumps(data,ensure_ascii=False,indent=1,default=str))
def is_accessory(category,title=''):
    c=category or '';head=(title or '').strip().split(' ')[0] if title else ''
    return any(w in c for w in ACCESSORY_WORDS) or head in ('کاور','قاب','گلس','محافظ','کیف','شارژر','کابل','بند','باتری')

# ---------------------------------------------------------------- labels
def build():
    from catalog import normalize
    db=sqlite3.connect(CATALOG);known={r[0] for r in db.execute('select distinct category_name from products')}
    out=[]
    for item in QUERIES:
        item=dict(item);cats=[c for c in item['categories'] if c in known];dropped=[c for c in item['categories'] if c not in known]
        if dropped:print('label',item['id'],'unknown categories dropped:',dropped)
        item['categories']=cats
        sql='select count(distinct p.product_id),count(*) from offers o join products p on p.product_id=o.product_id join shops s on s.shop_id=o.shop_id where o.price_toman>0'
        args=[]
        if cats:sql+=' and p.category_name in ('+','.join('?'*len(cats))+')';args+=cats
        for t in item.get('title_all',[]):sql+=' and p.title_norm like ?';args.append('%'+normalize(t)+'%')
        if item.get('title_any'):sql+=' and ('+' or '.join('p.title_norm like ?' for _ in item['title_any'])+')';args+=['%'+normalize(t)+'%' for t in item['title_any']]
        if item.get('budget'):sql+=' and o.price_toman<=?';args.append(item['budget'])
        if item.get('area'):sql+=' and (s.location_norm like ? or s.address_norm like ?)';args+=['%'+normalize(item['area'])+'%']*2
        if item.get('credit'):sql+=' and s.has_credit=1'
        # A product type with no catalogue category at all is, by definition, not in Torob.
        products,offers=db.execute(sql,args).fetchone() if cats else (0,0)
        item['truth']={'products':products,'offers':offers,'categories_valid':bool(cats),'open_filter_checked_at_runtime':bool(item.get('open_at'))}
        if item.get('not_found') and products:print('LABEL ERROR',item['id'],'expected not found but',products,'products match')
        if not item.get('not_found') and not products:print('LABEL ERROR',item['id'],'expected results but none match')
        out.append(item)
    save('queries.json',{'built':datetime.now().isoformat(),'catalog':Path(CATALOG).name,'queries':out})
    print('labels',len(out),'not_found',sum(1 for x in out if x.get('not_found')))

# ---------------------------------------------------------------- retrieval (no LLM)
def profile_of(item):
    p={}
    if item.get('budget'):p['budget_max_toman']=item['budget']
    if item.get('area'):p['neighborhood']=item['area']
    if item.get('open_at'):p['open_now']=True;p['visit_at']=item['open_at']
    if item.get('credit'):p['installment_only']=True
    return p

def checks(item,o):
    from catalog import normalize
    title=normalize(o.get('persian_title'))
    intent=o.get('category_name') in item['categories'] and all(normalize(t) in title for t in item.get('title_all',[])) and (not item.get('title_any') or any(normalize(t) in title for t in item['title_any']))
    cons={}
    if item.get('budget'):cons['budget']=o['effective_price_toman']<=item['budget']
    if item.get('area'):cons['area']=normalize(item['area']) in normalize(o.get('location_label'))+' '+normalize(o.get('address'))
    if item.get('open_at'):cons['open']=(o.get('opening') or {}).get('state')=='open'
    if item.get('credit'):cons['credit']=bool(o.get('in_person_credit'))
    return intent,cons

def retrieval(rounds=2):
    from catalog import Catalog
    from price_policy import initial
    cat=Catalog(CATALOG);items=load();rows=[];cold={}
    for r in range(rounds):  # round 0 = cold caches, round 1 = warm
        for item in items:
            p=profile_of(item)
            t=time.perf_counter();res=cat.search(item['search'],p,10);ranked_ms=(time.perf_counter()-t)*1000
            spread=initial(item['search'],item['request'],{},'balanced',p)
            t=time.perf_counter();agent=cat.search(item['search'],p,12,discovery=spread);agent_ms=(time.perf_counter()-t)*1000
            if r==0:cold[item['id']]=round(ranked_ms+agent_ms,1)
            if r<rounds-1:continue
            def score(offers):
                out=[]
                for o in offers:
                    intent,cons=checks(item,o)
                    out.append({'offer_id':o['offer_id'],'product_id':o['product_id'],'title':o['persian_title'][:90],'category':o.get('category_name'),'price':o['effective_price_toman'],
                                'intent':intent,'constraints':cons,'accessory':not intent and is_accessory(o.get('category_name'),o.get('persian_title'))})
                return out
            rows.append({'id':item['id'],'kind':item['kind'],'search':item['search'],'profile':p,'not_found':bool(item.get('not_found')),'trap':bool(item.get('trap')),
                         'truth':item['truth'],'ranked':score(res['offers']),'agent_first_search':score(agent['offers']),'relaxed_terms':res.get('relaxed_terms') or agent.get('relaxed_terms') or [],'corrected_terms':res.get('corrected_terms') or [],
                         'mostly_accessories':bool(res.get('mostly_accessories') or agent.get('mostly_accessories')),'ranked_ms':round(ranked_ms,1),'agent_ms':round(agent_ms,1),'cold_ms':cold.get(item['id'])})
    save('retrieval'+TAG+'.json',{'run_at':datetime.now().isoformat(),'rows':rows,'metrics':retrieval_metrics(rows)})
    print(json.dumps(retrieval_metrics(rows),ensure_ascii=False,indent=1))

def pct(a,b):return round(100*a/b,1) if b else None
def retrieval_metrics(rows):
    found=[r for r in rows if not r['not_found']];m={}
    for mode in ('ranked','agent_first_search'):
        p5=[sum(o['intent'] for o in r[mode][:5])/max(1,min(5,len(r[mode]))) for r in found if r[mode]]
        p10=[sum(o['intent'] for o in r[mode][:10])/max(1,min(10,len(r[mode]))) for r in found if r[mode]]
        hit5=sum(1 for r in found if any(o['intent'] for o in r[mode][:5]))
        empty=sum(1 for r in found if not r[mode])
        checksall=[v for r in rows for o in r[mode] for v in o['constraints'].values()]
        bykind={}
        for kind in ('budget','area','open','credit'):
            vals=[o['constraints'][kind] for r in rows for o in r[mode] if kind in o['constraints']]
            bykind[kind]=pct(sum(vals),len(vals))
        traps=[r for r in rows if r['trap']]
        acc=[o['accessory'] for r in traps for o in r[mode][:5]]
        m[mode]={'queries_expecting_results':len(found),'precision_at_5':round(100*statistics.mean(p5),1) if p5 else None,'precision_at_10':round(100*statistics.mean(p10),1) if p10 else None,
                 'success_at_5':pct(hit5,len(found)),'zero_result_rate':pct(empty,len(found)),'constraint_satisfaction':pct(sum(checksall),len(checksall)),'constraint_by_type':bykind,
                 'accessory_rate_on_traps_top5':pct(sum(acc),len(acc))}
    nf=[r for r in rows if r['not_found']]
    no_false=sum(1 for r in nf if not any(o['intent'] and all(o['constraints'].values()) for o in r['ranked']))
    signalled=sum(1 for r in nf if not r['ranked'] or r['relaxed_terms'] or r['mostly_accessories'] or not any(o['intent'] for o in r['ranked']))
    lat=sorted(r['ranked_ms'] for r in rows);alat=sorted(r['agent_ms'] for r in rows)
    q=lambda xs,p:round(xs[min(len(xs)-1,int(len(xs)*p))],1)
    m['not_found']={'queries':len(nf),'no_false_match':pct(no_false,len(nf)),'explicitly_signalled':pct(signalled,len(nf))}
    clat=sorted(r['cold_ms'] for r in rows if r.get('cold_ms') is not None)
    m['latency_ms']={'ranked_p50':q(lat,.5),'ranked_p95':q(lat,.95),'agent_first_search_p50':q(alat,.5),'agent_first_search_p95':q(alat,.95),'cold_both_searches_p50':q(clat,.5) if clat else None,'cold_both_searches_p95':q(clat,.95) if clat else None}
    m['by_kind']={}
    for kind in sorted({r['kind'] for r in rows}):
        rs=[r for r in rows if r['kind']==kind and not r['not_found']]
        if rs:m['by_kind'][kind]={'n':len(rs),'p@5':round(100*statistics.mean([sum(o['intent'] for o in r['ranked'][:5])/max(1,min(5,len(r['ranked']))) if r['ranked'] else 0 for r in rs]),1),
                                  'success@5':pct(sum(1 for r in rs if any(o['intent'] for o in r['ranked'][:5])),len(rs))}
    return m

# ---------------------------------------------------------------- end to end (agent)
PRICE=re.compile(r'(\d[\d,٬٫.]*)\s*(میلیون|هزار)?\s*(?:تومان|تومن)')
def prices_in(text):
    out=[]
    for m in PRICE.finditer(text.translate(FA)):
        raw=m.group(1).replace('٬','').replace(',','').replace('٫','.');unit=m.group(2)
        if raw.count('.')>1:raw=raw.replace('.','')  # 1.600.000 written with dots
        try:
            value=float(raw.rstrip('.'))*(1_000_000 if unit=='میلیون' else 1000 if unit=='هزار' else 1)
        except ValueError:continue
        out.append(value)
    return out
NOT_FOUND=re.compile(r'(پیدا نشد|پیدا نکردم|وجود ندارد|نبود|موجود نیست|نیافتم|ثبت نشده)')

async def e2e(base):
    import httpx
    items={x['id']:x for x in load()};rows=[]
    async with httpx.AsyncClient(base_url=base.rstrip('/')+'/',timeout=60) as c:
        H={'Origin':base.rstrip('/')}
        boot=(await c.get('api/bootstrap')).json();notices=boot.get('notices',[])
        for qid in E2E:
            item=items[qid];sid=(await c.post('api/sessions',json={},headers=H)).json()['id']
            if item.get('open_at'):await c.patch('api/profile',json={'session_id':sid,'values':{'visit_at':item['open_at']},'scope':'trip'},headers=H)
            t=time.time();run=(await c.post('api/chat-runs',json={'session_id':sid,'message':item['request'],'mode':'advanced'},headers=H)).json()
            after=0;done=None;error=None;tools=[];first_tool=None
            for _ in range(240):
                d=(await c.get('api/chat-runs/'+run['run_id'],params={'after':after})).json()
                for e in d['events']:
                    after=e['seq'];ev=e['event']
                    if ev['type']=='tool':tools.append(ev['name']);first_tool=first_tool or round(time.time()-t,1)
                    if ev['type']=='done':done=ev
                    if ev['type']=='error':error=ev.get('message')
                if d['status'] in ('completed','failed','cancelled','interrupted'):break
                await asyncio.sleep(0.5)
            secs=round(time.time()-t,1);reply=(done or {}).get('reply','') or '';cards=((done or {}).get('results') or {}).get('offers',[])
            card_checks=[];fidelity=[]
            for o in cards:
                intent,cons=checks(item,o);card_checks.append({'title':o['persian_title'][:80],'price':o.get('effective_price_toman'),'intent':intent,'constraints':cons})
                fidelity.append(fidelity_check(o))
            cited=[int(n) for n in re.findall(r'\[(\d{1,2})\]',reply.translate(FA))]
            prices=prices_in(reply);allowed=[o.get('effective_price_toman') or 0 for o in cards]+[x for x in (item.get('budget'),) if x]
            halluc=[p for p in prices if not any(abs(p-a)<=max(0.05*a,1000) for a in allowed if a)]
            rows.append({'id':qid,'kind':item['kind'],'request':item['request'],'seconds':secs,'first_tool_seconds':first_tool,'tools':tools,'error':error,
                         'rounds':((done or {}).get('research') or {}).get('rounds'),'cards':card_checks,'card_fidelity':fidelity,'reply':reply,
                         'citations':cited,'citations_valid':all(1<=n<=len(cards) for n in cited),'cites_when_cards':bool(cited) or not cards,
                         'says_not_found':bool(NOT_FOUND.search(reply)),'expected_not_found':bool(item.get('not_found')),
                         'price_mentions':prices,'hallucinated_prices':halluc})
            print(qid,secs,'s cards',len(cards),'cited',cited,'nf',rows[-1]['says_not_found'],'halluc',halluc,flush=True)
    save('e2e.json',{'run_at':datetime.now().isoformat(),'base':base,'notices':notices,'rows':rows,'metrics':e2e_metrics(rows,notices)})
    print(json.dumps(e2e_metrics(rows,notices),ensure_ascii=False,indent=1))

def fidelity_check(o):
    """Every displayed price/age/stock/identity label must trace back to dataset fields."""
    db=sqlite3.connect(CATALOG)
    row=db.execute('select o.price_toman,o.last_price_changed_at,s.instagram,s.website_url from offers o join shops s on s.shop_id=o.shop_id where o.offer_id=?',(o['offer_id'],)).fetchone()
    if not row:return {'offer_id':o['offer_id'],'exists':False}
    price,changed,insta,site=row
    try:age=(datetime.now(datetime.fromisoformat(changed).tzinfo)-datetime.fromisoformat(changed)).total_seconds()/86400
    except (TypeError,ValueError):age=None
    real=bool((insta and insta!='null') or (site and site!='null'))
    return {'offer_id':o['offer_id'],'exists':True,'price_matches':abs((o.get('price_toman') or 0)-price)<0.5 and (o.get('price_basis')!='dataset' or abs(o['effective_price_toman']-price)<0.5),
            'age_matches':o.get('price_age_days') is None if age is None else abs((o.get('price_age_days') or 0)-age)<1.5,
            'stock_unknown_without_report':o.get('availability')=='unknown' or o.get('price_basis')!='dataset',
            'identity_matches':o.get('identity_status')==('source_identity' if real else 'synthetic_name')}

def e2e_metrics(rows,notices):
    nf=[r for r in rows if r['expected_not_found']];found=[r for r in rows if not r['expected_not_found']]
    cards=[c for r in found for c in r['cards']];cons=[v for c in cards for v in c['constraints'].values()]
    fid=[f for r in rows for f in r['card_fidelity']]
    secs=sorted(r['seconds'] for r in rows);q=lambda xs,p:xs[min(len(xs)-1,int(len(xs)*p))]
    mentions=sum(len(r['price_mentions']) for r in rows);halluc=sum(len(r['hallucinated_prices']) for r in rows)
    return {'queries':len(rows),'errors':sum(1 for r in rows if r['error']),
            'card_intent_precision':pct(sum(c['intent'] for c in cards),len(cards)),'card_constraint_satisfaction':pct(sum(cons),len(cons)),
            'answers_with_results':pct(sum(1 for r in found if r['cards']),len(found)),
            'not_found_stated_when_expected':pct(sum(r['says_not_found'] for r in nf),len(nf)),
            'false_not_found_when_results_exist':pct(sum(1 for r in found if r['says_not_found'] and not r['cards']),len(found)),
            'citations_valid':pct(sum(r['citations_valid'] for r in rows),len(rows)),'cites_cards_when_results_expected':pct(sum(r['cites_when_cards'] for r in found),len(found)),
            'price_mentions':mentions,'hallucinated_price_rate':pct(halluc,mentions),
            'fidelity':{k:pct(sum(1 for f in fid if f.get(k)),len(fid)) for k in ('exists','price_matches','age_matches','stock_unknown_without_report','identity_matches')},
            'synthetic_names_disclosed':any('ساختگی' in n for n in notices),
            'seconds':{'p50':q(secs,.5),'p95':q(secs,.95),'max':secs[-1]},'mean_rounds':round(statistics.mean([r['rounds'] or 0 for r in rows]),1)}

def dataset():
    from dataset_facts import compute
    save('dataset.json',compute(CATALOG));print('dataset facts saved')

if __name__=='__main__':
    cmd=sys.argv[1] if len(sys.argv)>1 else 'retrieval'
    if cmd=='build':build()
    elif cmd=='retrieval':retrieval()
    elif cmd=='e2e':asyncio.run(e2e(sys.argv[2]))
    elif cmd=='dataset':dataset()
    elif cmd=='report':
        from report import write;write()
