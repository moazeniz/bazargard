"""Offline, atomic, repeatable canonical projection and all-category quality audit.

python backend/build_product_index.py --catalog data/catalog.db --output data/canonical-products.db --report /owned/evidence
Reads raw catalogue only; no private memory or network calls. Safe to rerun after
imports/rule changes. Requests can use bounded per-product fallback meanwhile.
"""
import argparse,sqlite3,json,time,os,zlib,hashlib
from pathlib import Path
from collections import Counter,defaultdict
from product_data import resolve,projection,fingerprint,VERSION,FIELDS,ALIASES
from catalog import normalize

def build(catalog,output,report):
 start=time.monotonic();output=Path(output);output.parent.mkdir(parents=True,exist_ok=True);temp=output.with_suffix('.building.db');temp.unlink(missing_ok=True)
 db=sqlite3.connect(temp);db.execute('CREATE TABLE product_facts(product_id TEXT PRIMARY KEY,fingerprint TEXT,payload BLOB)');db.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT)')
 source=sqlite3.connect('file:'+str(Path(catalog).resolve())+'?mode=ro',uri=True);source.row_factory=sqlite3.Row
 cats=defaultdict(lambda:{'products':0,'raw_label_presence':Counter(),'resolved_fields':Counter(),'numeric_fields':Counter(),'title_recovered':Counter(),'conflicts':Counter(),'unmapped_keys':Counter(),'unsupported_units':Counter()});totals=Counter();unknown=Counter();entities=defaultdict(list)
 legacy={'ram':['ظرفیت حافظه رم','ظرفیت رم','حافظه رم'],'screen':['اندازه نمایشگر','اندازه صفحه نمایش','سایز صفحه نمایش (اینچ)'],'storage':['ظرفیت حافظه داخلی'],'cpu':['مدل پردازنده'],'weight':['وزن','وزن (کیلوگرم)']}
 for row in source.execute('SELECT product_id,persian_title,brand_name,category_name,specs FROM products ORDER BY product_id'):
  p=dict(row);p['specs']=json.loads(p['specs'] or '[]');r=resolve(p);projection_data=projection(p)
  db.execute('INSERT INTO product_facts VALUES(?,?,?)',(p['product_id'],fingerprint(p),zlib.compress(json.dumps(projection_data,ensure_ascii=False,separators=(',',':')).encode())))
  group=cats[p['category_name']];group['products']+=1;totals['products']+=1;totals['source_spec_entries']+=len(p['specs'])
  keys={normalize(s.get('key')) for s in p['specs']}
  for k,names in legacy.items():
   if keys.intersection(map(normalize,names)):group['raw_label_presence'][k]+=1
  for field,f in r['facts'].items():
   if f['status']=='conflict':group['conflicts'][field]+=1
   elif f['typed_status']=='resolved':
    group['resolved_fields'][field]+=1
    if f['numeric'] is not None:group['numeric_fields'][field]+=1
    if f['source']=='listing_title':group['title_recovered'][field]+=1
   else:group['unsupported_units'][field]+=1
  for e in r['unmapped']:group['unmapped_keys'][normalize(e['key'])]+=1;unknown[normalize(e['key'])]+=1
  totals.update(r['quality'])
  # Candidate duplicates only: retain IDs and full listing/configuration title.
  entity=normalize(p['persian_title'])
  if entity:entities[entity].append(p['product_id'])
  if totals['products']%10000==0:db.commit();print(totals['products'],flush=True)
 db.execute('INSERT INTO metadata VALUES(?,?)',('version',VERSION));db.commit();assert db.execute('select count(*) from product_facts').fetchone()[0]==totals['products'];db.close();os.replace(temp,output)
 integrity={}
 for name,sql in {'orphan_products':'SELECT count(*) FROM offers o LEFT JOIN products p ON p.product_id=o.product_id WHERE p.product_id IS NULL','orphan_shops':'SELECT count(*) FROM offers o LEFT JOIN shops s ON s.shop_id=o.shop_id WHERE s.shop_id IS NULL'}.items():integrity[name]=source.execute(sql).fetchone()[0]
 source.close();report=Path(report);report.mkdir(parents=True,exist_ok=True)
 def convert(g):return {k:dict(v) if isinstance(v,Counter) else v for k,v in g.items()}
 (report/'canonical-category-coverage.json').write_text(json.dumps({k:convert(v) for k,v in cats.items()},ensure_ascii=False,indent=2))
 duplicates=[{'normalized_listing':key,'product_ids':ids} for key,ids in entities.items() if len(ids)>1]
 (report/'duplicate-candidates.json').write_text(json.dumps(duplicates,ensure_ascii=False))
 summary={'version':VERSION,'population':'All supplied products; one typed fact per product/field. Raw label presence is a separate baseline and is not typed correctness. Title claims remain listing scoped. Conflicts excluded from resolved counts. No web or seller upgrades in this index.','totals':dict(totals),'categories':len(cats),'unknown_top_fields':unknown.most_common(100),'integrity':integrity,'duplicate_candidate_groups':len(duplicates),'ids_merged':0,'seconds':round(time.monotonic()-start,2),'index_bytes':output.stat().st_size}
 (report/'canonical-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2));print(json.dumps(summary,ensure_ascii=False),flush=True)

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--catalog',required=True);parser.add_argument('--output',required=True);parser.add_argument('--report',required=True);args=parser.parse_args();build(args.catalog,args.output,args.report)
