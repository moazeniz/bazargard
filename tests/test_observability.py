"""Structured logs, retry/backoff/circuit breakers, durable job queue, error handling."""
import asyncio,importlib,json,os,sqlite3,sys,time
from pathlib import Path
import httpx,pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import obs,resilience
from jobqueue import JobQueue
from resilience import Breaker,Retryable,NonRetryable,CircuitOpen

def lines(capsys):
    return [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.startswith('{')]

@pytest.fixture(autouse=True)
def clean_breakers():
    resilience.BREAKERS.clear();yield;resilience.BREAKERS.clear()

# ---------------------------------------------------------------- logging
def test_log_is_one_json_object_with_standard_keys(capsys):
    rid,traceparent,tokens=obs.start_request({'x-request-id':'req-12345678','traceparent':'00-'+'a'*32+'-'+'b'*16+'-01'},user='user-cookie')
    try:obs.log('tool.call','info','search',tool='search_products',duration_ms=12)
    finally:obs.end_request(tokens)
    out=capsys.readouterr().out.strip().splitlines();record=json.loads(out[-1])
    for key in ('ts','level','app','env','release','event','request_id','trace_id','span_id','session','msg'):assert key in record
    assert record['request_id']=='req-12345678' and record['trace_id']=='a'*32 and record['span_id']!='b'*16
    assert record['session'].startswith('u:') and 'user-cookie' not in out[-1] and record['ts'].endswith('Z')
    assert traceparent.startswith('00-'+'a'*32+'-') and rid=='req-12345678'

def test_invalid_request_ids_are_replaced():
    rid,_,tokens=obs.start_request({'x-request-id':'bad id with spaces\n'});obs.end_request(tokens)
    assert len(rid)==32 and ' ' not in rid

def test_redaction_of_secrets_pii_and_long_text(monkeypatch,capsys):
    monkeypatch.setenv('BOXAPI_KEYS','sk-live-aaaaaaaaaaaa,second-key-bbbbbbbb');monkeypatch.setenv('ADMIN_TOKEN','admin-secret-123')
    obs.log('llm.error','warn','failed with sk-live-aaaaaaaaaaaa',authorization='Bearer abcdefghijkl',api_key='zzz',
            error='Bearer qwertyuiopas and second-key-bbbbbbbb for admin-secret-123, call 09121234567 or a.b@example.com',
            query='خرید '*200,nested={'token':'t0ps3cret','ok':1},key_index=2,tokens_cache_read=900,tokens_cache_write=12,output_tokens=5)
    text=capsys.readouterr().out;r=json.loads(text)
    for secret in ('sk-live-aaaaaaaaaaaa','abcdefghijkl','qwertyuiopas','second-key-bbbbbbbb','admin-secret-123','09121234567','a.b@example.com','t0ps3cret'):assert secret not in text
    assert r['authorization']=='[redacted]' and r['api_key']=='[redacted]' and r['nested']=={'token':'[redacted]','ok':1} and r['key_index']==2 and r['tokens_cache_read']==900 and r['tokens_cache_write']==12 and r['output_tokens']==5
    assert len(r['query'])<=215 and '…#' in r['query']

def test_print_lines_become_json(capsys):
    stream=obs.JsonLines(sys.stdout,'info')
    stream.write('harmonizer_failed TimeoutError\n{"already":"json"}\n')
    rows=lines(capsys)
    assert rows[0]['event']=='app.print' and rows[0]['level']=='warn' and rows[0]['source_event']=='harmonizer_failed'
    assert rows[1]=={'already':'json'}

def test_error_log_carries_stack(capsys):
    try:raise ValueError('boom')
    except ValueError as e:obs.error('app.crash',e,level='critical')
    r=lines(capsys)[-1];assert r['level']=='critical' and r['error_type']=='ValueError' and 'Traceback' in r['stack']

# ---------------------------------------------------------------- retry policy
def test_full_jitter_backoff_and_retry_after():
    assert resilience.backoff(1,0.5,20,rng=lambda:1.0)==0.5 and resilience.backoff(4,0.5,20,rng=lambda:1.0)==4.0
    assert resilience.backoff(10,0.5,20,rng=lambda:1.0)==20 and resilience.backoff(3,0.5,20,rng=lambda:0.0)==0.0
    assert resilience.backoff(1,0.5,20,after=7)==7 and resilience.backoff(1,0.5,20,after=99)==20
    r=httpx.Response(429,headers={'retry-after-ms':'1500'});assert resilience.response_retry_after(r)==1.5
    assert resilience.response_retry_after(httpx.Response(503,headers={'retry-after':'3'}))==3.0

def test_transient_responses_are_retried_honouring_retry_after(capsys):
    responses=[httpx.Response(503,headers={'retry-after':'2'}),httpx.Response(429),httpx.Response(200,json={'ok':1})];sleeps=[]
    async def fn():return responses.pop(0)
    async def nap(s):sleeps.append(s)
    r=asyncio.run(resilience.call('digikala',fn,attempts=3,sleep=nap))
    assert r.status_code==200 and sleeps[0]==2.0 and len(sleeps)==2
    calls=[x for x in lines(capsys) if x['event']=='external.call']
    assert [c['attempt'] for c in calls]==[1,2,3] and calls[0]['status']==503 and calls[0]['backoff_ms']==2000 and calls[-1]['circuit']=='closed'

def test_permanent_errors_are_not_retried():
    seen=[]
    async def bad():seen.append(1);return httpx.Response(404)
    assert asyncio.run(resilience.call('x',bad,attempts=3)).status_code==404 and len(seen)==1
    async def boom():seen.append(2);raise ValueError('bad input')
    with pytest.raises(ValueError):asyncio.run(resilience.call('x',boom,attempts=3))
    assert seen.count(2)==1

def test_transient_exceptions_retry_then_raise():
    seen=[]
    async def slow():seen.append(1);raise httpx.ReadTimeout('slow')
    async def nap(s):pass
    with pytest.raises(httpx.ReadTimeout):asyncio.run(resilience.call('osrm',slow,attempts=3,sleep=nap))
    assert len(seen)==3

def test_circuit_breaker_opens_half_opens_and_closes():
    now=[0.0];b=Breaker('up',threshold=3,cooldown=10,clock=lambda:now[0])
    for _ in range(3):assert b.allow();b.failure()
    assert b.state=='open' and not b.allow()
    now[0]=11;assert b.state=='half_open' and b.allow() and not b.allow()  # one probe only
    b.failure();assert b.state=='open'
    now[0]=22;assert b.allow();b.success();assert b.state=='closed' and b.allow()

def test_open_circuit_fails_fast(capsys):
    async def fail():raise httpx.ConnectError('down')
    async def nap(s):pass
    for _ in range(2):
        with pytest.raises(httpx.ConnectError):asyncio.run(resilience.call('gate',fail,attempts=2,breaker_threshold=4,sleep=nap))
    with pytest.raises(CircuitOpen):asyncio.run(resilience.call('gate',fail,attempts=2,breaker_threshold=4,sleep=nap))
    assert resilience.circuits()['gate']['state']=='open'
    assert any(x['event']=='circuit.open' for x in lines(capsys))

def test_sync_call_with_unavailable_results():
    results=[{'status':'unavailable'},{'status':'readable'}]
    out=resilience.call_sync('web:example.com',lambda:results.pop(0),attempts=2,sleep=lambda s:None,ok=lambda d:d['status']!='unavailable')
    assert out=={'status':'readable'}

def test_retryable_classification():
    class LLMError(Exception):
        def __init__(self,kind):self.kind=kind
    assert resilience.retryable(LLMError('rate_limited')) and resilience.retryable(LLMError('unavailable')) and not resilience.retryable(LLMError('request'))
    assert resilience.retryable(httpx.ConnectError('x')) and resilience.retryable(Retryable()) and not resilience.retryable(NonRetryable())
    assert resilience.retryable(sqlite3.OperationalError('database is locked')) and not resilience.retryable(KeyError('x'))

# ---------------------------------------------------------------- durable queue
def queue(tmp_path):return JobQueue(tmp_path/'q.db')

def test_job_succeeds_and_is_idempotent(tmp_path):
    async def go():
        q=queue(tmp_path);calls=[]
        async def run(p,job):calls.append(p);q.progress(job['id'],{'stage':'x'});return {'n':p['n']*2}
        q.register('double',run)
        a=q.enqueue('double',{'n':2},idem_key='k1');b=q.enqueue('double',{'n':2},idem_key='k1')
        assert a['id']==b['id']
        await asyncio.gather(*q.tasks.values())
        job=q.get(a['id']);assert job['state']=='succeeded' and job['result']=={'n':4} and job['progress'][0]['stage']=='x' and len(calls)==1
        assert q.enqueue('double',{'n':2},idem_key='k1')['id']==a['id']  # finished jobs are reused too
        assert q.enqueue('double',{'n':2},idem_key='k1',reuse_seconds=0)['id']!=a['id']
    asyncio.run(go())

def test_retryable_failure_retries_then_succeeds(tmp_path,capsys):
    async def go():
        q=queue(tmp_path);attempts=[];notes=[]
        async def flaky(p,job):
            attempts.append(job['attempts'])
            if len(attempts)<2:raise Retryable('provider busy')
            return 'ok'
        q.register('flaky',flaky,on_retry=lambda job,delay,e:notes.append(job['state']))
        job=q.enqueue('flaky',{});await asyncio.gather(*q.tasks.values())
        row=q.get(job['id']);assert row['state']=='retrying' and row['next_at']>time.time() and notes==['retrying']
        with q.conn() as db:db.execute('update job_queue set next_at=0 where id=?',(job['id'],))
        for jid in q.due():q.kick(jid)
        await asyncio.gather(*q.tasks.values())
        row=q.get(job['id']);assert row['state']=='succeeded' and row['attempts']==2 and attempts==[1,2]
        if q.worker:q.worker.cancel()
    asyncio.run(go())
    events=[x['event'] for x in lines(capsys)]
    assert 'job.retry' in events and 'job.succeeded' in events

def test_exhausted_or_permanent_failures_go_to_dead_letter_and_can_be_replayed(tmp_path):
    async def go():
        q=queue(tmp_path);dead=[]
        async def broken(p,job):
            if p.get('fixed'):return 'ok'
            raise NonRetryable('bad payload')
        q.register('broken',broken,on_dead=lambda job,e:dead.append(job['id']))
        job=q.enqueue('broken',{});await asyncio.gather(*q.tasks.values())
        assert q.get(job['id'])['state']=='dead' and dead==[job['id']] and q.metrics()['dead']==1
        assert [j['id'] for j in q.list('dead')]==[job['id']]
        async def always(p,job):raise Retryable('down')
        q.register('down',always,max_attempts=2)
        j2=q.enqueue('down',{});await asyncio.gather(*q.tasks.values())
        with q.conn() as db:db.execute('update job_queue set next_at=0 where id=?',(j2['id'],))
        for jid in q.due():q.kick(jid)
        await asyncio.gather(*q.tasks.values())
        assert q.get(j2['id'])['state']=='dead' and q.get(j2['id'])['attempts']==2
        q.register('broken',lambda p,job:asyncio.sleep(0,'replayed'))
        q.replay(job['id']);await asyncio.gather(*q.tasks.values())
        assert q.get(job['id'])['state']=='succeeded'
        assert q.discard(j2['id'])['state']=='discarded'
        with pytest.raises(ValueError):q.replay('missing')
        if q.worker:q.worker.cancel()
    asyncio.run(go())

def test_crash_recovery_requeues_running_jobs(tmp_path):
    q=queue(tmp_path);q.register('k',lambda p,j:None)
    with q.conn() as db:
        db.execute("insert into job_queue(id,kind,idem_key,state,payload,attempts,max_attempts,next_at,created,updated,progress,data,boot) values('j1','k','k:1','running','{}',1,3,0,0,0,'[]','{}','dead-process')")
    again=JobQueue(tmp_path/'q.db')
    row=again.get('j1');assert row['state']=='retrying' and row['attempts']==1 and 'restart' in row['last_error']

def test_graceful_shutdown_persists_running_jobs_without_spending_attempts(tmp_path):
    async def go():
        q=queue(tmp_path)
        async def slow(p,job):await asyncio.sleep(30)
        q.register('slow',slow)
        job=q.enqueue('slow',{});await asyncio.sleep(0.05)
        assert q.get(job['id'])['state']=='running'
        await q.shutdown(grace=0.05)
        row=q.get(job['id']);assert row['state']=='queued' and row['attempts']==0
    asyncio.run(go())

def test_timeouts_count_as_transient(tmp_path):
    async def go():
        q=queue(tmp_path)
        async def hang(p,job):await asyncio.sleep(5)
        q.register('hang',hang,timeout=0.05)
        job=q.enqueue('hang',{});await asyncio.gather(*q.tasks.values())
        assert q.get(job['id'])['state']=='retrying'
        if q.worker:q.worker.cancel()
    asyncio.run(go())

# ---------------------------------------------------------------- chat retry
def test_chat_turn_is_queued_when_provider_is_down_and_answered_later(tmp_path):
    from memory import Memory
    from runs import Runs
    async def go():
        mem=Memory(tmp_path/'m.db');u=mem.user();s=mem.session(u)['id'];q=JobQueue(tmp_path/'m.db')
        runs=Runs(mem,queue=q)
        async def outage():
            mem.save_turn(u,s,'user','هدفون');yield {'type':'status','message':'…'};yield {'type':'error','message':'x','retryable':True}
        async def later(uid,sid,mode):
            yield {'type':'done','reply':'پاسخ دیرتر'};mem.save_turn(uid,sid,'assistant','پاسخ دیرتر')
        runs.replay=later
        run=runs.start(u,s,'shopping',outage,retry={'mode':'simple'})
        await asyncio.gather(*runs.tasks.values())
        got=runs.get(u,run['run_id']);assert got['status']=='retry_queued' and got['events'][-1]['event']['type']=='retry_queued'
        with pytest.raises(ValueError):runs.start(u,s,'shopping',outage,retry={})  # no new turn while queued
        job=q.by_key('chat:'+run['run_id']);assert job['state']=='queued' and job['next_at']>time.time()+5  # first retry waits
        with q.conn() as db:db.execute('update job_queue set next_at=0')
        for jid in q.due():q.kick(jid)
        await asyncio.gather(*q.tasks.values())
        got=runs.get(u,run['run_id']);assert got['status']=='completed' and got['events'][-1]['event']['reply']=='پاسخ دیرتر'
        if q.worker:q.worker.cancel()
    asyncio.run(go())

# ---------------------------------------------------------------- HTTP layer
@pytest.fixture
def app_client(tmp_path,monkeypatch):
    monkeypatch.setenv('DATA_DIR',str(tmp_path))
    server=importlib.import_module('server')
    if not any(getattr(r,'path','')=='/api/test-crash' for r in server.app.routes):
        async def explode():raise RuntimeError('secret internal detail')
        server.app.add_api_route('/api/test-crash',explode,methods=['POST'])
    return server

def test_request_id_round_trip_and_crash_handler(app_client,capsys):
    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_client.app,raise_app_exceptions=False),base_url='http://t') as c:
            r=await c.get('/healthz',headers={'X-Request-ID':'abc-12345678'})
            assert r.status_code==200 and r.headers['x-request-id']=='abc-12345678' and r.headers['traceparent'].startswith('00-')
            assert (await c.get('/healthz')).headers['x-request-id']!='abc-12345678'
            crash=await c.post('/api/test-crash')
            body=crash.json();assert crash.status_code==500 and body['request_id']==crash.headers['x-request-id']
            assert 'secret internal detail' not in crash.text and 'Traceback' not in crash.text and 'خطا' in body['detail']
            missing=await c.get('/api/sessions/nope');assert missing.json()['request_id']==missing.headers['x-request-id']
            bad=await c.post('/api/web-alternatives',json={'session_id':1});assert bad.status_code in (401,422)
    asyncio.run(go())
    rows=lines(capsys)
    assert any(x['event']=='app.crash' and x['level']=='critical' and 'stack' in x for x in rows)
    reqs=[x for x in rows if x['event']=='http.request' and x.get('route')=='/api/test-crash']
    assert reqs and reqs[0]['status']==500 and reqs[0]['duration_ms']>=0 and reqs[0]['method']=='POST'

def test_validation_errors_are_persian_json(app_client):
    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_client.app),base_url='http://t') as c:
            r=await c.post('/api/chat-runs',json={'session_id':'any-session','message':''})
            assert r.status_code==422 and 'درخواست' in r.json()['detail'] and r.json()['request_id']
    asyncio.run(go())

def test_admin_endpoints_require_token_and_manage_dead_jobs(app_client,monkeypatch):
    server=app_client
    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://t') as c:
            monkeypatch.delenv('ADMIN_TOKEN',raising=False)
            assert (await c.get('/api/admin/jobs')).status_code==404
            monkeypatch.setenv('ADMIN_TOKEN','test-admin-token')
            assert (await c.get('/api/admin/jobs',headers={'Authorization':'Bearer wrong'})).status_code==401
            async def broken(p,job):raise NonRetryable('x')
            server.jobs.register('test_dead',broken)
            job=server.jobs.enqueue('test_dead',{});await asyncio.gather(*server.jobs.tasks.values())
            auth={'Authorization':'Bearer test-admin-token'}
            listed=(await c.get('/api/admin/jobs?state=dead&kind=test_dead',headers=auth)).json()['jobs']
            assert [j['id'] for j in listed]==[job['id']]
            assert (await c.get('/api/admin/jobs/'+job['id'],headers=auth)).json()['state']=='dead'
            assert (await c.post('/api/admin/jobs/'+job['id']+'/discard',headers=auth)).json()['state']=='discarded'
            assert (await c.post('/api/admin/jobs/'+job['id']+'/discard',headers=auth)).status_code==409
            metrics=(await c.get('/api/admin/queue',headers=auth)).json();assert 'depth' in metrics['metrics'] and 'circuits' in metrics
    asyncio.run(go())

def test_ready_reports_queue_and_provider(app_client):
    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_client.app),base_url='http://t') as c:
            d=(await c.get('/api/ready')).json()
            assert {'queue','provider','provider_status','database_ready','disk_free_mb','release'}<=set(d) and 'depth' in d['queue']
    asyncio.run(go())
