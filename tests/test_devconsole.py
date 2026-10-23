"""Developer console: master switch (404 unless DEV_CONSOLE=1), own-chats-only access, redaction, trace assembly, retention."""
import asyncio,importlib,json,os,sqlite3,sys,time
from collections import defaultdict,deque
from pathlib import Path
import httpx,pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import obs,devtrace

ADMIN='admin-token-for-tests-123456'
GATE='gate-token-abcdef123456'
KEY='sk-box-live-aaaaaaaaaaaa'

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
    for k,v in (('ADMIN_TOKEN',ADMIN),('RELAY_TOKEN',GATE),('BOXAPI_KEYS',KEY),('DEV_CONSOLE','1')):monkeypatch.setenv(k,v)
    cat=Catalog(path);mem=Memory(tmp_path/'dev.db');agent=Agent(cat,mem);features=Features(cat,mem,agent);agent.features=features
    async def fake_response(uid,sid,message,mode='advanced'):
        # The events a real turn emits, including things that must never reach a trace.
        mem.save_turn(uid,sid,'user',message)
        obs.log('llm.call','info','agent_advanced',provider='anthropic',model='claude-sonnet-5-5',purpose='agent_advanced',status=200,duration_ms=900,tokens_in=1000,tokens_out=200,tokens_cache_read=4000,tokens_cache_write=0,stop='tool_use',
                system='SYSTEM PROMPT SECRET RULES',authorization='Bearer '+GATE)
        result={'offers':[{'product_id':'p1','offer_id':'o1','persian_title':'هدفون بلوتوث','shop_name':'فروشگاه','effective_price_toman':1200000,'contacts':{'phone':'09121234567'}}],'matching_offers':7,
                'query':'هدفون بلوتوث','filters':{'budget_max_toman':2000000,'brand':None},'corrected_terms':{'هدفن':'هدفون'},'relaxed_terms':[]}
        obs.log('tool.call','info','search_products',tool='search_products',duration_ms=120,offers=1,**devtrace.tool_fields('search_products',{'query':'هدفون بلوتوث','note':'call me 09121234567 key '+KEY},result,'جست‌وجوی محصول'))
        obs.log('external.call','info','ok',upstream='osrm',attempt=1,duration_ms=40)
        obs.log('llm.error','warn','agent',provider='boxapi',status=401,error='bad key '+KEY+' / gate '+GATE+' / admin '+ADMIN)
        obs.log('http.request','info','GET /x',route='/x')  # not an AI event: never stored
        mem.save_turn(uid,sid,'assistant','پاسخ آزمایشی')
        yield {'type':'done','reply':'پاسخ آزمایشی','facts':[],'results':{'offers':[]},'references':{}}
    agent.response=fake_response
    async def no_judge(message,context=()):return None
    monkeypatch.setattr(guard,'judge',no_judge)
    traces=devtrace.Traces(tmp_path/'dev.db')
    for key,value in dict(cat=cat,mem=mem,agent=agent,features=features,runs=Runs(mem,asyncio.Semaphore(4)),limits=defaultdict(deque),traces=traces).items():monkeypatch.setattr(server,key,value)
    monkeypatch.setattr(obs,'SINKS',[traces.capture])
    return server,traces,tmp_path/'dev.db'

def client(server):return httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://dev-test')

async def chat(c,sid,text='هدفون بلوتوث زیر دو میلیون'):
    rid=(await c.post('/api/chat-runs',json={'session_id':sid,'message':text})).json()['run_id']
    for _ in range(200):
        if (await c.get('/api/chat-runs/'+rid)).json()['status'] in ('completed','failed'):return rid
        await asyncio.sleep(.02)
    raise AssertionError('run did not finish')

async def start(c):return (await c.get('/api/bootstrap')).json()['sessions'][0]['id']

@pytest.mark.asyncio
async def test_disabled_console_is_invisible_and_stores_nothing(app,monkeypatch):
    server,traces,db=app;monkeypatch.setenv('DEV_CONSOLE','0')
    async with client(server) as c:
        sid=await start(c);rid=await chat(c,sid)
        for method,path,body in (('GET','/api/dev/status',None),('GET','/api/dev/traces?session_id='+sid,None),('GET','/api/dev/runs/'+rid,None)):
            r=await c.request(method,path,json=body);assert r.status_code==404 and r.json()['detail']=='Not Found'
    with sqlite3.connect(db) as s:assert s.execute('select count(*) from dev_trace_events').fetchone()[0]==0
    assert devtrace.tool_fields('search_products',{'query':'x'},{'offers':[]})=={}  # production logs unchanged

@pytest.mark.asyncio
async def test_master_switch_alone_enables_it_without_any_login(app,monkeypatch):
    server,_,_=app;monkeypatch.delenv('ADMIN_TOKEN')
    async with client(server) as c:
        sid=await start(c)
        r=await c.get('/api/dev/status');assert r.status_code==200 and r.json()=={'enabled':True,'retention_hours':24.0} and 'set-cookie' not in r.headers
        assert (await c.get('/api/dev/traces',params={'session_id':sid})).json()['runs']==[]
        assert sorted(r.path for r in server.app.routes if getattr(r,'path','').startswith('/api/dev'))==['/api/dev/runs/{rid}','/api/dev/status','/api/dev/traces']  # no login/logout

@pytest.mark.asyncio
async def test_trace_assembly(app):
    server,_,_=app
    async with client(server) as c:
        sid=await start(c)
        rid=await chat(c,sid)
        d=(await c.get('/api/dev/traces',params={'session_id':sid})).json()
        run=d['runs'][0];assert run['run_id']==rid and run['status']=='completed' and run['message']=='هدفون بلوتوث زیر دو میلیون' and run['mode'] in ('simple','advanced')
        names=[e['event'] for e in run['events']]
        assert names[0]=='run.start' and {'guard.pass','llm.call','tool.call','external.call','llm.error'}<=set(names) and 'http.request' not in names
        guard=next(e for e in run['events'] if e['event']=='guard.pass');assert guard['action']=='allow' and guard['sanitised'] is False and 'score' in guard
        call=next(e for e in run['events'] if e['event']=='llm.call')
        assert call['provider']=='anthropic' and call['tokens_cache_read']==4000 and call['stop']=='tool_use' and call['kind']=='model'
        assert call['cost_estimate']==pytest.approx((1000*2+200*10+4000*0.2)/1e6)
        tool=next(e for e in run['events'] if e['event']=='tool.call')
        assert tool['label']=='جست‌وجوی محصول' and tool['args']['query']=='هدفون بلوتوث' and tool['result']['hits']==1 and tool['result']['matching_offers']==7
        assert tool['result']['top'][0]=={'product_id':'p1','offer_id':'o1','persian_title':'هدفون بلوتوث','shop_name':'فروشگاه','effective_price_toman':1200000}
        assert tool['result']['corrected_terms']=={'هدفن':'هدفون'} and tool['result']['filters']=={'budget_max_toman':2000000}
        t=run['totals'];assert t['model_calls']==1 and t['model_errors']==0 and t['tool_calls']==1 and t['model_ms']==900 and t['tool_ms']==120 and t['tokens_in']==1000
        assert d['totals']['runs']==1 and d['totals']['tokens_out']==200 and d['retention_hours']==24.0
        one=(await c.get('/api/dev/runs/'+rid)).json();assert one['run_id']==rid and len(one['events'])==len(run['events'])

@pytest.mark.asyncio
async def test_traces_never_contain_secrets_prompt_or_contacts(app):
    server,_,db=app
    async with client(server) as c:
        sid=await start(c);await chat(c,sid)
        text=(await c.get('/api/dev/traces',params={'session_id':sid})).text
    with sqlite3.connect(db) as s:stored=' '.join(r[0] for r in s.execute('select data from dev_trace_events'))
    for blob in (text,stored):
        for secret in (ADMIN,GATE,KEY,'SYSTEM PROMPT','09121234567'):assert secret not in blob
        assert '"system"' not in blob and '"session"' not in blob
    assert '[redacted]' in stored and '[phone]' in stored

@pytest.mark.asyncio
async def test_each_browser_sees_only_its_own_chats(app):
    server,_,_=app
    async with client(server) as a,client(server) as b,client(server) as anonymous:
        sid=await start(a);await b.get('/api/bootstrap');rid=await chat(a,sid)
        assert (await b.get('/api/dev/traces',params={'session_id':sid})).status_code==404
        assert (await b.get('/api/dev/runs/'+rid)).status_code==404
        assert (await anonymous.get('/api/dev/runs/'+rid)).status_code==401  # no user cookie at all
        assert (await a.get('/api/dev/runs/'+rid)).status_code==200

def test_retention_prunes_old_rows_and_runs(tmp_path,monkeypatch):
    monkeypatch.setenv('DEV_CONSOLE','1');monkeypatch.setenv('ADMIN_TOKEN',ADMIN);monkeypatch.setenv('DEV_TRACE_HOURS','24')
    from memory import Memory
    from runs import Runs
    mem=Memory(tmp_path/'m.db');Runs(mem);tr=devtrace.Traces(tmp_path/'m.db');old=time.time()-25*3600
    with sqlite3.connect(tmp_path/'m.db') as s:
        s.execute("insert into assistant_runs values('old','u','s','shopping','completed',?,?)",(old,old))
        s.execute("insert into assistant_runs values('new','u','s','shopping','completed',?,?)",(time.time()-5,time.time()))
        s.execute("insert into dev_trace_events values('old',?,'llm.call','{}')",(old,))
        s.execute("insert into dev_trace_events values('new',?,'llm.call','{}')",(time.time(),))
    tr.prune()
    with sqlite3.connect(tmp_path/'m.db') as s:assert [r[0] for r in s.execute('select run_id from dev_trace_events')]==['new']
    assert [r['run_id'] for r in tr.session('u','s')['runs']]==['new']
    with pytest.raises(KeyError):tr.run('u','old')

def test_assembly_splits_model_tool_and_other_time():
    run={'id':'r','status':'completed','kind':'shopping','created':100.0,'updated':110.0}
    row=lambda at,event,**d:{'at':at,'event':event,'data':json.dumps(d)}
    rows=[row(100.0,'run.start',message='سلام',mode='simple'),
          row(102.0,'llm.call',provider='anthropic',model='claude-sonnet-5-5',duration_ms=2000,status=200,tokens_in=10,tokens_out=5),
          row(104.5,'llm.call',provider='boxapi',model='openai/gpt-6.1-sol',duration_ms=1000,status=200,cost=0.003,purpose='review_web'),  # inside the tool below
          row(105.0,'tool.call',tool='review_product',duration_ms=3000),
          row(105.1,'llm.fallback',provider='anthropic',fallback_provider='boxapi',error_type='unavailable'),
          row(108.0,'llm.call',provider='anthropic',model='claude-sonnet-5-5',duration_ms=2500,status=429),
          row(108.5,'job.retry',queue='chat_retry',attempt=1)]
    a=devtrace.assemble(run,rows);t=a['totals']
    assert [e['start_ms'] for e in a['events']]==[0,0,2000,3500,5100,5500,8500] and [e['t_ms'] for e in a['events']]==[0,2000,5000,4500,5100,8000,8500]  # ordered by start
    assert t['wall_ms']==10000 and t['model_ms']==4500 and t['nested_model_ms']==1000 and t['tool_ms']==3000 and t['other_ms']==2500
    assert t['model_calls']==3 and t['model_errors']==1 and t['fallbacks']==1 and t['cost_reported']==0.003 and t['cost_estimate']>0
    assert [e['kind'] for e in a['events']]==['run','model','tool','model','model','model','queue'] and a['message']=='سلام' and a['mode']=='simple'
    assert a['events'][3]['nested'] and not a['events'][1]['nested'] and a['events'][3]['cost_estimate'] is None

def test_stop_reason_and_summary_shapes():
    assert devtrace.stop_reason({'stop_reason':'end_turn'})=='end_turn'
    assert devtrace.stop_reason({'status':'completed','output':[{'type':'function_call'}]})=='tool_use'
    assert devtrace.stop_reason({'status':'incomplete','incomplete_details':{'reason':'max_output_tokens'}})=='max_output_tokens'
    assert devtrace.stop_reason({'status':'completed','output':[{'type':'message'}]})=='end_turn'
    s=devtrace.summarise({'error':'Tool budget reached'});assert s['error']=='Tool budget reached'
    assert devtrace.summarise(['x'])=={'type':'list'}

@pytest.mark.asyncio
async def test_run_id_follows_the_turn_into_its_task(tmp_path,monkeypatch):
    monkeypatch.setenv('DEV_CONSOLE','1');monkeypatch.setenv('ADMIN_TOKEN',ADMIN)
    from memory import Memory
    from runs import Runs
    mem=Memory(tmp_path/'r.db');runs=Runs(mem);tr=devtrace.Traces(tmp_path/'r.db');monkeypatch.setattr(obs,'SINKS',[tr.capture])
    uid=mem.user();sid=mem.session(uid)['id']
    async def factory():
        obs.log('llm.call','info','x',provider='anthropic',model='claude-haiku-5-5',status=200,duration_ms=5)
        await asyncio.sleep(0);yield {'type':'done','reply':'ok'}
    rid=runs.start(uid,sid,'shopping',factory)['run_id']
    assert obs.run_id.get() is None  # the request context is not polluted
    await asyncio.gather(*runs.tasks.values())
    obs.log('llm.call','info','outside a run',provider='anthropic',status=200)
    with sqlite3.connect(tmp_path/'r.db') as s:assert s.execute('select run_id,event from dev_trace_events').fetchall()==[(rid,'llm.call')]
