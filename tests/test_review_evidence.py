import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from review_evidence import configuration,external_facts
from review_analysis import validate_claims
from public_research import identity


def laptop():
    return {'product_id':'p','persian_title':'LOQ 15IRX10','category_name':'لپ تاپ و نوت بوک','specs':[
        {'key':'مدل پردازنده','value':'i7-14700HX'}, {'key':'مدل گرافیک','value':'RTX 4060'},
        {'key':'ظرفیت حافظه رم','value':'16 GB'}, {'key':'ظرفیت SSD','value':'512 GB'},
        {'key':'سایز صفحه نمایش (اینچ)','value':'15.6 inch'}]}


def test_explicit_test_unit_is_required_and_variant_conflict_rejected():
    p=laptop();unit='Our review unit has i7-14700HX CPU, RTX 4060 GPU, 16 GB RAM, 512 GB SSD storage and 15.6 inch screen.'
    assert configuration(p,unit)['status']=='matched_test_unit'
    assert configuration(p,unit.replace('Our review unit','Specifications'))['status']=='unverified'
    assert configuration(p,unit.replace('RTX 4060','RTX 4060Ti'))['status']=='unverified'
    assert configuration(p,unit.replace('16 GB','8 GB'))['status']=='unverified'
    assert identity(p,unit,'LOQ 15IRX10 review')['allow_measured_claims']
    assert not identity(p,unit,'LOQ 15IRX9 review')['allow_measured_claims']
    p['specs'].append({'key':'ظرفیت حافظه رم','value':'8 GB'})
    assert configuration(p,unit)['status']=='unverified'


def test_external_quantities_remain_ambiguous_not_benchmarks():
    facts=external_facts('A 6.6-inch screen with 120 Hz refresh, 5000 mAh battery. Compared to a 6.4-inch display.',{},'https://example.com/review')
    assert facts[0]['status']=='ambiguous' and facts[0]['values']==['6.6','6.4']
    assert all(not f['measured'] and f['source']=='public_source' for f in facts)
    assert all(len(f['quote'].split())<=20 for f in facts)


def test_analysis_rejects_fake_citations_and_ungrounded_numbers():
    evidence=[{'id':'s1','kind':'specialist','text':'The display is bright but the camera is slow.','url':'https://example.com'}]
    result=validate_claims({'claims':[
        {'kind':'strength','text':'نویسنده نمایشگر را روشن می‌داند.','evidence':[{'id':'s1','quote':'display is bright'}]},
        {'kind':'weakness','text':'امتیاز 999 دارد.','evidence':[{'id':'s1','quote':'camera is slow'}]},
        {'kind':'fit','text':'مناسب است.','evidence':[{'id':'s2','quote':'invented'}]},
        {'kind':'strength','text':'خرید تأییدشده','evidence':[{'id':'s1','quote':'display is bright'}]},
    ]},evidence)
    assert len(result)==1 and result[0]['source']=='assistant_analysis'


import httpx,pytest
from review_analysis import analyze

@pytest.mark.asyncio
async def test_relay_analysis_handles_bad_unavailable_and_grounded_payload(monkeypatch):
    import llm
    from llm_fake import output
    monkeypatch.setenv('BOXAPI_KEYS','isolated-test');monkeypatch.setenv('LLM_FALLBACK_MODELS','');llm.reset()
    card={'title':'test','recorded_facts':[{'label':'رم','value':'8 GB'}],'research':{'sources':[]}}
    # A provider 5xx on the only key exhausts the pool; later calls retry that key.
    responses=[httpx.Response(503,json={'error':'private provider error'}),httpx.Response(200,json=output('not-json')),httpx.Response(200,json=output('{"claims":[{"kind":"fit","text":"رم ۸ گیگابایت ثبت شده است؛ عملکرد واقعی از این عدد نتیجه نمی‌شود.","evidence":[{"id":"catalogue:0","quote":"8 GB"}]}]}'))]
    class Client:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,headers,json):
            assert 'tools' not in json and json['text']['format']['type']=='json_object'
            return responses.pop(0)
    assert (await analyze(card,{},Client))['status']=='unavailable'
    assert (await analyze(card,{},Client))['status']=='unavailable'
    result=await analyze(card,{},Client)
    assert result['status']=='ready' and len(result['claims'])==1


def test_generated_customer_experience_needs_written_community_evidence():
    claims={'claims':[{'kind':'strength','text':'خریداران از این مدل راضی‌اند.','evidence':[{'id':'s','quote':'8 GB RAM'}]}]}
    assert not validate_claims(claims,[{'id':'s','kind':'catalogue','text':'8 GB RAM'}])
    community=[{'id':'c','kind':'community','text':'I liked the fit of my headphones.','url':'https://forum.example/post'}]
    quoted={'claims':[{'kind':'fit','text':'نویسنده این پست انجمن از تناسب هدفون خود گفته است.','evidence':[{'id':'c','quote':'I liked the fit'}]}]}
    assert len(validate_claims(quoted,community))==1
    quoted['claims'][0]['text']='اکثر کاربران از تناسب هدفون راضی‌اند.'
    assert not validate_claims(quoted,community)
