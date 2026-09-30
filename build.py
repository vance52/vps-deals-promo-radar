# ::ILANG [TYPE:code][ROLE:static-site-builder]
# ::RULE{从site.ilang与offers.json生成静态页面和可验证结构化数据}
# ::BOUNDARY{never:为SEO补造价格 折扣 截止日期或评价|scope:file}

"""Build the static site, SEO metadata, schemas, sitemap, and robots file."""

from __future__ import annotations

from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path
import re
import shutil
import sys
from typing import Any
from urllib.parse import urlparse

from ilang_config import Provider, load_site_config


ROOT = Path(__file__).resolve().parent
DATA_PATH = ROOT / "data" / "offers.json"
TEMPLATE_DIR = ROOT / "templates"
SITE_DIR = ROOT / "site"
MONEY = {"USD": "$", "EUR": "€", "GBP": "£", "CAD": "CA$", "AUD": "AU$"}


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "item"


def iso_date(value: str) -> str:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except (ValueError, AttributeError):
        return datetime.now(timezone.utc).date().isoformat()


def display_date(value: str) -> str:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%b %d, %Y at %H:%M UTC")
    except (ValueError, AttributeError):
        return "unknown"


def month_label(value: str) -> str:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%B %Y")
    except (ValueError, AttributeError):
        return "Current"


def price_text(offer: dict[str, Any]) -> str:
    value = escape(str(offer.get("price", "")))
    currency = str(offer.get("currency", "")).upper()
    suffix = "/mo" if offer.get("billing_period") == "month" else ""
    return f"{MONEY.get(currency, currency + ' ')}{value}{suffix}"


def verified_field(offer: dict[str, Any], key: str) -> str:
    value = offer.get(key)
    if value:
        return escape(str(value))
    return "Not stated"


def show_unpriced_sources(config: Any) -> bool:
    return str(config.render.get("show_unpriced_sources", "true")).strip().lower() in {"1", "true", "yes", "on"}


def render_list(config: Any, key: str) -> list[str]:
    return [item.strip() for item in str(config.render.get(key, "")).split(",") if item.strip()]


def provider_plan_path(offer: dict[str, Any]) -> str:
    """Return the single public destination for a plan: its provider-page anchor."""
    return f"/providers/{slugify(str(offer['provider']))}/#plan-{offer['id']}"


def output_path(url_path: str) -> Path:
    if url_path == "/":
        return SITE_DIR / "index.html"
    return SITE_DIR / url_path.strip("/") / "index.html"


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def json_script(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def google_tag_html(config: Any) -> str:
    measurement_id = str(config.render.get("ga4_measurement_id", "")).strip()
    if not measurement_id:
        return ""
    if not re.fullmatch(r"G-[A-Z0-9]+", measurement_id):
        raise ValueError("ga4_measurement_id must be a GA4 measurement ID such as G-XXXXXXXXXX")
    safe_id = escape(measurement_id, quote=True)
    return (
        f'<!-- Google tag (gtag.js) --><script async src="https://www.googletagmanager.com/gtag/js?id={safe_id}"></script>'
        "<script>window.dataLayer = window.dataLayer || [];"
        "function gtag(){dataLayer.push(arguments);}"
        "gtag('js', new Date());"
        f"gtag('config', '{safe_id}');</script>"
    )


def nav_html(brand: str, current: str) -> str:
    links = [("Deals", "/"), ("Compare", "/compare/"), ("About", "/about/"), ("Contact", "/contact/")]
    items = []
    for label, href in links:
        active = ' aria-current="page"' if current == href else ""
        items.append(f'<a href="{href}"{active}>{label}</a>')
    nav_items = "".join(items)
    return (
        f'<a class="brand" href="/" aria-label="{escape(brand)} home"><span>V</span>{escape(brand)}</a>'
        f'<nav class="desktop-nav" aria-label="Primary">{nav_items}</nav>'
        f'<details class="mobile-nav"><summary aria-label="Open navigation">Menu</summary>'
        f'<nav aria-label="Mobile primary">{nav_items}</nav></details>'
    )


def footer_links_html() -> str:
    return '<a href="/about/">About</a> · <a href="/contact/">Contact</a> · <a href="/privacy/">Privacy</a> · <a href="/methodology/">Methodology</a>'


def render_page(
    template_name: str,
    *,
    config: Any,
    title: str,
    description: str,
    canonical_path: str,
    content: str,
    schema: Any,
    updated_at: str,
) -> str:
    template = (TEMPLATE_DIR / template_name).read_text(encoding="utf-8")
    canonical = f"{config.domain}{canonical_path}"
    alternates = (
        f'<link rel="alternate" hreflang="en-US" href="{escape(canonical)}">\n'
        f'  <link rel="alternate" hreflang="x-default" href="{escape(canonical)}">'
    )
    replacements = {
        "[[LANG]]": escape(config.locale),
        "[[TITLE]]": escape(title),
        "[[DESCRIPTION]]": escape(description),
        "[[CANONICAL]]": escape(canonical),
        "[[OG_IMAGE]]": escape(f"{config.domain}/assets/og-card.svg"),
        "[[ALTERNATES]]": alternates,
        "[[SCHEMA]]": json_script(schema),
        "[[NAV]]": nav_html(config.brand, canonical_path),
        "[[FOOTER_LINKS]]": footer_links_html(),
        "[[CONTENT]]": content,
        "[[BRAND]]": escape(config.brand),
        "[[UPDATED]]": escape(display_date(updated_at)),
        "[[YEAR]]": str(datetime.now(timezone.utc).year),
    }
    for token, value in replacements.items():
        template = template.replace(token, value)
    template = template.replace("<head>", f"<head>{google_tag_html(config)}", 1)
    return template


def offer_card(offer: dict[str, Any]) -> str:
    provider_slug = slugify(str(offer["provider"]))
    plan_path = provider_plan_path(offer)
    affiliate = bool(offer.get("affiliate"))
    rel = "sponsored nofollow noopener" if affiliate else "nofollow noopener"
    link_label = "See disclosed offer" if affiliate else "Open official source"
    return f"""
    <article class="deal-card" id="plan-{escape(str(offer['id']))}">
      <div class="deal-topline"><a class="provider-chip" href="/providers/{provider_slug}/">{escape(str(offer['provider']))}</a><span class="verified">source verified</span></div>
      <h3><a href="{plan_path}">{escape(str(offer['title']))}</a></h3>
      <div class="price">{price_text(offer)}</div>
      <p class="fine"><strong>Billing:</strong> {verified_field(offer, 'billing_period')}<br><strong>Renewal:</strong> {verified_field(offer, 'renewal')}<br><strong>Resources:</strong> {verified_field(offer, 'resources')}<br>Checked {escape(iso_date(str(offer['fetched_at'])))}</p>
      <div class="actions"><a class="button" href="{plan_path}">Provider plans</a><a class="text-link" href="{escape(str(offer['offer_url']))}" rel="{rel}">{link_label} ↗</a></div>
    </article>"""


def provider_card(status: dict[str, Any]) -> str:
    state = str(status.get("status", "unknown"))
    state_label = {
        "ok": "checked",
        "blocked_by_robots": "robots blocked",
        "http_error": "source unavailable",
        "network_error": "fetch unavailable",
        "parse_error": "format changed",
    }.get(state, state.replace("_", " "))
    return f"""
    <article class="provider-card">
      <div class="provider-mark">{escape(str(status['name'])[0].upper())}</div>
      <div><h3><a href="/providers/{slugify(str(status['name']))}/">{escape(str(status['name']))}</a></h3>
      <p>{int(status.get('offer_count', 0))} verified plans · <span class="status status-{escape(state)}">{escape(state_label)}</span></p></div>
    </article>"""


def breadcrumb_schema(config: Any, crumbs: list[tuple[str, str]]) -> dict[str, Any]:
    return {
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": index, "name": name, "item": f"{config.domain}{path}"}
            for index, (name, path) in enumerate(crumbs, start=1)
        ],
    }


def copy_public_data() -> None:
    target = SITE_DIR / "data" / "offers.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(DATA_PATH, target)


def build_assets(config: Any) -> None:
    css = """
:root{--ink:#101828;--muted:#475467;--line:#e4e7ec;--paper:#fff;--wash:#f5f7fa;--accent:#d8ff3e;--accent-strong:#c8f529;--accent2:#7c5cff;--dark:#111827;--ok:#067647;--warn:#b54708;--radius:18px;--shadow:0 8px 24px rgba(16,24,40,.055)}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--wash);color:var(--ink);font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:16px;line-height:1.6}a{color:inherit}img{max-width:100%}.skip{position:absolute;left:-999px}.skip:focus{left:1rem;top:1rem;background:#fff;padding:.7rem 1rem;z-index:20;outline:3px solid var(--accent2)}
.site-header{position:sticky;top:0;z-index:10;background:rgba(245,247,250,.92);backdrop-filter:blur(14px);border-bottom:1px solid rgba(228,231,236,.9)}.header-inner,.container{width:min(1160px,calc(100% - 32px));margin:auto}.header-inner{height:68px;display:flex;align-items:center;justify-content:space-between;position:relative}.brand{font-weight:850;text-decoration:none;letter-spacing:-.03em;display:flex;gap:.55rem;align-items:center}.brand span{display:grid;place-items:center;width:31px;height:31px;border-radius:9px;background:var(--dark);color:var(--accent);font-weight:900}.site-header nav{display:flex;gap:.35rem}.site-header nav a{text-decoration:none;color:var(--muted);padding:.5rem .75rem;border-radius:10px;font-weight:700}.site-header nav a:hover,.site-header nav a[aria-current=page]{background:#fff;color:var(--ink)}.mobile-nav{display:none}.mobile-nav summary{list-style:none}.mobile-nav summary::-webkit-details-marker{display:none}
main{min-height:70vh}.hero{padding:64px 0 50px}.hero-grid{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(320px,.75fr);gap:clamp(2rem,5vw,4.5rem);align-items:center}.hero-copy{max-width:690px}.eyebrow{display:inline-flex;align-items:center;gap:.45rem;text-transform:uppercase;letter-spacing:.12em;font-size:.78rem;line-height:1.4;font-weight:800;color:var(--muted)}.eyebrow:before{content:"";width:8px;height:8px;background:var(--accent2);border-radius:50%}.hero h1,.page-hero h1{font-size:clamp(4rem,5.3vw,4.5rem);line-height:1;letter-spacing:-.04em;margin:.75rem 0 1.1rem;max-width:760px}.hero h1 em{font-style:normal;background:var(--accent);padding:0 .09em}.lede{font-size:clamp(1.05rem,1.6vw,1.2rem);line-height:1.6;color:var(--muted);max-width:64ch}.hero-actions{display:flex;align-items:center;gap:.8rem;margin-top:1.7rem}.button,.button-secondary{display:inline-flex;align-items:center;justify-content:center;min-height:46px;text-decoration:none;padding:.7rem 1rem;border-radius:11px;font-weight:800}.button{background:var(--accent);color:var(--ink);border:1px solid #b5df13}.button:hover{background:var(--accent-strong)}.button-secondary{background:var(--paper);border:1px solid var(--line);color:var(--ink)}.button-secondary:hover{border-color:#98a2b3}.hero-summary{background:var(--paper);border:1px solid var(--line);border-radius:24px;padding:1.5rem;box-shadow:var(--shadow)}.summary-kicker{margin:0;color:var(--accent2);font-size:.78rem;text-transform:uppercase;letter-spacing:.1em;font-weight:850}.hero-summary h2{font-size:clamp(1.55rem,2.3vw,2rem);line-height:1.15;letter-spacing:-.035em;margin:.55rem 0 1.3rem}.summary-note{margin:1rem 0 0;color:var(--muted);font-size:.9rem}.hero-meta{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.65rem}.metric{background:var(--wash);border:1px solid var(--line);border-radius:14px;padding:.85rem}.metric strong{font-size:1.65rem;line-height:1;display:block;letter-spacing:-.04em}.metric span{display:block;margin-top:.45rem;font-size:.78rem;line-height:1.35;color:var(--muted)}
.section{padding:42px 0 68px}.section-head{display:flex;justify-content:space-between;align-items:end;gap:1rem;margin-bottom:1.4rem}.section-head h2{font-size:clamp(2rem,3.2vw,2.4rem);line-height:1.15;letter-spacing:-.04em;margin:0}.section-head p{margin:0;color:var(--muted);max-width:520px}.deal-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:1rem}.deal-card,.provider-card,.panel{background:var(--paper);border:1px solid var(--line);border-radius:var(--radius);box-shadow:var(--shadow)}.deal-card{padding:1.3rem;display:flex;flex-direction:column;min-height:305px}.deal-topline{display:flex;justify-content:space-between;gap:.7rem;align-items:center}.provider-chip,.tag{font-size:.8rem;line-height:1.4;text-decoration:none;background:#f2f4f7;border-radius:999px;padding:.36rem .65rem;font-weight:750}.verified{font-size:.78rem;line-height:1.4;color:var(--ok);font-weight:750}.verified:before{content:"✓ ";}.deal-card h3{font-size:1.15rem;line-height:1.35;margin:1.05rem 0 .7rem}.deal-card h3 a{text-decoration:none}.price{font-size:2.15rem;line-height:1.1;letter-spacing:-.045em;font-weight:850;margin-top:auto}.fine{font-size:.875rem;line-height:1.55;color:var(--muted)}.actions{display:flex;align-items:center;gap:.9rem;margin-top:.8rem}.text-link{font-size:.875rem;font-weight:750;color:var(--accent2)}
.provider-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:1rem}.provider-card{padding:1.1rem;display:flex;align-items:center;gap:1rem}.provider-mark{width:48px;height:48px;border-radius:14px;background:var(--dark);color:var(--accent);display:grid;place-items:center;font-size:1.25rem;font-weight:900}.provider-card h3,.provider-card p{margin:0}.provider-card p{color:var(--muted);font-size:.875rem}.status{font-weight:700}.status-ok{color:var(--ok)}.status-http_error,.status-network_error,.status-blocked_by_robots{color:var(--warn)}
.page-hero{padding:56px 0 28px}.page-hero h1{font-size:clamp(2.5rem,5vw,4rem)}.breadcrumbs{font-size:.82rem;color:var(--muted)}.breadcrumbs a{color:inherit}.split{display:grid;grid-template-columns:2fr 1fr;gap:1.2rem}.panel{padding:clamp(1.2rem,3vw,2rem)}.panel h2:first-child,.panel h1:first-child{margin-top:0}.facts{display:grid;grid-template-columns:repeat(2,1fr);gap:.8rem}.fact{background:#f8fafc;padding:1rem;border-radius:12px}.fact span{font-size:.8rem;color:var(--muted);display:block}.fact strong{display:block;margin-top:.2rem}.source-box{border-left:4px solid var(--accent2);background:#f8f7ff;padding:1rem 1.1rem;border-radius:0 12px 12px 0;word-break:break-word}.notice{background:#fffceb;border:1px solid #fedf89;padding:1rem;border-radius:12px}.table-wrap{overflow:auto;background:white;border:1px solid var(--line);border-radius:var(--radius);box-shadow:var(--shadow)}table{width:100%;border-collapse:collapse;min-width:690px}th,td{text-align:left;padding:1rem;border-bottom:1px solid var(--line)}th{font-size:.75rem;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}tbody tr:last-child td{border-bottom:0}.empty{padding:2rem;text-align:center;background:white;border:1px dashed #98a2b3;border-radius:var(--radius)}.method-list{counter-reset:method;display:grid;gap:1rem}.method-step{counter-increment:method;background:white;border:1px solid var(--line);border-radius:var(--radius);padding:1.3rem}.method-step:before{content:counter(method);display:grid;place-items:center;width:32px;height:32px;border-radius:9px;background:var(--accent);font-weight:900;margin-bottom:.8rem}.faq details{background:white;border:1px solid var(--line);border-radius:14px;padding:1rem 1.1rem;margin:.7rem 0}.faq summary{font-weight:750;cursor:pointer}.site-footer{background:var(--dark);color:#d0d5dd;margin-top:70px;padding:44px 0}.footer-grid{display:flex;justify-content:space-between;gap:2rem;flex-wrap:wrap}.site-footer a{color:white}.site-footer p{max-width:660px}.proof-section{padding-top:0}.proof-strip{background:var(--dark);color:white;border-radius:var(--radius);padding:1.15rem 1.35rem;display:flex;justify-content:space-between;gap:1rem;align-items:center}.proof-strip strong{color:var(--accent)}code{background:#f2f4f7;padding:.15rem .35rem;border-radius:5px}a:focus-visible,summary:focus-visible{outline:3px solid var(--accent2);outline-offset:3px}
@media(max-width:860px){.hero-grid{grid-template-columns:1fr;gap:1.5rem}.hero-copy{max-width:720px}.hero-summary{max-width:720px}.deal-grid,.provider-grid{grid-template-columns:1fr 1fr}.split{grid-template-columns:1fr}.hero{padding-top:48px}}
@media(max-width:620px){.header-inner{height:64px}.desktop-nav{display:none!important}.mobile-nav{display:block;position:relative}.mobile-nav summary{cursor:pointer;background:var(--paper);border:1px solid var(--line);border-radius:10px;padding:.48rem .72rem;font-size:.875rem;font-weight:800}.mobile-nav[open] summary{background:var(--accent)}.mobile-nav nav{position:absolute;right:0;top:calc(100% + .55rem);width:190px;display:flex;flex-direction:column;gap:.2rem;background:var(--paper);border:1px solid var(--line);border-radius:14px;padding:.55rem;box-shadow:0 18px 40px rgba(16,24,40,.14)}.mobile-nav nav a{padding:.65rem .7rem}.hero{padding:38px 0 24px}.hero-grid{display:flex;flex-direction:column;align-items:stretch;gap:1.1rem}.hero h1{font-size:clamp(2.5rem,11vw,2.625rem);line-height:1.05;letter-spacing:-.025em;margin:.6rem 0 .85rem}.page-hero h1{letter-spacing:-.025em}.lede{font-size:1rem;line-height:1.6}.hero-actions{margin-top:1.15rem}.hero-actions .button{width:100%}.hero-actions .button-secondary{display:none}.hero-summary{padding:0;background:transparent;border:0;border-radius:0;box-shadow:none}.summary-copy{display:none}.hero-meta{gap:.45rem}.metric{padding:.75rem .55rem;text-align:center}.metric strong{font-size:1.45rem}.metric span{font-size:.72rem;line-height:1.3}.section{padding:30px 0 54px}.section-head{display:block;margin-bottom:1.1rem}.section-head h2{font-size:2rem}.section-head p{margin-top:.6rem}.deal-grid,.provider-grid{grid-template-columns:1fr}.deal-card{min-height:0;padding:1.15rem}.deal-card h3{margin:.9rem 0 .6rem}.fine{font-size:.875rem;line-height:1.55}.actions{align-items:stretch;flex-direction:column;gap:.65rem}.actions .button{width:100%}.actions .text-link{text-align:center}.facts{grid-template-columns:1fr}.proof-strip{align-items:flex-start;flex-direction:column}.brand{font-size:.94rem;gap:.45rem}.brand span{font-size:1rem;flex:0 0 auto}}
""".strip()
    css += """
.legal-copy{max-width:820px}.legal-copy h2{margin-top:2rem}.legal-copy li{margin:.55rem 0}.contact-address{font-size:clamp(1.2rem,4vw,2rem);font-weight:850;overflow-wrap:anywhere}
""".strip()
    write_text(SITE_DIR / "assets" / "styles.css", css + "\n")
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630"><rect width="1200" height="630" fill="#111827"/><circle cx="1040" cy="90" r="230" fill="#7c5cff" opacity=".85"/><circle cx="1050" cy="550" r="330" fill="#d8ff3e" opacity=".95"/><text x="80" y="245" fill="#d8ff3e" font-family="Arial,sans-serif" font-size="34" font-weight="700">OFFICIAL-SOURCE VPS PRICING</text><text x="80" y="360" fill="white" font-family="Arial,sans-serif" font-size="92" font-weight="900">{escape(config.brand)}</text><text x="84" y="430" fill="#d0d5dd" font-family="Arial,sans-serif" font-size="30">Checked. Linked. Never guessed.</text></svg>"""
    write_text(SITE_DIR / "assets" / "og-card.svg", svg)
    headers = """/*
  X-Content-Type-Options: nosniff
  Referrer-Policy: strict-origin-when-cross-origin
  Permissions-Policy: camera=(), microphone=(), geolocation=()
  X-Frame-Options: DENY
  Content-Security-Policy: default-src 'self'; style-src 'self'; img-src 'self' data:; script-src 'self' 'unsafe-inline' https://www.googletagmanager.com; connect-src 'self' https://www.google-analytics.com https://region1.google-analytics.com; object-src 'none'; base-uri 'self'; form-action 'none'; frame-ancestors 'none'

/assets/*
  Cache-Control: public, max-age=604800, immutable
"""
    write_text(SITE_DIR / "_headers", headers)


def build_index(config: Any, data: dict[str, Any]) -> None:
    offers = data["offers"]
    providers = data["providers"]
    updated = str(data["meta"]["generated_at"])
    cards = "".join(offer_card(offer) for offer in offers[:12])
    if not cards:
        cards = '<div class="empty"><h3>No unambiguous prices extracted on this run</h3><p>The official source checks are shown below. We publish nothing rather than guess.</p></div>'
    visible_providers = providers if show_unpriced_sources(config) else [provider for provider in providers if int(provider.get("offer_count", 0)) > 0]
    provider_cards = "".join(provider_card(provider) for provider in visible_providers)
    content = f"""
    <section class="hero"><div class="container hero-grid">
      <div class="hero-copy"><span class="eyebrow">US market · official sources only</span>
      <h1>VPS pricing,<br><em>without the guesswork.</em></h1>
      <p class="lede">Compare current VPS prices traced to official provider pages, with ambiguous data left unpublished.</p>
      <div class="hero-actions"><a class="button" href="/compare/">Compare verified plans</a><a class="button-secondary" href="/methodology/">How we verify prices</a></div></div>
      <aside class="hero-summary" aria-label="Tracker summary"><div class="summary-copy"><p class="summary-kicker">Live tracker</p><h2>Official-source checks, at a glance.</h2></div>
      <div class="hero-meta"><div class="metric"><strong>{len(offers)}</strong><span>verified plans</span></div><div class="metric"><strong>{len(providers)}</strong><span>official sources</span></div><div class="metric"><strong>6h</strong><span>refresh cycle</span></div></div>
      <p class="summary-note summary-copy">Missing or ambiguous prices stay unpublished.</p></aside>
    </div></section>
    <section class="section" id="deals"><div class="container"><div class="section-head"><h2>Verified plans</h2><p>Monthly prices extracted only when an official page exposes a clear price and billing period.</p></div><div class="deal-grid">{cards}</div></div></section>
    <section class="section proof-section"><div class="container"><div class="proof-strip"><div><strong>Evidence first.</strong> Source URL and check time ship with every record.</div><a href="/data/offers.json">Open the dataset →</a></div></div></section>
    <section class="section"><div class="container"><div class="section-head"><h2>Source coverage</h2><p>A provider can show zero plans when its public page is blocked, changed, or ambiguous.</p></div><div class="provider-grid">{provider_cards}</div></div></section>
    <section class="section faq"><div class="container"><div class="section-head"><h2>How to read this site</h2></div><details><summary>Is every link an affiliate link?</summary><p>No. The initial release uses official direct links. Approved affiliate links can be layered in later and are marked as sponsored.</p></details><details><summary>Why are some providers shown without a price?</summary><p>The source was checked, but the page did not expose an unambiguous price that the deterministic parser could verify. The site does not estimate missing values.</p></details></div></section>
    """
    item_list = {
        "@type": "ItemList",
        "itemListElement": [
            {"@type": "ListItem", "position": index, "url": f"{config.domain}{provider_plan_path(offer)}"}
            for index, offer in enumerate(offers, start=1)
        ],
    }
    faq = {
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": "Is every link an affiliate link?", "acceptedAnswer": {"@type": "Answer", "text": "No. The initial release uses official direct links. Any future affiliate links are disclosed and marked as sponsored."}},
            {"@type": "Question", "name": "Why are some providers shown without a price?", "acceptedAnswer": {"@type": "Answer", "text": "The deterministic parser publishes a price only when the official source exposes an unambiguous amount and billing context."}},
        ],
    }
    schema = {"@context": "https://schema.org", "@graph": [item_list, faq]}
    html = render_page("index.html", config=config, title=f"Verified VPS pricing — {month_label(updated)} | {config.brand}", description="Compare source-linked VPS pricing for US buyers. Every published amount is extracted from an official provider page and timestamped.", canonical_path="/", content=content, schema=schema, updated_at=updated)
    write_text(output_path("/"), html)


def build_provider_pages(config: Any, data: dict[str, Any], urls: list[tuple[str, str]]) -> None:
    updated = str(data["meta"]["generated_at"])
    offers_by_provider: dict[str, list[dict[str, Any]]] = {}
    for offer in data["offers"]:
        offers_by_provider.setdefault(str(offer["provider"]), []).append(offer)
    for status in data["providers"]:
        name = str(status["name"])
        path = f"/providers/{slugify(name)}/"
        offers = offers_by_provider.get(name, [])
        if not offers and not show_unpriced_sources(config):
            continue
        facts = [fact for fact in config.provider_facts if fact.provider == name]
        referral = next((item for item in config.provider_referrals if item.provider == name), None)
        verification = next((item for item in config.provider_verifications if item.provider == name), None)
        if facts:
            cards = "".join(
                f'''<article class="deal-card"><div class="deal-topline"><span class="provider-chip">{escape(fact.plan)}</span><span class="verified">Official price</span></div><h3>{escape(fact.vcpu)} · {escape(fact.memory)} memory</h3><div class="price">${escape(fact.monthly_price)} <small>/ month</small></div><div class="facts"><div class="fact"><span>Storage</span><strong>{escape(fact.storage)}</strong></div><div class="fact"><span>Bandwidth</span><strong>{escape(fact.bandwidth)}</strong></div></div>{f'<p class="notice">{escape(fact.note)}</p>' if fact.note else ''}<p class="fine">Source: {escape(fact.source_url)}<br>Checked: {escape(fact.checked_on)}</p></article>'''
                for fact in facts
            )
        else:
            cards = "".join(offer_card(offer) for offer in offers) or '<div class="empty"><h3>No verified priced plans in this refresh</h3><p>Use the official source below. We did not publish an ambiguous amount.</p></div>'
        referral_block = ""
        if referral:
            referral_block = f'''<div class="panel"><p class="notice">{escape(referral.disclosure)}</p><p><a class="button" href="{escape(referral.url)}" rel="sponsored nofollow noopener">{escape(referral.label)} ↗</a></p></div>'''
        checked_on = max((fact.checked_on for fact in facts), default=iso_date(str(status.get("checked_at", updated))))
        page_updated = checked_on if facts else str(status.get("checked_at", updated))
        published_count = len(facts) if facts else len(offers)
        if facts and verification:
            method_labels = {
                "browser_session_official_pricing_table": "Browser session: official pricing table",
            }
            http_labels = {
                "not_exposed_by_browser_session": "Not exposed by browser session",
            }
            price_method = method_labels.get(verification.method, verification.method.replace("_", " "))
            http_status = http_labels.get(verification.http_status, verification.http_status.replace("_", " "))
            checked_on = verification.checked_on
            page_updated = checked_on
            lede = f"{published_count} Cloud Compute Regular Performance monthly prices checked against Vultr's official pricing page on {checked_on}."
        else:
            methods = sorted({str(offer.get("extraction", "")).strip() for offer in offers if offer.get("extraction")})
            if methods:
                price_method = "Automated: " + "; ".join(methods)
            elif str(status.get("status")) == "ok":
                price_method = "Automated check: no price extracted"
            else:
                price_method = "No price published"
            http_status = str(status["http_status"]) if "http_status" in status else "No source response"
            lede = str(status.get('message', 'Official source checked.'))
        source_facts = f'''<div class="fact"><span>Price method</span><strong>{escape(price_method)}</strong></div><div class="fact"><span>Published prices</span><strong>{published_count}</strong></div><div class="fact"><span>Checked</span><strong>{escape(checked_on)}</strong></div><div class="fact"><span>HTTP status</span><strong>{escape(http_status)}</strong></div>'''
        content = f"""
        <section class="page-hero"><div class="container"><div class="breadcrumbs"><a href="/">Deals</a> / Providers / {escape(name)}</div><span class="eyebrow">Official-source provider file</span><h1>{escape(name)} VPS pricing</h1><p class="lede">{escape(lede)}</p></div></section>
        <section class="section"><div class="container split"><div><div class="deal-grid">{cards}</div>{referral_block}</div><aside class="panel"><h2>Source record</h2><div class="facts">{source_facts}</div><p class="source-box"><strong>Official source</strong><br><a href="{escape(str(status['source_url']))}" rel="nofollow noopener">{escape(str(status['source_url']))}</a></p></aside></div></section>
        """
        graph: list[dict[str, Any]] = [breadcrumb_schema(config, [("Deals", "/"), (name, path)])]
        priced_items = facts if facts else offers
        if priced_items:
            prices = [float(item.monthly_price) for item in facts] if facts else [float(item["price"]) for item in offers]
            currencies = {item.currency for item in facts} if facts else {str(item["currency"]) for item in offers}
            aggregate: dict[str, Any] = {"@type": "AggregateOffer", "lowPrice": min(prices), "highPrice": max(prices), "offerCount": len(priced_items), "url": f"{config.domain}{path}"}
            if len(currencies) == 1:
                aggregate["priceCurrency"] = next(iter(currencies))
            graph.insert(0, {"@type": "Service", "name": f"{name} VPS plans", "provider": {"@type": "Organization", "name": name, "url": status["homepage"]}, "offers": aggregate})
        schema = {"@context": "https://schema.org", "@graph": graph}
        html = render_page("provider.html", config=config, title=f"{name} VPS pricing — {month_label(updated)} | {config.brand}", description=f"Source-linked {name} VPS prices checked against the provider's official page. Missing or ambiguous prices are not estimated.", canonical_path=path, content=content, schema=schema, updated_at=page_updated)
        write_text(output_path(path), html)
        urls.append((path, iso_date(str(status.get("checked_at", updated)))))


def build_deal_pages(config: Any, data: dict[str, Any], urls: list[tuple[str, str]]) -> None:
    for offer in data["offers"]:
        provider = str(offer["provider"])
        path = f"/deals/{slugify(provider)}-{offer['id']}/"
        provider_path = f"/providers/{slugify(provider)}/"
        affiliate = bool(offer.get("affiliate"))
        rel = "sponsored nofollow noopener" if affiliate else "nofollow noopener"
        valid = escape(str(offer.get("valid_until", "Not stated by source")))
        content = f"""
        <section class="page-hero"><div class="container"><div class="breadcrumbs"><a href="/">Deals</a> / <a href="{provider_path}">{escape(provider)}</a> / Plan</div><span class="eyebrow">Verified {escape(iso_date(str(offer['fetched_at'])))}</span><h1>{escape(str(offer['title']))}</h1><p class="lede">A source-linked plan record. Price and billing context below were parsed from the official provider page.</p></div></section>
        <section class="section"><div class="container split"><article class="panel"><div class="price">{price_text(offer)}</div><div class="facts"><div class="fact"><span>Provider</span><strong>{escape(provider)}</strong></div><div class="fact"><span>Billing period</span><strong>{escape(str(offer.get('billing_period', 'not stated')))}</strong></div><div class="fact"><span>Currency</span><strong>{escape(str(offer['currency']))}</strong></div><div class="fact"><span>Valid until</span><strong>{valid}</strong></div></div><p class="notice">Verify configuration, regional availability, taxes, and the final checkout total with the provider before buying.</p><p><a class="button" href="{escape(str(offer['offer_url']))}" rel="{rel}">{'Open disclosed offer' if affiliate else 'Open official provider page'} ↗</a></p></article><aside class="panel"><h2>Provenance</h2><p><strong>Extraction</strong><br>{escape(str(offer['extraction']))}</p><p><strong>Fetched</strong><br>{escape(display_date(str(offer['fetched_at'])))}</p><p class="source-box"><strong>Source URL</strong><br><a href="{escape(str(offer['source_url']))}" rel="nofollow noopener">{escape(str(offer['source_url']))}</a></p></aside></div></section>
        """
        offer_schema: dict[str, Any] = {"@type": "Offer", "name": str(offer["title"]), "price": str(offer["price"]), "priceCurrency": str(offer["currency"]), "availability": "https://schema.org/InStock", "url": f"{config.domain}{path}", "seller": {"@type": "Organization", "name": provider}}
        if offer.get("valid_until"):
            offer_schema["priceValidUntil"] = offer["valid_until"]
        schema = {"@context": "https://schema.org", "@graph": [offer_schema, breadcrumb_schema(config, [("Deals", "/"), (provider, provider_path), (str(offer["title"]), path)])]}
        html = render_page("deal.html", config=config, title=f"{provider} VPS at {price_text(offer).replace('&', 'and')} — {month_label(str(offer['fetched_at']))}", description=f"Verified {provider} VPS price from the official source, checked {iso_date(str(offer['fetched_at']))}. No estimated price or expiration.", canonical_path=path, content=content, schema=schema, updated_at=str(offer["fetched_at"]))
        write_text(output_path(path), html)
        urls.append((path, iso_date(str(offer["fetched_at"]))))


def build_compare(config: Any, data: dict[str, Any], urls: list[tuple[str, str]]) -> None:
    updated = str(data["meta"]["generated_at"])
    rows = []
    for offer in data["offers"]:
        path = provider_plan_path(offer)
        rows.append(f"<tr><td><a href=\"{path}\">{escape(str(offer['title']))}</a></td><td>{escape(str(offer['provider']))}</td><td><strong>{price_text(offer)}</strong></td><td>{escape(str(offer.get('billing_period', 'not stated')))}</td><td>{escape(iso_date(str(offer['fetched_at'])))}</td></tr>")
    table_body = "".join(rows) if rows else '<tr><td colspan="5">No unambiguous priced plans were extracted on this run.</td></tr>'
    content = f"""
    <section class="page-hero"><div class="container"><div class="breadcrumbs"><a href="/">Deals</a> / Compare</div><span class="eyebrow">Same evidence, sortable by eye</span><h1>Compare verified VPS prices</h1><p class="lede">Prices are shown only when the provider's official page exposes a clear amount. Specs and checkout terms remain the provider's source of truth.</p></div></section>
    <section class="section"><div class="container"><div class="table-wrap"><table><thead><tr><th>Plan</th><th>Provider</th><th>Price</th><th>Period</th><th>Checked</th></tr></thead><tbody>{table_body}</tbody></table></div></div></section>
    """
    schema = {"@context": "https://schema.org", "@type": "ItemList", "itemListElement": [{"@type": "ListItem", "position": index, "url": f"{config.domain}{provider_plan_path(offer)}"} for index, offer in enumerate(data["offers"], 1)]}
    html = render_page("compare.html", config=config, title=f"Compare verified VPS prices — {month_label(updated)} | {config.brand}", description="Compare current source-linked VPS plan prices for US buyers. Each amount includes its official source and fetch date.", canonical_path="/compare/", content=content, schema=schema, updated_at=updated)
    write_text(output_path("/compare/"), html)
    urls.append(("/compare/", iso_date(updated)))


def build_methodology(config: Any, data: dict[str, Any], urls: list[tuple[str, str]]) -> None:
    updated = str(data["meta"]["generated_at"])
    content = f"""
    <section class="page-hero"><div class="container"><div class="breadcrumbs"><a href="/">Deals</a> / Methodology</div><span class="eyebrow">Auditable by design</span><h1>How the radar decides what to publish</h1><p class="lede">The pipeline is intentionally conservative. A missing plan is preferable to a confident-looking invented price.</p></div></section>
    <section class="section"><div class="container split"><div class="method-list"><article class="method-step"><h2>Read one configuration</h2><p>Brand, locale, provider list, official source URLs, fields, and schedule come from <code>.ilang/site.ilang</code>.</p></article><article class="method-step"><h2>Check robots.txt</h2><p>A source is fetched only when its robots policy permits this user agent. Login-only content and anti-bot bypass are out of bounds.</p></article><article class="method-step"><h2>Extract conservatively</h2><p>The parser accepts explicit JSON-LD Offers or a clearly labelled monthly-price table. It does not infer discounts, dates, reviews, or commissions.</p></article><article class="method-step"><h2>Publish provenance</h2><p>Every record carries the source URL, exact UTC fetch time, extraction method, and affiliate status in the public JSON dataset.</p></article></div><aside class="panel"><h2>Current run</h2><div class="facts"><div class="fact"><span>Sources</span><strong>{len(data['providers'])}</strong></div><div class="fact"><span>Priced plans</span><strong>{len(data['offers'])}</strong></div><div class="fact"><span>Locale</span><strong>{escape(config.locale)}</strong></div><div class="fact"><span>Refresh</span><strong>Every {config.update_hours} hours</strong></div></div><p><a class="button" href="/data/offers.json">Inspect raw JSON</a></p></aside></div></section>
    """
    schema = {"@context": "https://schema.org", "@type": "WebPage", "name": f"{config.brand} methodology", "url": f"{config.domain}/methodology/", "dateModified": updated}
    html = render_page("compare.html", config=config, title=f"Methodology | {config.brand}", description=f"How {config.brand} checks robots.txt, parses official provider pages, rejects ambiguous prices, and publishes source provenance.", canonical_path="/methodology/", content=content, schema=schema, updated_at=updated)
    write_text(output_path("/methodology/"), html)
    urls.append(("/methodology/", iso_date(updated)))


def build_trust_pages(config: Any, data: dict[str, Any], urls: list[tuple[str, str]]) -> None:
    updated = str(data["meta"]["generated_at"])
    pages = [
        (
            "/about/",
            "About HostDealsHub",
            "Who maintains HostDealsHub, what the site publishes, and the editorial rules behind its VPS pricing records.",
            """
            <section class="page-hero"><div class="container"><div class="breadcrumbs"><a href="/">Deals</a> / About</div><span class="eyebrow">Independent and evidence first</span><h1>About HostDealsHub</h1><p class="lede">HostDealsHub is built and maintained by an independent developer. No personal name or public alias is used on the site.</p></div></section>
            <section class="section"><div class="container"><article class="panel legal-copy"><h2>What the site does</h2><p>HostDealsHub tracks publicly available VPS pricing and promotions for buyers in the United States. It publishes a price only when a provider's official public page exposes a clear amount and billing context.</p><h2>How the records are produced</h2><p>A deterministic Python pipeline checks the official sources listed in the site's public configuration, respects robots.txt, records the source URL and fetch time, and rebuilds these static pages. Missing or ambiguous prices stay unpublished.</p><h2>Editorial independence</h2><p>Provider coverage and ordering are not sold. Future affiliate or advertising relationships will be disclosed, and they will not change the rule that every published price needs a linked official source.</p><h2>Corrections</h2><p>If a source changes or a record appears wrong, please send the page URL and the provider's official source to <a href="mailto:contact@hostdealshub.com">contact@hostdealshub.com</a>.</p></article></div></section>
            """,
        ),
        (
            "/contact/",
            "Contact HostDealsHub",
            "Contact HostDealsHub about corrections, official source changes, accessibility, privacy, or business inquiries.",
            """
            <section class="page-hero"><div class="container"><div class="breadcrumbs"><a href="/">Deals</a> / Contact</div><span class="eyebrow">Corrections and inquiries</span><h1>Contact HostDealsHub</h1><p class="lede">Use the address below for corrections, source updates, accessibility, privacy, or business inquiries.</p></div></section>
            <section class="section"><div class="container split"><article class="panel"><h2>Email</h2><p class="contact-address"><a href="mailto:contact@hostdealshub.com">contact@hostdealshub.com</a></p></article><aside class="panel"><h2>What to include</h2><ul><li>The HostDealsHub page URL.</li><li>The provider's official source URL when reporting a price or availability issue.</li><li>A concise description of the correction or request.</li></ul><p>Do not send passwords, billing details, server credentials, or other sensitive information.</p></aside></div></section>
            """,
        ),
        (
            "/privacy/",
            "Privacy Policy",
            "How HostDealsHub handles site delivery data, external provider links, future advertising, and privacy inquiries.",
            """
            <section class="page-hero"><div class="container"><div class="breadcrumbs"><a href="/">Deals</a> / Privacy</div><span class="eyebrow">Effective September 30, 2026</span><h1>Privacy Policy</h1><p class="lede">This policy describes the current static site and how privacy disclosures will change if third-party advertising is added.</p></div></section>
            <section class="section"><div class="container"><article class="panel legal-copy"><h2>Information HostDealsHub collects</h2><p>HostDealsHub does not provide user accounts, comments, checkout, or an on-site contact form. HostDealsHub uses Google Analytics, which writes first-party cookies and anonymous identifiers in visitors' browsers to measure site usage.</p><h2>Site delivery</h2><p>The site is hosted on Cloudflare Pages. Cloudflare may process network and request information needed to deliver, secure, and operate the service under its own policies. HostDealsHub does not receive payment-card details or hosting-provider account credentials from visitors.</p><h2>External links and affiliate disclosure</h2><p>Links can lead to provider websites with their own privacy practices. Direct provider links are used by default. If an approved affiliate link is added, it is marked as sponsored and does not change the site's source-verification rules.</p><h2>Third-party advertising</h2><p>HostDealsHub plans to display third-party advertising only after an advertising network approves the site. An advertising provider may use cookies, local storage, IP addresses, or device information to deliver, limit, measure, or personalize ads according to its own policies and applicable consent requirements. The site will update this policy and any required consent controls when an actual provider and its approved code are added. No advertising code is installed at the time this policy is published.</p><h2>Data retention</h2><p>The public VPS dataset retains official source URLs and UTC fetch times so readers can audit the records. HostDealsHub does not intentionally publish visitor identities in that dataset.</p><h2>Questions</h2><p>Privacy questions can be directed to <a href="mailto:contact@hostdealshub.com">contact@hostdealshub.com</a>.</p></article></div></section>
            """,
        ),
    ]
    for path, title, description, content in pages:
        schema = {"@context": "https://schema.org", "@type": "WebPage", "name": title, "url": f"{config.domain}{path}", "dateModified": updated}
        html = render_page("compare.html", config=config, title=f"{title} | {config.brand}", description=description, canonical_path=path, content=content, schema=schema, updated_at=updated)
        write_text(output_path(path), html)
        urls.append((path, iso_date(updated)))


def build_not_found(config: Any, data: dict[str, Any]) -> None:
    updated = str(data["meta"]["generated_at"])
    content = """
    <section class="page-hero"><div class="container"><span class="eyebrow">Error 404</span><h1>That page does not exist.</h1><p class="lede">The address may be mistyped, or the page may have moved. Nothing has been substituted for the missing page.</p></div></section>
    <section class="section"><div class="container"><article class="panel"><h2>Continue from a known page</h2><p>Browse the latest source-linked VPS prices, compare verified plans, or review how the dataset is produced.</p><div class="actions"><a class="button" href="/">Browse deals</a><a class="text-link" href="/compare/">Compare plans</a><a class="text-link" href="/methodology/">Read the methodology</a></div></article></div></section>
    """
    schema = {"@context": "https://schema.org", "@type": "WebPage", "name": "Page not found", "url": f"{config.domain}/404.html"}
    html = render_page(
        "compare.html",
        config=config,
        title=f"Page not found | {config.brand}",
        description="The requested HostDealsHub page does not exist.",
        canonical_path="/404.html",
        content=content,
        schema=schema,
        updated_at=updated,
    )
    html = html.replace('<link rel="canonical"', '<meta name="robots" content="noindex,follow"><link rel="canonical"', 1)
    write_text(SITE_DIR / "404.html", html)


def build_sitemap(config: Any, urls: list[tuple[str, str]]) -> None:
    unique: dict[str, str] = {}
    for path, lastmod in urls:
        unique[path] = lastmod
    entries = "".join(f"  <url><loc>{escape(config.domain + path)}</loc><lastmod>{escape(lastmod)}</lastmod></url>\n" for path, lastmod in unique.items())
    sitemap = f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{entries}</urlset>\n'
    write_text(SITE_DIR / "sitemap.xml", sitemap)
    write_text(SITE_DIR / "robots.txt", f"User-agent: *\nAllow: /\n\nSitemap: {config.domain}/sitemap.xml\n")


def build_url_dispositions(config: Any, data: dict[str, Any], urls: list[tuple[str, str]]) -> None:
    legacy_providers = set(render_list(config, "legacy_deal_providers"))
    retired_providers = render_list(config, "retired_providers")
    redirect_status = int(config.render.get("merged_deal_redirect_status", "301"))
    retired_status = int(config.render.get("retired_provider_status", "410"))
    expected_deals = int(config.render.get("legacy_deal_expected_count", "0"))
    expected_total = int(config.render.get("legacy_sitemap_expected_count", "0"))
    if redirect_status != 301:
        raise ValueError("Merged legacy deal pages must use permanent HTTP 301 redirects")
    if retired_status != 410:
        raise ValueError("Retired provider pages without an equivalent replacement must return HTTP 410")

    offers_by_provider: dict[str, list[dict[str, Any]]] = {}
    for offer in data["offers"]:
        offers_by_provider.setdefault(str(offer["provider"]), []).append(offer)

    redirects: list[str] = []
    dispositions: list[dict[str, Any]] = []
    for offer in data["offers"]:
        provider = str(offer["provider"])
        if provider not in legacy_providers:
            continue
        source = f"/deals/{slugify(provider)}-{offer['id']}/"
        target = provider_plan_path(offer)
        redirects.append(f"{source} {target} {redirect_status}")
        dispositions.append(
            {
                "source": source,
                "action": "merge",
                "status": redirect_status,
                "target": target,
                "reason": "The plan now lives as a source-linked anchor on its provider page; the permanent redirect avoids a duplicate standalone URL.",
            }
        )
    if len(redirects) != expected_deals:
        raise ValueError(f"Expected {expected_deals} legacy deal redirects, generated {len(redirects)}")
    write_text(SITE_DIR / "_redirects", "\n".join(redirects) + "\n")

    known_providers = {provider.name for provider in config.providers}
    retired_paths: list[str] = []
    for provider in retired_providers:
        if provider not in known_providers:
            raise ValueError(f"Unknown retired provider: {provider}")
        if offers_by_provider.get(provider):
            raise ValueError(f"Refusing to retire {provider}: the current run contains verified offers")
        path = f"/providers/{slugify(provider)}/"
        retired_paths.append(path)
        dispositions.append(
            {
                "source": path,
                "action": "retire",
                "status": retired_status,
                "target": None,
                "reason": "No current source-verified plan data exists and there is no equivalent replacement page, so a misleading redirect is not used.",
            }
        )

    normalized_retired = sorted(path.rstrip("/") for path in retired_paths)
    worker = f'''const RETIRED = new Set({json.dumps(normalized_retired, ensure_ascii=False)});\n\nexport default {{\n  async fetch(request, env) {{\n    const path = new URL(request.url).pathname.replace(/\\/+$/, "") || "/";\n    if (RETIRED.has(path)) {{\n      return new Response("This provider page is no longer available because no current source-verified plan data can be published.\\n", {{\n        status: {retired_status},\n        headers: {{\n          "Content-Type": "text/plain; charset=utf-8",\n          "Cache-Control": "public, max-age=300",\n          "X-Robots-Tag": "noindex",\n        }},\n      }});\n    }}\n    return env.ASSETS.fetch(request);\n  }},\n}};\n'''
    write_text(SITE_DIR / "_worker.js", worker)
    function_routes = sorted({route for path in retired_paths for route in (path.rstrip("/"), path)})
    write_text(SITE_DIR / "_routes.json", json.dumps({"version": 1, "include": function_routes, "exclude": []}, indent=2) + "\n")

    retired_sources = {item["source"] for item in dispositions}
    for path, _ in urls:
        if path in retired_sources:
            raise ValueError(f"Retired URL is still in the generated sitemap set: {path}")
        dispositions.append(
            {
                "source": path,
                "action": "keep",
                "status": 200,
                "target": path,
                "reason": "The URL remains generated by the current source-verified build.",
            }
        )
    dispositions.sort(key=lambda item: str(item["source"]))
    if len(dispositions) != expected_total:
        raise ValueError(f"Expected {expected_total} URL dispositions, generated {len(dispositions)}")
    manifest = {
        "previous_sitemap_count": expected_total,
        "current_sitemap_count": len(urls),
        "entries": dispositions,
    }
    write_text(SITE_DIR / "data" / "url-dispositions.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


def main() -> int:
    config = load_site_config()
    if not config.domain.startswith("https://"):
        raise ValueError("site.ilang domain must be an absolute HTTPS origin")
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    if SITE_DIR.exists():
        resolved = SITE_DIR.resolve()
        if resolved.parent != ROOT.resolve() or resolved.name != "site":
            raise RuntimeError("Refusing to replace an unexpected output directory")
        shutil.rmtree(resolved)
    SITE_DIR.mkdir(parents=True)
    build_assets(config)
    copy_public_data()
    urls: list[tuple[str, str]] = [("/", iso_date(str(data["meta"]["generated_at"])))]
    build_index(config, data)
    build_provider_pages(config, data, urls)
    build_compare(config, data, urls)
    build_methodology(config, data, urls)
    build_trust_pages(config, data, urls)
    build_not_found(config, data)
    build_url_dispositions(config, data, urls)
    build_sitemap(config, urls)
    print(f"Built {len(urls)} indexable pages in {SITE_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())


