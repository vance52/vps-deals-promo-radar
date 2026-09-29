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
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
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
OFFICIAL_NOT_STATED = "official_page_not_stated"
CHALLENGE_RE = re.compile(
    r"challenge-platform|cf-chl-|Checking your browser|Just a moment|Access Denied|请稍候",
    re.IGNORECASE,
)


class BrowserFetchError(RuntimeError):
    """Raised when a normal browser render cannot return usable official content."""


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


class VisibleTextParser(HTMLParser):
    """Collect visible text while excluding scripts, styles, and SVG internals."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if not self._ignored and tag in {"script", "style", "svg", "noscript"}:
            self._ignored = tag

    def handle_endtag(self, tag: str) -> None:
        if tag == self._ignored:
            self._ignored = ""

    def handle_data(self, data: str) -> None:
        if not self._ignored and clean_text(data):
            self.parts.append(clean_text(data))

    @property
    def text(self) -> str:
        return clean_text(" ".join(self.parts))


class HostingerCardParser(HTMLParser):
    """Extract the text of each official Hostinger VPS pricing card."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: dict[str, str] = {}
        self._slug = ""
        self._div_depth = 0
        self._ignored = ""
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        data_qa = values.get("data-qa") or ""
        if not self._slug and tag == "div" and data_qa.startswith("product-slug_vps:vps_kvm_"):
            self._slug = data_qa.rsplit("_", 1)[-1]
            self._div_depth = 1
            self._parts = []
            return
        if not self._slug:
            return
        if tag == "div":
            self._div_depth += 1
        if not self._ignored and tag in {"script", "style", "svg", "noscript"}:
            self._ignored = tag

    def handle_endtag(self, tag: str) -> None:
        if not self._slug:
            return
        if tag == self._ignored:
            self._ignored = ""
        if tag == "div":
            self._div_depth -= 1
            if self._div_depth == 0:
                self.cards.setdefault(self._slug, clean_text(" ".join(self._parts)))
                self._slug = ""
                self._parts = []

    def handle_data(self, data: str) -> None:
        if self._slug and not self._ignored and clean_text(data):
            self._parts.append(clean_text(data))


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


def find_browser_executable() -> str:
    configured = os.environ.get("CHROME_PATH", "").strip()
    candidates = [
        configured,
        shutil.which("google-chrome") or "",
        shutil.which("google-chrome-stable") or "",
        shutil.which("chrome") or "",
        shutil.which("chromium") or "",
        shutil.which("chromium-browser") or "",
        shutil.which("msedge") or "",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise BrowserFetchError("No supported Chrome or Edge executable was found.")


def fetch_browser_html(url: str) -> tuple[str, str]:
    """Render an allowed public page in a stock browser without evasion or login."""
    browser = find_browser_executable()
    with tempfile.TemporaryDirectory(prefix="vps-deals-browser-") as profile:
        command = [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--disable-background-networking",
            "--no-first-run",
            "--no-default-browser-check",
            f"--user-data-dir={profile}",
            "--dump-dom",
            url,
        ]
        try:
            completed = subprocess.run(command, capture_output=True, timeout=90, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            raise BrowserFetchError(f"Browser render failed: {type(exc).__name__}") from exc
    if completed.returncode != 0:
        raise BrowserFetchError(f"Browser exited with code {completed.returncode}.")
    html = completed.stdout.decode("utf-8", "replace")
    if not html.strip():
        raise BrowserFetchError("Browser returned an empty document.")
    if CHALLENGE_RE.search(html):
        raise BrowserFetchError("Browser received an anti-bot, challenge, or access-denied page.")
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    title = clean_text(re.sub(r"<[^>]+>", " ", title_match.group(1))) if title_match else ""
    return html, title


def hostinger_plan_details(html: str) -> dict[str, dict[str, Any]]:
    parser = HostingerCardParser()
    parser.feed(html)
    details: dict[str, dict[str, Any]] = {}
    for text in parser.cards.values():
        title_match = re.search(r"\b(KVM\s+\d+)\b", text, re.IGNORECASE)
        if not title_match:
            continue
        title = clean_text(title_match.group(1)).upper().replace("KVM ", "KVM ")
        billing_match = re.search(r"\$\s*\d+(?:\.\d+)?\s*/mo\b", text, re.IGNORECASE)
        renewal_match = re.search(r"Renews at .*?(?:Cancel anytime\.)", text, re.IGNORECASE)
        resource_patterns = [
            r"\b\d+\s+vCPU(?:\s+cores?)?\b",
            r"\b\d+\s+GB\s+RAM\b",
            r"\b\d+\s+GB\s+NVMe\s+disk\s+space\b",
            r"\b\d+\s+TB\s+bandwidth\b",
        ]
        resources: list[str] = []
        for pattern in resource_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                resources.append(clean_text(match.group(0)))
        details[title] = {
            "billing_period": "month" if billing_match else "",
            "billing_evidence": clean_text(billing_match.group(0)) if billing_match else "",
            "renewal": clean_text(renewal_match.group(0)) if renewal_match else None,
            "resources": " · ".join(resources) if resources else None,
        }
    return details


def ovh_browser_offers(html: str, provider: Provider, fetched_at: str, page_url: str) -> list[dict[str, Any]]:
    parser = VisibleTextParser()
    parser.feed(html)
    text = parser.text
    cards = list(
        re.finditer(
            r"\b(VPS-\d+)\s+From\s+\$\s*(\d+(?:\.\d+)?)\s*/month\s+Configure\s+(.*?)(?=\bVPS-\d+\s+From\s+\$|$)",
            text,
            re.IGNORECASE | re.DOTALL,
        )
    )
    offers: list[dict[str, Any]] = []
    for card in cards:
        plan, price, body = card.groups()
        resource_patterns = [
            r"\b\d+\s+vCores?\b",
            r"\b\d+\s*GB\s+RAM\b",
            r"\b\d+\s*GB\s+SSD\s+NVMe\b",
            r"\b\d+(?:\.\d+)?\s*(?:Mbps|Gbps)\s+public\s+bandwidth\b",
        ]
        resources: list[str] = []
        for pattern in resource_patterns:
            match = re.search(pattern, body, re.IGNORECASE)
            if match:
                resources.append(clean_text(match.group(0)))
        if not resources:
            continue
        resource_text = " · ".join(resources)
        offers.append(
            {
                "provider": provider.name,
                "title": f"{provider.name} — {plan} · {resource_text}"[:160],
                "price": price,
                "currency": "USD",
                "billing_period": "month",
                "billing_evidence": f"${price}/month",
                "renewal": None,
                "renewal_status": OFFICIAL_NOT_STATED,
                "resources": resource_text,
                "resources_status": "verified",
                "offer_url": provider.affiliate_url or page_url,
                "source_url": page_url,
                "fetched_at": fetched_at,
                "extraction": "official browser-rendered pricing card",
                "affiliate": bool(provider.affiliate_url),
            }
        )
    return offers


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
            headers = table[header_index] if header_index >= 0 else []
            resource_parts: list[str] = []
            for idx, value in enumerate(row):
                if idx == selected_index or idx >= len(headers):
                    continue
                header = clean_text(headers[idx])
                item = clean_text(value)
                lower_header = header.lower()
                if not header or not item or len(item) > 160 or "{" in item:
                    continue
                if any(marker in lower_header for marker in ("price", "/hr", "/mo", "month")):
                    continue
                if lower_header in {"plan", "plan name", "bundle name", "name"}:
                    continue
                resource_parts.append(f"{header}: {item}")
            offers.append(
                {
                    "provider": provider.name,
                    "title": f"{provider.name} — {plan}",
                    "price": amount,
                    "currency": currency,
                    "billing_period": "month",
                    "billing_evidence": headers[selected_index] if 0 <= selected_index < len(headers) else "monthly price column",
                    "renewal": None,
                    "renewal_status": OFFICIAL_NOT_STATED,
                    "resources": " · ".join(resource_parts) if resource_parts else None,
                    "resources_status": "verified" if resource_parts else OFFICIAL_NOT_STATED,
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
        if record.get("billing_period"):
            record.setdefault("billing_status", "verified")
        else:
            record["billing_period"] = ""
            record["billing_status"] = OFFICIAL_NOT_STATED
        if record.get("renewal"):
            record.setdefault("renewal_status", "verified")
        else:
            record["renewal"] = None
            record["renewal_status"] = OFFICIAL_NOT_STATED
        if record.get("resources"):
            record.setdefault("resources_status", "verified")
        else:
            record["resources"] = None
            record["resources_status"] = OFFICIAL_NOT_STATED
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


def extract_offers(
    html: str,
    provider: Provider,
    fetched_at: str,
    page_url: str,
    limit: int,
    *,
    browser_rendered: bool = False,
) -> tuple[SourceHTMLParser, list[dict[str, Any]]]:
    parser = SourceHTMLParser()
    parser.feed(html)
    extracted: list[dict[str, Any]] = []
    if provider.parser in {"auto", "jsonld", "browser_auto", "source_only"}:
        extracted.extend(jsonld_offers(parser, provider, fetched_at, page_url))
    if provider.parser in {"auto", "table", "browser_auto", "source_only"}:
        extracted.extend(table_offers(parser, provider, fetched_at, page_url))
    if provider.name == "Hostinger":
        details = hostinger_plan_details(html)
        for offer in extracted:
            detail = details.get(str(offer.get("title", "")).upper())
            if detail:
                offer.update(detail)
    if browser_rendered and provider.name == "OVHcloud":
        extracted.extend(ovh_browser_offers(html, provider, fetched_at, page_url))
    return parser, finalize_offers(extracted, limit)


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
    standard_error = ""
    try:
        html, http_status, final_url = fetch_html(provider.source_url)
        parser, offers = extract_offers(html, provider, checked_at, final_url, limit)
        status.update(
            status="ok",
            http_status=http_status,
            final_url=final_url,
            page_title=parser.page_title,
            offer_count=len(offers),
            message=("Verified offers extracted." if offers else "Official page checked; no unambiguous machine-readable monthly price was extracted."),
        )
        if offers or provider.parser != "browser_auto":
            return status, offers
        standard_error = status["message"]
    except HTTPError as exc:
        status.update(status="http_error", http_status=exc.code, message=f"Official source returned HTTP {exc.code}.")
        standard_error = status["message"]
    except (URLError, TimeoutError, OSError) as exc:
        status.update(status="network_error", message=f"Official source fetch failed: {type(exc).__name__}.")
        standard_error = status["message"]
    except (ValueError, UnicodeError) as exc:
        status.update(status="parse_error", message=str(exc)[:240])
        standard_error = status["message"]
    if provider.parser == "browser_auto":
        try:
            browser_html, browser_title = fetch_browser_html(provider.source_url)
            _, offers = extract_offers(
                browser_html,
                provider,
                checked_at,
                provider.source_url,
                limit,
                browser_rendered=True,
            )
            if offers:
                status.update(
                    status="browser_ok",
                    page_title=browser_title,
                    offer_count=len(offers),
                    fetch_method="stock Chrome/Edge rendered DOM",
                    message="Verified offers extracted from the official page after a normal browser render.",
                )
                return status, offers
            status.update(
                status="browser_no_offers",
                page_title=browser_title,
                offer_count=0,
                fetch_method="stock Chrome/Edge rendered DOM",
                message="Browser rendered the official page, but no unambiguous price and specification pair was extracted.",
                standard_fetch=standard_error,
            )
        except BrowserFetchError as exc:
            status.update(
                status="browser_unavailable",
                offer_count=0,
                fetch_method="stock Chrome/Edge rendered DOM",
                message=str(exc),
                standard_fetch=standard_error,
            )
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
