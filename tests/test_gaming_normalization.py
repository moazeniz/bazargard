"""Public production catalogue capture supplied with the human's 2.5.0 regression."""
import sys,json,copy
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from product_data import resolve,matches,cell
from comparison import build_comparison
PRODUCTS=json.loads((Path(__file__).parent/'fixtures/gaming_catalogue.json').read_text())['products']

def test_five_exact_skus_existing_facts_reach_main_comparison_before_network():
 class Cat:
  def product(self,pid):return next(p for p in PRODUCTS if p['product_id']==pid)
 rows={r['id']:r for r in build_comparison(Cat(),[],[p['product_id'] for p in PRODUCTS])['rows']}
 expected={'ram':[16,16,16,16,12],'storage':[512,512,1024,512,512],'screen':[16.1,15.6,15.6,15.6,15.6],'vram':[6,8,6,4,6],'battery':[70,70,60,53.5,52.5]}
 for field,values in expected.items():
  assert [c['numeric'] for c in rows[field]['cells']]==values,field
 for field in ('brand','cpu','gpu','ram_type','panel','dimensions','usb_a','usb_c','hdmi'):
  # HDMI on the final SKU is explicitly contained in the ports aggregate.
  cells=rows[field]['cells'][:4] if field=='hdmi' else rows[field]['cells']
  assert all(c['status']=='recorded' and c['raw'] for c in cells),field
 assert 'ports' in rows and rows['ports']['cells'][4]['status']=='recorded'
 assert 'cores' not in rows # CPU threads are not cable strands.
 assert all(c['status']!='conflict' for c in rows['brand']['cells']+rows['storage']['cells'])
 assert rows['dimensions']['cells'][0]['raw'][0]['original']['value']=='369x259.3x23.6'
 assert matches(PRODUCTS[4],'ظرفیت حافظه رم (RAM)','12GB')
 assert not matches(PRODUCTS[4],'ram','32GB')
 assert matches(PRODUCTS[0],'سایز صفحه نمایش (اینچ)','16.1 inch')

def test_drive_presence_role_and_genuine_conflicts():
 p={'category_name':'لپ تاپ','specs':[{'key':'ظرفیت SSD','value':'512GB'},{'key':'ظرفیت HDD','value':'ندارد'}]}
 r=resolve(p)['facts'];assert r['storage']['numeric']==512 and r['hdd']['presence'] is False
 p['specs'][1]['value']='1TB';r=resolve(p)['facts'];assert r['storage']['numeric']==1536
 p['specs'].append({'key':'حافظه داخلی','value':'256GB'});assert resolve(p)['facts']['storage']['status']=='conflict'
 p['specs']=[{'key':'برند','value':'Lenovo'}];p['brand_name']='MSI';assert resolve(p)['facts']['brand']['status']=='conflict'

def test_qualifiers_units_and_category_boundaries():
 p={'category_name':'لپ تاپ','specs':[{'key':'ظرفیت حافظه رم (RAM)','value':'12GB'},{'key':'حداکثر حافظه رم','value':'64GB'},{'key':'ظرفیت باتری','value':'70 W'},{'key':'سایز صفحه نمایش','value':'16.1'}]}
 r=resolve(p)['facts'];assert r['ram']['numeric']==12 and r['ram_max']['numeric']==64
 assert 'battery' not in r and 'screen' not in r
 p={'category_name':'سیم و کابل برق','specs':[{'key':'تعداد رشته','value':'3'},{'key':'سطح مقطع','value':'2.5 mm2'}]}
 r=resolve(p)['facts'];assert r['cores']['value']=='3' and r['cross_section']['numeric']==2.5 and 'ram' not in r
