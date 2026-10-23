# Agent reasoning: Quick and Advanced modes

The shopping chat runs a bounded tool loop (`backend/agent.py`) over typed tools. The model never receives SQL, shell, arbitrary URL fetch or messaging capabilities; tool results are untrusted data.

## Modes

The mode is chosen per shopping trip (new trips start in **Quick**) and cannot change while that session has a running answer.

| | Quick (سریع) | Advanced (پیشرفته) |
|---|---|---|
| Goal | Direct retrieval and a short answer | Dependent investigation and refinement |
| Model rounds | ≤ 4 | ≤ 12 |
| Tools | small essential set, ≤ 10 calls | full set, ≤ 40 calls |
| Catalogue retrieval | ≤ 1 direct search | complementary batches (≤ 18 queries), expansion, seller joins |
| Output cap per request | 1,400 tokens | 3,600 tokens |
| Automatic clarification | off | one optional question after retrieval |
| Hard constraints | enforced | enforced |

Limits are ceilings, not promised durations or costs. Changing modes never clears preferences or relaxes filters.

## Multi-hop investigation (Advanced)

1. `plan_research` records up to six short task questions with dependencies — reviewable user tasks, not hidden chain-of-thought.
2. `search_query_round` runs up to four complementary queries in parallel, deduplicates identical requests, merges explicit offer records and retries equivalent category terms on empty branches. Model tokens are kept; every branch re-reads current hard constraints and seller reports. A budget correction invalidates cached result signatures — wider retrieval never loosens budget, radius, credit or brand.
3. `find_product_sellers` joins discovered products to sellers with full specs; `compare_products`, `inspect_offer`, seller quotes, timing and cost tools provide further evidence.
4. `verify_recommendation` (and automatically `show_shortlist`) re-checks eligibility and returns recorded specs/unknowns. Only offer IDs retrieved in this turn or displayed in the owning session can enter the shortlist.
5. Final citations bind to an immutable offer snapshot; a collapsible query trail shows what was searched, with filters and sample counts (not market-wide counts).

The final model round disables tools to force synthesis. Runs time out after six minutes; the UI polls persisted events and recovers after a reload. A service restart marks unfinished runs as interrupted rather than complete.

When the catalogue has nothing suitable, the agent can call `suggest_shopping_areas`, `discover_tehran_merchants` or `find_web_alternatives`. Their results are labelled leads, never qualifying offers or route stops.

## Progressive clarification

Retrieve first, then ask. Advanced examines the candidates before asking one optional question: purpose for relevant categories, a budget when returned prices differ widely, or a specification chosen by entropy over the current models' actual variation. The UI offers choices plus "نمی‌دانم / فعلاً رد کن"; skipped questions are remembered for the trip. A budget answer is a hard ceiling; a specification answer can be mandatory (filters) or a preference (re-evaluation). Answers never overwrite permanent preferences.

## Memory rules

- Every durable preference needs an exact quote from the current user message; explicit corrections replace previous values.
- Chat changes default to the current trip; "remember for me" stores permanent facts; recipient profiles omit location and visit time.
- Seller replies entered by the user are `user_reported`; simulated replies are `simulated`; neither is independent verification.

## Example prompts

- «بین M19 و QCY T13 برای مکالمه مقایسه کن، فروشنده‌های همان مدل‌ها را پیدا کن، آفرهای خیلی ارزان را بررسی کن و دو پیشنهاد زیر ۳ میلیون بده. اگر مشخصات لازم موجود نیست، بگو.»
- «برای گوشی مدل مشخص من کابل و شارژر بخر؛ توان و اتصال را از مشخصات بررسی کن، هزینه کل را زیر بودجه نگه دار و تعداد توقف‌ها را کم کن. سازگاری نامشخص را تأییدشده نگو.»
- «این فروشنده گفت موجود نیست؛ همان مدل را جای دیگری پیدا کن، شرایط قسطی را حفظ کن و بگو با مهلت سفر می‌رسم یا نه.»

## Known limits

More queries do not create evidence the catalogue lacks. Missing compatibility, stock or specifications stay unknown; the agent asks for evidence or drafts a seller inquiry. There is no guarantee that the model chooses the best query — evaluate task success, unsupported claims, retained constraints and missing-evidence handling, not the number of tool calls.
