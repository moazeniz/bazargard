"""Key facts about the Torob catalogue, computed from the database itself.

Used by docs/DATASET.md (bench/dataset_stats.py) and the in-app «درباره داده‌ها»
section (/api/dataset-facts). Computed once and cached next to the catalogue.
"""
import json,sqlite3,statistics,time
from collections import Counter
from datetime import datetime
from pathlib import Path

def _json(v,default):
    try:return json.loads(v) if v and v!='null' else default
    except ValueError:return default

def compute(path):
    c=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True)
    try:
        meta={k:_json(v,v) for k,v in c.execute('select key,value from metadata')}
        imported=datetime.fromisoformat(str(meta.get('imported_at')))
        shops=c.execute('select count(*) from shops').fetchone()[0]
        real=c.execute("select count(*) from shops where (instagram is not null and instagram!='null') or (website_url is not null and website_url!='null')").fetchone()[0]
        insta=c.execute("select count(*) from shops where instagram is not null and instagram!='null'").fetchone()[0]
        site=c.execute("select count(*) from shops where website_url is not null and website_url!='null'").fetchone()[0]
        coords=c.execute('select count(*) from shops where lat is not null and lon is not null').fetchone()[0]
        tehran=c.execute('select count(*) from shops where lat between 35 and 36 and lon between 50.8 and 52').fetchone()[0]
        hours=Counter();providers=Counter();credit_shops=0;phones=0
        for wh,po,contacts in c.execute('select weekly_hours,payment_options,contacts from shops'):
            days=_json(wh,{});declared=sum(1 for d in days.values() if isinstance(d,dict) and d.get('status')=='declared')
            hours['declared_any' if declared else 'none']+=1
            if declared==7:hours['all_week']+=1
            opts=_json(po,[]);inperson=[o for o in opts if isinstance(o,dict) and o.get('channel')=='in_person']
            if inperson:credit_shops+=1
            for o in opts:
                if isinstance(o,dict):providers[(o.get('provider_name') or '?')+' · '+(o.get('channel') or '?')]+=1
            if any(isinstance(x,dict) and x.get('channel')=='phone' for x in _json(contacts,[])):phones+=1
        areas=Counter(r[0] for r in c.execute("select location_label from shops where location_label is not null and trim(location_label)!=''"))
        offers=c.execute('select count(*) from offers').fetchone()[0]
        per_shop=[r[0] for r in c.execute('select count(*) from offers group by shop_id')]
        products_per_shop=[r[0] for r in c.execute('select count(distinct product_id) from offers group by shop_id')]
        outliers=c.execute('select sum(price_toman<1000),sum(price_toman>10000000000) from offers').fetchone()
        offered_products=c.execute('select count(distinct product_id) from offers').fetchone()[0]
        products=c.execute('select count(*) from products').fetchone()[0]
        categories=c.execute('select count(distinct category_name) from products').fetchone()[0]
        top_categories=c.execute('select p.category_name,count(*) n from offers o join products p on p.product_id=o.product_id group by p.category_name order by n desc limit 12').fetchall()
        branded=c.execute("select count(*) from products where brand_name is not null and brand_name not in ('','null')").fetchone()[0]
        prices=sorted(r[0] for r in c.execute('select price_toman from offers where price_toman>0'))
        ages=Counter();warranty=0;described=0
        for changed,w,desc in c.execute('select last_price_changed_at,warranty,description from offers'):
            try:days=(imported-datetime.fromisoformat(changed)).total_seconds()/86400
            except (TypeError,ValueError):ages['unknown']+=1;continue
            ages['≤7 days' if days<=7 else '≤30 days' if days<=30 else '≤90 days' if days<=90 else '≤365 days' if days<=365 else '>1 year']+=1
            if w and w!='null':warranty+=1
            if desc and desc!='null':described+=1
        q=lambda p:prices[min(len(prices)-1,int(len(prices)*p))]
        return {'computed_at':time.time(),'source':meta.get('source'),'imported_at':str(meta.get('imported_at')),
                'shops':{'total':shops,'real_identity':real,'instagram':insta,'website':site,'with_coordinates':coords,'in_tehran_box':tehran,
                         'with_declared_hours':hours['declared_any'],'hours_all_week':hours['all_week'],'without_hours':hours['none'],
                         'with_in_person_credit':credit_shops,'with_phone_field':phones,'top_areas':areas.most_common(12),'areas':len(areas)},
                'offers':{'total':offers,'per_shop_max':max(per_shop),'per_shop_median':statistics.median(per_shop),'shops_at_cap_300':sum(1 for n in per_shop if n>=300),
                          'price_min':prices[0],'price_median':q(.5),'price_p90':q(.9),'price_max':prices[-1],
                          'price_age':dict(ages),'with_warranty_text':warranty,'with_description':described,
                          'products_per_shop_max':max(products_per_shop),'shops_at_300_products':sum(1 for n in products_per_shop if n>=300),
                          'price_below_1000_toman':outliers[0],'price_above_10b_toman':outliers[1]},
                'products':{'total':products,'with_offers':offered_products,'categories':categories,'with_brand':branded,
                            'top_categories_by_offers':top_categories},
                'payment_providers':providers.most_common(12)}
    finally:c.close()

def facts(path,cache_dir=None):
    cache=Path(cache_dir or Path(path).parent)/'dataset-facts.json'
    try:
        if cache.exists() and cache.stat().st_mtime>=Path(path).stat().st_mtime:return json.loads(cache.read_text())
    except (OSError,ValueError):pass
    data=compute(path)
    try:cache.write_text(json.dumps(data,ensure_ascii=False))
    except OSError:pass
    return data
