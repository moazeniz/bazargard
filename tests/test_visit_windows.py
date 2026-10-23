import sys,time
from pathlib import Path
from datetime import datetime,timedelta
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from catalog import TEHRAN
from evidence import SellerReports
from visit_checks import shopping_window,arrival_evidence


def at(day,hour,minute=0):return datetime(2026,10,day,hour,minute,tzinfo=TEHRAN)
def shop(hours):return {'shop_id':'s','weekly_hours':hours}
def shift(a,b):return {'status':'declared','shifts':[{'start_time':a,'end_time':b}]}


def test_full_shopping_interval_closing_split_shift_and_overnight():
    s=shop({'wed':shift('09:00','20:00')});reports=SellerReports()
    assert shopping_window(s,reports,at(21,19,50),at(21,20,5),TEHRAN)['state']=='closed'
    assert shopping_window(s,reports,at(21,19,50),at(21,20),TEHRAN)['state']=='open'
    s=shop({'wed':{'status':'declared','shifts':[{'start_time':'09:00','end_time':'12:00'},{'start_time':'12:10','end_time':'20:00'}]}})
    assert shopping_window(s,reports,at(21,11,55),at(21,12,15),TEHRAN)['state']=='closed'
    s=shop({'wed':shift('22:00','02:00')})
    assert shopping_window(s,reports,at(21,23,55),at(22,0,15),TEHRAN)['state']=='open'
    assert shopping_window(shop({}),reports,at(21,10),at(21,10,15),TEHRAN)['state']=='unknown'


def test_future_report_expiry_is_unknown_not_confirmation():
    start=at(21,10);end=start+timedelta(minutes=30)
    event={'id':1,'shop_id':'s','product_id':'p','status':'reported','source':'simulated','created':start.timestamp(),
           'price':100,'available':True,'hours':'open','details':{'valid_until':(start+timedelta(minutes=20)).isoformat(),'opening_intervals':[{'start':start.isoformat(),'end':(start+timedelta(hours=2)).isoformat()}]}}
    reports=SellerReports([event],now=start.timestamp()+1)
    result=shopping_window(shop({}),reports,start,end,TEHRAN)
    assert result['state']=='unknown' and result['source']=='simulated'
    check=arrival_evidence('s',reports,start,end,TEHRAN)[0]
    assert check['valid_at_arrival'] and not check['valid_through_shopping'] and check['needs_reconfirmation']
    event['details']['valid_until']=(start+timedelta(hours=2)).isoformat();event['details']['hold_until']=start.isoformat()
    assert arrival_evidence('s',SellerReports([event],now=start.timestamp()+1),start,end,TEHRAN)[0]['hold_expired_on_arrival']


import os,pytest

@pytest.mark.asyncio
async def test_planner_integrates_expired_future_quote_and_full_window(tmp_path,monkeypatch):
    from catalog import Catalog
    from memory import Memory
    from agent import Agent
    from features import Features
    path=os.getenv('TEST_CATALOG')
    if not path:pytest.skip('TEST_CATALOG required')
    cat=Catalog(path);mem=Memory(tmp_path/'m.db');a=Agent(cat,mem);f=Features(cat,mem,a);a.features=f
    u=mem.user();sid=mem.session(u)['id'];o=cat.search('هدفون',limit=1)['offers'][0];s=cat.shop(o['shop_id'])
    s['weekly_hours']={day:shift('00:00','23:59:59') for day in ('mon','tue','wed','thu','fri','sat','sun')}
    monkeypatch.setattr(a,'route',lambda *args:{'stops':[dict(s)],'skipped_stops':[],'google_maps_url':'https://example.com'})
    # A fixed daytime reference: near midnight the 15-minute window would cross the shop's 23:59 close.
    now=(datetime.now(TEHRAN)+timedelta(days=1)).replace(hour=10,minute=0,second=0,microsecond=0);depart=now+timedelta(hours=1)
    mem.update(u,[{'key':'lat','value':35.7},{'key':'lon','value':51.4},{'key':'travel_mode','value':'walking'}],sid=sid)
    f.mission(u,sid,{'departure':depart.isoformat(),'manual_leg_minutes':0,'shopping_minutes':15,'return_to_origin':False})
    mem.event(u,sid,{'shop_id':s['shop_id'],'product_id':o['product_id'],'status':'reported','source':'user_reported','available':True,'price':100,
                     'details':{'valid_until':(now+timedelta(minutes=30)).isoformat()}})
    plan=await f.plan(u,sid,[s['shop_id']])
    assert plan['feasible'] is None and any('اعتبار پاسخ' in x for x in plan['unknowns'])
    assert plan['stops'][0]['shopping_window']['state']=='open'
    assert plan['stops'][0]['seller_evidence_at_visit'][0]['valid_at_arrival'] is False
    with mem.conn() as db:db.execute('delete from seller_events')
    depart=(now+timedelta(days=1)).replace(hour=13,minute=55,second=0,microsecond=0)
    s['weekly_hours']={depart.strftime('%a').lower():shift('09:00','14:00')}
    f.mission(u,sid,{'departure':depart.isoformat(),'shopping_minutes':15})
    plan=await f.plan(u,sid,[s['shop_id']])
    assert plan['feasible'] is False and any('بازه کامل خرید' in x for x in plan['problems'])
