# ::ILANG [TYPE:code][ROLE:official-source-scraper]
# ::RULE{只抓site.ilang声明的公开官方来源并遵守robots.txt}
# ::BOUNDARY{never:猜价格 猜截止日期 绕登录或反爬|scope:file}

"""Fetch conservative, source-linked VPS offers using only the Python stdlib."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
import urllib.robotparser

from ilang_config import Provider, load_site_config


ROOT = Path(__file__).resolve().parent
DATA_PATH = ROOT / "data" / "offers.json"
USER_AGENT = "vps-deals-promo-radar/1.0 (+https://vps-deals-promo-radar.pages.dev/methodology/)"
PRICE_RE = re.compile(
    r"(?:(?P<code>USD|EUR|GBP)\s*)?(?P<symbol>US\$|CA\$|AU\$|\$|€|£)\s*(?P<amount>\d{1,5}(?:[.,]\d{1,4})?)",
    re.IGNORECASE,
)
CURRENCY_SYMBOLS = {"$": "USD", "US$": "USD", "CA$": "CAD", "AU$": "AUD", "€": "EUR", "£": "GBP"}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


class SourceHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.page_title = ""
        self._in_title = False
        self._title_parts: list[str] = []
        self._jsonld = False
        self._jsonld_parts: list[str] = []
        self.jsonld_blocks: list[str] = []
        self.tables: list[list[list[str]]] = []
        self.table_labels: list[str] = []
        self._table: list[list[str]] | None = None
        self._table_label = ""
        self._row: list[str] | None = None
        self._cell_parts: list[str] | None = None
        self._heading_parts: list[str] | None = None
        self._last_heading = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "title":
            self._in_title = True
        elif tag == "script" and (values.get("type") or "").lower() == "application/ld+json":
            self._jsonld = True
            self._jsonld_parts = []
        elif tag == "table":
            self._table = []
            self._table_label = self._last_heading
        elif tag in {"h2", "h3", "h4"}:
            self._heading_parts = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
            self.page_title = clean_text("".join(self._title_parts))
        elif tag == "script" and self._jsonld:
            self._jsonld = False
            block = "".join(self._jsonld_parts).strip()
            if block:
                self.jsonld_blocks.append(block)
        elif tag in {"td", "th"} and self._cell_parts is not None and self._row is not None:
            self._row.append(clean_text("".join(self._cell_parts)))
            self._cell_parts = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if any(self._row):
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            if self._table:
                self.tables.append(self._table)
                self.table_labels.append(self._table_label)
            self._table = None
        elif tag in {"h2", "h3", "h4"} and self._heading_parts is not None:
            heading = clean_text("".join(self._heading_parts))
            if heading:
                self._last_heading = heading
            self._heading_parts = None

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)
        if self._jsonld:
            self._jsonld_parts.append(data)
        if self._cell_parts is not None:
            self._cell_parts.append(data)
        if self._heading_parts is not None:
            self._heading_parts.append(data)


def robots_allowed(url: str) -> tuple[bool, str]:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(robots_url)
    try:
        request = Request(robots_url, headers={"User-Agent": USER_AGENT, "Accept": "text/plain,*/*;q=0.1"})
        with urlopen(request, timeout=20) as response:
            body = response.read(1_000_000).decode(response.headers.get_content_charset() or "utf-8", "replace")
        parser.parse(body.splitlines())
        return parser.can_fetch(USER_AGENT, url), robots_url
    except HTTPError as exc:
        if exc.code == 404:
            return True, robots_url
        return False, f"{robots_url} returned HTTP {exc.code}"
    except (URLError, TimeoutError, OSError) as exc:
        return False, f"robots.txt unavailable: {type(exc).__name__}"


def fetch_html(url: str) -> tuple[str, int, str]:
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
            "Accept-Language": "en-US,en;q=0.8",
        },
    )
    with urlopen(request, timeout=30) as response:
        final_url = response.geturl()
        content_type = response.headers.get("Content-Type", "")
        if "html" not in content_type.lower() and "json" not in content_type.lower():
            raise ValueError(f"Unsupported content type: {content_type}")
        body = response.read(5_000_000)
        encoding = response.headers.get_content_charset() or "utf-8"
        return body.decode(encoding, "replace"), response.status, final_url


def _walk_json(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _walk_json(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_json(nested)


def _first_text(value: Any) -> str:
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, dict):
        return _first_text(value.get("name") or value.get("title") or "")
    return ""


def _normal_price(value: Any) -> str | None:
    if isinstance(value, (int, float)):
        return f"{value:g}"
    if isinstance(value, str):
        match = re.fullmatch(r"\s*(\d{1,7}(?:[.,]\d{1,4})?)\s*", value)
        if match:
            return match.group(1).replace(",", ".")
    return None


def jsonld_offers(parser: SourceHTMLParser, provider: Provider, fetched_at: str, page_url: str) -> list[dict[str, Any]]:
    offers: list[dict[str, Any]] = []
    for block in parser.jsonld_blocks:
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        for node in _walk_json(data):
            node_types = node.get("@type", "")
            if isinstance(node_types, list):
                is_offer = "Offer" in node_types
            else:
                is_offer = str(node_types).lower() == "offer"
            if not is_offer:
                continue
            price = _normal_price(node.get("price"))
            currency = str(node.get("priceCurrency") or provider.currency_hint).upper()
            if price is None or not re.fullmatch(r"[A-Z]{3}", currency):
                continue
            title = _first_text(node.get("name")) or _first_text(node.get("itemOffered"))
            if not title:
                title = f"{provider.name} VPS plan"
            offer_url = urljoin(page_url, str(node.get("url") or page_url))
            record: dict[str, Any] = {
                "provider": provider.name,
                "title": title[:160],
                "price": price,
                "currency": currency,
                "billing_period": "unspecified",
                "offer_url": provider.affiliate_url or offer_url,
                "source_url": page_url,
                "fetched_at": fetched_at,
                "extraction": "official JSON-LD Offer",
                "affiliate": bool(provider.affiliate_url),
            }
            valid_until = node.get("priceValidUntil") or node.get("validThrough")
            if isinstance(valid_until, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:T.*)?", valid_until):
                record["valid_until"] = valid_until
            offers.append(record)
    return offers


def _price_from_cell(cell: str, currency_hint: str) -> tuple[str, str] | None:
    match = PRICE_RE.search(cell)
    if not match:
        return None
    amount = match.group("amount").replace(",", ".")
    symbol = match.group("symbol")
    currency = (match.group("code") or CURRENCY_SYMBOLS.get(symbol, currency_hint)).upper()
    return amount, currency


def table_offers(parser: SourceHTMLParser, provider: Provider, fetched_at: str, page_url: str) -> list[dict[str, Any]]:
    offers: list[dict[str, Any]] = []
    for table_number, table in enumerate(parser.tables):
        if len(table) < 2:
            continue
        header_index = -1
        price_index = -1
        for row_index, row in enumerate(table[:4]):
            for cell_index, cell in enumerate(row):
                lower = cell.lower()
                if ("/mo" in lower or "month" in lower or "monthly" in lower) and ("$" in cell or "€" in cell or "£" in cell or "price" in lower):
                    header_index, price_index = row_index, cell_index
                    break
            if price_index >= 0:
                break
        for row in table[header_index + 1 :] if header_index >= 0 else table:
            selected: tuple[str, str] | None = None
            selected_index = -1
            if 0 <= price_index < len(row):
                selected = _price_from_cell(row[price_index], provider.currency_hint)
                selected_index = price_index
            if selected is None:
                for idx, cell in enumerate(row):
                    lower = cell.lower()
                    if "/mo" in lower or "month" in lower or "monthly" in lower:
                        selected = _price_from_cell(cell, provider.currency_hint)
                        selected_index = idx
                        if selected:
                            break
            if selected is None:
                continue
            amount, currency = selected
            labels = [clean_text(cell) for idx, cell in enumerate(row) if idx != selected_index and clean_text(cell)]
            descriptive = [item for item in labels if not PRICE_RE.fullmatch(item)]
            if not descriptive:
                continue
            table_label = parser.table_labels[table_number] if table_number < len(parser.table_labels) else ""
            label_parts = ([table_label] if table_label else []) + descriptive[:3]
            plan = " · ".join(label_parts)[:120]
            offers.append(
                {
                    "provider": provider.name,
                    "title": f"{provider.name} — {plan}",
                    "price": amount,
                    "currency": currency,
                    "billing_period": "month",
                    "offer_url": provider.affiliate_url or page_url,
                    "source_url": page_url,
                    "fetched_at": fetched_at,
                    "extraction": "official HTML monthly-price table",
                    "affiliate": bool(provider.affiliate_url),
                }
            )
    return offers


def finalize_offers(records: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    now = datetime.now(timezone.utc)
    for record in records:
        valid_until = record.get("valid_until")
        if isinstance(valid_until, str):
            try:
                end = datetime.fromisoformat(valid_until.replace("Z", "+00:00"))
                if end.tzinfo is None:
                    end = end.replace(tzinfo=timezone.utc)
                if end < now:
                    record["expired"] = True
            except ValueError:
                record.pop("valid_until", None)
        key_material = "|".join(
            str(record.get(key, "")) for key in ("provider", "title", "price", "currency", "offer_url")
        )
        record["id"] = sha256(key_material.encode("utf-8")).hexdigest()[:16]
        unique[record["id"]] = record
    active = [record for record in unique.values() if not record.get("expired")]
    return active[:limit]


def scrape_provider(provider: Provider, limit: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    checked_at = utc_now()
    status: dict[str, Any] = {
        **asdict(provider),
        "checked_at": checked_at,
        "offer_count": 0,
        "status": "not_checked",
    }
    allowed, robots_note = robots_allowed(provider.source_url)
    status["robots"] = robots_note
    if not allowed:
        status.update(status="blocked_by_robots", message="Source was not fetched because robots.txt did not allow it.")
        return status, []
    try:
        html, http_status, final_url = fetch_html(provider.source_url)
        parser = SourceHTMLParser()
        parser.feed(html)
        extracted: list[dict[str, Any]] = []
        if provider.parser in {"auto", "jsonld"}:
            extracted.extend(jsonld_offers(parser, provider, checked_at, final_url))
        if provider.parser in {"auto", "table"}:
            extracted.extend(table_offers(parser, provider, checked_at, final_url))
        offers = finalize_offers(extracted, limit)
        status.update(
            status="ok",
            http_status=http_status,
            final_url=final_url,
            page_title=parser.page_title,
            offer_count=len(offers),
            message=("Verified offers extracted." if offers else "Official page checked; no unambiguous machine-readable monthly price was extracted."),
        )
        return status, offers
    except HTTPError as exc:
        status.update(status="http_error", http_status=exc.code, message=f"Official source returned HTTP {exc.code}.")
    except (URLError, TimeoutError, OSError) as exc:
        status.update(status="network_error", message=f"Official source fetch failed: {type(exc).__name__}.")
    except (ValueError, UnicodeError) as exc:
        status.update(status="parse_error", message=str(exc)[:240])
    return status, []


def main() -> int:
    config = load_site_config()
    try:
        limit = int(config.render.get("max_offers_per_provider", "12"))
    except ValueError:
        limit = 12
    providers: list[dict[str, Any]] = []
    offers: list[dict[str, Any]] = []
    for provider in config.providers:
        status, found = scrape_provider(provider, limit)
        providers.append(status)
        offers.extend(found)
        print(f"{provider.name}: {status['status']} ({len(found)} offers)")

    generated_at = utc_now()
    payload = {
        "meta": {
            "brand": config.brand,
            "niche": config.niche,
            "locale": config.locale,
            "currency": config.currency,
            "generated_at": generated_at,
            "source_count": len(providers),
            "offer_count": len(offers),
            "method": "deterministic official-page fetch; JSON-LD Offer and explicit monthly table extraction",
        },
        "providers": providers,
        "offers": offers,
    }
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(offers)} verified offers from {len(providers)} configured sources to {DATA_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
