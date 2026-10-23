"""«تازه‌سازی قیمت»: seller one-tap links, refresh queue, crowd trust, web verification, estimates."""
import importlib,os,sys,time
from pathlib import Path
import httpx,pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from freshness import Freshness,estimate

@pytest.fixture
def env(tmp_path):
    from catalog import Catalog
    path=os.getenv('TEST_CATALOG')
    if not path:pytest.skip('TEST_CATALOG required')
    cat=Catalog(path);f=Freshness(tmp_path/'m.db',cat);cat.freshness=f
    offers=cat.search('هدفون بی سیم',{})['offers']
    return cat,f,offers

def pick(cat,offer):return next(o for o in cat.search(profile={},offer_ids=[offer['offer_id']],full=True)['offers'])

def test_estimate_is_a_labelled_range_only_for_old_listings():
    assert estimate(1_000_000,5)==None
    e=estimate(1_000_000,90,[(1_150_000,3),(1_120_000,10),(1_130_000,2)])
    assert e['low']<=e['high'] and e['low']>=1_000_000 and e['confidence']=='high' and 'قیمت فروشنده نیست' in e['text']
    lone=estimate(1_000_000,90,[]);assert lone['confidence']=='low' and lone['high']>lone['low']

def test_signed_link_cannot_be_forged_or_reused_after_expiry(env):
    cat,f,offers=env;tok=f.token(offers[0]['shop_id'])
    assert f.verify(tok)==offers[0]['shop_id']
    other=f.token(next(x['shop_id'] for x in offers if x['shop_id']!=offers[0]['shop_id']))
    with pytest.raises(ValueError):f.verify(tok.split('.')[0]+'.'+other.split('.')[1])
    with pytest.raises(ValueError):f.verify(f.token(offers[0]['shop_id'],ttl=-1))

def test_seller_update_flows_into_search_with_a_confirmed_badge(env):
    cat,f,offers=env;o=offers[0];tok=f.token(o['shop_id']);f.demand([o],'plan')
    page=f.shop_page(tok);assert any(x['offer_id']==o['offer_id'] for x in page['offers'])
    new=round(o['price_toman']*1.1,-3)
    f.shop_update(tok,[{'offer_id':o['offer_id'],'action':'price','price':new}])
    d=pick(cat,o)
    assert d['effective_price_toman']==new and d['price_basis']=='merchant_link' and d['price_confirmed_today'] and d['freshness_label']=='قیمت تأییدشده امروز'
    assert 'price_estimate' not in d and 'قیمت امروز با لینک فروشنده تأیید شده است' in d['why']
    under=cat.search('هدفون بی سیم',{'budget_max_toman':new-1},offer_ids=None)
    assert o['offer_id'] not in {x['offer_id'] for x in under['offers']}  # budget filters see the new price

def test_same_unavailable_and_validation(env):
    cat,f,offers=env;o,o2=offers[0],next(x for x in offers if x['shop_id']!=offers[0]['shop_id'])
    tok=f.token(o['shop_id'])
    f.shop_update(tok,[{'offer_id':o['offer_id'],'action':'same'}])
    d=pick(cat,o);assert d['effective_price_toman']==o['price_toman'] and d['price_basis']=='merchant_link'
    with pytest.raises(ValueError):f.shop_update(tok,[{'offer_id':o2['offer_id'],'action':'same'}])   # not this shop's offer
    with pytest.raises(ValueError):f.shop_update(tok,[{'offer_id':o['offer_id'],'action':'price','price':1}])  # implausible
    f.shop_update(tok,[{'offer_id':o['offer_id'],'action':'unavailable'}])
    assert cat.search(profile={},offer_ids=[o['offer_id']],full=True)['offers']==[]

def test_crowd_needs_agreement_and_never_beats_the_seller(env):
    cat,f,offers=env;o=offers[2]
    p=round(o['price_toman']*1.2,-3)
    assert f.crowd('user-a',o['shop_id'],o['product_id'],o['offer_id'],p)['status']=='pending'
    assert f.crowd('user-a',o['shop_id'],o['product_id'],o['offer_id'],p)['status']=='pending'  # same shopper twice is one voice
    assert f.crowd('user-b',o['shop_id'],o['product_id'],o['offer_id'],p*1.5)['status']=='disagree'
    assert f.crowd('user-c',o['shop_id'],o['product_id'],o['offer_id'],p*1.01)['status']=='accepted'
    d=pick(cat,o);assert d['price_basis']=='crowd'
    f.shop_update(f.token(o['shop_id']),[{'offer_id':o['offer_id'],'action':'same'}])
    assert pick(cat,o)['price_basis']=='merchant_link'
    assert f.crowd('user-d',o['shop_id'],o['product_id'],o['offer_id'],p)['status'] in ('merchant_update_wins','pending','disagree')
    assert pick(cat,o)['price_basis']=='merchant_link'

def test_queue_ranks_demand_age_and_planned_visits(env):
    cat,f,offers=env
    f.demand(offers[:6],'search')
    f.demand([offers[3]],'plan');f.demand([{'shop_id':offers[3]['shop_id']}],'visit')
    q=f.queue()
    assert q and q[0]['offer_id']==offers[3]['offer_id'] and q[0]['factors']['visit']==2.0
    assert all(set(x['factors'])=={'age','demand','uncertainty','visit'} for x in q)
    sent=f.auto_ask('https://example.test/',top=2)
    assert sent and all('?shop=' in s['url'] and s['mode'] in ('prepared_real_channel','simulated_send') for s in sent)
    assert f.stats()['asks_7d']==len(sent)

def test_web_answers_are_accepted_only_from_the_shop_site_for_the_same_model():
    o={'persian_title':'هدفون بی سیم شیائومی کیو سی وای T13 ANC 2','price_toman':2_750_000}
    ok={'title':'هدفون بی سیم کیو سی وای T13 ANC 2','price_toman':2_900_000}
    assert Freshness.verify_web(o,'shop.ir','https://www.shop.ir/p/1',ok)=={'ok':True,'status':'verified','price':2900000}
    assert Freshness.verify_web(o,'shop.ir','https://other.ir/p/1',ok)['status']=='not_on_shop_site'
    assert Freshness.verify_web(o,'shop.ir','https://shop.ir/p/1',{**ok,'title':'هدفون کیو سی وای T13'})['status']=='different_model'
    assert Freshness.verify_web(o,'shop.ir','https://shop.ir/p/1',{**ok,'price_toman':29_000})['status']=='implausible_price'
    assert Freshness.verify_web(o,'shop.ir','https://shop.ir/p/1',ok,cited={'https://shop.ir/other'})['status']=='url_not_cited'

def test_agent_has_the_refresh_tool():
    import agent
    names={t['name'] for t in agent.TOOLS}
    assert 'request_price_refresh' in names and 'request_price_refresh' in agent.LABELS and 'price_stale' in agent.SEARCH_QUERY_INSTRUCTIONS

@pytest.mark.asyncio
async def test_api_end_to_end(tmp_path,monkeypatch,env):
    cat,f,offers=env
    from memory import Memory
    from agent import Agent
    from features import Features
    monkeypatch.setenv('DATA_DIR',str(tmp_path));server=importlib.import_module('server')
    mem=Memory(tmp_path/'api.db');agent=Agent(cat,mem);features=Features(cat,mem,agent);agent.features=features
    for k,v in dict(cat=cat,mem=mem,agent=agent,features=features,fresh=f).items():monkeypatch.setattr(server,k,v)
    o=offers[0]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://fresh-test') as c:
        sid=(await c.get('/api/bootstrap')).json()['sessions'][0]['id'];f.demand([o],'plan')
        ask=(await c.post('/api/freshness/ask',json={'session_id':sid,'shop_id':o['shop_id']})).json()
        tok=ask['url'].split('?shop=')[1];assert ask['share'] and 'یک لمس' in ask['message']
        page=(await c.get('/api/shop-link/'+tok)).json();assert page['offers']
        assert (await c.get('/api/shop-link/'+tok[:-2]+'xx')).status_code==404
        r=await c.post('/api/shop-link/'+tok,json={'items':[{'offer_id':o['offer_id'],'action':'price','price':round(o['price_toman']*1.05,-3)}]})
        assert r.status_code==200 and r.json()['updated'][0]['action']=='price'
        fr=(await c.post('/api/offers/refresh',json={'session_id':sid,'offer_ids':[o['offer_id']]})).json()
        assert fr['offers'][0]['price_basis']=='merchant_link' and fr['offers'][0]['price_confirmed_today']
        fu=(await c.get('/api/followups/'+sid)).json()['followups'][o['shop_id']]
        assert fu['status']=='confirmed' and fu['channel']=='price_link'
        q=(await c.get('/api/freshness/queue')).json();assert 'stats' in q and q['stats']['confirmed_today']>=1
