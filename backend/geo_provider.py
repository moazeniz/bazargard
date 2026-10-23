"""Small, cached, read-only Overpass queries for this Tehran hackathon demo.
No user query, credentials, arbitrary URL, or personal origin is forwarded.
"""
import asyncio,json,math,os,sqlite3,time
from pathlib import Path
import httpx
import obs,resilience

UPSTREAM=os.getenv('OVERPASS_URL','https://overpass.private.coffee/api/interpreter')
USER_AGENT=os.getenv('HTTP_USER_AGENT','Bazargard/4.1 (+'+(os.getenv('PUBLIC_BASE_URL') or 'https://bazargard-ai.liara.run')+')')
DATA_API='https://api.openstreetmap.org/api/0.6/map.json'
ATTRIBUTION={'label':'© OpenStreetMap contributors','url':'https://www.openstreetmap.org/copyright','license':'ODbL 1.0'}
TAGS={'name','name:fa','name:en','building','shop','entrance','level','addr:floor','addr:unit','addr:housenumber','addr:street','addr:full','wheelchair','access','highway','indoor','ref','door','automatic_door','building:levels','image'}

def coordinates(lat,lon):
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in (lat,lon)):raise ValueError('Invalid coordinates')
    if not 35<=lat<=36 or not 50.8<=lon<=52:raise ValueError('Only Tehran catalogue locations are supported')
    return round(lat,6),round(lon,6)

def query_for(lat,lon):
    lat,lon=coordinates(lat,lon);p=f'(around:180,{lat:.6f},{lon:.6f})'
    return '[out:json][timeout:5][maxsize:33554432];('+f'nwr{p}["shop"="mall"];way{p}["building"];node{p}["entrance"];node{p}["highway"="elevator"];'+');out body center geom 100;'

def map_elements(payload):
    """Convert a tiny OSM map snapshot; discard mapper metadata and irrelevant objects."""
    source=payload.get('elements',[]);nodes={e['id']:e for e in source if e.get('type')=='node' and isinstance(e.get('id'),int)};out=[]
    for e in source:
        tags=e.get('tags',{})
        if not (tags.get('building') or tags.get('shop')=='mall' or tags.get('entrance') or tags.get('highway')=='elevator'):continue
        d={k:e[k] for k in ('type','id','lat','lon','tags','nodes') if k in e}
        if e.get('type')=='way':
            geometry=[{k:nodes[nid][k] for k in ('lat','lon')} for nid in e.get('nodes',[]) if nid in nodes]
            if len(geometry)!=len(e.get('nodes',[])):continue
            if geometry:
                d['geometry']=geometry;d['center']={'lat':sum(x['lat'] for x in geometry)/len(geometry),'lon':sum(x['lon'] for x in geometry)/len(geometry)}
        out.append(d)
    return out[:100]

class GeoProvider:
    MIN_INTERVAL=2.0
    def __init__(self,path,transport=None):
        self.path=str(path);Path(path).parent.mkdir(parents=True,exist_ok=True);self.transport=transport;self.lock=asyncio.Lock();self.last_call=0
        with self.conn() as c:c.executescript('CREATE TABLE IF NOT EXISTS osm_cache(key TEXT PRIMARY KEY, fetched REAL, payload TEXT);CREATE TABLE IF NOT EXISTS osm_calls(day TEXT PRIMARY KEY,count INTEGER);CREATE TABLE IF NOT EXISTS bbox_calls(day TEXT PRIMARY KEY,count INTEGER);')
    def conn(self):return sqlite3.connect(self.path,timeout=10)
    async def fetch(self,lat,lon):
        lat,lon=coordinates(lat,lon);key=f'v22:{lat:.6f},{lon:.6f}'
        async with self.lock:
            now=time.time()
            with self.conn() as c:row=c.execute('select fetched,payload from osm_cache where key=?',(key,)).fetchone()
            if row:
                data=json.loads(row[1]);ttl=data.get('ttl',86400) if data['status']=='ok' else 900
                if now-row[0]<ttl:return dict(data,cache_hit=True)
            day=time.strftime('%Y-%m-%d',time.gmtime())
            with self.conn() as c:
                c.execute('insert or ignore into osm_calls values(?,0)',(day,));count=c.execute('select count from osm_calls where day=?',(day,)).fetchone()[0]
                if count>=200:return {'status':'daily_limit','elements':[],'fetched_at':now,'attribution':ATTRIBUTION,'cache_hit':False}
                c.execute('update osm_calls set count=count+1 where day=?',(day,))
            await asyncio.sleep(max(0,self.MIN_INTERVAL-(time.monotonic()-self.last_call)));self.last_call=time.monotonic()
            status='unavailable';elements=[];map_at=None;provider='Overpass / Private.coffee'
            # The public Overpass mirror is often unreachable: its breaker skips it fast.
            overpass=resilience.breaker('overpass',3,600);skipped=not overpass.allow();started=time.monotonic()
            async with httpx.AsyncClient(timeout=6,transport=self.transport,follow_redirects=False) as client:
                try:
                    if skipped:raise ValueError('circuit open')
                    async with client.stream('POST',UPSTREAM,data={'data':query_for(lat,lon)},headers={'User-Agent':USER_AGENT,'Accept':'application/json'}) as response:
                        if response.status_code==429:status='busy'
                        else:
                            response.raise_for_status();raw=bytearray()
                            async for chunk in response.aiter_bytes():
                                raw.extend(chunk)
                                if len(raw)>1_500_000:raise ValueError('Map response too large')
                            payload=json.loads(raw)
                            if payload.get('remark'):raise ValueError('Partial Overpass response')
                            elements=payload.get('elements',[])[:100]
                            map_at=payload.get('osm3s',{}).get('timestamp_osm_base');status='ok'
                except (httpx.HTTPError,ValueError,TypeError,KeyError):pass
            if not skipped:
                if status in ('ok','busy'):overpass.success()
                else:overpass.failure()
                obs.log('external.call','info' if status=='ok' else 'warn',status,upstream='overpass',duration_ms=round((time.monotonic()-started)*1000),circuit=overpass.state)
            # Demo-only bounded snapshot fallback: at most 20 tiny boxes/day, cached 7 days.
            # The editing API is never used for writes, bulk import, or map-tile serving.
            if status=='unavailable':
                with self.conn() as c:
                    c.execute('insert or ignore into bbox_calls values(?,0)',(day,));box_count=c.execute('select count from bbox_calls where day=?',(day,)).fetchone()[0]
                    if box_count<20:c.execute('update bbox_calls set count=count+1 where day=?',(day,))
                if box_count<20:
                    dy=180/111320;dx=dy/math.cos(math.radians(lat));bbox=f'{lon-dx:.7f},{lat-dy:.7f},{lon+dx:.7f},{lat+dy:.7f}'
                    started=time.monotonic()
                    try:
                        async with httpx.AsyncClient(timeout=9,transport=self.transport,follow_redirects=False) as client:
                            async with client.stream('GET',DATA_API,params={'bbox':bbox},headers={'User-Agent':USER_AGENT,'Accept':'application/json'}) as response:
                                response.raise_for_status();raw=bytearray()
                                async for chunk in response.aiter_bytes():
                                    raw.extend(chunk)
                                    if len(raw)>1_500_000:raise ValueError('Map box too large')
                                elements=map_elements(json.loads(raw));status='ok';provider='OSM bounded map snapshot'
                    except (httpx.HTTPError,ValueError,TypeError,KeyError) as e:obs.log('external.call','warn',type(e).__name__,upstream='osm_map',error=str(e),duration_ms=round((time.monotonic()-started)*1000))
                    else:obs.log('external.call','info','ok',upstream='osm_map',duration_ms=round((time.monotonic()-started)*1000))
            sanitized=[]
            for item in elements:
                if item.get('type') not in ('node','way','relation') or not isinstance(item.get('id'),int) or item['id']<=0:continue
                d={k:item[k] for k in ('type','id','lat','lon','center') if k in item}
                d['tags']={k:str(v)[:500] for k,v in item.get('tags',{}).items() if k in TAGS}
                if isinstance(item.get('geometry'),list):d['geometry']=item['geometry'][:1500]
                if isinstance(item.get('nodes'),list):d['nodes']=item['nodes'][:1500]
                sanitized.append(d)
            elements=sanitized
            data={'status':status,'elements':elements if status=='ok' else [],'fetched_at':time.time(),'map_data_at':map_at,'radius_m':180,'attribution':ATTRIBUTION,'cache_hit':False,'provider':provider,'ttl':604800 if provider=='OSM bounded map snapshot' else 86400}
            with self.conn() as c:
                c.execute('insert or replace into osm_cache values(?,?,?)',(key,time.time(),json.dumps(data,ensure_ascii=False)))
                c.execute('delete from osm_cache where fetched<?',(now-30*86400,));c.execute('delete from bbox_calls where day<?',(time.strftime('%Y-%m-%d',time.gmtime(now-7*86400)),));c.execute('delete from osm_calls where day<?',(time.strftime('%Y-%m-%d',time.gmtime(now-7*86400)),))
            return data
