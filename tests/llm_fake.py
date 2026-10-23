"""Test doubles for the BoxAPI layer (backend/llm.py)."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import llm


def use_client(monkeypatch,Client,key='test-key'):
    """Route llm.chat through an Anthropic-shaped fake client: the client sees
    the exact Anthropic-style request body a call site built and answers with
    Anthropic-style content, so agent-loop tests stay provider-neutral."""
    async def chat(body,**kw):
        async with Client() as c:
            r=await c.post('boxapi',headers={},json=body)
        if r.status_code!=200:raise llm.LLMError('fake provider failure',r.status_code,'exhausted')
        return r.json()
    llm.reset();monkeypatch.setattr(llm,'chat',chat);monkeypatch.setenv('BOXAPI_KEYS',key)


def output(text='',calls=(),citations=(),sources=(),usage=None,status='completed'):
    """A Responses API result body."""
    items=[{'type':'web_search_call','action':{'type':'search','query':'q','sources':[{'type':'url','url':u} for u in sources]}}] if sources else []
    for name,args,cid in calls:items.append({'type':'function_call','call_id':cid,'name':name,'arguments':args})
    if text:
        anns=[];body=text
        for url in citations:
            start=len(body);body+=' ([src]('+url+'))';anns.append({'type':'url_citation','url':url,'title':'T '+url,'start_index':start,'end_index':len(body)})
        items.append({'type':'message','role':'assistant','content':[{'type':'output_text','text':body,'annotations':anns}]})
    return {'id':'resp_test','status':status,'output':items,'usage':usage or {'input_tokens':10,'output_tokens':5}}


def is_web_search(body):return any(t.get('type')=='web_search' for t in body.get('tools') or [])
def user_text(body):
    """Concatenated user input of a Responses body."""
    if isinstance(body.get('input'),str):return body['input']
    out=[]
    for item in body.get('input') or []:
        c=item.get('content')
        if isinstance(c,str):out.append(c)
        elif isinstance(c,list):out+=[p.get('text','') for p in c]
    return '\n'.join(out)
