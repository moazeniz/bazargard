import importlib,os,sys
from pathlib import Path
from datetime import datetime,timedelta
import httpx,pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))

@pytest.mark.asyncio
async def test_buyer_seller_api_flow_and_ownership(tmp_path,monkeypatch):
 from catalog import Catalog,TEHRAN
 from memory import Memory
 from agent import Agent
 from features import Features
 path=os.getenv('TEST_CATALOG')
 if not path:pytest.skip('TEST_CATALOG required')
 monkeypatch.setenv('DATA_DIR',str(tmp_path));monkeypatch.delenv('PUBLIC_BASE_URL',raising=False)
 server=importlib.import_module('server');cat=Catalog(path);mem=Memory(tmp_path/'api-memory.db');agent=Agent(cat,mem);features=Features(cat,mem,agent);agent.features=features
 for key,value in dict(cat=cat,mem=mem,agent=agent,features=features).items():monkeypatch.setattr(server,key,value)
 transport=httpx.ASGITransport(app=server.app)
 async with httpx.AsyncClient(transport=transport,base_url='http://testserver') as buyer,httpx.AsyncClient(transport=transport,base_url='http://testserver') as seller:
  b=(await buyer.get('/api/bootstrap')).json();sid=b['sessions'][0]['id']
  r=(await buyer.post('/api/search',json={'session_id':sid,'query':'هدفون'})).json();o=r['offers'][0]
  changed=await buyer.patch('/api/profile',json={'session_id':sid,'values':{'budget_max_toman':3000000,'recipient':'پدر'}});assert changed.status_code==200
  await buyer.patch('/api/profile',json={'session_id':sid,'scope':'memory','values':{'budget_max_toman':4000000}})
  current=(await buyer.get('/api/sessions/'+sid)).json();assert next(x['value'] for x in current['facts'] if x['key']=='budget_max_toman')==4000000
  fresh=(await buyer.post('/api/sessions',json={})).json()['id'];facts=(await buyer.get('/api/sessions/'+fresh)).json()['facts'];assert not any(f['key']=='recipient' for f in facts)
  link=(await buyer.post('/api/merchant-requests',json={'session_id':sid,'offer_id':o['offer_id']})).json();assert link['url'].startswith('http://testserver/?seller=')
  public=(await seller.get('/api/merchant-requests/'+link['token'])).json();assert 'session_id' not in public and not public['answered']
  now=datetime.now(TEHRAN);reply=await seller.post('/api/merchant-requests/'+link['token']+'/reply',json={'available':True,'price_toman':500000,'exact_variant':'M19','valid_until':(now+timedelta(hours=2)).isoformat(),'accept_hold':True,'hold_until':(now+timedelta(hours=1)).isoformat(),'indoor':{'unit':'۲۱۷'}});assert reply.status_code==200,reply.text
  assert (await buyer.get('/api/quotes/'+sid)).json()['quotes'][0]['status']=='held'
  assert (await seller.post('/api/merchant-requests/'+link['token']+'/reply',json={'available':False,'valid_until':(now+timedelta(hours=1)).isoformat()})).status_code==422
  await seller.get('/api/bootstrap')
  assert (await seller.get('/api/sessions/'+sid)).status_code==404
  assert (await seller.get('/api/quotes/'+sid)).status_code==404
  assert (await seller.post('/api/route',json={'session_id':sid,'shop_ids':[o['shop_id']]})).status_code==404
  assert (await buyer.post('/api/basket',json={'session_id':sid,'query':o['persian_title'],'product_id':o['product_id'],'quantity':1})).status_code==200
  plans=(await buyer.get('/api/basket/'+sid+'/plans')).json();assert plans['plans']
  assert (await buyer.get('/api/decision/'+sid)).status_code==200
  image_search=(await buyer.post('/api/search',json={'session_id':sid,'query':'هدفون M19 wireless earbuds','recognized_model':'M19'})).json()
  assert image_search['offers'] and any(o['image_match']=='exact_model' for o in image_search['offers'])
  cost=(await buyer.post('/api/costs',json={'session_id':sid,'offer_ids':[o['offer_id']]})).json();assert cost['subtotal_toman']==500000
  assert (await buyer.post('/api/identify-image',json={'session_id':sid,'image':'not base64','media_type':'image/png'})).status_code==422
  assert (await buyer.post('/api/seller-events',json={'session_id':sid,'shop_id':o['shop_id'],'product_id':'not-a-product','status':'reported'})).status_code==404
  await buyer.delete('/api/memory?all_data=true')
  assert (await seller.get('/api/merchant-requests/'+link['token'])).status_code==404

@pytest.mark.asyncio
async def test_sse_heartbeat_preserves_final_reply_and_history(tmp_path,monkeypatch):
 import asyncio
 from catalog import Catalog
 from memory import Memory
 from agent import Agent
 from features import Features
 from starlette.requests import Request
 path=os.getenv('TEST_CATALOG')
 if not path:pytest.skip('TEST_CATALOG required')
 monkeypatch.setenv('DATA_DIR',str(tmp_path));server=importlib.import_module('server')
 cat=Catalog(path);mem=Memory(tmp_path/'stream-memory.db');agent=Agent(cat,mem);features=Features(cat,mem,agent);agent.features=features
 for key,value in dict(cat=cat,mem=mem,agent=agent,features=features,CHAT_HEARTBEAT_SECONDS=.01).items():monkeypatch.setattr(server,key,value)
 async def consumed_request_flag(self):return True
 monkeypatch.setattr(Request,'is_disconnected',consumed_request_flag)
 async def slow_response(uid,sid,message,mode='advanced'):
  yield {'type':'status','message':'در حال بررسی'}
  await asyncio.sleep(.08)
  mem.save_turn(uid,sid,'assistant','نتیجه آماده شد')
  yield {'type':'done','reply':'نتیجه آماده شد','results':{'offers':[]}}
 monkeypatch.setattr(agent,'response',slow_response)
 async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://stream-test') as client:
  sid=(await client.get('/api/bootstrap')).json()['sessions'][0]['id']
  r=await client.post('/api/chat',json={'session_id':sid,'message':'آزمون پاسخ طولانی'})
  assert r.status_code==200 and ': keepalive' in r.text and '"type": "done"' in r.text
  state=(await client.get('/api/sessions/'+sid)).json()
  assert state['turns'][-1]['content']=='نتیجه آماده شد'

@pytest.mark.asyncio
async def test_background_run_survives_submission_and_is_owner_scoped(tmp_path,monkeypatch):
 import asyncio
 from catalog import Catalog
 from memory import Memory
 from agent import Agent
 from features import Features
 from runs import Runs
 path=os.getenv('TEST_CATALOG')
 if not path:pytest.skip('TEST_CATALOG required')
 monkeypatch.setenv('DATA_DIR',str(tmp_path));server=importlib.import_module('server')
 cat=Catalog(path);mem=Memory(tmp_path/'runs.db');agent=Agent(cat,mem);features=Features(cat,mem,agent);agent.features=features;runner=Runs(mem)
 for key,value in dict(cat=cat,mem=mem,agent=agent,features=features,runs=runner).items():monkeypatch.setattr(server,key,value)
 release=asyncio.Event()
 async def response(u,sid,message,mode='advanced'):
  mem.save_turn(u,sid,'user',message)
  yield {'type':'status','message':'در حال بررسی'}
  await release.wait()
  mem.save_turn(u,sid,'assistant','پاسخ ذخیره شد')
  yield {'type':'done','reply':'پاسخ ذخیره شد','facts':[],'results':{'offers':[]}}
 monkeypatch.setattr(agent,'response',response)
 transport=httpx.ASGITransport(app=server.app)
 async with httpx.AsyncClient(transport=transport,base_url='http://run-test') as client,httpx.AsyncClient(transport=transport,base_url='http://run-test') as other:
  sid=(await client.get('/api/bootstrap')).json()['sessions'][0]['id'];await other.get('/api/bootstrap')
  accepted=(await client.post('/api/chat-runs',json={'session_id':sid,'message':'پاسخ را ذخیره کن'})).json();rid=accepted['run_id']
  assert (await other.get('/api/chat-runs/'+rid)).status_code==404
  assert (await client.post('/api/chat-runs',json={'session_id':sid,'message':'تکرار همزمان'})).status_code==409
  release.set()
  for _ in range(100):
   poll=(await client.get('/api/chat-runs/'+rid)).json()
   if poll['status']=='completed':break
   await asyncio.sleep(.01)
  assert poll['status']=='completed' and poll['events'][-1]['event']['type']=='done'
  state=(await client.get('/api/sessions/'+sid)).json();assert state['turns'][-1]['content']=='پاسخ ذخیره شد'
  after=poll['events'][-1]['seq'];assert (await client.get('/api/chat-runs/'+rid,params={'after':after})).json()['events']==[]
  await client.delete('/api/memory?all_data=true');assert (await client.get('/api/chat-runs/'+rid)).status_code==404

@pytest.mark.asyncio
async def test_operations_routes_isolate_owners_and_shopping_sessions(tmp_path,monkeypatch):
 from catalog import Catalog
 from memory import Memory
 from agent import Agent
 from features import Features
 from ops import Operations
 from runs import Runs
 path=os.getenv('TEST_CATALOG')
 if not path:pytest.skip('TEST_CATALOG required')
 monkeypatch.setenv('DATA_DIR',str(tmp_path));server=importlib.import_module('server');cat=Catalog(path);mem=Memory(tmp_path/'ops-api.db');a=Agent(cat,mem);f=Features(cat,mem,a);a.features=f;o=Operations(cat,mem,Path(__file__).resolve().parents[1]);runner=Runs(mem)
 for k,v in dict(mem=mem,cat=cat,agent=a,features=f,ops=o,runs=runner).items():monkeypatch.setattr(server,k,v)
 try:
  transport=httpx.ASGITransport(app=server.app)
  async with httpx.AsyncClient(transport=transport,base_url='http://ops-api') as c,httpx.AsyncClient(transport=transport,base_url='http://ops-api') as other:
   sid=(await c.get('/api/ops/bootstrap')).json()['session_id'];await other.get('/api/ops/bootstrap')
   assert (await other.get('/api/ops/state/'+sid)).status_code==404
   shopping=(await c.get('/api/bootstrap')).json()['sessions'][0]['id'];assert shopping!=sid
   assert (await c.post('/api/chat-runs',json={'session_id':sid,'message':'wrong workspace'})).status_code==404
   assert (await c.post('/api/ops/chat-runs',json={'session_id':shopping,'message':'wrong workspace'})).status_code==404
   a=(await c.post('/api/ops/actions',json={'session_id':sid,'kind':'deploy_v1'})).json()
   assert (await other.post('/api/ops/actions/'+a['id']+'/approve',json={'session_id':sid,'nonce':a['nonce'],'accept':True})).status_code==404
   denied=await c.post('/api/ops/actions/'+a['id']+'/approve',json={'session_id':sid,'nonce':a['nonce'],'accept':False});assert denied.status_code==200
   assert (await c.get('/api/ops/state/'+sid)).json()['evidence']['snapshot']['state']=='not_deployed'
   assert (await c.get('/api/ready')).status_code==200
   await c.delete('/api/memory?all_data=true')
   with mem.conn() as db:assert db.execute('select count(*) from ops_actions').fetchone()[0]==0
 finally:o.labs.close()

@pytest.mark.asyncio
async def test_modes_persist_per_trip_and_block_changes_while_running(tmp_path,monkeypatch):
 import asyncio
 from catalog import Catalog
 from memory import Memory
 from agent import Agent
 from features import Features
 from runs import Runs
 path=os.getenv('TEST_CATALOG')
 if not path:pytest.skip('TEST_CATALOG required')
 monkeypatch.setenv('DATA_DIR',str(tmp_path));server=importlib.import_module('server');cat=Catalog(path);mem=Memory(tmp_path/'modes-api.db');a=Agent(cat,mem);f=Features(cat,mem,a);a.features=f;runner=Runs(mem);seen=[]
 for k,v in dict(mem=mem,cat=cat,agent=a,features=f,runs=runner).items():monkeypatch.setattr(server,k,v)
 async def response(u,s,message,mode):
  seen.append(mode);await asyncio.sleep(.05);yield {'type':'done','reply':'ok','facts':[],'results':{'offers':[]}}
 monkeypatch.setattr(a,'response',response)
 async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://modes') as c,httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://modes') as other:
  sid=(await c.get('/api/bootstrap')).json()['sessions'][0]['id'];await other.get('/api/bootstrap')
  assert (await c.get('/api/sessions/'+sid)).json()['session']['chat_mode']=='simple'
  assert (await other.patch('/api/sessions/'+sid+'/mode',json={'mode':'advanced'})).status_code==404
  assert (await c.patch('/api/sessions/'+sid+'/mode',json={'mode':'advanced'})).status_code==200
  r=await c.post('/api/chat-runs',json={'session_id':sid,'message':'آزمون حالت'});assert r.json()['mode']=='advanced'
  assert (await c.patch('/api/sessions/'+sid+'/mode',json={'mode':'simple'})).status_code==409
  await asyncio.sleep(.1);assert seen==['advanced']
  assert (await c.patch('/api/sessions/'+sid+'/mode',json={'mode':'simple'})).status_code==200
  fresh=(await c.post('/api/sessions',json={})).json();assert fresh['chat_mode']=='simple'
  assert (await c.patch('/api/sessions/'+sid+'/mode',json={'mode':'unlimited'})).status_code==422

@pytest.mark.asyncio
async def test_free_location_api_is_owner_scoped_and_uses_catalogue_coordinates(tmp_path,monkeypatch):
 from catalog import Catalog
 from memory import Memory
 from agent import Agent
 from features import Features
 path=os.getenv('TEST_CATALOG')
 if not path:pytest.skip('TEST_CATALOG required')
 monkeypatch.setenv('DATA_DIR',str(tmp_path));server=importlib.import_module('server');cat=Catalog(path);mem=Memory(tmp_path/'geo-api.db');a=Agent(cat,mem);f=Features(cat,mem,a);a.features=f
 for k,v in dict(mem=mem,cat=cat,agent=a,features=f).items():monkeypatch.setattr(server,k,v)
 async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://geo') as c,httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://geo') as other:
  sid=(await c.get('/api/bootstrap')).json()['sessions'][0]['id'];await other.get('/api/bootstrap');shop=cat.search('هدفون')['offers'][0]['shop_id']
  result=await c.get('/api/locations/'+shop,params={'session_id':sid});assert result.status_code==200 and result.json()['osm']['status']=='not_requested'
  assert (await other.post('/api/locations/enrich',json={'session_id':sid,'shop_id':shop})).status_code==404
  assert (await c.post('/api/locations/enrich',json={'session_id':sid,'shop_id':'https://malicious.invalid'})).status_code==404
