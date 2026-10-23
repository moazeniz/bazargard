# «تازه‌سازی قیمت»: price freshness in بازارگرد (release 4.5.0)

## The problem

The organisers said it plainly: sellers *should* update their prices, but they are busy and don't. In the Torob dataset, half of all offers are at least a month old:

| Offers with a price (315,665) | |
|---|---|
| Median price age | 31 days |
| Older than 30 days | 50.6% |
| Older than 90 days | 23.5% |
| Older than one year | 1.3% |

An in-person shopper who drives to a shop on a 90-day-old price is the failure we must prevent. Searching the open web for every listing is too slow and too unreliable, and a seller API does not exist. So بازارگرد runs a loop that asks the right sellers at the right time, makes answering cost one tap, uses other evidence where it is trustworthy, and is honest when nothing fresh exists.

## Our five-part solution

All five parts write to one global store (`price_updates`, plus `price_demand`, `price_asks` and `crowd_reports` in `memory.db`). Every update keeps its basis, time, actor and source. The catalogue applies a still-valid update over the dataset price everywhere: search, budget filters, the product page, the plan and the agent.

| Part | What it does | Basis shown to shoppers | Valid for |
|---|---|---|---|
| 1. One-tap seller link | A signed, expiring (7 days), no-login link per shop: `/?shop=<token>`. It lists the shop's offers that matter (in a shopper's plan, searched, or stale), each with «قیمت هنوز درسته» / «قیمت جدید…» / «موجود نیست», plus a bulk «همهٔ قیمت‌ها درست است». Sent with the prepared message through WhatsApp, Telegram or SMS deep links, or the shop's real website or Instagram. | «قیمت تأییدشدهٔ فروشنده» + «قیمت تأییدشده امروز» badge | 7 days |
| 2. Smart refresh queue | `score = age × demand × (1 + uncertainty) × visit`, where: age = 0.1 + days/90 (0.15 once confirmed); demand = 1 + ln(1 + searches) + 2×plans + 1.5×comparisons; uncertainty = distance from the median price of the same product's other sellers; visit ×2 when a shopper has a route through the shop. The «وضعیت تازگی قیمت‌ها» page (`/?workspace=freshness`) shows the queue and its factors and can send the asks for the top shops in one click. | — | — |
| 3. Shopper reports | A price reported in «ثبت پاسخ فروشنده» stays in that shopper's trip. It becomes a global price only when at least two different shoppers agree within 5% in 72 hours, and it never overrides a seller update. | «قیمت گزارش چند خریدار» | 3 days |
| 4. Shop-website check | For the 112 shops with a real identity (90 websites, 22 Instagram), a web search restricted to the shop's own site reads the current price. It is recorded only if the page is on that site, the page is cited, every model code and Latin model word of the product appears in the page title, and the price is plausible (0.2× to 5× the dataset price). The source link is kept. | «قیمت سایت فروشگاه» | 3 days |
| 5. «برآورد امروز» | For listings at least 30 days old with no fresh confirmation: the price drifted by the general monthly price change (`PRICE_DRIFT_MONTHLY`, default 3%), combined with the recent prices (under 30 days old) of other sellers of the same product. Other sellers' prices below 0.6× or above 1.8× this listing are ignored. The range is clamped to 0.85×–1.25× of the drifted price. The confidence label (بالا/متوسط/کم) comes from the agreement and number of references. Always labelled «قیمت فروشنده نیست». | estimate line under the price | — |

Ranking: a dataset price older than 30 days loses a little score (0.02; 0.05 after 60 days), and a seller-confirmed price today gains a little (0.04). Budget, area and opening filters are not changed.

In the conversation, the agent sees `price_age_days`, `price_stale`, `price_estimate` and `freshness_label`. For a stale recommendation it states the age and the estimate and offers to ask the seller now. The `request_price_refresh` tool prepares the link, the panel shows it with the send buttons, and the tracker marks the stop «پرسیدم؛ منتظر پاسخ · لینک تازه‌سازی قیمت». When the seller answers, the plan page shows the new price with «قیمت تأییدشده امروز» and the tracker switches to «قیمت و موجودی تأیید شد». Stale result cards also carry a «قیمت امروز را از فروشنده بپرس» button.

## What is real and what is simulated

- **Real:**
  - the link, the signature and expiry checks, the seller page, the updates and their audit trail;
  - the queue and its scores from live demand;
  - the website check, which makes real web searches with strict verification;
  - shopper reports and their agreement rule;
  - the estimate, which is computed from the dataset.
- **Simulated:** the dataset's phone numbers are fictional, so for the 6,364 shops without a real website or Instagram, «درخواست خودکار» logs a simulated send (`mode=simulated_send`) instead of messaging anyone. For real-identity shops it prepares the message for a one-tap send through their real channel; nothing is sent automatically.
- **Demo:** on the live site, any seller page opened from a link can answer, so the loop can be shown end to end with a chosen demo shop (آودیس ماهان, QCY T13 ANC 2).

## Metrics

Shown on «وضعیت تازگی قیمت‌ها»:
- prices confirmed today;
- seller, website and shopper updates over 7 days;
- asks sent, opened and answered over 7 days;
- the share of a session's shown prices that are fresh, through the cards' basis labels.

Tests: `tests/test_freshness.py` (9) covers:
- estimate ranges and labels;
- forged and expired links;
- the seller update reaching search, the budget filter and the badge;
- «همه درسته», «موجود نیست» and validation;
- crowd agreement and seller precedence;
- queue order;
- web verification rules;
- the agent tool;
- the API end to end, including the tracker.

UI check: `tools/ui/ui_fresh.py` (desktop and a 390-pixel seller page).
