"""A saved brand constrains only the product family it was asked for and never silently empties a search."""
import asyncio,importlib,os,sys,time
from pathlib import Path
import httpx,pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from catalog import normalize

@pytest.fixture(scope='module')
def cat():
    from catalog import Catalog
    path=os.getenv('TEST_CATALOG')
    if not path:pytest.skip('TEST_CATALOG required')
    return Catalog(path)

def branded(o,brand):return normalize(brand) in normalize(o.get('brand_name'))+' '+normalize(o['persian_title'])
WATCH={'brand':'گارمین','brand_scope':'ساعت هوشمند گارمین Forerunner 265','budget_max_toman':3000000}
PHONE={'brand':'سامسونگ','brand_scope':'گوشی موبایل سامسونگ'}

def test_brand_for_one_category_does_not_touch_an_unrelated_search(cat):
    for query in ('هدفون بی سیم','شارژر سامسونگ 25 وات'):
        r=cat.search(query,WATCH)
        assert r['offers'] and not any(branded(o,'گارمین') for o in r['offers'])
        assert r['relaxed_preferences']==[{'key':'brand','value':'گارمین','reason':'other_category'}] and 'گارمین' in r['preference_notice']
        plain=cat.search(query,{'budget_max_toman':3000000})
        assert {o['offer_id'] for o in r['offers']}=={o['offer_id'] for o in plain['offers']}

def test_same_category_is_still_filtered(cat):
    r=cat.search('گوشی موبایل',PHONE)
    assert r['offers'] and all(branded(o,'سامسونگ') for o in r['offers']) and 'relaxed_preferences' not in r
    other=cat.search('هدفون بی سیم',PHONE)  # Samsung sells earbuds, but the preference was stated for phones
    assert other['relaxed_preferences'][0]['reason']=='other_category' and not all(branded(o,'سامسونگ') for o in other['offers'])

def test_a_preference_that_alone_empties_the_search_is_relaxed_and_reported(cat):
    p={'brand':'برندناموجودتست','brand_scope':'هدفون بی سیم برندناموجودتست'}
    r=cat.search('هدفون بی سیم',p)
    assert r['offers'] and r['relaxed_preferences']==[{'key':'brand','value':'برندناموجودتست','reason':'no_match'}] and 'نتیجه‌ای نبود' in r['preference_notice']
    assert cat.search('هدفون بی سیم',{'budget_max_toman':10})['offers']==[]  # other hard filters are not relaxed

def test_without_a_scope_the_brand_applies_where_it_actually_sells(cat):
    r=cat.search('هدفون بی سیم',{'brand':'گارمین'})  # e.g. set from the filter form, no request to scope it
    assert r['offers'] and r['relaxed_preferences'][0]['reason']=='other_category'
    assert all(branded(o,'سامسونگ') for o in cat.search('گوشی موبایل',{'brand':'سامسونگ'})['offers'])

def test_offer_level_checks_follow_the_same_scope(cat):
    phone=next(o for o in cat.search('گوشی موبایل',{})['offers'] if not branded(o,'سامسونگ'))
    buds=next(o for o in cat.search('هدفون بی سیم',{})['offers'] if not branded(o,'سامسونگ'))
    assert cat.brand_blocks(phone,PHONE) and not cat.brand_blocks(buds,PHONE)
    assert not cat.brand_blocks(phone,{}) and not cat.brand_blocks(phone,{'brand':'سامسونگ','brand_scope':'هدفون سامسونگ'})

def test_specific_product_lookups_are_not_rescoped(cat):
    pid=cat.search('هدفون بی سیم',{})['offers'][0]['product_id']
    r=cat.search(profile=WATCH,product_ids=[pid])
    assert 'relaxed_preferences' not in r

def test_profile_scopes_the_brand_to_the_request_that_named_it(tmp_path):
    from memory import Memory
    mem=Memory(tmp_path/'m.db');uid=mem.user();sid=mem.session(uid)['id']
    msg='یه ساعت هوشمند گارمین Forerunner 265 هم می‌خوام'
    mem.update(uid,[{'key':'product_query','value':'ساعت هوشمند گارمین Forerunner 265','quote':'ساعت هوشمند گارمین Forerunner 265'},
                    {'key':'brand','value':'گارمین','quote':'ساعت هوشمند گارمین Forerunner 265'}],'chat',msg,sid)
    p=mem.profile(uid,sid);assert p['brand']=='گارمین' and 'ساعت هوشمند' in p['brand_scope']
    mem.update(uid,[{'key':'brand','value':None,'quote':'برند مهم نیست'}],'chat','برند مهم نیست',sid)
    assert 'brand_scope' not in mem.profile(uid,sid)
    mem.update(uid,[{'key':'brand','value':'گارمین','quote':'تنظیم توسط کاربر'}],'form','',sid)  # filter form: no product words to scope by
    assert 'تنظیم' not in mem.profile(uid,sid)['brand_scope']

@pytest.mark.asyncio
async def test_results_page_search_after_a_watch_request(tmp_path,monkeypatch,cat):
    from memory import Memory
    from agent import Agent
    from features import Features
    monkeypatch.setenv('DATA_DIR',str(tmp_path));server=importlib.import_module('server')
    mem=Memory(tmp_path/'api.db');agent=Agent(cat,mem);features=Features(cat,mem,agent);agent.features=features
    for k,v in dict(cat=cat,mem=mem,agent=agent,features=features).items():monkeypatch.setattr(server,k,v)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://brand-test') as c:
        boot=(await c.get('/api/bootstrap')).json();sid=boot['sessions'][0]['id'];uid=c.cookies.get('hamrah_user')
        msg='یه ساعت هوشمند گارمین Forerunner 265 هم می‌خوام، تا ۳ میلیون'
        mem.update(uid,[{'key':'product_query','value':'ساعت هوشمند گارمین Forerunner 265','quote':'ساعت هوشمند گارمین Forerunner 265'},
                        {'key':'brand','value':'گارمین','quote':'ساعت هوشمند گارمین Forerunner 265'},{'key':'budget_max_toman','value':3000000,'quote':'تا ۳ میلیون'}],'chat',msg,sid)
        r=(await c.post('/api/search',json={'session_id':sid,'query':'کیو سی وای T13 ANC 2'})).json()
        assert r['offers'] and r['preference_notice'] and all(o['effective_price_toman']<=3000000 for o in r['offers'])
