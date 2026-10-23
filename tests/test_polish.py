"""Polish round: chat latency paths, follow-up kit/tracker, comparison prices, routing deadline,
Digikala model matching, and removed rate limits."""
import asyncio,os,sys,time
from pathlib import Path
import httpx,pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from llm_fake import use_client
import llm

@pytest.fixture
def env(tmp_path):
    path=os.getenv('TEST_CATALOG')
    if not path:pytest.skip('TEST_CATALOG required')
    from catalog import Catalog
    from memory import Memory
    from agent import Agent
    from features import Features
    cat=Catalog(path);mem=Memory(tmp_path/'memory.db');u=mem.user();s=mem.session(u)['id'];a=Agent(cat,mem);f=Features(cat,mem,a);a.features=f
    return cat,mem,u,s,a,f

# ---------------------------------------------------------------- latency
def test_final_answer_written_with_shortlist_is_used_and_renumbered():
    from agent import final_from_shortlist
    content=[{'type':'text','text':'گزینه اول [2] برای ورزش مناسب‌تر است و گزینه [1] ارزان‌تر است؛ هر دو داخل بودجه‌اند و قیمت‌ها ثبت‌شده در ترب است، نه قیمت زنده.'},
             {'type':'tool_use','id':'t','name':'show_shortlist','input':{'offer_ids':['a','b']}}]
    calls=[content[1]]
    out=final_from_shortlist(content,calls,(['a','b'],['b','a','c']))
    assert '[1] برای ورزش' in out and 'گزینه [2] ارزان' in out
    assert final_from_shortlist(content,calls,(['a','b'],['a']))is None  # a cited offer is not on the cards
    assert final_from_shortlist([{'type':'text','text':'چند گزینه را نشان می‌دهم.'}]+calls,calls,(['a','b'],['a','b'])) is None  # a preamble is not an answer
    assert final_from_shortlist(content,calls+[{'name':'search_products'}],(['a','b'],['a','b'])) is None

def test_agent_finishes_in_the_shortlist_round_and_caches_prompt(env,monkeypatch):
    cat,mem,u,s,a,f=env;payloads=[]
    class Client:
        def __init__(self,**kw):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*x):pass
        async def post(self,url,headers,json):
            payloads.append(json)
            if len(payloads)==1:
                return httpx.Response(200,json={'content':[{'type':'tool_use','id':'s','name':'search_products','input':{'query':'هدفون'}}]})
            offers=mem.results(u,s)['offers'][:2]
            text='این دو گزینه داخل بودجه‌اند: [1] ارزان‌تر است و [2] مشخصات کامل‌تری دارد. قیمت‌ها ثبت‌شده در ترب‌اند و موجودی زنده تأیید نشده است.'
            return httpx.Response(200,json={'content':[{'type':'text','text':text},{'type':'tool_use','id':'k','name':'show_shortlist','input':{'offer_ids':[o['offer_id'] for o in offers]}}]})
    use_client(monkeypatch,Client)
    events=asyncio.run(_collect(a.response(u,s,'هدفون می‌خواهم','simple')))
    done=events[-1];assert done['type']=='done' and len(payloads)==2 and done['reply'].startswith('این دو گزینه')
    system=payloads[0]['system'];assert isinstance(system,list) and system[0]['cache_control']=={'type':'ephemeral'} and 'Context' in system[1]['text']
    assert payloads[0]['tools'][-1]['cache_control']=={'type':'ephemeral'}

async def _collect(gen):return [e async for e in gen]

def test_anthropic_tool_loops_get_an_incremental_cache_breakpoint(monkeypatch):
    monkeypatch.setenv('RELAY_URL','http://gate.test');monkeypatch.setenv('RELAY_TOKEN','gate-token-123456');monkeypatch.delenv('BOXAPI_KEYS',raising=False);llm.reset()
    seen=[]
    class C:
        def __init__(self,**kw):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*a):return False
        async def post(self,url,headers=None,json=None):seen.append(json);return httpx.Response(200,json={'content':[{'type':'text','text':'ok'}],'usage':{'cache_read_input_tokens':900}})
    body={'system':'S','max_tokens':100,'tools':[{'name':'t','input_schema':{'type':'object'}}],'messages':[{'role':'user','content':'سلام'},{'role':'assistant','content':[{'type':'tool_use','id':'x','name':'t','input':{}}]},{'role':'user','content':[{'type':'tool_result','tool_use_id':'x','content':'{}'}]}]}
    asyncio.run(llm.chat(body,client_factory=C))
    last=seen[0]['messages'][-1]['content'][-1];assert last['cache_control']=={'type':'ephemeral'} and 'cache_control' not in body['messages'][-1]['content'][-1]
    asyncio.run(llm.chat({'max_tokens':10,'messages':[{'role':'user','content':'x'}]},client_factory=C))
    assert seen[1]['messages'][0]['content']=='x'  # single-shot calls are not cached

def test_catalogue_search_projects_specs_only_for_returned_offers(env):
    cat,*_=env
    calls=[];original=cat.projections.read
    cat.projections.read=lambda d:calls.append(d['product_id']) or original(d)
    r=cat.search('هدفون',{'budget_max_toman':20000000},12)
    assert len(calls)<=len(r['offers']) and all(o.get('canonical') for o in r['offers'])

# ---------------------------------------------------------------- follow-up
def test_follow_up_kit_has_real_channels_only_when_known_and_no_phone_numbers(env):
    cat,mem,u,s,a,f=env
    with cat.conn() as db:
        real=db.execute("select o.shop_id,o.product_id from offers o join shops s on s.shop_id=o.shop_id where s.instagram is not null and s.instagram!='null' limit 1").fetchone()
        plain=db.execute("select o.shop_id,o.product_id from offers o join shops s on s.shop_id=o.shop_id where (s.instagram is null or s.instagram='null') and (s.website_url is null or s.website_url='null') limit 1").fetchone()
    kit=a.contact(real[0],real[1],'2026-10-24T17:30')
    assert kit['channels'][0]['kind']=='instagram' and kit['channels'][0]['url'].startswith('https://ig.me/m/') and kit['identity']=='source_identity'
    assert '17:30' in kit['message'] and 'تومان' in kit['message']
    assert {x['kind'] for x in kit['share']}=={'whatsapp','telegram','sms'} and kit['share'][0]['url'].startswith('https://wa.me/?text=')
    other=a.contact(plain[0],plain[1])
    assert other['channels']==[] and other['identity']=='synthetic_name' and 'شبیه‌سازی' in other['paths']['simulated']
    import re
    assert not re.search(r'09\d{9}|\+98',kit['message']+other['message'])

def test_follow_up_tracker_is_user_maintained_per_stop(env):
    cat,mem,u,s,a,f=env;o=cat.search('هدفون')['offers'][0]
    assert f.followups(u,s)=={}
    state=f.set_followup(u,s,o['shop_id'],'asked',o['product_id'],'whatsapp')
    assert state[o['shop_id']]['status']=='asked' and state[o['shop_id']]['channel']=='whatsapp' and state[o['shop_id']]['asked_at']
    state=f.set_followup(u,s,o['shop_id'],'confirmed');assert state[o['shop_id']]['status']=='confirmed' and state[o['shop_id']]['channel']=='whatsapp'
    with pytest.raises(ValueError):f.set_followup(u,s,o['shop_id'],'sent_by_bot')
    other=mem.session(mem.user());assert f.followups(other['user_id'],other['id'])=={}

# ---------------------------------------------------------------- comparison / plan regressions
def test_comparing_products_outside_the_shortlist_keeps_prices(env):
    cat,mem,u,s,a,f=env
    full=cat.search('هدفون',{},30,full=True)['offers'];ids=list(dict.fromkeys(o['product_id'] for o in full))[:12]
    mem.results(u,s,{'offers':[o for o in full if o['product_id']==ids[0]][:1],'query':'هدفون'})
    data=a.comparison(u,s,ids)
    prices=next(r for r in data['rows'] if r['id']=='price')['cells']
    assert sum(c.get('numeric') is not None for c in prices)>=10

def test_plan_never_waits_on_a_hanging_road_service(env,monkeypatch):
    cat,mem,u,s,a,f=env;offers=cat.search('هدفون')['offers'][:2]
    mem.update(u,[{'key':'lat','value':35.70},{'key':'lon','value':51.40}],sid=s)
    async def hang(points,mode):await asyncio.sleep(30)
    monkeypatch.setattr(f,'road',hang);f.ROAD_SECONDS=0.2
    started=time.monotonic();route=asyncio.run(f.plan(u,s,[o['shop_id'] for o in offers]))
    assert time.monotonic()-started<5 and route['stops']

# ---------------------------------------------------------------- reviews
def test_digikala_match_uses_title_codes_and_prefers_the_exact_variant():
    import user_reviews as U
    title='هدفون بی سیم کیو سی وای مدل T13 ANC'
    products=[{'title_fa':'هدفون مدل T13X'},{'title_fa':'هدفون بلوتوثی برند کیو سی وای مدل T13 ANC2'},{'title_fa':'هدفون بلوتوثی کیو سی وای مدل T13 ANC با نویز کنسلینگ'}]
    assert U.model_codes(title)==['t13']
    assert U.best_match(products,U.model_codes(title),U.latin_words(title))['title_fa'].endswith('نویز کنسلینگ')
    assert U.best_match([{'title_fa':'هدفون T130'}],['t13'],['t13']) is None
    # An accessory made for the product is never taken for the product itself.
    covers=[{'title_fa':'کاور مدل T13 ANC مناسب برای کیس هدفون بی سیم کیو سی وای T13'}]
    assert U.best_match(covers,['t13'],['t13','anc'],title) is None
    assert U.best_match(covers,['t13'],['t13','anc'],'کاور مدل T13 ANC')['title_fa'].startswith('کاور')

def test_digikala_ignores_internal_model_slugs(tmp_path):
    import user_reviews as U
    class Http:
        def __init__(self):self.queries=[]
        async def get(self,url,params=None):
            self.queries.append((params or {}).get('q'))
            class R:status_code=200
            R.json=staticmethod(lambda:{'data':{'products':[{'id':5,'title_fa':'هدفون بلوتوثی کیو سی وای مدل T13 ANC'}]}} if url.endswith('/search/') else {'data':{'comments':[{'id':1,'body':'خوب است','rate':'5'}],'pager':{'total_pages':1}}})
            return R
    http=Http()
    dk=asyncio.run(U.UserReviews(tmp_path/'r.db',http=http).digikala({'persian_title':'هدفون بی سیم کیو سی وای مدل T13 ANC','model':'authentic-base-10396','brand_name':'شیائومی'},lambda e:None))
    assert dk['status']=='ok' and dk['product_id']==5 and len(dk['reviews'])==1

# ---------------------------------------------------------------- limits
def test_chat_and_new_chats_are_not_rate_limited(tmp_path,monkeypatch):
    import importlib,guard
    assert guard.MAX_CHARS>=3000 and guard.analyze('گوشی '*500)['verdict']!='flood'
    server=importlib.import_module('server')
    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://t',cookies={'hamrah_user':server.mem.user()}) as c:
            codes=[(await c.post('/api/sessions',json={})).status_code for _ in range(65)]
            assert codes==[200]*65
    asyncio.run(go())
