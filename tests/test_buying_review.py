import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from buying_review import build_review
from public_research import identity


def test_card_separates_conflicts_missing_reviews_and_analysis():
    p={'product_id':'p','persian_title':'لپ تاپ E410KA','model':'e 410','specs':[
       {'key':'ظرفیت حافظه رم','value':'4 GB'}, {'key':'ظرفیت حافظه رم','value':'8 GB'},
       {'key':'تعداد نظرات','value':'234'}]}
    r={'model':'E410KA','sources':[], 'status':'unavailable'}
    card=build_review(p,r,{'offers':[]},{'needs':'برنامه‌نویسی','installment_only':True})
    assert card['conflicts']==['حافظه رم'] and card['missing_facts']
    assert card['specialist_reviews']==[] and card['community_evidence']==[]
    assert card['aggregate_reviews']['review_text_count']==0
    assert '234' not in ' '.join(card['assistant_analysis'])
    assert any('اعتبار' in x for x in card['seller_questions'])
    assert any('تعارض' in x for x in card['seller_questions'])


def test_mismatched_specialist_and_readable_community_are_separate():
    p={'product_id':'p','persian_title':'هدفون WH-1000XM4','model':'WH-1000XM4','specs':[]}
    wrong={'url':'https://www.soundguys.com/review','kind':'specialist','excerpts':[], 'match':identity(p,'WH-1000XM5')}
    community={'url':'https://www.reddit.com/comments/test','kind':'community','excerpts':['WH-1000XM4 owner text'], 'match':identity(p,'WH-1000XM4')}
    card=build_review(p,{'model':'WH-1000XM4','sources':[wrong,community]}, {'offers':[]}, {})
    assert card['specialist_reviews']==[] and card['unavailable_sources']==[wrong]
    assert card['community_evidence']==[community]
    assert 'تأییدشده نیست' in card['customer_notice']



import os
import importlib
import httpx
import pytest

@pytest.mark.asyncio
async def test_review_api_owner_isolation_reload_and_hard_budget(tmp_path, monkeypatch):
    from catalog import Catalog
    from memory import Memory
    from agent import Agent
    path=os.getenv('TEST_CATALOG')
    if not path:pytest.skip('TEST_CATALOG required')
    monkeypatch.setenv('DATA_DIR',str(tmp_path))
    server=importlib.import_module('server')
    cat=Catalog(path);mem=Memory(tmp_path/'isolated.db');agent=Agent(cat,mem)
    for k,v in dict(cat=cat,mem=mem,agent=agent).items():monkeypatch.setattr(server,k,v)
    def unavailable(self,product):return {'model':product['model'],'sources':[],'status':'unavailable','cached':False,'notice':'blocked','discovery':{'status':'blocked'}}
    monkeypatch.setattr('agent.PublicResearch.investigate',unavailable)
    transport=httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport,base_url='http://test') as buyer,httpx.AsyncClient(transport=transport,base_url='http://test') as other:
        sid=(await buyer.get('/api/bootstrap')).json()['sessions'][0]['id']
        await other.get('/api/bootstrap')
        pid=cat.search('هدفون')['offers'][0]['product_id']
        body={'session_id':sid,'product_id':pid}
        assert (await other.post('/api/buying-review',json=body)).status_code==404
        await buyer.patch('/api/profile',json={'session_id':sid,'values':{'budget_max_toman':1}})
        response=await buyer.post('/api/buying-review',json=body)
        assert response.status_code==200,response.text
        card=response.json()
        assert not card['catalogue']['offers'] and card['research']['status']=='unavailable'
        with mem.conn() as db:owner=db.execute('select user_id from sessions where id=?',(sid,)).fetchone()[0]
        persisted=Memory(mem.path).turns(owner,sid)[-1]['references']['buying_reviews'][0]
        assert persisted==card
        assert (await buyer.post('/api/buying-review',json=dict(body,product_id='bad'))).status_code==422


def test_external_conflict_is_visible_without_overwriting_catalogue():
    p={'product_id':'p','persian_title':'گوشی A55','model':'a55','category_name':'گوشی موبایل','specs':[{'key':'سایز صفحه نمایش (اینچ)','value':'6.1 inch'}]}
    external={'field':'screen','label':'اندازه نمایشگر','values':['6.6'],'unit':'inch','url':'https://example.com/review','quote':'6.6 inch screen','status':'source_statement_configuration_unverified'}
    source={'kind':'specialist','excerpts':['A55 has a 6.6 inch screen.'],'external_facts':[external]}
    card=build_review(p,{'model':'a55','sources':[source]},{'offers':[]},{})
    assert card['external_conflicts'][0]['recorded']=='6.1 inch'
    assert next(f for f in card['recorded_facts'] if f['label']=='اندازه نمایشگر')['numeric']==6.1
    assert card['external_facts'][0]['values']==['6.6']


def test_used_listing_is_not_validated_by_editorial_review():
    p={'product_id':'p','persian_title':'گوشی استوک A55','model':'a55','specs':[]}
    card=build_review(p,{'model':'a55','sources':[]},{'offers':[]},{})
    assert card['condition_evidence'][0]['wording']=='استوک'
    assert any('سلامت' in s for s in card['assistant_analysis'])
    assert any('سریال' in s for s in card['seller_questions'])
