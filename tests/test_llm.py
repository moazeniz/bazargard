import asyncio,json,sys
from pathlib import Path
import httpx,pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import llm
from llm_fake import output

KEYS='sk-alpha-1111111111,sk-bravo-2222222222,sk-charlie-3333333333'

def client(responses,seen):
    """Fake httpx client: pops scripted (status, json) or exceptions."""
    class Client:
        def __init__(self,**kw):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*a):return False
        async def post(self,url,headers=None,json=None):
            seen.append({'url':url,'key':headers['Authorization'].split()[-1],'body':__import__('copy').deepcopy(json)})
            r=responses.pop(0)
            if isinstance(r,Exception):raise r
            return httpx.Response(r[0],json=r[1])
    return Client

@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setenv('BOXAPI_KEYS',KEYS);monkeypatch.setenv('BOXAPI_BASE_URL','https://box.test/ai/v1')
    for name in ('RELAY_URL','RELAY_TOKEN','LLM_PROVIDER','LLM_FALLBACK_PROVIDER','LLM_FALLBACK_MODELS','ANTHROPIC_MODEL','LLM_MODEL'):monkeypatch.delenv(name,raising=False)
    llm.reset();yield;llm.reset()

def run(coro):return asyncio.run(coro)

def test_round_robin_spreads_calls_over_keys():
    seen=[];C=client([(200,output('a'))]*4,seen)
    for _ in range(4):run(llm.text('s','u',client_factory=C))
    assert [s['key'] for s in seen]==['sk-alpha-1111111111','sk-bravo-2222222222','sk-charlie-3333333333','sk-alpha-1111111111']
    assert seen[0]['url']=='https://box.test/ai/v1/responses'
    stats=llm.stats();assert stats['calls']==4 and stats['by_key']['0']['calls']==2
    assert 'sk-' not in json.dumps(stats)

@pytest.mark.parametrize('failure',[(401,{'error':{'message':'Invalid API key.'}}),(402,{}),(403,{}),(429,{}),(503,{}),httpx.ReadTimeout('slow'),httpx.ConnectError('down')])
def test_failover_cools_failed_key_and_uses_next(failure):
    seen=[];C=client([failure,(200,output('ok'))],seen)
    assert run(llm.text('s','u',client_factory=C))=='ok'
    assert seen[0]['key']!=seen[1]['key'] and llm.stats()['failovers']==1
    assert '0' in {str(k) for k in llm.stats()['cooling']}
    # The cooled key is skipped by the next calls.
    seen.clear();C=client([(200,output('a')),(200,output('b'))],seen)
    run(llm.text('s','u',client_factory=C));run(llm.text('s','u',client_factory=C))
    assert 'sk-alpha-1111111111' not in [s['key'] for s in seen]

def test_all_keys_exhausted_gives_clear_redacted_error(monkeypatch):
    monkeypatch.setenv('LLM_FALLBACK_MODELS','')
    async def nap(s):pass
    monkeypatch.setattr(llm.asyncio,'sleep',nap)
    seen=[];C=client([(429,{'error':{'message':'quota for sk-alpha-1111111111 exceeded'}})]*6,seen)
    with pytest.raises(llm.LLMError) as e:run(llm.text('s','u',client_factory=C))
    # Two passes over the three keys (one pause for rate limits), then a clear error.
    assert e.value.kind=='rate_limited' and '3 tried' in str(e.value) and 'sk-alpha' not in str(e.value) and len(seen)==6

def test_request_errors_do_not_fail_over():
    seen=[];C=client([(400,{'error':{'message':'bad schema'}})],seen)
    with pytest.raises(llm.LLMError) as e:run(llm.text('s','u',client_factory=C))
    assert e.value.status==400 and e.value.kind=='request' and len(seen)==1

def test_not_configured(monkeypatch):
    monkeypatch.delenv('BOXAPI_KEYS')
    assert not llm.configured()
    with pytest.raises(llm.LLMError) as e:run(llm.text('s','u'))
    assert e.value.kind=='not_configured'

def test_redaction_masks_keys_and_bearer_tokens():
    text=llm.redact('key sk-bravo-2222222222 header Bearer abcdefghijk api_key=zzzzzzzzzz')
    assert 'bravo' not in text and 'abcdefghijk' not in text and 'zzzzzzzzzz' not in text

def test_anthropic_request_translates_to_responses():
    body={'system':'SYS','max_tokens':1400,'tool_choice':{'type':'none'},
          'tools':[{'name':'search_products','description':'d','input_schema':{'type':'object','properties':{'query':{'type':'string'}}}}],
          'messages':[{'role':'user','content':'سلام'},
                      {'role':'assistant','content':[{'type':'thinking','thinking':'x','signature':'s'},{'type':'text','text':'جست‌وجو می‌کنم'},{'type':'tool_use','id':'c1','name':'search_products','input':{'query':'هدفون'}}]},
                      {'role':'user','content':[{'type':'tool_result','tool_use_id':'c1','content':'{"offers":[]}'}]},
                      {'role':'user','content':[{'type':'image','source':{'type':'base64','media_type':'image/png','data':'AAAA'}},{'type':'text','text':'این چیست؟'}]}]}
    out=llm.to_responses(body)
    assert out['instructions']=='SYS' and out['model']==llm.model()=='openai/gpt-6.1-sol' and 'max_tokens' not in out and out['max_output_tokens']>=1400
    assert out['tools']==[{'type':'function','name':'search_products','description':'d','parameters':body['tools'][0]['input_schema'],'strict':False}] and out['tool_choice']=='none'
    items=out['input']
    assert items[0]=={'role':'user','content':'سلام'} and items[1]=={'role':'assistant','content':'جست‌وجو می‌کنم'}
    assert items[2]=={'type':'function_call','call_id':'c1','name':'search_products','arguments':'{"query": "هدفون"}'}
    assert items[3]=={'type':'function_call_output','call_id':'c1','output':'{"offers":[]}'}
    assert items[4]['content'][0]=={'type':'input_image','image_url':'data:image/png;base64,AAAA'} and items[4]['content'][1]['type']=='input_text'
    assert 'temperature' not in out
    with pytest.raises(ValueError):llm.to_responses({'messages':[],'tools':[{'type':'web_search_20250305','name':'web_search'}]})

def test_json_mode_requires_json_word_in_input():
    out=llm.to_responses({'system':'Return ONLY JSON','messages':[{'role':'user','content':'{"a":1}'}]},json_mode=True)
    assert out['text']=={'format':{'type':'json_object'}} and 'json' in json.dumps(out['input']).lower()

def test_responses_output_translates_back_to_anthropic():
    data=output('متن',calls=[('search_products','{"query":"A55"}','call_1'),('show_shortlist','not json','call_2')],usage={'input_tokens':7,'output_tokens':3})
    msg=llm.from_responses(data)
    assert msg['stop_reason']=='tool_use' and msg['usage']=={'input_tokens':7,'output_tokens':3}
    assert msg['content'][0]=={'type':'tool_use','id':'call_1','name':'search_products','input':{'query':'A55'}}
    assert msg['content'][1]['input']=={} and msg['content'][2]=={'type':'text','text':'متن'}
    assert llm.from_responses(output('done'))['stop_reason']=='end_turn'

def test_empty_answer_after_reasoning_budget_is_retried_with_more_room():
    seen=[];C=client([(200,{'status':'incomplete','incomplete_details':{'reason':'max_output_tokens'},'output':[{'type':'reasoning'}]}),(200,output('ok'))],seen)
    assert run(llm.text('s','u',max_tokens=100,client_factory=C))=='ok'
    assert seen[1]['body']['max_output_tokens']==2*seen[0]['body']['max_output_tokens']

def test_web_search_returns_citations_and_sources():
    seen=[];C=client([(200,output('Screen is 6.1 inches.',citations=['https://www.gsmarena.com/x.php'],sources=['https://a.example/1']))],seen)
    found=run(llm.web_search('find',client_factory=C))
    assert seen[0]['body']['tools'][0]['type']=='web_search' and seen[0]['body']['max_tool_calls']==3
    assert found['citations'][0]['url']=='https://www.gsmarena.com/x.php' and found['citations'][0]['text']=='Screen is 6.1 inches.'
    assert found['sources']==['https://a.example/1']

def test_parse_json_tolerates_fences():
    assert llm.parse_json('```json\n{"a": 1}\n```')=={'a':1} and llm.parse_json('no json') is None

def test_all_keys_rate_limited_get_one_more_pass(monkeypatch):
    sleeps=[]
    async def nap(s):sleeps.append(s)
    monkeypatch.setattr(llm.asyncio,'sleep',nap)
    seen=[];C=client([(429,{})]*3+[(200,output('ok'))],seen)
    assert run(llm.text('s','u',client_factory=C))=='ok' and sleeps==[1.0] and len(seen)==4

def test_model_wide_rate_limit_moves_to_fallback_model(monkeypatch):
    async def nap(s):pass
    monkeypatch.setattr(llm.asyncio,'sleep',nap)
    seen=[];C=client([(429,{'error':{'message':'token rate limit'}})]*6+[(200,output('ok'))],seen)
    assert run(llm.text('s','u',client_factory=C))=='ok'
    assert seen[0]['body']['model']=='openai/gpt-6.1-sol' and seen[-1]['body']['model']=='openai/gpt-6-sol' and llm.stats()['model_fallbacks']==1

def test_fallback_chain_covers_5xx_and_reports_serving_model():
    seen=[];C=client([(503,{})]*3+[(502,{})]*3+[(200,output('ok'))],seen)
    assert run(llm.text('s','u',client_factory=C))=='ok'
    assert [s['body']['model'] for s in seen]==['openai/gpt-6.1-sol']*3+['openai/gpt-6-sol']*3+['openai/gpt-6-luna']
    stats=llm.stats();assert stats['by_model']=={'openai/gpt-6-luna':1} and stats['model_fallbacks']==2 and stats['recent'][-1]['model']=='openai/gpt-6-luna'

def test_auth_and_request_failures_do_not_switch_model():
    seen=[];C=client([(401,{})]*3,seen)
    with pytest.raises(llm.LLMError) as e:run(llm.text('s','u',client_factory=C))
    assert e.value.kind=='auth' and len(seen)==3
    llm.reset();seen=[];C=client([(400,{'error':{'message':'bad'}})],seen)
    with pytest.raises(llm.LLMError) as e:run(llm.text('s','u',client_factory=C))
    assert e.value.kind=='request' and len(seen)==1

def test_agent_resends_older_tool_results_in_brief():
    from agent import compact_history,brief_result
    big='<untrusted_data source="x">\n'+'y'*5000+'\n</untrusted_data>'
    messages=[{'role':'user','content':'هدفون'},
              {'role':'assistant','content':[{'type':'tool_use','id':'t1','name':'search_products','input':{}}]},
              {'role':'user','content':[{'type':'tool_result','tool_use_id':'t1','content':big}]},
              {'role':'assistant','content':[{'type':'tool_use','id':'t2','name':'inspect_offer','input':{}}]},
              {'role':'user','content':[{'type':'tool_result','tool_use_id':'t2','content':big}]}]
    out=compact_history(messages,{'t1':'BRIEF'},latest_chars=1000)
    assert out[2]['content'][0]['content']=='BRIEF' and len(out[4]['content'][0]['content'])<=1000
    assert out[4]['content'][0]['content'].endswith('</untrusted_data>') and messages[2]['content'][0]['content']==big
    brief=brief_result({'query':'q','offers':[{'offer_id':'o1','number':1,'persian_title':'t','description':'long','specs':[1,2]}]})
    assert brief['offers']==[{'number':1,'offer_id':'o1','product_id':None,'shop_id':None,'persian_title':'t','shop_name':None,'effective_price_toman':None,'price_basis':None,'distance_km':None}]


# ---- provider switch: anthropic (relay / Arvan LLM gate) and boxapi ----
GATE='http://gate.test/meow-llm'
def gate(monkeypatch,provider=None,fallback=None):
    monkeypatch.setenv('RELAY_URL',GATE);monkeypatch.setenv('RELAY_TOKEN','gate-token-123456')
    if provider:monkeypatch.setenv('LLM_PROVIDER',provider)
    if fallback is not None:monkeypatch.setenv('LLM_FALLBACK_PROVIDER',fallback)

def test_provider_chain_follows_env(monkeypatch):
    assert llm.chain()==['boxapi']  # anthropic is the default but needs the relay/gate
    gate(monkeypatch)
    assert llm.chain()==['anthropic','boxapi'] and llm.model()=='claude-sonnet-5-5'
    monkeypatch.setenv('LLM_PROVIDER','boxapi');assert llm.chain()==['boxapi','anthropic'] and llm.model()=='openai/gpt-6.1-sol'
    monkeypatch.setenv('LLM_FALLBACK_PROVIDER','');assert llm.chain()==['boxapi']
    monkeypatch.delenv('BOXAPI_KEYS');monkeypatch.setenv('LLM_PROVIDER','anthropic');assert llm.chain()==['anthropic'] and llm.configured()

def test_anthropic_body_passes_through_the_gate(monkeypatch):
    gate(monkeypatch)
    seen=[];reply={'content':[{'type':'tool_use','id':'toolu_1','name':'search_products','input':{'query':'A55'}}],'stop_reason':'tool_use','usage':{'input_tokens':9,'output_tokens':2}}
    C=client([(200,reply)],seen)
    body={'model':'ignored','max_tokens':9000,'temperature':0.3,'system':'S','tools':[{'name':'search_products','input_schema':{'type':'object'}}],'messages':[{'role':'user','content':'x'}]}
    assert run(llm.chat(body,client_factory=C))==reply
    sent=seen[0];assert sent['url']==GATE+'/v1/messages' and sent['key']=='gate-token-123456'
    assert sent['body']['model']=='claude-sonnet-5-5' and sent['body']['max_tokens']==5000 and 'temperature' not in sent['body'] and sent['body']['tools']==body['tools']
    assert llm.stats()['by_model']=={'claude-sonnet-5-5':1}

@pytest.mark.parametrize('status',[401,403,503,429,402])
def test_gate_refusal_or_capacity_falls_back_to_boxapi(monkeypatch,status):
    gate(monkeypatch)
    async def nap(s):pass
    monkeypatch.setattr(llm.asyncio,'sleep',nap)
    retries=3 if status==429 else 1
    seen=[];C=client([(status,{'error':{'message':'gate says no'}})]*retries+[(200,output('از BoxAPI'))],seen)
    assert run(llm.text('s','u',client_factory=C))=='از BoxAPI'
    assert seen[-1]['url']=='https://box.test/ai/v1/responses' and llm.stats()['provider_fallbacks']==1

def test_request_errors_do_not_switch_provider(monkeypatch):
    gate(monkeypatch)
    seen=[];C=client([(400,{'error':{'message':'bad request'}})],seen)
    with pytest.raises(llm.LLMError) as e:run(llm.text('s','u',client_factory=C))
    assert e.value.kind=='request' and len(seen)==1

def test_anthropic_web_search_keeps_verbatim_citations(monkeypatch):
    gate(monkeypatch)
    data={'content':[{'type':'server_tool_use','id':'s','name':'web_search','input':{'query':'iphone 16e specs'}},
                     {'type':'web_search_tool_result','tool_use_id':'s','content':[{'type':'web_search_result','url':'https://www.gsmarena.com/a.php','title':'GSMArena'}]},
                     {'type':'text','text':'Screen is 6.1 inches','citations':[{'type':'web_search_result_location','url':'https://www.gsmarena.com/a.php','title':'GSMArena','cited_text':'Size 6.1 inches, 91.7 cm2'}]}]}
    seen=[];C=client([(200,data)],seen)
    found=run(llm.web_search({'anthropic':'A prompt','boxapi':'B prompt'},max_tool_calls=2,client_factory=C))
    tool=seen[0]['body']['tools'][0]
    assert tool=={'type':'web_search_20250305','name':'web_search','max_uses':2} and seen[0]['body']['output_config']=={'effort':'low'} and seen[0]['body']['messages'][0]['content']=='A prompt'
    assert found['provider']=='anthropic' and found['citations']==[{'url':'https://www.gsmarena.com/a.php','title':'GSMArena','text':'Size 6.1 inches, 91.7 cm2','verbatim':True}]
    assert found['sources']==['https://www.gsmarena.com/a.php'] and found['queries']==['iphone 16e specs']

def test_relay_token_is_redacted(monkeypatch):
    gate(monkeypatch)
    assert 'gate-token-123456' not in llm.redact('failed with gate-token-123456')

def test_health_reports_both_providers(monkeypatch):
    gate(monkeypatch)
    class Get:
        def __init__(self,**kw):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*a):return False
        async def get(self,url,headers=None):return httpx.Response(200 if url==GATE+'/health' else 503,json={})
    monkeypatch.setattr(llm.httpx,'AsyncClient',Get)
    h=run(llm.health())
    assert h['provider']=='anthropic' and h['model']=='claude-sonnet-5-5' and h['provider_status']=='reachable' and h['fallback_provider']=='boxapi'
    assert h['providers']['boxapi']=={'configured':True,'status':'unavailable','model':'openai/gpt-6.1-sol','light_model':'openai/gpt-6-luna'}

def test_boxapi_spending_limit_is_a_credit_error_that_moves_to_anthropic(monkeypatch):
    seen=[];C=client([(402,{'error':{'message':'Spending limit reached for this API key.'}})]*3,seen)
    with pytest.raises(llm.LLMError) as e:run(llm.text('s','u',client_factory=C))
    assert e.value.kind=='credit' and len(seen)==3  # no model fallback for budget errors
    llm.reset();gate(monkeypatch,provider='boxapi')
    seen=[];C=client([(402,{})]*3+[(200,{'content':[{'type':'text','text':'از Claude'}],'usage':{}})],seen)
    assert run(llm.text('s','u',client_factory=C))=='از Claude' and seen[-1]['url']==GATE+'/v1/messages'

def test_light_tier_maps_to_haiku_and_luna(monkeypatch):
    gate(monkeypatch)
    seen=[];C=client([(200,{'content':[{'type':'text','text':'ok'}],'usage':{}}),(200,{'content':[{'type':'text','text':'ok'}],'usage':{}})],seen)
    run(llm.text('s','u',purpose='guard',client_factory=C));run(llm.text('s','u',client_factory=C))
    assert [x['body']['model'] for x in seen]==['claude-haiku-5-5','claude-sonnet-5-5']
    monkeypatch.setenv('LLM_PROVIDER','boxapi');monkeypatch.setenv('ANTHROPIC_LIGHT_MODEL','claude-x');llm.reset()
    seen=[];C=client([(200,output('ok'))],seen)
    run(llm.text('s','u',tier='light',client_factory=C))
    assert seen[0]['body']['model']=='openai/gpt-6-luna' and llm.anthropic_model('light')=='claude-x'
