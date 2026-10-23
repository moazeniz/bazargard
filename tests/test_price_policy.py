import os,sys,time,copy
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import pytest
from price_policy import initial,select

def offers(prices):return [{'product_id':'p'+str(i),'offer_id':'o'+str(i),'shop_id':'s'+str(i),'effective_price_toman':n,'price_toman':n,'price_basis':'dataset','score':100-i,'persian_title':'Product '+str(i)} for i,n in enumerate(prices)]

def test_balanced_five_distinct_products_source_bounds_and_alternatives():
 source=offers([100,200,300,400,500,600,700,800,900]);source.append(dict(source[3],offer_id='other',shop_id='other',effective_price_toman=450))
 original=copy.deepcopy(source);result,p=select(source,{'budget_max_toman':900})
 assert len(result)==5 and len({o['product_id'] for o in result})==5
 assert p['distribution']=={'lower':1,'middle':2,'upper':2}
 assert all(o['price_band']['source']=='dataset' for o in result) and source==original
 assert all('بودجه شما' in o['price_band']['label'] for o in result if o['price_band']['tier']=='upper')
 assert next(o for o in result if o['product_id']=='p3')['seller_alternatives'][0]['price_toman']==450

@pytest.mark.parametrize('prices',[[100]*8,[100,100,200],[100,200],[]])
def test_ties_and_sparse_sets_never_fake_bands_or_candidates(prices):
 result,p=select(offers(prices));assert len(result)==min(5,len(prices))
 bands={}
 for o in result:bands.setdefault(o['effective_price_toman'],set()).add(o['price_band']['tier'])
 assert all(len(v)==1 for v in bands.values())
 if prices:assert 'کافی نیست' in p['notice']

@pytest.mark.parametrize('query,message,previous,sort,profile',[
 ('A55','A55',{},'balanced',{}),('هدفون','ارزان ترین هدفون',{},'balanced',{}),('گوشی','نزدیک ترین گوشی',{},'balanced',{}),('هدفون','سه مدل هدفون',{},'balanced',{}),('هدفون','هدفون',{},'price',{}),('هدفون','هدفون',{},'balanced',{'priority':'distance'}),('گوشی','همین مدل گوشی',{},'balanced',{})])
def test_explicit_and_followup_intents_override_default(query,message,previous,sort,profile):
 assert not initial(query,message,previous,sort,profile)
 assert initial('لپ تاپ','لپ تاپ برای دانشگاه میخوام',{},'balanced',{'budget_max_toman':50000000})

@pytest.mark.skipif(not os.getenv('TEST_CATALOG'),reason='Full catalogue required')
@pytest.mark.parametrize('query',['هدفون','گوشی سامسونگ','لپ تاپ','دوربین مدار بسته','سیم و کابل برق'])
def test_real_category_price_spread_respects_constraints_and_reports(query):
 from catalog import Catalog
 c=Catalog(os.environ['TEST_CATALOG']);base=c.search(query,discovery=True);assert len(base['offers'])==5 and base['price_diversity']['distribution']=={'lower':1,'middle':2,'upper':2}
 assert len({o['product_id'] for o in base['offers']})==5
 ceiling=sorted(o['effective_price_toman'] for o in base['offers'])[2]
 limited=c.search(query,{'budget_max_toman':ceiling},discovery=True)
 assert limited['offers'] and all(o['effective_price_toman']<=ceiling for o in limited['offers'])
 chosen=limited['offers'][0]
 event=dict(shop_id=chosen['shop_id'],product_id=chosen['product_id'],status='reported',price=ceiling+1,created=time.time(),source='user_reported',available=1,details={})
 changed=c.search(query,{'budget_max_toman':ceiling},seller_events=[event],discovery=True)
 assert all(o['effective_price_toman']<=ceiling for o in changed['offers'])
 assert all(o['offer_id']!=chosen['offer_id'] for o in changed['offers'])
 assert all(o['price_band']['price_toman']==o['effective_price_toman'] for o in changed['offers'])
 credit=c.search(query,{'installment_only':True},discovery=True)
 assert all(o['in_person_credit'] for o in credit['offers'])

@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv('TEST_CATALOG'),reason='Full catalogue required')
@pytest.mark.parametrize('mode',['simple','advanced'])
async def test_quick_and_advanced_tools_share_initial_policy_and_compare_all(tmp_path,mode):
 from catalog import Catalog
 from memory import Memory
 from agent import Agent
 from research import Research
 c=Catalog(os.environ['TEST_CATALOG']);m=Memory(tmp_path/'m.db');u=m.user();sid=m.session(u)['id'];a=Agent(c,m)
 if mode=='simple':r=await a.execute('search_products',{'query':'هدفون'},u,sid,'هدفون میخوام')
 else:
  research=Research(a,u,sid);research.initial_message='هدفون میخوام';r=await research.search(['هدفون'],'شروع خرید');m.results(u,sid,r);assert research.query_count==1
 assert len(r['offers'])==5 and r['price_diversity']['distribution']=={'lower':1,'middle':2,'upper':2}
 ids=[o['product_id'] for o in r['offers']];snapshot=a.comparison(u,sid,ids);assert snapshot['product_ids']==ids
 m.save_turn(u,sid,'assistant','five offers',{'offers':r['offers'],'comparison':snapshot})
 reloaded=Memory(m.path).turns(u,sid)[-1]['references'];assert all(o['price_band'] for o in reloaded['offers'])
 assert all(c['price_band'] for c in next(r for r in snapshot['rows'] if r['id']=='price')['cells'])

@pytest.mark.skipif(not os.getenv('TEST_CATALOG'),reason='Full catalogue required')
def test_real_credit_positive_and_price_evidence_scope():
 from catalog import Catalog
 c=Catalog(os.environ['TEST_CATALOG']);r=c.search('گوشی',{'installment_only':True},discovery=True)
 assert r['offers'] and all(o['in_person_credit'] for o in r['offers'])
 assert r['sampling_stats']['eligible_products']>=len(r['offers'])
 assert all(o['price_band']['source']==o['price_basis'] for o in r['offers'])

def test_repeated_search_after_filter_change_keeps_spread():
 # Re-running the same broad search (e.g. after a filter change) keeps the 1/2/2 spread.
 assert initial('هدفون','هدفون',{'query':'هدفون','offers':[{}]},'balanced',{})
