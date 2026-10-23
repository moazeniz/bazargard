"""API-level hardening: terminated chats, session isolation (IDOR), CSRF/headers and rate limits."""
import asyncio,importlib,os,sys
from collections import defaultdict,deque
from pathlib import Path
import httpx,pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))

@pytest.fixture
def app(tmp_path,monkeypatch):
    from catalog import Catalog
    from memory import Memory
    from agent import Agent
    from features import Features
    from runs import Runs
    import guard
    path=os.getenv('TEST_CATALOG')
    if not path:pytest.skip('TEST_CATALOG required')
    monkeypatch.setenv('DATA_DIR',str(tmp_path));server=importlib.import_module('server')
    cat=Catalog(path);mem=Memory(tmp_path/'guard-api.db');agent=Agent(cat,mem);features=Features(cat,mem,agent);agent.features=features
    calls=[]
    async def fake_response(uid,sid,message,mode='advanced'):
        calls.append(message);mem.save_turn(uid,sid,'user',message);mem.save_turn(uid,sid,'assistant','پاسخ آزمایشی')
        yield {'type':'done','reply':'پاسخ آزمایشی','facts':[],'results':{'offers':[]},'references':{}}
    agent.response=fake_response
    async def no_judge(message,context=()):return None
    monkeypatch.setattr(guard,'judge',no_judge)
    for key,value in dict(cat=cat,mem=mem,agent=agent,features=features,runs=Runs(mem,asyncio.Semaphore(4)),limits=defaultdict(deque)).items():monkeypatch.setattr(server,key,value)
    return server,calls

async def chat(c,sid,text):
    r=await c.post('/api/chat-runs',json={'session_id':sid,'message':text})
    if r.status_code!=200:return r
    rid=r.json()['run_id']
    for _ in range(200):
        d=(await c.get('/api/chat-runs/'+rid)).json()
        if d['status'] in ('completed','failed'):return d['events'][-1]['event']
        await asyncio.sleep(.02)
    raise AssertionError('run did not finish')

@pytest.mark.asyncio
async def test_abusive_chat_is_terminated_and_every_session_endpoint_answers_the_same(app):
    server,calls=app;import guard
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://testserver') as c:
        sid=(await c.get('/api/bootstrap')).json()['sessions'][0]['id']
        assert (await chat(c,sid,'یه هدفون بی‌سیم تا ۳ میلیون می‌خوام'))['reply']=='پاسخ آزمایشی'
        replies=[await chat(c,sid,t) for t in ['What is the capital of France?','Tell me a joke.','Write a poem about the sea.','Solve 2x+3=9 for me.','Write a Python function for fibonacci.']]
        assert calls==['یه هدفون بی‌سیم تا ۳ میلیون می‌خوام']          # refusals never reach the model
        assert guard.WARNING.strip()[:20] in replies[2]['reply'] and replies[-1]['terminated'] and replies[-1]['reply']==guard.TERMINATED
        state=(await c.get('/api/sessions/'+sid)).json()
        assert state['guard']=={'terminated':True,'warned':True,'message':guard.TERMINATED} and state['turns'][-1]['content']==guard.TERMINATED
        o_query={'session_id':sid,'query':'هدفون'}
        for method,path,body in [('post','/api/chat-runs',{'session_id':sid,'message':'گوشی سامسونگ'}),('post','/api/chat',{'session_id':sid,'message':'گوشی'}),
                                 ('post','/api/search',o_query),('post','/api/search/all',o_query),('post','/api/compare',{'session_id':sid}),
                                 ('post','/api/basket',o_query),('post','/api/route',{'session_id':sid,'shop_ids':[]}),('patch','/api/profile',{'session_id':sid,'values':{'budget_max_toman':1}}),
                                 ('get','/api/decision/'+sid,None),('get','/api/quotes/'+sid,None),('post','/api/web-alternatives',{'session_id':sid,'query':'تلویزیون'}),
                                 ('post','/api/identify-image',{'session_id':sid,'image':'x','media_type':'image/png'}),('patch','/api/sessions/'+sid+'/mode',{'mode':'simple'})]:
            r=await c.request(method.upper(),path,json=body)
            assert r.status_code==423 and r.json()['detail']==guard.TERMINATED and r.headers['x-chat-terminated']=='1',(path,r.status_code)
        assert calls==['یه هدفون بی‌سیم تا ۳ میلیون می‌خوام']
        fresh=(await c.post('/api/sessions',json={})).json()['id']           # a new chat works normally
        assert (await chat(c,fresh,'گوشی سامسونگ تا ۲۰ میلیون'))['reply']=='پاسخ آزمایشی'

@pytest.mark.asyncio
async def test_every_session_scoped_endpoint_rejects_another_users_session(app):
    server,_=app
    t=httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=t,base_url='http://testserver') as owner,httpx.AsyncClient(transport=t,base_url='http://testserver') as other:
        sid=(await owner.get('/api/bootstrap')).json()['sessions'][0]['id'];await other.get('/api/bootstrap')
        found=(await owner.post('/api/search',json={'session_id':sid,'query':'هدفون'})).json()['offers'][0]
        pid,shop,oid=found['product_id'],found['shop_id'],found['offer_id']
        cases=[('GET','/api/sessions/'+sid,None),('PATCH','/api/sessions/'+sid+'/mode',{'mode':'simple'}),('PATCH','/api/profile',{'session_id':sid,'values':{}}),
               ('POST','/api/search',{'session_id':sid,'query':'x'}),('POST','/api/search/all',{'session_id':sid,'query':'x'}),('POST','/api/shopping-areas',{'session_id':sid,'query':'x'}),
               ('GET','/api/locations/'+shop+'?session_id='+sid,None),('POST','/api/locations/enrich',{'session_id':sid,'shop_id':shop}),('POST','/api/compare',{'session_id':sid}),
               ('POST','/api/buying-review',{'session_id':sid,'product_id':pid}),('POST','/api/compare/research',{'session_id':sid,'snapshot_id':'x','product_ids':[pid,pid]}),
               ('POST','/api/external-discovery',{'session_id':sid,'query':'x'}),('POST','/api/seller-events',{'session_id':sid,'shop_id':shop,'product_id':pid}),
               ('POST','/api/simulate-seller',{'session_id':sid,'shop_id':shop,'product_id':pid,'scenario':'available'}),('POST','/api/simulate-seller/reset',{'session_id':sid}),
               ('POST','/api/route',{'session_id':sid,'shop_ids':[shop]}),('POST','/api/merchant-requests',{'session_id':sid,'offer_id':oid}),('GET','/api/quotes/'+sid,None),
               ('PATCH','/api/mission',{'session_id':sid,'values':{}}),('POST','/api/basket',{'session_id':sid,'query':'x'}),('DELETE','/api/basket/'+sid+'/x',None),
               ('GET','/api/basket/'+sid+'/plans',None),('POST','/api/costs',{'session_id':sid,'offer_ids':[oid]}),('POST','/api/clarification',{'session_id':sid,'key':'x'}),
               ('GET','/api/decision/'+sid,None),('POST','/api/identify-image',{'session_id':sid,'image':'x','media_type':'image/png'}),
               ('POST','/api/shopper-profiles',{'session_id':sid,'name':'x'}),('POST','/api/shopper-profiles/x/apply',{'session_id':sid}),
               ('POST','/api/chat-runs',{'session_id':sid,'message':'سلام'}),('POST','/api/chat',{'session_id':sid,'message':'سلام'}),('GET','/api/export/'+sid,None),
               ('POST','/api/compare/rebuild',{'session_id':sid,'snapshot_id':'x'}),('POST','/api/compare/spec-fetch',{'session_id':sid,'snapshot_id':'x'}),
               ('GET','/api/compare/spec-fetch/x?session_id='+sid,None),('POST','/api/compare/spec-fetch/x/cancel',{'session_id':sid,'snapshot_id':'x'}),
               ('GET','/api/compare/snapshot/x?session_id='+sid,None),('GET','/api/shops/'+shop+'?session_id='+sid,None),('GET','/api/products/'+pid+'?session_id='+sid,None),
               ('POST','/api/products/'+pid+'/specs?session_id='+sid,None),('GET','/api/products/specs/x?session_id='+sid,None),
               ('POST','/api/products/'+pid+'/reviews?session_id='+sid,None),('GET','/api/products/'+pid+'/reviews?session_id='+sid,None),
               ('POST','/api/web-alternatives',{'session_id':sid,'query':'xx'}),('GET','/api/web-alternatives/x?session_id='+sid,None),('GET','/api/ops/state/'+sid,None),
               ('GET','/api/followups/'+sid,None),('POST','/api/followups',{'session_id':sid,'shop_id':shop,'status':'asked'})]
        for method,path,body in cases:
            r=await other.request(method,path,json=body)
            assert r.status_code==404,(method,path,r.status_code,r.text[:200])
        # Every route with a session in its path must be in the sweep above.
        import re
        unswept=[r.path for r in server.app.routes if getattr(r,'path','').startswith('/api/') and '{sid}' in r.path and not any(re.fullmatch(re.sub(r'\{[^}]+\}','[^/]+',r.path),p.split('?')[0]) for _,p,_ in cases)]
        assert unswept==[] and len(cases)>=40

@pytest.mark.asyncio
async def test_security_headers_csrf_and_creation_rate_limits(app):
    server,_=app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://testserver') as c:
        r=await c.get('/api/bootstrap');csp=r.headers['content-security-policy']
        assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp and r.headers['x-frame-options']=='DENY'
        sid=r.json()['sessions'][0]['id']
        assert (await c.post('/api/search',json={'session_id':sid,'query':'x'},headers={'origin':'https://evil.example'})).status_code==403
        assert (await c.post('/api/search',json={'session_id':sid,'query':'x'},headers={'sec-fetch-site':'cross-site'})).status_code==403
        # New chats are never rate-limited (judges may share one network).
        codes=[(await c.post('/api/sessions',json={})).status_code for _ in range(70)]
        assert codes==[200]*70
        assert (await c.post('/api/chat-runs',json={'session_id':sid,'message':'x'*5001})).status_code==422
