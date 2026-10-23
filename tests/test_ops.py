import asyncio,json,os,sys
from pathlib import Path
import httpx,pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from memory import Memory
from catalog import Catalog
from ops import Operations,Liara,ApprovedAction,clean

@pytest.fixture
def operations(tmp_path):
 mem=Memory(tmp_path/'memory.db');catalog=Catalog(os.getenv('TEST_CATALOG',str(tmp_path/'catalog.db')));o=Operations(catalog,mem,Path(__file__).resolve().parents[1]);u=mem.user();s=o.session(u)['id']
 yield o,u,s
 o.labs.close()

async def run(o,u,s,kind):
 a=o.propose(u,s,kind);return await o.approve(u,s,a['id'],a['nonce'])

@pytest.mark.asyncio
async def test_real_deployment_500_evidence_approved_fix_slow_and_rollback(operations):
 o,u,s=operations
 plan=o.propose(u,s,'deploy_v1');assert (await o.observe(u,s))['snapshot']['state']=='not_deployed'
 deployed=await o.approve(u,s,plan['id'],plan['nonce']);assert deployed['status']=='succeeded' and deployed['result']['after']['http_status']==200
 assert deployed['result']['after']['rss_mb']>0 and deployed['result']['after']['instance_ram_limit_mb'] is None
 with pytest.raises(ValueError):await o.approve(u,s,plan['id'],plan['nonce'])
 fault=await run(o,u,s,'fault_500');assert fault['result']['after']['http_status']==500
 evidence=await o.observe(u,s);assert any('KeyError' in l for l in evidence['logs']);assert o.diagnose(evidence)['recommended_action']=='set_title'
 proposed=o.propose(u,s,'set_title');assert (await o.observe(u,s))['snapshot']['http_status']==500 # proposal does not change the service
 fixed=await o.approve(u,s,proposed['id'],proposed['nonce']);assert fixed['result']['verified'] and fixed['result']['after']['http_status']==200
 await run(o,u,s,'fault_slow');evidence=await o.observe(u,s);assert evidence['snapshot']['latency_ms']>=650 and o.diagnose(evidence)['recommended_action']=='clear_delay'
 assert (await run(o,u,s,'clear_delay'))['result']['after']['latency_ms']<500
 failed_release=await run(o,u,s,'deploy_broken');assert failed_release['status']=='rolled_back' and failed_release['result']['after']['http_status']==200
 assert any(h['event']=='health_check_failed' for h in o.history(u,s))

@pytest.mark.asyncio
async def test_approval_rejection_expiry_owner_and_state_preconditions(operations):
 o,u,s=operations;other=o.mem.user();foreign=o.session(other)['id'];a=o.propose(u,s,'deploy_v1')
 with pytest.raises(KeyError):await o.approve(other,foreign,a['id'],a['nonce'])
 assert (await o.approve(u,s,a['id'],a['nonce'],False))['status']=='rejected'
 assert (await o.observe(u,s))['snapshot']['state']=='not_deployed'
 a=o.propose(u,s,'deploy_v1');o.labs.write(u,s,{'deployed':False,'version':'changed'})
 with pytest.raises(ValueError,match='وضعیت'):await o.approve(u,s,a['id'],a['nonce'])
 a=o.propose(u,s,'deploy_v1')
 with o.mem.conn() as c:c.execute('update ops_actions set expires=0 where id=?',(a['id'],))
 with pytest.raises(ValueError):await o.approve(u,s,a['id'],a['nonce'])
 assert not o.mem.sessions(u) and o.mem.sessions(u,'ops')
 with pytest.raises(KeyError):o.session(u,o.mem.session(u)['id'])

@pytest.mark.asyncio
async def test_concurrent_approval_cannot_execute_twice(operations):
 o,u,s=operations;a=o.propose(u,s,'deploy_v1');results=await asyncio.gather(o.approve(u,s,a['id'],a['nonce']),o.approve(u,s,a['id'],a['nonce']),return_exceptions=True)
 assert sum(isinstance(x,dict) and x['status']=='succeeded' for x in results)==1
 assert sum(isinstance(x,ValueError) for x in results)==1

@pytest.mark.asyncio
async def test_liara_official_api_contract_and_mutation_gate(monkeypatch):
 calls=[]
 def handler(req):
  assert req.url.host=='api.liara.ir' and req.headers['authorization']=='Bearer test-only-placeholder'
  calls.append((req.method,req.url.path,dict(req.url.params),req.content,req.headers.get('content-type')));return httpx.Response(200,json={'ok':True})
 l=Liara('test-only-placeholder',httpx.MockTransport(handler));approval=ApprovedAction('a','fp');monkeypatch.delenv('LIARA_ALLOW_MUTATIONS',raising=False)
 await l.apps();await l.status('hamrah');await l.logs('hamrah');await l.metrics('hamrah')
 assert calls[2][1]=='/v1/projects/hamrah/logs' and 'since' in calls[2][2]
 with pytest.raises(PermissionError):await l.restart('hamrah',approval)
 assert len(calls)==4
 monkeypatch.setenv('LIARA_ALLOW_MUTATIONS','true')
 with pytest.raises(PermissionError):await l.restart('hamrah',None)
 await l.restart('hamrah',approval);await l.update_env('hamrah',{'SITE_TITLE':'hello'},approval);await l.create('hamrah','docker','actual-plan-id',approval);await l.resize('hamrah','actual-plan-id',approval)
 await l.upload('hamrah',b'test gzip data',approval);await l.release('hamrah','actual-source-id',8080,'docker',approval);await l.domain('hamrah','example.ir',approval)
 assert calls[4][1]=='/v1/projects/hamrah/actions/restart'
 assert json.loads(calls[5][3])=={'project':'hamrah','variables':[{'key':'SITE_TITLE','value':'hello'}]}
 assert calls[8][1]=='/v2/projects/hamrah/sources' and calls[8][4].startswith('multipart/form-data')
 assert json.loads(calls[9][3])=={'sourceID':'actual-source-id','port':8080,'type':'docker'}
 with pytest.raises(ValueError):await l.status('../another-service')


def test_log_redaction_and_dns_no_fabricated_values(operations):
 o,u,s=operations
 data=clean({'api_key':'hidden','msg':'Authorization: Bearer secret123 password=hidden sk-ant-test-placeholder','nested':{'token':'hidden'}})
 assert 'hidden' not in json.dumps(data) and 'secret123' not in json.dumps(data)
 guide=o.dns_guide('example.ir');assert not guide['verified_dns'] and guide['expected_record'] is None
 with pytest.raises(ValueError):o.dns_guide('https://example.ir/path')
