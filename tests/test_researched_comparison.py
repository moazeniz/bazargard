import os,sys,copy,json
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from catalog import Catalog
from memory import Memory
from agent import Agent

@pytest.mark.asyncio
async def test_researched_comparison_retains_original_table_and_is_immutable(tmp_path,monkeypatch):
    path=os.getenv('TEST_CATALOG')
    if not path:pytest.skip('TEST_CATALOG required')
    a=Agent(Catalog(path),Memory(tmp_path/'m.db'));u=a.mem.user();sid=a.mem.session(u)['id']
    offers=a.cat.search('لپ تاپ',limit=20)
    a.mem.results(u,sid,offers)
    ids=list(dict.fromkeys(o['product_id'] for o in offers['offers']))[:5]
    base=a.comparison(u,sid,ids);original=copy.deepcopy(base)
    async def review(uid,session,pid):return {'product_id':pid,'title':'test','research':{'sources':[],'status':'unavailable'},'catalogue':{'offers':[]},'grounded_analysis':{'claims':[]}}
    monkeypatch.setattr(a,'review',review)
    # Both the UI shortlist and hard preferences can change after comparison.
    a.mem.results(u,sid,{'offers':[]})
    a.mem.update(u,[{'key':'budget_max_toman','value':1}],sid=sid)
    result=await a.researched_comparison(u,sid,base['snapshot_id'],ids[:2])
    assert result['rows']==base['rows'] and result['charts']==base['charts']
    assert result['product_ids']==ids and len(result['buying_reviews'])==2
    assert result['base_snapshot_id']==base['snapshot_id'] and result['snapshot_id']!=base['snapshot_id']
    assert a.mem.comparison_snapshot(u,sid,base['snapshot_id'])==original
    reloaded=Memory(a.mem.path).comparison_snapshot(u,sid,result['snapshot_id'])
    assert reloaded==result
    other_sid=a.mem.session(u)['id'];other_u=a.mem.user();other_user_sid=a.mem.session(other_u)['id']
    for owner,session in [(u,other_sid),(other_u,other_user_sid)]:
        with pytest.raises(ValueError):await a.researched_comparison(owner,session,base['snapshot_id'],ids[:2])
    for invalid in [ids[:1],ids[:1]+['not-in-comparison']]:
        with pytest.raises(ValueError):await a.researched_comparison(u,sid,base['snapshot_id'],invalid)
    a.mem.clear(u,True)
    with a.mem.conn() as db:assert db.execute('select count(*) from comparison_snapshots where user_id=?',(u,)).fetchone()[0]==0

@pytest.mark.asyncio
async def test_research_keeps_removed_column_view_and_rejects_hidden_targets(tmp_path,monkeypatch):
    path=os.getenv('TEST_CATALOG')
    if not path:pytest.skip('TEST_CATALOG required')
    a=Agent(Catalog(path),Memory(tmp_path/'m.db'));u=a.mem.user();s=a.mem.session(u)['id'];found=a.cat.search('لپ تاپ',limit=20);a.mem.results(u,s,found)
    ids=list(dict.fromkeys(o['product_id'] for o in found['offers']))[:5];base=a.comparison(u,s,ids)
    async def review(*args):return {'product_id':args[-1],'research':{'sources':[]}}
    monkeypatch.setattr(a,'review',review)
    view=ids[1:]
    enriched=await a.researched_comparison(u,s,base['snapshot_id'],view[:2],view)
    assert enriched['view_product_ids']==view and enriched['rows']==base['rows']
    assert enriched['origin_snapshot_id']==base['snapshot_id']
    with pytest.raises(ValueError):await a.researched_comparison(u,s,base['snapshot_id'],ids[:2],view)
