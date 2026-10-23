import sys,time,copy,asyncio
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import pytest
from memory import Memory
from spec_jobs import SpecJobs
from comparison import build_comparison

@pytest.mark.asyncio
async def test_owner_bound_job_progress_literal_overlay_reply_reload_and_original(tmp_path,monkeypatch):
 import spec_jobs
 from agent import Agent
 p={'product_id':'p','persian_title':'Surface Book 1','category_name':'لپ تاپ و نوت بوک','specs':[]};q=dict(p,product_id='q')
 class Catalog:
  def product(self,pid):return p if pid=='p' else q
 cat=Catalog();m=Memory(tmp_path/'m.db');u=m.user();sid=m.session(u)['id'];a=Agent(cat,m)
 base=m.save_comparison(u,sid,build_comparison(cat,[],['p','q']));original=copy.deepcopy(base)
 async def catalogue(*args):return {'offers':[]}
 monkeypatch.setattr(spec_jobs.CatalogueFirst,'for_product',catalogue)
 def investigate(self,product,progress):
  progress({'stage':'source','model':'Surface Book 1','url':'https://support.microsoft.com/specs'})
  return {'sources':[{'url':'https://support.microsoft.com/specs','retrieved_at':time.time(),'kind':'manufacturer','basic_specs':[{'field':'bluetooth','value':'4.0','numeric':None,'status':'accepted','quote':'Bluetooth 4.0','unit':None,'model_match':'exact_family','variant_match':'shared_model'}]}]}
 monkeypatch.setattr(spec_jobs.SpecFetcher,'investigate',investigate)
 jobs=SpecJobs(m,a);start=jobs.start(u,sid,base['snapshot_id'],['p','q']);await jobs.tasks[start['id']]
 result=jobs.owned(u,sid,start['id']);assert result['status']=='completed' and result['progress']
 data=result['result'];assert next(r for r in data['rows'] if r['id']=='bluetooth')['cells'][0]['status']=='web_completed'
 assert m.comparison_snapshot(u,sid,base['snapshot_id'])==original
 assert Memory(m.path).comparison_snapshot(u,sid,data['snapshot_id'])==data
 other=m.user();other_sid=m.session(other)['id']
 with pytest.raises(ValueError):jobs.owned(other,other_sid,start['id'])
 m.clear(u,True)
 with m.conn() as db:assert db.execute('select count(*) from spec_fetch_jobs').fetchone()[0]==0

@pytest.mark.asyncio
async def test_auto_dedup_origin_view_owner_and_cancel_retry(tmp_path,monkeypatch):
 import spec_jobs,threading
 from agent import Agent
 p={'product_id':'p','persian_title':'Surface Book 1','category_name':'لپ تاپ','specs':[]}
 class Cat:
  def product(self,pid):return dict(p,product_id=pid)
 m=Memory(tmp_path/'m.db');u=m.user();sid=m.session(u)['id'];a=Agent(Cat(),m)
 base=m.save_comparison(u,sid,build_comparison(a.cat,[],['p','q','r']))
 async def catalogue(*args):return {'offers':[]}
 monkeypatch.setattr(spec_jobs.CatalogueFirst,'for_product',catalogue)
 monkeypatch.setattr(spec_jobs.SpecFetcher,'investigate',lambda *args:{'sources':[]})
 jobs=SpecJobs(m,a);first=jobs.automatic(u,sid,base);job=first['spec_job'];assert first['auto_completion']
 assert jobs.start(u,sid,base['snapshot_id'],None)['id']==job['id']
 await jobs.tasks[job['id']];done=jobs.owned(u,sid,job['id']);assert done['status']=='completed'
 assert not jobs.automatic(u,sid,done['result']).get('auto_completion')
 assert jobs.start(u,sid,done['result']['snapshot_id'],None)['id']==job['id']
 assert jobs.start(u,sid,base['snapshot_id'],['p','q'])['id']!=job['id']
 subset=jobs.start(u,sid,base['snapshot_id'],['p','q']);jobs.cancel(u,sid,subset['id']);await asyncio.sleep(0)
 assert jobs.owned(u,sid,subset['id'])['status']=='cancelled'
 retry=jobs.start(u,sid,base['snapshot_id'],['p','q'],True);assert retry['id']!=subset['id'];await jobs.tasks[retry['id']]
 assert m.comparison_snapshot(u,sid,base['snapshot_id'])==base
 assert all(c.get('completion_state')=='unavailable' for r in done['result']['rows'] if r['id']=='cpu' for c in r['cells'])
 other=m.user();other_sid=m.session(other)['id']
 with pytest.raises(ValueError):jobs.start(other,other_sid,base['snapshot_id'],None)
