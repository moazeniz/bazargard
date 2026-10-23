"""Isolated browser identities, source-backed facts, resumable conversations and seller events."""
import sqlite3,json,time,secrets,math
from catalog import ClosingConnection
from pathlib import Path

FIELDS={'budget_max_toman','budget_min_toman','installment_only','payment_provider','neighborhood','lat','lon','radius_km','needs','brand','priority','open_now','travel_mode','visit_at','product_query','recipient','spec_filters'}

def validate_profile(p):
    out={}
    for k,v in p.items():
        if k not in FIELDS:continue
        if v is None or v=='':out[k]=None;continue
        if k in ['lat','lon','budget_max_toman','budget_min_toman','radius_km']:
            v=float(v)
            bounds={'lat':(-90,90),'lon':(-180,180),'radius_km':(.1,100),'budget_max_toman':(0,1e13),'budget_min_toman':(0,1e13)}
            if not bounds[k][0]<=v<=bounds[k][1]:raise ValueError('مقدار خارج از محدوده است')
        elif k in ['installment_only','open_now']:
            if not isinstance(v,bool):raise ValueError('Boolean required')
        elif k=='priority' and v not in ['balanced','price','distance']:raise ValueError('Invalid priority')
        elif k=='travel_mode' and v not in ['driving','walking','transit']:raise ValueError('Invalid mode')
        elif k=='spec_filters':
            if not isinstance(v,dict) or len(v)>8:raise ValueError('Invalid specification filters')
            v={str(a)[:100]:str(b)[:200] for a,b in v.items()}
        else:v=str(v)[:500]
        out[k]=v
    return out

class Memory:
    def __init__(self,path):
        self.path=str(path);Path(path).parent.mkdir(parents=True,exist_ok=True)
        with self.conn() as c:c.executescript('''PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,created REAL);
        CREATE TABLE IF NOT EXISTS facts(user_id TEXT,key TEXT,value TEXT,quote TEXT,source TEXT,updated REAL,PRIMARY KEY(user_id,key));
        CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,user_id TEXT,title TEXT,created REAL,updated REAL,summary TEXT DEFAULT '',last_results TEXT DEFAULT '{}');
        CREATE TABLE IF NOT EXISTS turns(id INTEGER PRIMARY KEY,user_id TEXT,session_id TEXT,role TEXT,content TEXT,created REAL);
        CREATE INDEX IF NOT EXISTS turns_session ON turns(session_id,id);
        CREATE TABLE IF NOT EXISTS seller_events(id INTEGER PRIMARY KEY,user_id TEXT,session_id TEXT,shop_id TEXT,product_id TEXT,status TEXT,price REAL,available INTEGER,hours TEXT,note TEXT,created REAL,source TEXT);
        CREATE TABLE IF NOT EXISTS merchant_requests(token TEXT PRIMARY KEY,user_id TEXT,session_id TEXT,shop_id TEXT,product_id TEXT,offer_id TEXT,created REAL,expires REAL,requested_hold_until TEXT,response TEXT);
        CREATE TABLE IF NOT EXISTS basket_items(id TEXT PRIMARY KEY,user_id TEXT,session_id TEXT,query TEXT,product_id TEXT,quantity INTEGER);
        CREATE TABLE IF NOT EXISTS comparison_snapshots(id TEXT PRIMARY KEY,user_id TEXT,session_id TEXT,created REAL,payload TEXT);
        CREATE INDEX IF NOT EXISTS comparison_owner ON comparison_snapshots(user_id,session_id);
        CREATE TABLE IF NOT EXISTS shopper_profiles(id TEXT PRIMARY KEY,user_id TEXT,name TEXT,preferences TEXT);
        ''')
        with self.conn() as c:
            for table,column,definition in [('turns','references_json',"TEXT DEFAULT '{}'"),('sessions','trip_preferences',"TEXT DEFAULT '{}'"),('sessions','mission',"TEXT DEFAULT '{}'"),('sessions','kind',"TEXT DEFAULT 'shopping'"),('sessions','chat_mode',"TEXT DEFAULT 'simple'"),('seller_events','details',"TEXT DEFAULT '{}'" )]:
                if column not in [r['name'] for r in c.execute('PRAGMA table_info('+table+')')]:c.execute('ALTER TABLE '+table+' ADD COLUMN '+column+' '+definition)
    def conn(self):
        c=sqlite3.connect(self.path,timeout=20,factory=ClosingConnection);c.row_factory=sqlite3.Row;return c
    def user(self,token=None):
        with self.conn() as c:
            if token and c.execute('select 1 from users where id=?',(token,)).fetchone():return token
            token=secrets.token_urlsafe(32);c.execute('insert into users values(?,?)',(token,time.time()));return token
    def session(self,uid,sid=None,kind='shopping'):
        with self.conn() as c:
            if sid:
                r=c.execute('select * from sessions where id=? and user_id=?',(sid,uid)).fetchone()
                if not r:raise KeyError('Conversation not found')
                return dict(r)
            sid=secrets.token_urlsafe(20);now=time.time();c.execute('insert into sessions(id,user_id,title,created,updated,kind) values(?,?,?,?,?,?)',(sid,uid,'مدیریت وب‌سایت' if kind=='ops' else 'خرید تازه',now,now,kind))
        return self.session(uid,sid)
    def sessions(self,uid,kind='shopping'):
        with self.conn() as c:return [dict(x) for x in c.execute('select id,title,updated from sessions where user_id=? and kind=? order by updated desc limit 30',(uid,kind))]
    def facts(self,uid,sid=None):
        with self.conn() as c:out={x['key']:dict(x)|{'value':json.loads(x['value']),'scope':'memory'} for x in c.execute('select key,value,quote,source,updated from facts where user_id=?',(uid,))}
        if sid:
            for key,fact in json.loads(self.session(uid,sid)['trip_preferences']).items():
                if fact['value'] is None:out.pop(key,None)
                else:out[key]=dict(fact,key=key,scope='trip')
        return list(out.values())
    def profile(self,uid,sid=None):return {x['key']:x['value'] for x in self.facts(uid,sid)}
    def update(self,uid,updates,source='form',message='',sid=None):
        accepted=[]
        for fact in updates:
            quote=str(fact.get('quote',''))
            if source=='chat' and (not quote or quote not in message):continue
            validated=validate_profile({fact['key']:fact.get('value')})
            if not validated:continue
            key,value=next(iter(validated.items()))
            if sid:
                s=self.session(uid,sid);trip=json.loads(s['trip_preferences'])
                trip[key]={'value':value,'quote':quote,'source':source,'updated':time.time()}
                if key=='neighborhood' and value is not None:
                    for k in ('lat','lon'):trip[k]={'value':None,'quote':quote,'source':source,'updated':time.time()}
                if key=='installment_only' and value is False:trip['payment_provider']={'value':None,'quote':quote,'source':source,'updated':time.time()}
                with self.conn() as c:c.execute('update sessions set trip_preferences=? where id=?',(json.dumps(trip,ensure_ascii=False),sid))
                accepted.append(key);continue
            with self.conn() as c:
                if value is None:c.execute('delete from facts where user_id=? and key=?',(uid,key))
                else:c.execute('insert into facts values(?,?,?,?,?,?) on conflict(user_id,key) do update set value=excluded.value,quote=excluded.quote,source=excluded.source,updated=excluded.updated',(uid,key,json.dumps(value,ensure_ascii=False),quote,source,time.time()))
            if key=='neighborhood' and value is not None:
                with self.conn() as c:c.execute("delete from facts where user_id=? and key in ('lat','lon')",(uid,))
            if key=='installment_only' and value is False:
                with self.conn() as c:c.execute("delete from facts where user_id=? and key='payment_provider'",(uid,))
            accepted.append(key)
        return {'saved':accepted,'profile':self.profile(uid,sid)}
    def turns(self,uid,sid,limit=20):
        self.session(uid,sid)
        with self.conn() as c:return [dict(x)|{'references':json.loads(x['references_json'] or '{}')} for x in reversed(c.execute('select role,content,created,references_json from turns where session_id=? and user_id=? order by id desc limit ?',(sid,uid,limit)).fetchall())]
    def clear_trip_overrides(self,uid,sid,keys):
        trip=json.loads(self.session(uid,sid)['trip_preferences'])
        for key in keys:trip.pop(key,None)
        if 'neighborhood' in keys:
            for key in ('lat','lon'):trip.pop(key,None)
        with self.conn() as c:c.execute('update sessions set trip_preferences=? where id=?',(json.dumps(trip,ensure_ascii=False),sid))
    def save_turn(self,uid,sid,role,content,references=None):
        self.session(uid,sid);now=time.time()
        with self.conn() as c:
            c.execute('insert into turns(user_id,session_id,role,content,created,references_json) values(?,?,?,?,?,?)',(uid,sid,role,content,now,json.dumps(references or {},ensure_ascii=False)))
            c.execute('update sessions set updated=? where id=?',(now,sid))
            if role=='user':c.execute("update sessions set title=? where id=? and title='خرید تازه'",(content[:55],sid))
    def results(self,uid,sid,result=None):
        s=self.session(uid,sid)
        if result is None:return json.loads(s['last_results'])
        with self.conn() as c:c.execute('update sessions set last_results=? where id=?',(json.dumps(result,ensure_ascii=False),sid))
    def save_comparison(self,uid,sid,payload):
        self.session(uid,sid)
        token=secrets.token_urlsafe(24)
        data=dict(payload,snapshot_id=token)
        with self.conn() as c:c.execute('insert into comparison_snapshots values(?,?,?,?,?)',(token,uid,sid,time.time(),json.dumps(data,ensure_ascii=False)))
        return data
    def latest_comparison(self,uid,sid):
        self.session(uid,sid)
        with self.conn() as c:row=c.execute('select payload from comparison_snapshots where user_id=? and session_id=? order by created desc limit 1',(uid,sid)).fetchone()
        return json.loads(row['payload']) if row else None
    def comparison_snapshot(self,uid,sid,token):
        self.session(uid,sid)
        with self.conn() as c:row=c.execute('select payload from comparison_snapshots where id=? and user_id=? and session_id=?',(token,uid,sid)).fetchone()
        if not row:raise ValueError('نسخه مقایسه در این گفت‌وگو پیدا نشد؛ دوباره مقایسه بسازید.')
        return json.loads(row['payload'])
    def event(self,uid,sid,data):
        self.session(uid,sid)
        if data['status'] not in ['reported','no_answer','closed','unavailable']:raise ValueError('Invalid seller status')
        price=data.get('price')
        if price is not None and (not isinstance(price,(int,float)) or not math.isfinite(price) or not 0<=price<=1e13):raise ValueError('Invalid reported price')
        with self.conn() as c:
            c.execute('insert into seller_events(user_id,session_id,shop_id,product_id,status,price,available,hours,note,created,source,details) values(?,?,?,?,?,?,?,?,?,?,?,?)',
            (uid,sid,data['shop_id'],data.get('product_id'),data['status'],price,data.get('available'),str(data.get('hours',''))[:300],str(data.get('note',''))[:2000],time.time(),data.get('source','user_reported'),json.dumps(data.get('details',{}),ensure_ascii=False)))
    def events(self,uid,sid):
        self.session(uid,sid)
        with self.conn() as c:return [dict(x)|{'details':json.loads(x['details'] or '{}')} for x in c.execute('select * from seller_events where user_id=? and session_id=? order by id desc',(uid,sid))]
    def clear(self,uid,all_data=False):
        with self.conn() as c:
            c.execute('delete from facts where user_id=?',(uid,))
            if all_data:
                if c.execute("select 1 from sqlite_master where type='table' and name='spec_fetch_jobs'").fetchone():c.execute('delete from spec_fetch_jobs where user_id=?',(uid,))
                for table in ['seller_events','turns','sessions','merchant_requests','basket_items','shopper_profiles','comparison_snapshots']:c.execute('delete from '+table+' where user_id=?',(uid,))
                for table in ('clarification_state','assistant_run_events','assistant_runs','ops_actions','ops_audit'):
                    if c.execute("select 1 from sqlite_master where type='table' and name=?",(table,)).fetchone():c.execute('delete from '+table+' where user_id=?',(uid,))
            else:
                c.execute("update sessions set trip_preferences='{}' where user_id=?",(uid,))
                if c.execute("select 1 from sqlite_master where name='clarification_state'").fetchone():c.execute('delete from clarification_state where user_id=?',(uid,))
