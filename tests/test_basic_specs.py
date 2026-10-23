import copy,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import pytest
from basic_specs import extract,overlay,missing_fields
from comparison import cell
from public_research import PublicResearch

P={'product_id':'p','model':'UX3402ZA','persian_title':'ASUS UX3402ZA','brand_name':'ASUS','category_name':'لپ تاپ و نوت بوک','specs':[]}
TEXT='ASUS UX3402ZA specifications\nScreen size: 14 inches\nRAM: 16 GB\nProcessor: Intel Core i7-1260P\nStorage: 512 GB\nDevice weight: 1400 g\nRefresh rate: 90 Hz'
def facts(text=TEXT,product=P,title='ASUS UX3402ZA specifications',kind='manufacturer',**kw):return extract(product,text,title,'UX3402ZA',kind,**kw)
def source(fs,url='https://www.asus.com/specs'):return {'url':url,'retrieved_at':time.time(),'kind':'manufacturer','basic_specs':fs}
def snapshot():return {'product_ids':['p','q'],'rows':[{'id':'screen','label':'اندازه نمایشگر','unit':'inch','cells':[cell([],'inch'),cell([{'key':'screen size','value':'15 inches'}],'inch')]}],'charts':[]}
def cards(fs,url='https://www.asus.com/specs'):return [{'product_id':'p','research':{'sources':[source(fs,url)]}}]

def test_positive_static_fields_units_literal_and_no_test_unit_required():
    f={v['field']:v for v in facts()}
    assert all(v['status']=='accepted' for v in f.values())
    assert {'screen','ram','cpu','storage','weight'}<=f.keys()
    assert f['weight']['numeric']==1.4 and f['ram']['numeric']==16
    assert f['cpu']['value']=='Intel Core i7-1260P' and f['cpu']['numeric'] is None
    assert all(v['quote'] in TEXT for v in f.values())
    assert all(v['model_match']=='exact_title' for v in f.values())

@pytest.mark.parametrize('text,title,reason',[
 (TEXT.replace('1400 g','3 lb'),'ASUS UX3402ZA specifications','weight'),
 (TEXT.replace('Device weight','Shipping weight'),'ASUS UX3402ZA specifications','weight'),
 (TEXT.replace('Device weight','Package weight'),'ASUS UX3402ZA specifications','weight'),
 (TEXT.replace('Device weight: 1400 g','Weight: 1400 g including packaging'),'ASUS UX3402ZA specifications','weight'),
 (TEXT.replace('16 GB','16 GB / 32 GB'),'ASUS UX3402ZA specifications','ram'),
 (TEXT.replace('16 GB','up to 16 GB'),'ASUS UX3402ZA specifications','ram'),
 (TEXT+'\nUX3402VA specifications','ASUS UX3402ZA specifications','ram'),
 (TEXT,'ASUS UX3402VA specifications','ram'),
 (TEXT+'\nAvailable in several configurations','ASUS UX3402ZA specifications','weight'),
 (TEXT.replace('Storage: 512 GB','Storage: 512 GB or 1 TB'),'ASUS UX3402ZA specifications','storage'),
 (TEXT+'\nProcessor: Intel Core i5-1240P','ASUS UX3402ZA specifications','cpu'),
])
def test_negative_variants_units_alternatives_shipping(text,title,reason):
    result={f['field']:f for f in facts(text,title=title)}
    assert reason not in result or result[reason]['status']!='accepted'

def test_other_source_truncation_configuration_and_unrelated_quantity():
    assert all(f['status']!='accepted' for f in facts(kind='specialist'))
    assert all(f['status']!='accepted' for f in facts(truncated=True))
    p=dict(P,specs=[{'key':'ram','value':'8 GB'}])
    assert all(f['status']=='configuration_conflict' for f in facts(product=p))
    assert not facts('UX3402ZA temperatures 90 C; score 12000; package mass 3kg; 120 Hz game score')

def test_overlay_missing_only_conflicts_charts_and_immutable():
    original=snapshot();saved=copy.deepcopy(original)
    result=overlay(original,cards(facts()))
    assert original==saved
    rows={r['id']:r for r in result['rows']}
    assert rows['screen']['cells'][0]['source']=='public_spec'
    assert rows['screen']['cells'][1]==original['rows'][0]['cells'][1]
    assert rows['weight']['cells'][0]['numeric']==1.4
    assert rows['cpu']['cells'][0]['numeric'] is None
    assert all(c['id']!='cpu' for c in result['charts'])
    chart=next(c for c in result['charts'] if c['id']=='screen')
    assert chart['values'][0]['research_evidence'][0]['url'].startswith('https:')
    assert rows['screen']['cells'][0]['original_cell']['status']=='missing'
    # Conflicting later source is inspectable, original missing value restored.
    disputed=overlay(result,cards(facts(TEXT.replace('14 inches','13 inches')),'https://www.asus.com/other'))
    c=next(r for r in disputed['rows'] if r['id']=='screen')['cells'][0]
    assert c['status']=='missing' and c['numeric'] is None and c['research_status']=='source_conflict'
    assert len(c['research_evidence'])==2
    assert result['rows'][0]['cells'][0]['status']=='web_completed'


def test_catalogue_conflict_never_overwritten_and_source_disagreement_retained():
    base=snapshot();base['rows'][0]['cells'][0]=cell([{'key':'screen size','value':'12 inches'},{'key':'display size','value':'13 inches'}],'inch')
    result=overlay(base,cards(facts()))
    c=result['rows'][0]['cells'][0]
    assert c['status']=='conflict' and c['value']==base['rows'][0]['cells'][0]['value']
    assert c['research_status']=='catalogue_disagreement'


def test_discovery_targets_gaps_prefers_manufacturer_and_shared_evidence(tmp_path):
    calls=[]
    def fetch(u,d):
        calls.append(u)
        if 'duckduckgo' in u:return {'url':u,'status':'readable','links':['https://other.example/review','https://www.asus.com/specs']}
        return {'url':u,'status':'readable','title':'ASUS UX3402ZA specifications','text':TEXT,'links':[]}
    result=PublicResearch(tmp_path/'cache.db',fetch).investigate(P)
    assert 'specifications' in result['discovery']['query'] and 'ram' in result['target_missing_fields']
    assert result['sources'][0]['kind']=='manufacturer'
    assert any(f['field']=='screen' and f['status']=='accepted' for f in result['sources'][0]['basic_specs'])
    assert all(f['status']!='accepted' for f in result['sources'][0]['basic_specs'] if f['field'] in ('ram','cpu','storage'))
    assert PublicResearch(tmp_path/'cache.db',fetch).investigate(P)['cached']
    assert sum(len(f['quote'].split()) for f in result['sources'][0]['basic_specs'])<=70

@pytest.mark.parametrize('text,field',[
 ('RAM: 16 GBps','ram'),('Screen size: 14 inchesFoo','screen'),
 ('RAM: 16 GB x2','ram'),('RAM: 16 GB + 16 GB','ram'),
 ('Shipping\nWeight: 1400 g','weight'),('Weight: 1.4 kg (1.8 kg with adapter)','weight'),
 ('GPU memory: 16 GB','ram'),
])
def test_rejects_partial_units_modules_and_package_headings(text,field):
    result={f['field']:f for f in facts('ASUS UX3402ZA\n'+text)}
    assert field not in result or result[field]['status']!='accepted'


def test_family_alternative_ram_blocks_weight_too():
    result=facts(TEXT.replace('16 GB','16 GB or 32 GB'))
    assert all(f['status']!='accepted' for f in result)

@pytest.mark.asyncio
async def test_positive_researched_snapshot_reply_reload_and_owner_isolation(tmp_path,monkeypatch):
    from memory import Memory
    from agent import Agent
    m=Memory(tmp_path/'private.db');u=m.user();sid=m.session(u)['id'];a=Agent(None,m)
    base=snapshot();base.update(created=time.time(),products=[dict(P),dict(P,product_id='q')],reviews=[],notice='fixture')
    base=m.save_comparison(u,sid,base);original=copy.deepcopy(base)
    async def review(uid,session,pid):return {'product_id':pid,'research':{'sources':[source(facts())]}}
    monkeypatch.setattr(a,'review',review)
    enriched=await a.researched_comparison(u,sid,base['snapshot_id'],['p','q'])
    m.save_turn(u,sid,'assistant','Controlled specification fixture',{'comparison':enriched})
    assert enriched['basic_specs_completed']>=5
    assert m.comparison_snapshot(u,sid,base['snapshot_id'])==original
    assert Memory(m.path).comparison_snapshot(u,sid,enriched['snapshot_id'])==enriched
    assert m.turns(u,sid)[-1]['references']['comparison']==enriched
    other=m.user();other_sid=m.session(other)['id']
    with pytest.raises(ValueError):await a.researched_comparison(other,other_sid,enriched['snapshot_id'],['p','q'])

@pytest.mark.asyncio
async def test_research_api_persists_completed_cells_and_rejects_other_owner(tmp_path,monkeypatch):
    import importlib,httpx
    from memory import Memory
    from agent import Agent
    monkeypatch.setenv('DATA_DIR',str(tmp_path))
    server=importlib.import_module('server');m=Memory(tmp_path/'api-private.db');a=Agent(None,m)
    monkeypatch.setattr(server,'mem',m);monkeypatch.setattr(server,'agent',a)
    u=m.user();sid=m.session(u)['id'];base=snapshot();base.update(created=time.time(),products=[dict(P),dict(P,product_id='q')],reviews=[],notice='fixture')
    base=m.save_comparison(u,sid,base)
    async def review(uid,session,pid):return {'product_id':pid,'research':{'sources':[source(facts())]}}
    monkeypatch.setattr(a,'review',review)
    transport=httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport,base_url='http://testserver',cookies={'hamrah_user':u}) as c:
        body={'session_id':sid,'snapshot_id':base['snapshot_id'],'product_ids':['p','q']}
        response=await c.post('/api/compare/research',json=body);assert response.status_code==200,response.text
        result=response.json();assert result['basic_specs_completed']>=5
        assert m.turns(u,sid)[-1]['references']['comparison']==result
        assert m.comparison_snapshot(u,sid,base['snapshot_id'])==base
        other=m.user();c.cookies.set('hamrah_user',other)
        assert (await c.post('/api/compare/research',json=body)).status_code==404

@pytest.mark.parametrize('title',['ASUS UX3402ZA 32 GB RAM','ASUS UX3402ZA حافظه 256 رم 8','ASUS UX3402ZA i5-1240P'])
def test_title_variant_anchors_are_not_ignored(title):
    assert all(f['status']!='accepted' for f in facts(product=dict(P,persian_title=title)))
