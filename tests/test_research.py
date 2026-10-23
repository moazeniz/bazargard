import os,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from catalog import Catalog
from memory import Memory
from agent import Agent
from features import Features
from research import Research,expansions

@pytest.mark.asyncio
async def test_multihop_model_to_sellers_verification_and_hard_filter_preservation(tmp_path):
 path=os.getenv('TEST_CATALOG')
 if not path:pytest.skip('TEST_CATALOG required')
 cat=Catalog(path);mem=Memory(tmp_path/'memory.db');agent=Agent(cat,mem);agent.features=Features(cat,mem,agent);u=mem.user();sid=mem.session(u)['id'];mem.update(u,[{'key':'budget_max_toman','value':3000000}],sid=sid);r=Research(agent,u,sid)
 plan=r.planning([{'id':'model','question':'کدام مدل مناسب است؟'},{'id':'seller','question':'کدام فروشنده؟','depends_on':['model']}]);assert plan['tasks'][1]['depends_on']==['model']
 first=await r.search(['هدفون M19','هندزفری M19'],'مدل‌های دارای مشخصات',True)
 assert first['offers'] and all(o['effective_price_toman']<=3000000 for o in first['offers'])
 pid=first['offers'][0]['product_id'];sellers=await r.sellers([pid],'فروشنده‌های همان مدل')
 assert sellers['products'][0]['product_id']==pid and all(o['product_id']==pid for o in sellers['offers'])
 assert r.verify(sellers['offers'][:3])['all_eligible']
 used=r.query_count;await r.search(['هدفون M19','هندزفری M19'],'بررسی تکراری');assert r.query_count==used
 mem.update(u,[{'key':'budget_max_toman','value':0}],sid=sid)
 assert not r.verify(sellers['offers'][:3])['all_eligible']
 no=await r.search(['هدفون M19'],'بودجه اصلاح‌شده',True);assert not no['offers'] and no['filters']['budget_max_toman']==0
 assert all(t['filters'].get('budget_max_toman') in (0,3000000) for t in r.trace)


def test_query_expansion_preserves_model_and_dependency_validation():
 assert expansions('هدفون M19') and all('m19' in q for q in expansions('هدفون M19'))
 assert not expansions('نام مدل اختصاصی XYZ9')
 with pytest.raises(ValueError):Research(None,'u','s').planning([{'id':'1','question':'x','depends_on':['future']}])
