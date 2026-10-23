import sys,os,tempfile
from pathlib import Path
from datetime import datetime
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from catalog import Catalog,opening,TEHRAN,payment_matches,normalize
from memory import Memory
import pytest

@pytest.fixture
def memory(tmp_path):return Memory(tmp_path/'memory.db')

def test_memory_requires_user_evidence_and_handles_corrections(memory):
 u=memory.user();v=memory.user()
 r=memory.update(u,[{'key':'budget_max_toman','value':3000000,'quote':'۳ میلیون'},{'key':'brand','value':'سامسونگ','quote':'سامسونگ'}],'chat','تا ۳ میلیون می‌خوام')
 assert r['saved']==['budget_max_toman']
 memory.update(u,[{'key':'budget_max_toman','value':5000000,'quote':'۵ میلیون'}],'chat','بودجه‌ام ۵ میلیون شد')
 assert memory.profile(u)['budget_max_toman']==5000000 and memory.profile(v)=={}
 s=memory.session(u);memory.save_turn(u,s['id'],'user','پیام خصوصی')
 with pytest.raises(KeyError):memory.turns(v,s['id'])
 memory.update(u,[{'key':'budget_max_toman','value':None}]);assert memory.profile(u)=={}

def test_credit_rejects_online_unknown_limits_and_category():
 opts=[{'channel':'online','provider_name':'تارا'},{'channel':'unknown','provider_name':'تارا'},{'channel':'in_person','provider_name':'تارا','min_price_toman':100,'max_price_toman':1000,'category_names':['گوشی موبایل']}]
 assert len(payment_matches(opts,500,'گوشی موبایل'))==1
 assert payment_matches(opts,1200,'گوشی موبایل')==[]
 assert payment_matches(opts,500,'هدفون')==[]
 assert payment_matches(opts,500,'گوشی موبایل','اسنپ پی')==[]

def test_hours_split_overnight_closed_unknown():
 h={'wed':{'status':'declared','shifts':[{'start_time':'09:00:00','end_time':'12:00:00'},{'start_time':'16:00:00','end_time':'20:00:00'}]}}
 assert opening(h,datetime(2026,10,21,10,tzinfo=TEHRAN))['state']=='open'
 assert opening(h,datetime(2026,10,21,14,tzinfo=TEHRAN))['state']=='closed'
 assert opening({},datetime(2026,10,21,10,tzinfo=TEHRAN))['state']=='unknown'
 h={'tue':{'status':'declared','shifts':[{'start_time':'22:00:00','end_time':'02:00:00'}]},'wed':{'status':'closed'}}
 assert opening(h,datetime(2026,10,21,1,tzinfo=TEHRAN))['state']=='open'

def test_persian_normalization():assert normalize('گوشي‌ سامسونگ ۱۲٣')=='گوشی سامسونگ 123'

@pytest.fixture
def catalog():
 path=os.getenv('TEST_CATALOG')
 if not path:pytest.skip('TEST_CATALOG needed for full-dataset integration')
 return Catalog(path)

def test_full_dataset_integrity(catalog):
 assert catalog.stats()['products']==225903
 with catalog.conn() as c:
  assert c.execute('select count(*) from offers o left join products p using(product_id) left join shops s using(shop_id) where p.product_id is null or s.shop_id is null').fetchone()[0]==0

def test_primary_product_queries_do_not_return_accessories(catalog):
 for q,allowed in [('گوشی سامسونگ',{'گوشی موبایل'}),('لپ تاپ',{'لپ تاپ و نوت بوک'}),('هدفون',{'هدفون، هدست و هندزفری'})]:
  r=catalog.search(q);assert r['offers'];assert all(o['category_name'] in allowed for o in r['offers'])

def test_budget_distance_and_credit_are_hard_filters(catalog):
 r=catalog.search('هدفون',{'budget_max_toman':3000000,'lat':35.70,'lon':51.41,'radius_km':3})
 assert r['offers']
 assert all(x['price_toman']<=3000000 and x['distance_km']<=3.01 for x in r['offers'])
 r=catalog.search('گوشی',{'installment_only':True})
 assert r['offers'] and all(x['in_person_credit'] for x in r['offers'])
 assert catalog.search('هدفون',{'budget_max_toman':1})['offers']==[]

def test_contact_requires_explicit_offer(catalog,memory):
 from agent import Agent
 a=Agent(catalog,memory);o=catalog.search('هدفون')['offers'][0]
 d=a.contact(o['shop_id'],o['product_id']);assert d['status']=='draft_not_sent' and d['synthetic_phone']
 with catalog.conn() as c:
  other=c.execute('select shop_id from shops where shop_id not in (select shop_id from offers where product_id=?) limit 1',(o['product_id'],)).fetchone()[0]
 with pytest.raises(ValueError):a.contact(other,o['product_id'])

@pytest.mark.asyncio
async def test_agent_keeps_thinking_signature_prefix_stable(memory,monkeypatch):
 import agent as module
 import httpx
 u=memory.user();s=memory.session(u);payloads=[]
 class FakeClient:
  def __init__(self,**kw):pass
  async def __aenter__(self):return self
  async def __aexit__(self,*args):pass
  async def post(self,url,headers,json):
   payloads.append(json)
   if len(payloads)==1:
    data={'content':[{'type':'thinking','thinking':'opaque','signature':'bound-prefix'},{'type':'tool_use','id':'t1','name':'update_preferences','input':{'facts':[{'key':'budget_max_toman','value':3000000,'quote':'۳ میلیون'}]}}]}
   else:data={'content':[{'type':'text','text':'بودجه ذخیره شد.'}]}
   return httpx.Response(200,json=data)
 from llm_fake import use_client;use_client(monkeypatch,FakeClient)
 a=module.Agent(None,memory)
 events=[e async for e in a.response(u,s['id'],'بودجه ۳ میلیون')]
 assert payloads[0]['system']==payloads[1]['system']
 assert memory.profile(u,s['id'])['budget_max_toman']==3000000
 assert 'budget_max_toman' not in memory.profile(u)
 assert events[-1]['type']=='done'

@pytest.mark.asyncio
async def test_seller_demo_requires_explicit_intent_and_keeps_source(catalog,memory):
 from agent import Agent
 u=memory.user();s=memory.session(u);a=Agent(catalog,memory)
 o=catalog.search('هدفون')['offers'][0]
 args={'shop_id':o['shop_id'],'product_id':o['product_id'],'scenario':'no_answer','quote':'پیگیری کن'}
 with pytest.raises(ValueError):await a.execute('run_seller_demo',args,u,s['id'],'پیگیری کن')
 args['quote']='آزمایشی'
 r=await a.execute('run_seller_demo',args,u,s['id'],'آزمایشی فروشنده جواب نداد')
 assert memory.events(u,s['id'])[0]['source']=='simulated'
 assert all(x['shop_id']!=o['shop_id'] for x in r['offers'])
 assert r['simulation_notice']
 assert a.alternatives(u,s['id'],o['product_id'])['simulation_notice']
