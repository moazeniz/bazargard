# Data: catalogue, runtime state and disclosures

## 1. The Torob challenge dataset

Bazargard's catalogue is built from the dataset distributed to participants of the Torob challenge (Starcoach Agentic AI Hackathon, "Starcoach v16 cap300 / Torob"). It is **not included in this repository** and must not be published; obtain it from the challenge organisers' materials.

The importer expects a directory with three CSV files:

| File | Columns used |
|---|---|
| `products.csv` | `product_id, persian_title, brand_name, category_name, model, image_urls, specs` (JSON list of key/value specs) |
| `shops.csv` | `shop_id, name, address, location_label, lat, lon, contacts, payment_options` (JSON), `weekly_hours` (JSON), `category_names, instagram, website_url, supports_fast_delivery` |
| `offers.csv` | `offer_id, product_id, shop_id, price_toman, seller_title, description, last_price_changed_at, warranty` |

The literal string `null` is read as missing.

## 2. Building the catalogue

```bash
make install
make catalog DATASET_DIR=/secure/torob-dataset        # = python backend/catalog.py <dir> data/catalog.db
```

- Creates `products`, `shops`, `offers`, the FTS5 index `product_fts` (Persian-normalised title/category/brand + model), relational indexes and a `metadata` table (row counts, import time, source, number of in-person-credit shops).
- Refuses to overwrite an existing database — build to a new path and swap while the service is stopped.
- The reference build contains 225,903 products, 331,506 offers and 6,476 shops (~430 MB). Validate a new build with `make test-full TEST_CATALOG=$PWD/data/catalog.db`.

Optional derived index (faster typed-spec lookups and an all-category coverage report):

```bash
make product-index    # python backend/build_product_index.py --catalog data/catalog.db --output data/canonical-products.db --report data/index-report
```

It reads the catalogue read-only, writes atomically, and stores per-product fingerprints and rule versions; stale or missing rows fall back to per-request projection, so the app runs without it. `backend/propose_mappings.py` is an offline review helper that suggests spec-key aliases from a field inventory; its output never changes runtime rules.

## 3. Shipping the catalogue to servers

- **Liara (production) / containers:** gzip it and serve it behind a bearer token; the container's `backend/start.py` downloads it on first boot when `DATA_DIR/catalog.db` is missing, checks `CATALOG_SHA256`, and unpacks atomically. `scripts/publish-catalog-seed.sh` prepares this on a seed host (see [DEPLOYMENT.md §1.1](DEPLOYMENT.md#11-first-time-setup)).
- Never put the catalogue, its gzip or `memory.db` in git, Docker images or public URLs.

## 4. Runtime state

| File | Contains personal data | Backup |
|---|---|---|
| `memory.db` | yes — chat turns, preferences, trips, seller replies, ops audit | **daily** (`scripts/backup-data.sh`) |
| `public-research.db` | no — public product-only cache | optional |
| `catalog.db`, `canonical-products.db` | no | rebuildable; keep the dataset archive |

## 5. Data-integrity disclosures (shown in the UI)

- **All dataset phone numbers and most shop names are synthetic.** Names of the ~112 shops with official website/Instagram fields are real identities. The app never calls or messages dataset contacts; map searches use the textual address, not the anonymised name.
- An offer proves a product-to-shop listing, not current availability. `last_price_changed_at` is not a live price confirmation. Weekly hours describe the recorded schedule.
- User-entered seller replies stay `user_reported`; simulated replies stay `simulated`; neither is presented as independent verification. Reports expire after 24 h or the merchant-stated quote expiry.
- All merchant times are interpreted in Asia/Tehran regardless of browser time zone.
- The dataset covers roughly 10 % of Tehran shops, selectively by area, at most 300 products per shop. "No result" is not proof that a product is unavailable in the market.

## 6. Third-party data

- Map tiles and place context: © OpenStreetMap contributors, ODbL 1.0 (attribution shown in the UI). Overpass and the OSM map API are used at very low volume with caching (successful lookups 1 day, map snapshots 7 days, ≤ 20 snapshot boxes/day). A larger deployment should import a regional OSM extract instead.
- Road estimates (3.3.0, via the retired relay): OSRM demo server (`router.project-osrm.org`), cached 5 minutes; no live traffic.
- Public web sources: only short excerpts and literal facts are stored, with URL and retrieval date.
