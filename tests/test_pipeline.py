# ::ILANG [TYPE:test][ROLE:pipeline-verifier]
# ::RULE{验证配置驱动 抽取保守 构建结果无占位符}
# ::BOUNDARY{never:测试联网或依赖实时价格|scope:file}

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import build
from ilang_config import load_site_config
from scraper import SourceHTMLParser, finalize_offers, jsonld_offers, table_offers


ROOT = Path(__file__).resolve().parents[1]


class ConfigTests(unittest.TestCase):
    def test_real_config_is_ilang_and_has_providers(self) -> None:
        config = load_site_config()
        self.assertEqual(config.brand, "HostDealsHub")
        self.assertGreaterEqual(len(config.providers), 3)
        self.assertTrue(all(provider.source_url.startswith("https://") for provider in config.providers))
        self.assertEqual(len([fact for fact in config.provider_facts if fact.provider == "Vultr"]), 11)
        self.assertEqual(len([item for item in config.provider_referrals if item.provider == "Vultr"]), 1)

    def test_provider_change_drives_provider_page(self) -> None:
        source = (ROOT / ".ilang" / "site.ilang").read_text(encoding="utf-8")
        changed = source.replace("DigitalOcean |", "Fixture Cloud |", 1)
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            config_path = temp_path / "site.ilang"
            config_path.write_text(changed, encoding="utf-8")
            config = load_site_config(config_path)
            self.assertEqual(config.providers[0].name, "Fixture Cloud")
            data = {
                "meta": {"generated_at": "2026-01-01T00:00:00Z"},
                "providers": [{
                    "name": provider.name,
                    "homepage": provider.homepage,
                    "source_url": provider.source_url,
                    "affiliate_url": provider.affiliate_url,
                    "checked_at": "2026-01-01T00:00:00Z",
                    "offer_count": 0,
                    "status": "ok",
                    "message": "fixture",
                } for provider in config.providers],
                "offers": [],
            }
            original_site = build.SITE_DIR
            try:
                build.SITE_DIR = temp_path / "site"
                build.build_provider_pages(config, data, [])
                generated = temp_path / "site" / "providers" / "fixture-cloud" / "index.html"
                self.assertTrue(generated.exists())
                self.assertIn("Fixture Cloud VPS pricing", generated.read_text(encoding="utf-8"))
            finally:
                build.SITE_DIR = original_site


class ExtractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = load_site_config().providers[0]
        self.fetched = "2026-01-01T00:00:00Z"

    def test_jsonld_requires_real_price(self) -> None:
        parser = SourceHTMLParser()
        parser.feed('<script type="application/ld+json">{"@type":"Offer","name":"Small VPS","price":"4.00","priceCurrency":"USD","url":"/buy"}</script>')
        offers = jsonld_offers(parser, self.provider, self.fetched, "https://example.com/pricing")
        self.assertEqual(offers[0]["price"], "4.00")
        self.assertEqual(offers[0]["extraction"], "official JSON-LD Offer")

    def test_jsonld_without_price_is_rejected(self) -> None:
        parser = SourceHTMLParser()
        parser.feed('<script type="application/ld+json">{"@type":"Offer","name":"Mystery VPS"}</script>')
        self.assertEqual(jsonld_offers(parser, self.provider, self.fetched, "https://example.com"), [])

    def test_monthly_table_header_selects_monthly_column(self) -> None:
        parser = SourceHTMLParser()
        parser.feed("<table><tr><th>Plan</th><th>$/hr</th><th>$/mo</th></tr><tr><td>1 GB VPS</td><td>$0.01</td><td>$6.00</td></tr></table>")
        offers = table_offers(parser, self.provider, self.fetched, "https://example.com/pricing")
        self.assertEqual(offers[0]["price"], "6.00")
        self.assertEqual(offers[0]["billing_period"], "month")

    def test_expired_offer_is_removed(self) -> None:
        expired = {"provider": "Fixture", "title": "Old", "price": "1", "currency": "USD", "offer_url": "https://example.com", "valid_until": "2020-01-01"}
        self.assertEqual(finalize_offers([expired], 10), [])


class GeneratedSiteTests(unittest.TestCase):
    def test_vultr_page_has_verified_facts_and_one_disclosed_referral(self) -> None:
        page = (ROOT / "site" / "providers" / "vultr" / "index.html").read_text(encoding="utf-8")
        self.assertIn("$2.50", page)
        self.assertIn("$640.00", page)
        self.assertIn("IPv6 only", page)
        self.assertEqual(page.count("https://www.vultr.com/?ref=7999218"), 1)
        self.assertIn("This is an affiliate referral link.", page)
        self.assertIn('<a href="https://www.vultr.com/pricing/" rel="nofollow noopener">', page)

    def test_generated_site_has_core_files_and_no_tokens(self) -> None:
        required = [ROOT / "site" / "index.html", ROOT / "site" / "404.html", ROOT / "site" / "robots.txt", ROOT / "site" / "sitemap.xml", ROOT / "site" / "data" / "offers.json"]
        for path in required:
            self.assertTrue(path.exists(), str(path))
        index = required[0].read_text(encoding="utf-8")
        self.assertNotIn("[[", index)
        self.assertIn('rel="canonical"', index)
        self.assertIn('application/ld+json', index)
        json.loads((ROOT / "site" / "data" / "offers.json").read_text(encoding="utf-8"))

    def test_trust_pages_and_brand_are_generated(self) -> None:
        for name in ("about", "contact", "privacy"):
            page = ROOT / "site" / name / "index.html"
            self.assertTrue(page.exists(), str(page))
            html = page.read_text(encoding="utf-8")
            self.assertIn("HostDealsHub", html)
        index = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn(">vps-deals<", index)
        self.assertIn('/privacy/', index)

    def test_contact_address_is_live_without_setup_disclaimer(self) -> None:
        pages = [
            ROOT / "site" / "about" / "index.html",
            ROOT / "site" / "contact" / "index.html",
            ROOT / "site" / "privacy" / "index.html",
        ]
        for page in pages:
            html = page.read_text(encoding="utf-8")
            self.assertIn("contact@hostdealshub.com", html)
            self.assertNotIn("may not yet accept messages", html)
            self.assertNotIn("scheduled for a separate", html)

    def test_custom_404_disables_spa_fallback(self) -> None:
        page = (ROOT / "site" / "404.html").read_text(encoding="utf-8")
        sitemap = (ROOT / "site" / "sitemap.xml").read_text(encoding="utf-8")
        self.assertIn('name="robots" content="noindex,follow"', page)
        self.assertIn("That page does not exist.", page)
        self.assertNotIn("/404.html", sitemap)
        self.assertFalse((ROOT / "site" / "_redirects").exists())


if __name__ == "__main__":
    unittest.main()
