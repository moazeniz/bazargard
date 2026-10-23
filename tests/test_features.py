import asyncio,base64,json,os,sys,time
from pathlib import Path
from datetime import datetime,timedelta
import httpx,pytest
from llm_fake import use_client
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from catalog import Catalog,TEHRAN
from memory import Memory
from agent import Agent
from features import Features

@pytest.fixture
def env(tmp_path):
 path=os.getenv('TEST_CATALOG')
 if not path:pytest.skip('TEST_CATALOG required')
 cat=Catalog(path);mem=Memory(tmp_path/'memory.db');u=mem.user();sid=mem.session(u)['id'];a=Agent(cat,mem);f=Features(cat,mem,a);a.features=f
 return cat,mem,u,sid,a,f

def sample(cat):return cat.search('هدفون')['offers'][0]
def report(mem,u,sid,o,**kw):mem.event(u,sid,dict(shop_id=o['shop_id'],product_id=o['product_id'],status='reported',**kw))

def test_reported_price_filters_both_directions_and_ranks(env):
 cat,mem,u,sid,a,f=env;o=sample(cat)
 report(mem,u,sid,o,price=5000000,available=True)
 p={'budget_max_toman':3000000,'priority':'price'}
 r=cat.search(profile=p,product_ids=[o['product_id']],seller_events=mem.events(u,sid))
 assert all(x['offer_id']!=o['offer_id'] for x in r['offers'])
 report(mem,u,sid,o,price=100,available=True)
 r=cat.search(profile={'budget_max_toman':150,'priority':'price'},product_ids=[o['product_id']],seller_events=mem.events(u,sid))
 assert r['offers'][0]['offer_id']==o['offer_id'] and r['offers'][0]['effective_price_toman']==100
 assert r['offers'][0]['price_toman']==o['price_toman']
 assert not cat.search(profile={'budget_min_toman':200,'budget_max_toman':300},product_ids=[o['product_id']],seller_events=mem.events(u,sid))['offers']

def test_credit_eligibility_uses_reported_price(env):
 cat,mem,u,sid,a,f=env;o=sample(cat);o['payment_options']=[{'channel':'in_person','provider_name':'test','max_price_toman':1000}]
 report(mem,u,sid,o,price=5000)
 from evidence import SellerReports
 d=cat.hydrate_offer(o,{},SellerReports(mem.events(u,sid)))
 assert d['in_person_credit']==[] and not cat.eligible(d,{'installment_only':True})
 report(mem,u,sid,o,price=500)
 d=cat.hydrate_offer(o,{},SellerReports(mem.events(u,sid)))
 assert d['in_person_credit']

def test_expired_cached_fields_are_cleared_and_isolated(env):
 cat,mem,u,sid,a,f=env;o=sample(cat)
 report(mem,u,sid,o,price=2500000,available=True,hours='امروز تعطیل؛ فردا از ساعت ۱۴ باز می‌کنیم')
 fresh=a.overlay(u,sid,{'offers':[o]});assert fresh['offers'][0]['reported_price_toman']==2500000
 with mem.conn() as c:c.execute('update seller_events set created=?',(time.time()-90001,))
 aged=a.overlay(u,sid,fresh)['offers'][0]
 assert aged['availability']=='unknown' and 'reported_price_toman' not in aged
 assert aged['effective_price_toman']==o['price_toman'] and aged['opening']['source']=='dataset'
 assert aged['seller_report_expired'] and fresh['offers'][0]['reported_price_toman']==2500000
 other=mem.session(u)['id'];assert a.overlay(u,other,{'offers':[o]})['offers'][0]['price_basis']=='dataset'

def test_reported_hours_reach_results_and_route_without_guessing(env):
 cat,mem,u,sid,a,f=env;o=sample(cat);hours='امروز تعطیل؛ فردا از ساعت ۱۴ باز می‌کنیم'
 report(mem,u,sid,o,hours=hours)
 d=a.overlay(u,sid,{'offers':[o]})['offers'][0]
 assert d['opening']['state']=='unknown' and hours in d['opening']['label']
 assert hours in a.route(u,[o['shop_id']],sid)['stops'][0]['opening']['label']
 mem.update(u,[{'key':'open_now','value':True}],sid=sid)
 assert a.overlay(u,sid,{'offers':[o]})['offers']==[]

def test_trip_preferences_do_not_overwrite_memory(env):
 cat,mem,u,sid,a,f=env
 mem.update(u,[{'key':'budget_max_toman','value':5000000}]);mem.update(u,[{'key':'budget_max_toman','value':2000000},{'key':'recipient','value':'پدر'}],sid=sid)
 assert mem.profile(u)['budget_max_toman']==5000000
 assert mem.profile(u,sid)['budget_max_toman']==2000000
 other=mem.session(u)['id'];assert 'recipient' not in mem.profile(u,other)
 mem.update(u,[{'key':'budget_max_toman','value':None}],sid=sid)
 assert 'budget_max_toman' not in mem.profile(u,sid) and mem.profile(u)['budget_max_toman']==5000000

def test_merchant_link_confirm_hold_expiry_and_one_use(env):
 cat,mem,u,sid,a,f=env;o=sample(cat);now=datetime.now(TEHRAN)
 link=f.create_request(u,sid,o['offer_id'],(now+timedelta(minutes=30)).isoformat())
 assert link['status']=='awaiting_seller'
 public=f.public_request(link['token']);assert 'user_id' not in public and 'session_id' not in public
 data={'available':True,'exact_variant':'M19 مشکی','price_toman':123456,'valid_until':(now+timedelta(hours=1)).isoformat(),'accept_hold':True,'hold_until':(now+timedelta(minutes=30)).isoformat(),'open_at':(now-timedelta(minutes=5)).isoformat(),'close_at':(now+timedelta(hours=2)).isoformat(),'indoor':{'floor':'۲','unit':'۲۱۷','entrance':'در شمالی','step_free':'بله'}}
 saved=f.merchant_reply(link['token'],data);assert saved['hold_code']
 assert f.quotes(u,sid)[0]['status']=='held'
 with pytest.raises(ValueError):f.merchant_reply(link['token'],data)
 current=f.offer(u,sid,o['offer_id']);assert current['price_basis']=='merchant_link' and current['effective_price_toman']==123456
 assert current['indoor']['unit']=='۲۱۷' and current['opening']['state']=='open'
 assert current['merchant_quote']['identity']=='bearer_link_not_independently_verified'
 with mem.conn() as c:c.execute('update seller_events set created=?',(time.time()-90001,))
 assert f.offer(u,sid,o['offer_id'])['price_basis']=='dataset'

def test_merchant_cannot_accept_impossible_hold(env):
 cat,mem,u,sid,a,f=env;o=sample(cat);now=datetime.now(TEHRAN);link=f.create_request(u,sid,o['offer_id'])
 with pytest.raises(ValueError):f.merchant_reply(link['token'],{'available':True,'price_toman':100,'exact_variant':'x','valid_until':(now+timedelta(minutes=20)).isoformat(),'accept_hold':True,'hold_until':(now+timedelta(hours=1)).isoformat()})
 assert not f.public_request(link['token'])['answered']

def test_inspection_quotes_source_and_variant_differences(env):
 *_,f=env
 d=f.inspect({'persian_title':'گوشی 256GB','seller_title':'گوشی 128GB غیر اصل عمده','description':'کارکرده','warranty':None,'availability':'unknown'})
 assert {'authenticity','condition','wholesale','variant'}<={x['code'] for x in d['flags']}
 assert d['missing'] and all(x['quote'] for x in d['flags'])

def test_basket_respects_total_budget_quantity_and_stop_count(env):
 cat,mem,u,sid,a,f=env;o=sample(cat)
 f.add_item(u,sid,o['persian_title'],o['product_id'],2)
 with cat.conn() as c:cheapest=c.execute('select min(price_toman) from offers where product_id=? and price_toman>0',(o['product_id'],)).fetchone()[0]
 mem.update(u,[{'key':'budget_max_toman','value':cheapest*2-1}],sid=sid)
 assert f.basket_plans(u,sid)['plans']==[]
 mem.update(u,[{'key':'budget_max_toman','value':cheapest*3}],sid=sid)
 plans=f.basket_plans(u,sid)['plans'];assert plans
 assert all(p['subtotal_toman']<=cheapest*3 and p['offers'][0]['quantity']==2 and p['stop_count']<=4 for p in plans)

def test_decision_options_actually_partition_products(env):
 cat,mem,u,sid,a,f=env
 with mem.conn() as c:c.execute("update sessions set chat_mode='advanced' where id=?",(sid,))
 # Ranking depends on opening hours at the visit time; pin it so the top set is reproducible.
 mem.update(u,[{'key':'needs','value':'مکالمه'},{'key':'budget_max_toman','value':3000000},{'key':'visit_at','value':'2026-10-24T12:30'}],sid=sid)
 r=a.overlay(u,sid,cat.search('هدفون',mem.profile(u,sid)))
 # The clarification contract needs a populated facet covering >= half of
 # the candidates. Time-dependent opening/ranking can change the top set.
 r['offers']=[o for o in r['offers'] if any(s['key']=='نوع اتصال' for s in o.get('specs',[]))]
 mem.results(u,sid,r);q=f.decision(u,sid)
 assert q['question'] and q['kind']=='spec' and 2<=len(q['options'])<=4
 key=q['spec_key'];value=q['options'][0]['value'];f.clarification.answer(u,sid,q['key'],value,strict=True)
 narrowed=a.overlay(u,sid,r)
 assert narrowed['offers'] and len(narrowed['offers'])<len(r['offers'])
 from catalog import spec_value
 assert all(any(s['key']==key and spec_value(key,s['value'])==spec_value(key,value) for s in o['specs']) for o in narrowed['offers'])


@pytest.mark.asyncio
async def test_trip_timing_includes_return_shopping_buffer_and_deadline(env,monkeypatch):
 cat,mem,u,sid,a,f=env;o=sample(cat);depart=datetime.now(TEHRAN)
 mem.update(u,[{'key':'lat','value':35.7},{'key':'lon','value':51.4}],sid=sid)
 f.mission(u,sid,{'departure':depart.isoformat(),'deadline':(depart+timedelta(minutes=30)).isoformat(),'shopping_minutes':15,'buffer_minutes':10,'return_to_origin':True})
 async def road(points,mode):return {'distance_meters':5000,'legs':[{'duration_seconds':600},{'duration_seconds':600}]}
 monkeypatch.setattr(f,'road',road)
 route=await f.plan(u,sid,[o['shop_id']]);assert route['travel_minutes']==20 and route['feasible'] is False
 assert (datetime.fromisoformat(route['finish_at'])-depart).total_seconds()==45*60
 assert route['timing_source']=='osrm_no_live_traffic'

@pytest.mark.asyncio
async def test_missing_routing_never_claims_trip_feasible(env,monkeypatch):
 cat,mem,u,sid,a,f=env;o=sample(cat)
 route=await f.plan(u,sid,[o['shop_id']]);assert route['finish_at'] is None and route['feasible'] is not True and route['unknowns']

def test_costs_do_not_invent_terms_and_calculate_zero_fees(env):
 cat,mem,u,sid,a,f=env;o=sample(cat)
 unknown=f.costs(u,sid,[o['offer_id']]);assert unknown['trip_total_toman'] is None and unknown['installment_total_toman'] is None
 d=f.costs(u,sid,[o['offer_id']],{'travel_km':10,'cost_per_km_toman':10000,'parking_toman':0,'upfront':100000,'monthly':50000,'months':4,'fees':0})
 assert d['trip_total_toman']==o['price_toman']+100000 and d['installment_total_toman']==300000

@pytest.mark.asyncio
async def test_photo_requires_valid_image_and_confirmation(env,monkeypatch):
 cat,mem,u,sid,a,f=env
 with pytest.raises(ValueError):await f.identify(base64.b64encode(b'not an image'*10).decode(),'image/png')
 class Client:
  def __init__(self,**kw):pass
  async def __aenter__(self):return self
  async def __aexit__(self,*args):pass
  async def post(self,url,headers,json):
   assert json['messages'][0]['content'][0]['type']=='image'
   return httpx.Response(200,json={'content':[{'type':'text','text':'{"query":"هدفون M19","model":"M19","confidence":"high","visible_text":"M19"}'}]})
 use_client(monkeypatch,Client)
 d=await f.identify(base64.b64encode(b'\x89PNG\r\n\x1a\n'+b'x'*40).decode(),'image/png')
 assert d['requires_confirmation'] and d['query']=='M19'

def test_references_survive_new_search_and_session_reload(env):
 cat,mem,u,sid,a,f=env;first=sample(cat)
 refs={'offers':[{k:first[k] for k in ('offer_id','product_id','persian_title','price_toman')}]}
 mem.save_turn(u,sid,'assistant','پیشنهاد من [1] است',refs);mem.results(u,sid,cat.search('گوشی'))
 stored=mem.turns(u,sid)[0]['references']['offers'][0]
 assert stored['offer_id']==first['offer_id'] and stored['persian_title']==first['persian_title']

@pytest.mark.asyncio
async def test_new_reports_supersede_old_reservation_promises(env):
 cat,mem,u,sid,a,f=env;o=sample(cat);now=datetime.now(TEHRAN)
 def reply(token,price):
  return f.merchant_reply(token,{'available':True,'exact_variant':o['persian_title'],'price_toman':price,'valid_until':(now+timedelta(hours=2)).isoformat(),'accept_hold':True,'hold_until':(now+timedelta(hours=1)).isoformat()})
 first=f.create_request(u,sid,o['offer_id']);reply(first['token'],500000)
 second=f.create_request(u,sid,o['offer_id']);reply(second['token'],600000)
 quotes={q['token']:q for q in f.quotes(u,sid)}
 assert quotes[first['token']]['status']=='superseded' and quotes[second['token']]['status']=='held'
 report(mem,u,sid,o,price=700000)
 assert all(q['status']=='needs_reconfirmation' for q in f.quotes(u,sid))
 report(mem,u,sid,o,available=False)
 assert all(q['status']=='unavailable' for q in f.quotes(u,sid))
 mem.update(u,[{'key':'travel_mode','value':'walking'},{'key':'lat','value':35.7},{'key':'lon','value':51.4}],sid=sid)
 f.mission(u,sid,{'manual_leg_minutes':5})
 plan=await f.plan(u,sid,[o['shop_id']])
 assert plan['feasible'] is False and any('نگهداری قبلی' in s for s in plan['problems'])
