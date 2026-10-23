"""Deterministic catalogue evidence retention for one assistant reply.

External evidence never enters this ledger. Failed exploratory searches must not
make already retrieved, still eligible Torob offers disappear. A deliberate
nonempty shortlist remains authoritative; changed hard filters are rechecked.
"""
import copy
import re
from catalog import normalize


class CatalogueFirst:
    def __init__(self, agent, uid, sid):
        self.agent, self.uid, self.sid = agent, uid, sid
        self.offers = {}
        self.checks = []

    def record(self, result, purpose='catalogue'):
        if not isinstance(result, dict) or 'offers' not in result:
            return
        self.checks.append({'query': result.get('query', ''), 'purpose': purpose,
                            'returned': len(result['offers']),
                            'filters': copy.deepcopy(result.get('filters', {}))})
        for offer in result['offers']:
            self.offers[offer['offer_id']] = copy.deepcopy(offer)

    @staticmethod
    def explicit_query(message, profile):
        """A narrow fallback when a model attempts advice without any retrieval.

        Only literal category/model mentions or an evidence-backed saved query
        are used. Conversation text is never sent wholesale to public search.
        """
        text = normalize(message)
        categories = ('لپ تاپ', 'laptop', 'هدفون', 'هندزفری', 'headphones',
                      'گوشی', 'موبایل', 'smartphone', 'کفش', 'کتانی', 'کتونی',
                      'پاوربانک', 'پاور بانک', 'تلویزیون', 'یخچال', 'ماشین لباسشویی')
        category = next((c for c in categories if re.search(r'(?<!\w)' + re.escape(c) + r'(?!\w)', text)), '')
        models = re.findall(r'(?<![a-z0-9])[a-z][a-z0-9-]*[0-9][a-z0-9-]*(?![a-z0-9])', message.lower())[:2]
        if category: return ' '.join([category] + models)
        if models: return ' '.join(models)
        return str(profile.get('product_query') or '')[:160]

    @classmethod
    def needs_lookup(cls, message, profile, current):
        if not current.get('offers'): return bool(cls.explicit_query(message, profile))
        # Existing cards only discharge catalogue-first for the same intent.
        # A new model/category must not be answered using unrelated old cards.
        explicit=cls.explicit_query(message,{})
        if not explicit:return False
        titles=' '.join(o.get('persian_title','') for o in current['offers'])
        models=re.findall(r'(?<![a-z0-9])[a-z][a-z0-9-]*[0-9][a-z0-9-]*(?![a-z0-9])',message.lower())[:2]
        for model in models:
            tokens=re.findall(r'[a-z]+|[0-9]+',model)
            pattern=r'(?<![a-z0-9])'+r'[\s-]*'.join(tokens)+r'(?![a-z0-9])'
            if not re.search(pattern,titles,re.I):return True
        groups=[('لپ تاپ','laptop','notebook'),('هدفون','هندزفری','هدست','headphones'),
                ('گوشی','موبایل','smartphone'),('کفش','کتانی','کتونی'),
                ('پاوربانک','پاور بانک'),('تلویزیون',),('یخچال',),('ماشین لباسشویی',)]
        text=normalize(message);titles=normalize(titles)
        for group in groups:
            if any(re.search(r'(?<!\w)'+re.escape(c)+r'(?!\w)',text) for c in group):
                if not any(c in titles for c in group):return True
        return False

    def reset(self):
        # A correction can change product intent as well as hard constraints.
        self.offers.clear()
        self.checks.clear()

    def retain(self, result):
        current = self.agent.overlay(self.uid, self.sid, result)
        restored = False
        if not current.get('offers') and self.offers:
            eligible = self.agent.overlay(self.uid, self.sid, {'offers': list(self.offers.values())})
            if eligible['offers']:
                current = dict(eligible, query=result.get('query', ''),
                               matching_offers=len(eligible['offers']),
                               price_status='dataset_not_live')
                current['catalogue_notice'] = 'جست‌وجوی تکمیلی نتیجه نداشت؛ گزینه‌های واجد شرایط دیتاست در کارت‌ها حفظ شدند. قیمت و موجودی زنده تأیید نشده‌اند.'
                restored = True
        current['catalogue_evidence'] = {'checks': copy.deepcopy(self.checks),
                                         'retained_after_empty_search': restored,
                                         'source': 'supplied_dataset'}
        return current

    async def for_product(self, product_id):
        """Mandatory eligible-seller lookup before enriching an exact product.

        A research target may be outside current budget; its matching offers then
        remain empty rather than silently relaxing the shopper's hard filters.
        """
        import asyncio
        a = self.agent
        if not a.cat.product(product_id):
            raise ValueError('محصول در دیتاست پیدا نشد')
        result = await asyncio.to_thread(
            a.cat.search, profile=a.mem.profile(self.uid, self.sid),
            product_ids=[product_id], limit=12,
            exclude_shops=a.exclusions(self.uid, self.sid),
            exclude_pairs=a.unavailable_pairs(self.uid, self.sid),
            seller_events=a.mem.events(self.uid, self.sid))
        result = a.overlay(self.uid, self.sid, result)
        self.record(result, 'before_external_research')
        return result
