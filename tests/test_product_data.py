import sys,os,copy,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import pytest
from product_data import resolve,matches,scalar,offer_claims,VERSION
from spec_schema import category
from comparison import build_comparison
from spec_fetcher import extract,identity,SpecFetcher

def product(specs,category_name='لپ تاپ و نوت بوک',title='ASUS UX3402ZA'):
 return {'product_id':'p','category_name':category_name,'persian_title':title,'specs':[{'key':k,'value':v} for k,v in specs]}

def test_shared_alias_units_contexts_and_conflicts():
 p=product([('حافظه RAM','۱۶ گیگابایت'),('ظرفیت رم','16 GB'),('حداکثر حافظه رم','32 GB'),('رم کارت گرافیک مجزا','2 GB'),('وزن بسته بندی','3 kg'),('وزن (گرم)','1400'),('مدل پردازنده','نسل دهم'),('پردازنده','10510U'),('حافظه HDD','512 گیگابایت'),('نوع هارد','SSD')]);original=copy.deepcopy(p)
 f=resolve(p)['facts'];assert f['ram']['numeric']==16 and f['ram']['status']=='recorded';assert f['ram_max']['numeric']==32 and f['vram']['numeric']==2
 assert f['weight']['numeric']==1.4 and f['cpu']['value']=='10510U' and f['cpu_generation']['value']=='نسل دهم'
 assert f['storage']['numeric']==512 and f['storage_type']['value']=='SSD';assert p==original
 assert matches(p,'ظرفیت حافظه RAM','16 GB') and not matches(p,'ram','32 GB')
 p['specs'].append({'key':'حافظه رم','value':'8 GB'});assert resolve(p)['facts']['ram']['status']=='conflict' and not matches(p,'ram','16 GB')

def test_offer_upgrade_never_leaks_and_scopes_are_preserved():
 p=product([('حافظه RAM','16 GB')]);a=dict(p,offer_id='a',seller_title='RAM 32 GB upgraded');b=dict(p,offer_id='b',seller_title='original')
 assert resolve(a)['facts']['ram']['numeric']==16 and offer_claims(a)[0]['numeric']==32
 assert not matches(a,'ram','16 GB') and not matches(a,'ram','32 GB') and matches(b,'ram','16 GB')

def test_category_boundaries_and_title_facts():
 for cat,expected in [('سیم و کابل برق ساختمان','cable'),('دوربین مدار بسته','camera'),('ژنراتور و موتور برق','industrial'),('قهوه و اسپرسو ساز','appliance'),('کتانی و کفش اسپرت','clothing'),('کیف و کاور گوشی','generic')]:
  p=product([],cat);r=resolve(p);assert category(p)==expected;assert 'ram' not in r['schema'] and 'cpu' not in r['schema']
 p=product([],title='لپ تاپ i7-10510U 16-512GB SSD');f=resolve(p)['facts'];assert f['ram']['source']=='listing_title' and f['ram']['numeric']==16 and f['storage']['numeric']==512
 assert scalar('2.5 mm2','سطح مقطع','mm2')==2.5 and scalar('100 m','طول','mm')==100000

@pytest.mark.parametrize('model,bad_title',[('Surface Book 1','Surface Studio 2 features and specs'),('Surface Book 1','Surface Book 2 specifications'),('iPhone 13 Pro Max','iPhone 13 Pro specifications'),('WH-1000XM4','Sony WH-1000XM5 specs')])
def test_model_topic_cannot_be_established_by_sidebar(model,bad_title):
 p=product([],title=model);p['category_name']='گوشی موبایل' if 'iPhone' in model else 'لپ تاپ و نوت بوک'
 doc={'title':bad_title,'text':model+'\nResolution: 3000 x 2000\nBluetooth: 4.0','url':'https://support.microsoft.com/specs','status':'readable'}
 f,m=extract(p,doc);assert m['status']=='mismatch_or_unproven';assert all(x['status']!='accepted' for x in f)

def test_shared_fields_survive_variant_options_and_no_shipping_weight():
 p=product([],title='Surface Book 1');doc={'title':'Surface Book (1st Gen) specs and features','text':'Screen: 13.5 inches\nResolution: 3000 x 2000\nMemory: 8GB or 16GB RAM\nBluetooth 4.0\nShipping weight: 3 kg','url':'https://support.microsoft.com/specs','status':'readable'}
 f,m=extract(p,doc);assert any(v['field']=='bluetooth' and v['status']=='accepted' for v in f);assert all(v['status']!='accepted' for v in f if v['field'] in ('ram','weight'))

@pytest.mark.skipif(not os.getenv('TEST_CATALOG'),reason='full catalogue required')
def test_real_laptop_and_exact_iphone_regression_and_cross_consumers(tmp_path):
 from catalog import Catalog
 from buying_review import build_review
 from basic_specs import missing_fields
 c=Catalog(os.environ['TEST_CATALOG']);ids=['product_944b328308284a2adeeeb6d1','product_03f86fc7e3ad5666c24c54d2','product_ba20a2836e0902a8a85726c3'];d=build_comparison(c,[],ids)
 rows={r['id']:r for r in d['rows']}
 assert [v['numeric'] for v in rows['ram']['cells']]==[16,16,16]
 assert [v['numeric'] for v in rows['storage']['cells']]==[512,512,512]
 assert [v['numeric'] for v in rows['screen']['cells']]==[13.5,15.6,17.3]
 assert rows['cpu']['cells'][2]['value']=='10510U'
 iphones=['product_0992d40feccbbe4d390a5132','product_229874869305e78206e1837e','product_76d08413c6fa1d8efc4cea19','product_4152f716c35748e7c9d21940']
 p=c.product(iphones[0]);f=resolve(p)['facts'];assert f['ram']['numeric']==8 and f['storage']['numeric']==256 and f['weight']['numeric']==.167 and 'dimensions' in f
 assert f['cpu']['value'].startswith('Apple A18') and f['resolution']['value'].startswith('2532')
 assert f['os_launch']['value'].startswith('iOS 18.4') and f['os_family']['value']=='iOS'
 assert matches(p,'ظرفیت حافظه رم','8 GB') and 'ram' not in missing_fields(p)
 card=build_review(p,{'sources':[]},{'offers':[]},{});assert any(x['field']=='ram' and x['numeric']==8 for x in card['recorded_facts'])
 d=build_comparison(c,[],iphones);assert len([r for r in d['rows'] if r['id']=='ram'])==1
 assert identity(c.product(iphones[1]))=='iPhone 13 Pro Max'
 assert next(r for r in d['rows'] if r['id']=='resolution')['cells'][2]['status']=='recorded'
