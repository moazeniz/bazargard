"""Source-separated catalogue/seller details and nearby open-map context."""
import asyncio,copy,json,os,re,time
from urllib.parse import urlencode
import httpx
from catalog import normalize,haversine
from evidence import SellerReports
from geo_provider import ATTRIBUTION,coordinates,GeoProvider
from pathlib import Path
import resilience


def dataset_details(address):
    text=str(address or '');out={'address':text,'floor':None,'unit':None,'building_hint':None,'source':'torob_dataset','quotes':[]}
    patterns={'floor':r'(?:طبقه\s*(?:ی\s*)?)([^،,؛;\n]{1,30}?)(?=\s*(?:واحد|پلاک|غرفه|حجره|،|,|؛|;|$))',
              'unit':r'(?:واحد|غرفه|حجره)\s*(?:شماره\s*)?([\w\u200c/-]{1,20})',
              'building_hint':r'(?:پاساژ|مجتمع|مرکز\s*خرید|ساختمان)\s+[^،,؛;\n]{1,65}'}
    for key,pattern in patterns.items():
        match=re.search(pattern,text)
        if match:
            value=match.group(1).strip() if key!='building_hint' else re.split(r'\s+(?:طبقه|واحد|پلاک)\b',match.group(0))[0].strip()
            out[key]=value;out['quotes'].append({'field':key,'quote':match.group(0)})
    return out


def contains(lat,lon,geometry):
    try:
        if len(geometry)<4 or geometry[0]!=geometry[-1]:return False
        inside=False;j=len(geometry)-1
        for i,a in enumerate(geometry):
            b=geometry[j];ay,ax=float(a['lat']),float(a['lon']);by,bx=float(b['lat']),float(b['lon'])
            if ((ay>lat)!=(by>lat)) and lon<(bx-ax)*(lat-ay)/(by-ay)+ax:inside=not inside
            j=i
        return inside
    except (ValueError,TypeError,KeyError):return False


def nearby_context(shop,data):
    lat,lon=shop.get('lat'),shop.get('lon');features=[];enclosing=set();objects=data.get('elements',[])
    for e in objects:
        if e['type']=='way' and contains(lat,lon,e.get('geometry',[])):enclosing.update(e.get('nodes',[]))
    for e in objects:
        tags=e.get('tags',{});point=e if 'lat' in e else e.get('center',{})
        if point.get('lat') is None or point.get('lon') is None:continue
        distance=haversine(lat,lon,point['lat'],point['lon'])
        if distance is None:continue
        is_inside=e['type']=='way' and contains(lat,lon,e.get('geometry',[]))
        kind='entrance' if tags.get('entrance') else 'elevator' if tags.get('highway')=='elevator' else 'mall' if tags.get('shop')=='mall' else 'building' if tags.get('building') else None
        if not kind or (kind=='building' and not is_inside and not tags.get('name') and not tags.get('name:fa')):continue
        if distance>.3 and not is_inside:continue
        url=f"https://www.openstreetmap.org/{e['type']}/{e['id']}"
        title=tags.get('name:fa') or tags.get('name') or tags.get('name:en') or {'entrance':'ورودی ثبت‌شده','elevator':'آسانسور ثبت‌شده','mall':'مرکز خرید ثبت‌شده','building':'ساختمان پیرامون مختصات'}[kind]
        image=tags.get('image','');image=image if image.startswith('https://') else None
        features.append({'type':kind,'title':title,'osm_id':f"{e['type']}/{e['id']}",'source_url':url,'lat':point['lat'],'lon':point['lon'],'distance_m':round(distance*1000),'relationship':'coordinate_within_building' if is_inside else 'entrance_of_enclosing_building' if kind=='entrance' and e['id'] in enclosing else 'nearby_only',
            'level':tags.get('level') or tags.get('addr:floor'),'unit':tags.get('addr:unit'),'ref':tags.get('ref'),'entrance_kind':tags.get('entrance'),'wheelchair':tags.get('wheelchair'),'access':tags.get('access'),'building_levels':tags.get('building:levels'),'image_url':image,
            'map_url':'https://www.openstreetmap.org/?'+urlencode({'mlat':point['lat'],'mlon':point['lon']})+f"#map=19/{point['lat']}/{point['lon']}",
            'tags':{k:v for k,v in tags.items() if k in ('name','name:fa','entrance','level','addr:floor','addr:unit','wheelchair','access','ref','highway','building:levels')}})
    features.sort(key=lambda x:(0 if x['relationship']=='coordinate_within_building' else 1 if x['relationship']=='entrance_of_enclosing_building' else 2,x['distance_m']))
    return {'status':data.get('status','not_requested'),'features':features[:12],'fetched_at':data.get('fetched_at'),'map_data_at':data.get('map_data_at'),'cache_hit':data.get('cache_hit',False),'radius_m':180,'provider':data.get('provider'),'ttl':data.get('ttl',86400),'attribution':ATTRIBUTION,
            'notice':'این‌ها عوارض نقشه پیرامون مختصات فروشگاه‌اند؛ نزدیکی یا قرارگرفتن مختصات در ساختمان، تطبیق هویت فروشگاه یا مسیر داخل پاساژ را ثابت نمی‌کند. طبقه و واحد این عوارض به فروشگاه نسبت داده نمی‌شود.'}

class Locations:
    def __init__(self,cat,mem):
        self.cat=cat;self.mem=mem;self.locks={}
        # Open-map context is queried directly (OSM is reachable from Iran); no relay.
        self.geo=GeoProvider(Path(mem.path).parent/'places.db')
        with mem.conn() as c:c.execute('CREATE TABLE IF NOT EXISTS location_cache(shop_id TEXT PRIMARY KEY,lat REAL,lon REAL,fetched REAL,payload TEXT)')
    def cached(self,shop):
        with self.mem.conn() as c:row=c.execute('select * from location_cache where shop_id=?',(shop['shop_id'],)).fetchone()
        if not row:return None
        if (row['lat'],row['lon'])!=(shop.get('lat'),shop.get('lon')):return None
        data=json.loads(row['payload']);data['cache_hit']=True;data['stale']=time.time()-row['fetched']>=data.get('ttl',86400);return data
    def result(self,u,sid,shop_id):
        self.mem.session(u,sid);shop=self.cat.shop(shop_id)
        if not shop:raise ValueError('فروشگاه پیدا نشد')
        return {'shop_id':shop_id,'address':shop['address'],'dataset':dataset_details(shop['address']),'seller':SellerReports(self.mem.events(u,sid)).indoor(shop_id),'osm':self.cached(shop) or {'status':'not_requested','features':[],'attribution':ATTRIBUTION},'cost':'بدون کلید یا API پولی جدید؛ سرویس عمومی نقشه محدودیت ظرفیت دارد.'}
    async def enrich(self,u,sid,shop_id):
        self.mem.session(u,sid);shop=self.cat.shop(shop_id)
        if not shop:raise ValueError('فروشگاه پیدا نشد')
        async with self.locks.setdefault(shop_id,asyncio.Lock()):
            cached=self.cached(shop)
            ttl=cached.get('ttl',86400) if cached and cached['status']=='ok' else 900
            if cached and time.time()-(cached.get('fetched_at') or 0)<ttl:return self.result(u,sid,shop_id)
            try:lat,lon=coordinates(shop.get('lat'),shop.get('lon'))
            except ValueError:
                data={'status':'no_coordinates','elements':[],'fetched_at':time.time()}
            else:
                try:data=await asyncio.wait_for(self.geo.fetch(lat,lon),24)
                except (httpx.HTTPError,ValueError,TypeError,KeyError,TimeoutError,asyncio.TimeoutError):data={'status':'unavailable','elements':[],'fetched_at':time.time()}
                if data.get('status')!='ok' and os.getenv('RELAY_URL') and os.getenv('RELAY_TOKEN'):
                    # Optional fallback through the relay/gate place-context endpoint.
                    try:
                        async with httpx.AsyncClient(timeout=24) as client:
                            r=await resilience.call('relay_place_context',lambda:client.post(os.getenv('RELAY_URL').rstrip('/')+'/place-context',headers={'Authorization':'Bearer '+os.getenv('RELAY_TOKEN')},json={'lat':lat,'lon':lon}),attempts=2,timeout=24);r.raise_for_status();data=r.json()
                    except (httpx.HTTPError,ValueError,resilience.CircuitOpen,asyncio.TimeoutError):pass
            if data.get('status') not in ('ok','unavailable','busy','daily_limit','no_coordinates'):data={'status':'unavailable','elements':[],'fetched_at':time.time()}
            context=nearby_context(shop,data)
            if context['status']!='ok' and cached and cached['status']=='ok':
                result=self.result(u,sid,shop_id);result['osm']['stale']=True;result['osm']['refresh_status']=context['status'];return result
            with self.mem.conn() as c:c.execute('insert or replace into location_cache values(?,?,?,?,?)',(shop_id,shop.get('lat'),shop.get('lon'),time.time(),json.dumps(context,ensure_ascii=False)))
            return self.result(u,sid,shop_id)
