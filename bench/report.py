"""docs/BENCHMARK.md and docs/DATASET.md from bench/results/*.json."""
import json
from datetime import datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent;R=ROOT/'bench'/'results';DOCS=ROOT/'docs'

def load(name):
    p=R/name
    return json.loads(p.read_text()) if p.exists() else None
def f(v,suffix='%'):return '—' if v is None else (f'{v:g}{suffix}' if isinstance(v,(int,float)) else str(v))
def n(v):return f'{int(v):,}' if isinstance(v,(int,float)) else str(v)
KINDS={'budget':'Budget','brand':'Brand','area':'Area / district','open':'Open now','credit':'In-person credit','model':'Model codes','spelling':'Misspelling / Finglish',
       'trap':'Accessory trap','multi':'Multi-constraint','not_found':'Not in Torob'}

def benchmark():
    q=load('queries.json');ret=load('retrieval.json');base=load('retrieval-baseline.json');e2e=load('e2e.json')
    m=ret['metrics'];b=(base or {}).get('metrics')
    L=['# بازارگرد benchmark on the Torob dataset','',
       f"Generated {datetime.now():%Y-%m-%d %H:%M} by `make bench` from `bench/results/*.json` (raw per-query results are committed next to this report). "
       f"Labelled set: **{len(q['queries'])} Persian requests** in {len(KINDS)} groups. Retrieval runs without any model; the agent run uses the production agent with Claude through the LLM gate.",'',
       '## 1. How the labels were made','',
       '- Each request in `bench/spec.py` has: the natural sentence a shopper types, the concise catalogue phrase plus hard constraints a correct parse yields (budget, district, open at a fixed visit time, in-person credit), and the expectation (acceptable catalogue categories, model or brand tokens that must be in the title, or *not in Torob*).',
       '- `bench/bench.py build` checks every label against the catalogue and records the ground truth (`truth.products` = how many products satisfy all constraints). For «not in Torob» items the truth is 0 by construction (e.g. no Leica TV, no Kindle, no Bosch dishwasher, no electric bicycle, no microwave oven under 8M toman). A label that disagrees with the catalogue stops the build.',
       '- Open-now uses fixed visit times (Saturday 11:00 and Friday 20:00 Tehran) so results are reproducible.','',
       '## 2. Retrieval without the LLM (catalogue search + ranking)','',
       'Two paths are measured: **ranked** (balanced ranking, top 10) and **agent first search** (what the agent receives first: the price-stratified 1/2/2 spread of five distinct products).','',
       '| Metric | Ranked | Agent first search | Baseline ranked* | Baseline agent* |','|---|---|---|---|---|']
    def row(label,key,sub=None):
        get=lambda mm,mode:(mm or {}).get(mode,{}).get(key) if sub is None else ((mm or {}).get(mode,{}).get(key) or {}).get(sub)
        L.append(f"| {label} | {f(get(m,'ranked'))} | {f(get(m,'agent_first_search'))} | {f(get(b,'ranked'))} | {f(get(b,'agent_first_search'))} |")
    row('Precision@5 (category/intent match)','precision_at_5');row('Precision@10','precision_at_10');row('Success@5 (≥1 correct item in top 5)','success_at_5')
    row('Zero-result rate (when results exist)','zero_result_rate');row('Constraint satisfaction (all checks)','constraint_satisfaction')
    for k in ('budget','area','open','credit'):row(f'— {k}','constraint_by_type',k)
    row('Accessories in top 5 of «main product» queries','accessory_rate_on_traps_top5')
    L+=['','\\* Baseline = release 4.2.0 search code on the same labels (`BENCH_TAG=-baseline`), before the general fixes listed in section 6.','',
        f"**Not in Torob ({m['not_found']['queries']} queries):** no false match in {f(m['not_found']['no_false_match'])}; the gap is explicitly signalled (empty result, relaxed term reported, accessories-only flag or no matching item) in {f(m['not_found']['explicitly_signalled'])}. The agent turns this signal into «در دیتاست ترب پیدا نشد» (section 3).",'',
        '**Latency** (catalogue only, 225,903 products / 331,506 offers, one process):','',
        '| | p50 | p95 |','|---|---|---|',
        f"| Ranked search, warm | {m['latency_ms']['ranked_p50']} ms | {m['latency_ms']['ranked_p95']} ms |",
        f"| Agent first search (spread), warm | {m['latency_ms']['agent_first_search_p50']} ms | {m['latency_ms']['agent_first_search_p95']} ms |",
        f"| Both searches, cold caches | {m['latency_ms'].get('cold_both_searches_p50')} ms | {m['latency_ms'].get('cold_both_searches_p95')} ms |",'',
        '### By request type (ranked, precision@5)','','```mermaid','xychart-beta','  title "Precision@5 by request type (ranked search)"']
    kinds=[k for k in KINDS if k in m['by_kind']]
    L.append('  x-axis ['+', '.join('"'+KINDS[k].split(' ')[0]+'"' for k in kinds)+']')
    L.append('  y-axis "precision@5 %" 0 --> 100')
    if b:L.append('  bar ['+', '.join(str(b['by_kind'].get(k,{}).get('p@5',0)) for k in kinds)+']')
    L.append('  bar ['+', '.join(str(m['by_kind'][k]['p@5']) for k in kinds)+']')
    L+=['```','',('Bars: baseline (first), current (second).' if b else ''),'','| Type | Queries | p@5 now | success@5 now | p@5 baseline |','|---|---|---|---|---|']
    for k in kinds:
        L.append(f"| {KINDS[k]} | {m['by_kind'][k]['n']} | {f(m['by_kind'][k]['p@5'])} | {f(m['by_kind'][k]['success@5'])} | {f((b or {}).get('by_kind',{}).get(k,{}).get('p@5'))} |")
    misses=[r for r in ret['rows'] if not r['not_found'] and not any(o['intent'] for o in r['ranked'][:5])]
    L+=['','**Remaining misses (no correct item in the ranked top 5):** '+('; '.join(f"`{r['id']}` «{r['search']}»" for r in misses) or 'none')+'. '
        'Finglish such as «goshi samsung» and split words such as «لبتاپ» (for «لپ تاپ») are not recognised by the catalogue search. In the agent, the model rewrites these requests into catalogue words before searching (section 3).','']
    if e2e:
        em=e2e['metrics']
        L+=['## 3. End to end with the agent','',
            f"{em['queries']} requests from the set (`E2E` in `bench/spec.py`), sent one at a time to the running app as a shopper would (new chat, advanced mode). The model is Claude Sonnet through the LLM gate, the same path as production. Run at {e2e['run_at'][:16]}.",'',
            '| Metric | Result |','|---|---|',
            f"| Requests answered without error | {em['queries']-em['errors']}/{em['queries']} |",
            f"| Cards in the requested category / intent | {f(em['card_intent_precision'])} |",
            f"| Cards satisfying the hard constraints (budget, district, open, credit) | {f(em['card_constraint_satisfaction'])} |",
            f"| Answers with result cards when results exist | {f(em['answers_with_results'])} |",
            f"| «Not in Torob» said plainly when nothing matches | {f(em['not_found_stated_when_expected'])} |",
            f"| Wrong «not found» when the catalogue has a match | {f(em['false_not_found_when_results_exist'])} |",
            f"| Citations [n] that point to a visible card | {f(em['citations_valid'])} |",
            f"| Answers that cite cards when results exist | {f(em.get('cites_cards_when_results_expected'))} |",
            f"| Price mentions in answers / hallucinated (no card or stated budget within 5%) | {em['price_mentions']} / {f(em['hallucinated_price_rate'])} |",
            f"| Response time p50 / p95 | {em['seconds']['p50']} s / {em['seconds']['p95']} s |",
            f"| Mean model rounds per answer | {em['mean_rounds']} |",'',
            '| Request | Type | Time | Cards | Card intent / constraints | Cited | Says «not found» | Prices / hallucinated |','|---|---|---|---|---|---|---|---|']
        for r in e2e['rows']:
            ci=sum(c['intent'] for c in r['cards']);cc=sum(all(c['constraints'].values()) for c in r['cards'])
            L.append(f"| «{r['request']}» | {KINDS.get(r['kind'],r['kind'])} | {r['seconds']} s | {len(r['cards'])} | {ci}/{len(r['cards'])} · {cc}/{len(r['cards'])} | {len(set(r['citations']))} | {'yes' if r['says_not_found'] else 'no'} | {len(r['price_mentions'])} / {len(r['hallucinated_prices'])} |")
        L+=['','«Says not found» also triggers on partial statements («X در این بازه پیدا نشد») next to real results. The strict metric above only counts a wrong claim when no card is shown. «Near alternative» cards may accompany a «not in Torob» answer. They are shown with their own label and are not cited as matches.','',
            '## 4. Data fidelity','',
            'For every card shown in the agent run, the displayed fields were traced back to the catalogue row by `offer_id`:','',
            '| Check | Result |','|---|---|']
        labels={'exists':'Offer exists in the dataset','price_matches':'Displayed price = dataset `price_toman` (or a labelled seller report)','age_matches':'Displayed price age = now − `last_price_changed_at`',
                'stock_unknown_without_report':'Stock shown as unknown unless a seller report exists','identity_matches':'«Real name» only for shops with website/Instagram in the dataset; others labelled synthetic'}
        for k,v in em['fidelity'].items():L.append(f'| {labels[k]} | {f(v)} |')
        L.append(f"| Synthetic shop names and phone numbers disclosed in the app notices | {'yes' if em['synthetic_names_disclosed'] else 'no'} |")
        L.append('')
    L+=['## 5. Rerun','','```bash','make bench                        # labels + dataset facts + retrieval benchmark + this report (no model calls, ~1 min)',
        'make bench-e2e BASE=http://127.0.0.1:8611/   # agent benchmark against a running app (17 model-backed chats, ~4 min)',
        'make bench-baseline OLD=/path/to/old/backend # optional: same labels on an older build','```','',
        'Inputs: `bench/spec.py` (labels), the catalogue at `data/catalog.db` (override with `CATALOG=`). Outputs: `bench/results/queries.json`, `retrieval.json`, `retrieval-baseline.json`, `e2e.json`, `dataset.json`. No secrets are stored in results.','',
        '## 6. What the benchmark changed (general fixes, no per-query rules)','',
        '- **Persian suffixes:** catalogue words of 4+ letters now match by prefix, so «بلوتوث» finds «بلوتوثی» and «گوشی» finds «گوشی‌ها». Bluetooth speaker budget queries went from wrong categories to correct ones.',
        '- **Spelling:** a rare query word is corrected to the most frequent catalogue word one edit away («هدفن» → «هدفون»), using the catalogue vocabulary (FTS5 vocab). Known words are never changed. The correction is reported to the agent as `corrected_terms`.',
        '- **Accessories versus products:** a category the query itself names is the product, not an accessory, so «پاوربانک» returns power banks, not power-bank modules. Categories that start with an accessory noun (پاکت، کارتریج، شارژر، لوازم یدکی…) sink unless that noun is asked for. Accessories in the top 5 of main-product queries in the agent\'s first search: baseline → current shown above.',
        '- **Category ranking:** categories that contain more of the query words rank first («لامپ LED» → «لامپ LED و کم مصرف»).',
        '- **Brand filter:** a brand also matches when it is named in the title. The first agent run showed «هدفون QCY T13» failing because QCY products are listed under the brand شیائومی. That request now returns the right cards.',
        '- **Implausible prices:** offers priced under 1,000 or over 10 billion toman (data errors, see DATASET.md) are flagged in the offer inspection.','',
        '## 7. Limits of this benchmark','',
        '- The labels were written by the team. They were checked against the catalogue, but there is no external relevance judgement. The category labels decide what counts as «correct».',
        '- Open-now and in-person credit are checked with the same schedule and credit rules the app uses (dataset weekly hours, payment options). They show the filter is applied consistently, not that a shop is really open.',
        '- The agent set is small (15 requests) to keep model cost low, and answers vary from run to run.','']
    (DOCS/'BENCHMARK.md').write_text('\n'.join(L))

def dataset():
    d=load('dataset.json')
    if not d:return
    s,o,p=d['shops'],d['offers'],d['products'];ages=o['price_age'];ta=sum(ages.values())
    L=['# The Torob dataset in بازارگرد','',f"All numbers are computed from the catalogue database by `backend/dataset_facts.py` (`make bench` → `bench/results/dataset.json`). Source: {d['source']}, imported {d['imported_at'][:10]}. The same numbers appear in the app under «درباره داده‌ها» (menu → درباره داده‌ها, rail → داده‌ها).",'',
       '## Size and coverage','','| Item | Value |','|---|---|',
       f"| Shops | {n(s['total'])} (all with coordinates, all inside Tehran) |",
       f"| Shops with a real identity (website or Instagram in the source) | {n(s['real_identity'])} — website {n(s['website'])}, Instagram {n(s['instagram'])} |",
       f"| Shops whose names are synthetic | {n(s['total']-s['real_identity'])} |",
       f"| Phone numbers | {n(s['with_phone_field'])} shops have a phone field; **all numbers are synthetic** |",
       f"| Offers (shop × product with a price) | {n(o['total'])} |",
       f"| Products | {n(p['total'])} in {n(p['categories'])} categories; {n(p['with_brand'])} have a brand |",
       f"| Products per shop | at most {n(o['products_per_shop_max'])} (the dataset cap); {n(o['shops_at_300_products'])} shops are at the cap; median {n(o['per_shop_median'])} offers per shop |",
       f"| Districts (location labels) | {n(s['areas'])}; largest: "+', '.join(f"{a} ({n(c)})" for a,c in s['top_areas'][:6])+' |',
       f"| Declared opening hours | {n(s['with_declared_hours'])} shops ({round(100*s['with_declared_hours']/s['total'])}%); {n(s['hours_all_week'])} declare all 7 days; {n(s['without_hours'])} have none |",
       f"| In-person credit / installments | {n(s['with_in_person_credit'])} shops |",
       f"| Offers with warranty text / description | {n(o['with_warranty_text'])} / {n(o['with_description'])} |",'',
       '## Prices','','| | toman |','|---|---|',f"| Median | {n(o['price_median'])} |",f"| 90th percentile | {n(o['price_p90'])} |",
       f"| Implausible (< 1,000 toman) | {n(o['price_below_1000_toman'])} offers |",f"| Implausible (> 10 billion toman) | {n(o['price_above_10b_toman'])} offers |",'',
       'Price age is measured from `last_price_changed_at` to the import date:','','| Age | Offers | Share |','|---|---|---|']
    for k in ('≤7 days','≤30 days','≤90 days','≤365 days','>1 year','unknown'):
        if k in ages:L.append(f"| {k} | {n(ages[k])} | {round(100*ages[k]/ta,1)}% |")
    L+=['','```mermaid','pie title Price age at import']+[f'  "{k}" : {v}' for k,v in ages.items()]+['```','',
        '## Top categories by offers','','| Category | Offers |','|---|---|']+[f'| {c} | {n(v)} |' for c,v in p['top_categories_by_offers']]+['',
        '## Payment options in the data','','| Provider · channel | Shops |','|---|---|']+[f'| {k} | {n(v)} |' for k,v in d['payment_providers']]+['',
        '## How each field is used and labelled','','| Field | Used for | Shown as |','|---|---|---|',
        '| `offers.price_toman`, `last_price_changed_at` | Budget filters, ranking, price spread, comparison price row | «قیمت ثبت‌شده در ترب، نه زنده» with the age of the price; seller reports appear as a separate labelled price |',
        '| Stock | Not in the dataset | «موجودی نامشخص» until the shopper records a seller reply (labelled «گزارش کاربر», «پاسخ لینک فروشنده» or «شبیه‌سازی») |',
        '| `shops.name`, `contacts` | Shop identity | Cards say «نام ساختگی» unless the shop has a website/Instagram («هویت منبع»). Phone numbers are never dialled or messaged; the follow-up dialog uses the shopper\'s own apps |',
        '| `shops.instagram`, `website_url` | Real follow-up channels | «پیام در اینستاگرام» / «سایت فروشگاه» buttons (112 shops) |',
        '| `lat`, `lon`, `address`, `location_label` | Distance, district filter, map, route order | Straight-line distance; road time from OSRM without live traffic; indoor floor/unit quoted from the address text |',
        '| `weekly_hours` | Open-now filter and visit planning | «طبق برنامه باز/بسته» (schedule, not verified); shops without hours are «ساعت نامشخص» and are never assumed open |',
        '| `payment_options` (channel, provider, price range, categories) | In-person credit filter | Only `in_person` options whose price range and categories fit count; online or unknown credit is not proof |',
        '| `products.specs`, `category_name`, `brand_name` | Search, comparison table, decision questions | Each comparison cell quotes the catalogue text it came from; web-completed cells are marked with their source link |',
        '| `seller_title`, `description`, `warranty` | Offer inspection (used/stock/copy wording, variant mismatch, missing warranty, implausible price) | Flags with the quoted words |','',
        '## Known data issues','',
        f"- {n(o['price_below_1000_toman'])} offers under 1,000 toman and {n(o['price_above_10b_toman'])} over 10 billion toman: data errors. They are flagged in the offer inspection and the benchmark excludes none of them.",
        f"- {n(s['without_hours'])} shops have no opening hours. Open-now filters exclude them, and plans show «ساعت نامشخص».",
        '- The dataset `model` field is often an internal slug (e.g. `authentic-base-10396`). Model codes for review lookup are taken from the title instead.',
        '- Coverage is partial: about 10% of Tehran shops and at most 300 products per shop. «Not in the dataset» is never presented as «not in the market».','']
    (DOCS/'DATASET.md').write_text('\n'.join(L))

def write():
    DOCS.mkdir(exist_ok=True);benchmark();dataset();print('docs/BENCHMARK.md and docs/DATASET.md written')

if __name__=='__main__':write()
