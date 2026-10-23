import os,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from catalog import Catalog
from memory import Memory
from agent import Agent
from features import Features
from seller_simulation import simulate

@pytest.fixture
def setup(tmp_path):
    path=os.getenv('TEST_CATALOG')
    if not path:pytest.skip('TEST_CATALOG required')
    a=Agent(Catalog(path),Memory(tmp_path/'m.db'));a.features=Features(a.cat,a.mem,a)
    u=a.mem.user();sid=a.mem.session(u)['id']
    return a,u,sid

@pytest.mark.asyncio
@pytest.mark.parametrize('scenario',['unavailable','no_answer','closed','variant_mismatch','price_changed'])
async def test_failure_reapplies_constraints_and_invalidates_visit(setup,scenario):
    a,u,s=setup;o=a.cat.search('هدفون',limit=1)['offers'][0]
    a.mem.update(u,[{'key':'budget_max_toman','value':o['price_toman']*1.1}],sid=s)
    data=simulate(a,u,s,o['shop_id'],o['product_id'],scenario)
    assert data['source']=='simulated' and data['dialogue'][-1]['role']=='simulated_seller'
    assert data['visit_update']['invalidated_shop_ids']==[o['shop_id']]
    assert all(not(x['product_id']==o['product_id'] and x['shop_id']==o['shop_id']) for x in data['results']['offers'])
    assert all(x['effective_price_toman']<=o['price_toman']*1.1 for x in data['results']['offers'])
    route=await a.features.plan(u,s,[o['shop_id']])
    assert route['stops']==[] and route['skipped_stops'] and route['feasible'] is False
    other=a.mem.session(u)['id']
    assert a.overlay(u,other,{'offers':[o]})['offers'][0]['price_basis']=='dataset'


def test_credit_failure_respects_cash_vs_credit_and_reset_is_isolated(setup):
    a,u,s=setup;o=a.cat.search('گوشی',{'installment_only':True},limit=1)['offers'][0]
    a.mem.update(u,[{'key':'installment_only','value':True}],sid=s)
    data=simulate(a,u,s,o['shop_id'],o['product_id'],'credit_declined')
    assert all(x['in_person_credit'] for x in data['results']['offers'])
    assert o['shop_id'] in data['visit_update']['invalidated_shop_ids']
    a.mem.update(u,[{'key':'installment_only','value':False}],sid=s)
    assert a.overlay(u,s,{'offers':[o]})['offers']
    assert a.overlay(u,s,{'offers':[o]})['offers'][0]['in_person_credit']==[]


def test_positive_simulation_is_labelled_and_uses_review_questions(setup):
    a,u,s=setup;o=a.cat.search('هدفون',limit=1)['offers'][0]
    a.mem.save_turn(u,s,'assistant','review',{'buying_reviews':[{'product_id':o['product_id'],'seller_questions':['آیا مشکل دکمه را می‌توان حضوری تست کرد؟']}]})
    data=simulate(a,u,s,o['shop_id'],o['product_id'],'available')
    current=a.overlay(u,s,{'offers':[o]})['offers'][0]
    assert current['price_basis']=='simulated' and current['availability']=='simulated_available'
    assert 'آیا مشکل دکمه را می‌توان حضوری تست کرد؟' in data['seller_questions']
    assert current['price_toman']==o['price_toman']
    with pytest.raises(ValueError):simulate(a,u,s,o['shop_id'],o['product_id'],'not-real')
