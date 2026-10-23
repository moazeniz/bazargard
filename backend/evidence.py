"""Session-scoped seller evidence. Catalogue facts remain available as provenance."""
import time
from datetime import datetime

REPORT_TTL = 86400


class SellerReports:
    def __init__(self, events=(), now=None):
        self.now = time.time() if now is None else now
        self.events = sorted(events, key=lambda e: e.get('id', e.get('created', 0)), reverse=True)
        self.active = [e for e in self.events if self.fresh(e)]
        self.real = [e for e in self.active if e.get('source') in ('user_reported', 'merchant_link')]
        self.actionable=[e for e in self.active if e.get('source') in ('user_reported','merchant_link','simulated')]
        self.prices = {}
        for e in self.actionable:
            if e.get('product_id') and e.get('price') is not None:
                self.prices.setdefault((e['shop_id'], e['product_id']), e)

    def fresh(self, e):
        if not 0 <= self.now - e['created'] < REPORT_TTL:
            return False
        expiry = (e.get('details') or {}).get('valid_until')
        if expiry:
            try:
                return datetime.fromisoformat(expiry).timestamp() > self.now
            except (ValueError, TypeError):
                return False
        return True

    def latest(self, shop, product, events=None):
        return next((e for e in (self.events if events is None else events)
                     if e['shop_id'] == shop and e.get('product_id') == product), None)

    def price_updates(self):
        return self.prices

    def failed_shops(self):
        latest = {}
        for e in self.active:
            latest.setdefault(e['shop_id'], e)
        return {s for s, e in latest.items() if e['status'] in ('closed', 'no_answer')}

    def unavailable_pairs(self):
        latest = {}
        for e in self.active:
            latest.setdefault((e['shop_id'], e.get('product_id')), e)
        return {pair for pair, e in latest.items() if e['status'] == 'unavailable' or e.get('available') == 0 or (e.get('details') or {}).get('variant_match') is False}

    def shop_evidence(self, shop):
        return next((e for e in self.active if e['shop_id'] == shop and
                     (e.get('hours') or e['status'] == 'closed' or (e.get('details') or {}).get('opening_intervals'))), None)

    def indoor(self, shop):
        return next(((e.get('details') or {}).get('indoor') for e in self.real
                     if e['shop_id'] == shop and (e.get('details') or {}).get('indoor')), None)

    def opening(self, shop, dataset, at, tz):
        e = self.shop_evidence(shop)
        if not e:
            return dataset
        source = e['source']
        who = 'پاسخ لینک فروشنده' if source == 'merchant_link' else 'گزارش کاربر' if source == 'user_reported' else 'شبیه‌سازی'
        state = 'unknown'
        details = e.get('details') or {}
        expiry = datetime.fromisoformat(details['valid_until']).timestamp() if details.get('valid_until') else e['created']+REPORT_TTL
        if at.timestamp() >= expiry:
            return {'state':'unknown','label':f'اعتبار گزارش تا زمان مراجعه تمام می‌شود؛ {who}: '+(e.get('hours') or 'ساعت را دوباره بپرسید'),'source':source,'reported_at':e['created'],'independently_verified':False}
        intervals = details.get('opening_intervals') or []
        try:
            periods = [(datetime.fromisoformat(x['start']), datetime.fromisoformat(x['end'])) for x in intervals]
            if periods:
                state = 'open' if any(a <= at < b for a, b in periods) else 'closed'
        except (TypeError, ValueError, KeyError):
            periods = []
        if not intervals and e['status'] == 'closed' and datetime.fromtimestamp(e['created'], tz).date() == at.date():
            state = 'closed'
        text = e.get('hours') or ('فروشگاه بسته گزارش شده است' if e['status'] == 'closed' else 'ساعت دقیق نامشخص')
        return {'state': state, 'label': f'{who}: {text}', 'source': source,
                'reported_at': e['created'], 'independently_verified': False}
