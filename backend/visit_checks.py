"""Visit windows: cover the whole shopping interval, not just arrival."""
from datetime import datetime,timedelta,time as clock_time
from evidence import REPORT_TTL
from catalog import opening


def _stamp(value,tz):
    value=datetime.fromisoformat(value)
    return value.replace(tzinfo=tz) if value.tzinfo is None else value.astimezone(tz)


def _covered(periods,start,end):
    cursor=start
    for a,b in sorted(periods):
        if a>cursor:break
        if b>cursor:cursor=b
        if cursor>=end:return True
    return False


def shopping_window(shop,reports,start,end,tz):
    event=reports.shop_evidence(shop['shop_id'])
    source=event['source'] if event else 'dataset'
    result={'start':start.isoformat(),'end':end.isoformat(),'source':source,'state':'unknown','independently_verified':False}
    if end<=start:
        state=reports.opening(shop['shop_id'],opening(shop.get('weekly_hours') or {},start),start,tz).get('state','unknown')
        return dict(result,state=state)
    if event:
        details=event.get('details') or {}
        try:
            expiry=_stamp(details['valid_until'],tz).timestamp() if details.get('valid_until') else event['created']+REPORT_TTL
            if end.timestamp()>=expiry:return dict(result,reason='opening_report_expires_before_finish')
            periods=[(_stamp(p['start'],tz),_stamp(p['end'],tz)) for p in details.get('opening_intervals',[])]
        except (ValueError,TypeError,KeyError):return dict(result,reason='invalid_opening_report')
        if periods:return dict(result,state='open' if _covered(periods,start,end) else 'closed',reason='reported_intervals')
        if event['status']=='closed' and datetime.fromtimestamp(event['created'],tz).date()==start.date():
            return dict(result,state='closed',reason='reported_closed')
        return dict(result,reason='unstructured_opening_report')
    periods=[];unknown=False
    first=start.date();last=(end-timedelta(microseconds=1)).date()
    for offset in range(-1,(last-first).days+1):
        day=first+timedelta(days=offset)
        hours=shop.get('weekly_hours') or {}
        record=hours.get(day.strftime('%a').lower()) if isinstance(hours,dict) else None
        if not isinstance(record,dict):record={}
        if record.get('status') not in ('declared','closed'):
            if offset>=0:unknown=True
            continue
        if record.get('status')=='closed':continue
        for shift in (record.get('shifts') or []):
            try:
                a=datetime.combine(day,clock_time.fromisoformat(shift['start_time']),tz)
                b=datetime.combine(day,clock_time.fromisoformat(shift['end_time']),tz)
                if b<a:b+=timedelta(days=1)
                if a!=b:periods.append((a,b))
            except (ValueError,TypeError,KeyError):unknown=True
    state='open' if _covered(periods,start,end) else 'unknown' if unknown else 'closed'
    return dict(result,state=state,reason='dataset_full_interval')


def arrival_evidence(shop_id,reports,start,end,tz):
    latest={}
    for e in reports.active:
        if e['shop_id']==shop_id and e.get('product_id'):
            latest.setdefault(e['product_id'],e)
    checks=[]
    for pid,e in latest.items():
        details=e.get('details') or {}
        expiry=e['created']+REPORT_TTL
        try:
            if details.get('valid_until'):expiry=_stamp(details['valid_until'],tz).timestamp()
            expires=expiry<=end.timestamp()
            hold_expired=bool(details.get('hold_until') and _stamp(details['hold_until'],tz)<=start)
        except (ValueError,TypeError):expires=True;hold_expired=False
        checks.append({'product_id':pid,'source':e['source'],'valid_at_arrival':expiry>start.timestamp(),
                       'valid_through_shopping':not expires,'hold_expired_on_arrival':hold_expired,
                       'needs_reconfirmation':expires or hold_expired})
    return checks
