import sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from external_discovery import ExternalDiscovery,merchant_lead,apply_constraints,public_query


def doc(text,url='https://shop.example/a55'):
    return {'url':url,'status':'readable','title':text[:70],'text':text,'links':[]}


def test_public_query_drops_private_need_budget_and_recipient():
    public,model,topic=public_query('برای پدرم گوشی A55 تا ۹۰ میلیون می‌خواهم')
    assert public=='A55' and model=='A55' and topic
    assert 'پدر' not in public and 'میلیون' not in public
    assert public_query('برای مادرم کفش راحت تا پنج میلیون')[0]=='کفش'


def test_wrong_model_overseas_and_online_only_pages_cannot_be_offers():
    assert merchant_lead(doc('A555 تهران خرید حضوری'),'A55',[])[0] is None
    assert merchant_lead(doc('Samsung A55 in-store London'),'A55',[])[0] is None
    assert merchant_lead(doc('Samsung A55 فروش اینترنتی ارسال تهران'),'A55',[])[0] is None
    lead,_=merchant_lead(doc('Samsung A55 تهران، خیابان جمهوری پلاک ۱۰ خرید حضوری قیمت ۹۰۰۰۰ تومان و نسخه دیگر ۱۰۰۰۰۰ تومان'),'A55',[])
    assert lead and not lead['eligible_offer'] and lead['availability']=='unknown'
    assert lead['price_status']=='ambiguous_page_text' and lead['credit']=='unknown'
    assert lead['lat'] is None and lead['model_match']['configuration']=='unverified'


def test_cache_bounded_sources_and_private_filters_are_applied_after_cache(tmp_path):
    calls=[]
    def fetch(url,deadline):
        calls.append(url)
        if 'duckduckgo' in url:return {'url':url,'status':'readable','links':['https://shop.example/a55','https://outside.example/a55','https://wrong.example/a55','https://unused.example/a55']}
        if 'shop.example' in url:return doc('Samsung A55 خرید حضوری تهران خیابان جمهوری پلاک ۱۰',url)
        if 'outside' in url:raise TimeoutError()
        return doc('Samsung A56 تهران خرید حضوری',url)
    store=ExternalDiscovery(tmp_path/'public.db',fetch)
    known=[{'shop_id':'s','domain':'shop.example','address':'تهران، جمهوری','lat':35.7,'lon':51.4}]
    result=store.discover('گوشی A55',known)
    assert len(calls)==4 and len(result['leads'])==1
    assert result['leads'][0]['tehran_evidence']=='catalogue_shop_address'
    original=result['leads'][0].copy()
    private=apply_constraints(result,{'budget_max_toman':1,'installment_only':True,'lat':35.8,'lon':51.5,'radius_km':.1})
    assert private['leads'][0]['outside_radius'] and private['leads'][0]['credit_constraint_unverified']
    assert result['leads'][0]==original
    cached=store.discover('گوشی A55',known)
    assert cached['cached'] and len(calls)==4 and 'credit_constraint_unverified' not in cached['leads'][0]


def test_blocked_discovery_and_model_missing_are_honest(tmp_path):
    store=ExternalDiscovery(tmp_path/'p.db',lambda u,d:{'url':u,'status':'blocked'})
    result=store.discover('گوشی A55')
    assert result['status']=='unavailable' and result['leads']==[]
    assert len(result['sources'])==2
    assert store.discover('کفش')['status']=='needs_exact_model'


def test_website_category_alone_never_proves_product(tmp_path):
    def fetch(url,deadline):return {'url':url,'status':'readable','text':'فروشگاه موبایل تهران خرید حضوری بدون ذکر مدل','title':'فروشگاه موبایل','links':[]}
    shop={'shop_id':'s','domain':'shop.example','address':'تهران','lat':35.7,'lon':51.4,'matches_category':True}
    result=ExternalDiscovery(tmp_path/'p.db',fetch).discover('گوشی A55',[shop])
    assert result['leads']==[] and result['sources'][-1]['reason']=='model_unproven'


import importlib,os,httpx,pytest

@pytest.mark.asyncio
async def test_external_api_catalogue_first_constraints_owner_and_snapshot(tmp_path,monkeypatch):
    from catalog import Catalog
    from memory import Memory
    from agent import Agent
    from features import Features
    path=os.getenv('TEST_CATALOG')
    if not path:pytest.skip('TEST_CATALOG required')
    monkeypatch.setenv('DATA_DIR',str(tmp_path));server=importlib.import_module('server');mem=Memory(tmp_path/'m.db');cat=Catalog(path);a=Agent(cat,mem);features=Features(cat,mem,a);a.features=features
    for key,value in dict(mem=mem,cat=cat,agent=a,features=features).items():monkeypatch.setattr(server,key,value)
    calls=[]
    def unavailable(self,query,known):
        calls.append(query)
        return {'query':'A55','model':'A55','topic':'گوشی موبایل','leads':[],'sources':[],'cached':False,'created':time.time(),'status':'unavailable','notice':'منبع مسدود'}
    monkeypatch.setattr('agent.ExternalDiscovery.discover',unavailable)
    transport=httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport,base_url='http://test') as c,httpx.AsyncClient(transport=transport,base_url='http://test') as other:
        sid=(await c.get('/api/bootstrap')).json()['sessions'][0]['id'];await other.get('/api/bootstrap')
        body={'session_id':sid,'query':'گوشی A55'}
        assert (await other.post('/api/external-discovery',json=body)).status_code==404 and not calls
        response=await c.post('/api/external-discovery',json=body);assert response.status_code==200,response.text
        data=response.json();assert data['catalogue']['offers'] and not data['leads']
        assert all(o['product_id'] for o in data['catalogue']['offers'])
        await c.patch('/api/profile',json={'session_id':sid,'values':{'budget_max_toman':1,'installment_only':True}})
        bounded=(await c.post('/api/external-discovery',json=body)).json();assert not bounded['catalogue']['offers']
        state=(await c.get('/api/sessions/'+sid)).json();assert state['turns'][-1]['references']['external_discovery']==bounded
        assert state['facts'] and bounded['areas']['changes_preferences'] is False


def test_editorial_and_community_mentions_are_not_merchant_offers():
    assert merchant_lead(doc('A55 خرید حضوری تهران','https://www.reddit.com/comments/test'),'A55',[])[0] is None
    assert merchant_lead(doc('A55 خرید حضوری تهران','https://www.techradar.com/review'),'A55',[])[1]=='not_merchant_source'


def test_same_site_model_link_uses_the_same_document_budget(tmp_path):
    calls=[]
    def fetch(url,deadline):
        calls.append(url)
        if 'duckduckgo' in url or 'bing.com' in url:return {'url':url,'status':'blocked'}
        if url.endswith('/a55'):return doc('Samsung A55 تهران خرید حضوری',url)
        return {'url':url,'status':'readable','title':'فروشگاه تهران','text':'فروشگاه موبایل تهران، دسته گوشی','links':['https://shop.example/a55','https://evil.example/a55']}
    known=[{'shop_id':'s','domain':'shop.example','address':'تهران','lat':35.7,'lon':51.4,'matches_category':True}]
    result=ExternalDiscovery(tmp_path/'p.db',fetch).discover('گوشی A55',known)
    assert len(result['leads'])==1 and result['leads'][0]['url']=='https://shop.example/a55'
    assert len(calls)==4 and not any('evil.example' in u for u in calls)
