# vps-deals promo radar

An evidence-first, static VPS pricing and promotion tracker for US buyers. Every listed price is extracted from a linked official provider page; missing or ambiguous prices are left out instead of estimated.

Live site: https://vps-deals-promo-radar.pages.dev

## What is in the repository

- `scraper.py` checks the official sources declared in `.ilang/site.ilang`, obeys `robots.txt`, and writes provenance-rich results to `data/offers.json`.
- `build.py` turns that dataset into a static site in `site/`, including canonical URLs, JSON-LD, Open Graph metadata, `robots.txt`, and `sitemap.xml`.
- `.github/workflows/update.yml` refreshes and commits the dataset and generated site every six hours.
- `templates/` contains the four page templates used by the builder.

The pipeline uses only the Python standard library. It has no runtime LLM, API key, database, analytics beacon, or server process.

## Run locally

```bash
python scraper.py
python build.py
python -m unittest discover -s tests
python -m http.server 8000 --directory site
```

Then open `http://localhost:8000`.

## Add or change a provider

Edit only the `PROVIDERS` module in `.ilang/site.ilang`:

```text
Provider name | homepage | official pricing or promotion page | optional affiliate URL | currency hint | auto
```

Run the pipeline again. Provider navigation, status cards, offer pages, comparison pages, sitemap entries, and the public provenance dataset will be regenerated from the configuration.

## Data policy

- Official public pages only.
- `robots.txt` is checked before each fetch.
- No login-only content, anti-bot bypass, guessed price, guessed expiration, copied review, or invented discount.
- A provider may appear with zero offers when its page is unavailable or does not expose an unambiguous machine-readable/table price. That is an honest result, not a pipeline error.
- Affiliate URLs are empty in the initial configuration. When a legitimate program approves this site, add its disclosed tracking URL to the provider row; the source URL remains visible.

## Cloudflare Pages

- Production branch: `main`
- Build command: `python build.py`
- Build output directory: `site`
- Root directory: repository root

Site rules are described with the I-Lang protocol in `.ilang/site.ilang`; protocol information: https://ilang.ai.
