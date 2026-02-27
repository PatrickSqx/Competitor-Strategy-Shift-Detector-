from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from app.models import DiscoveryCandidate, OfferView
from app.services.product_matcher import extract_model_identifier, infer_brand

PROMO_PATTERNS = ('sale', 'save', 'discount', 'coupon', 'deal', '% off', 'clearance', 'gift card')
AVAILABILITY_MAP = {
    'instock': 'in stock',
    'outofstock': 'out of stock',
    'preorder': 'preorder',
    'limitedavailability': 'limited availability',
}


class ProductExtractorService:
    def __init__(self, timeout_seconds: float = 8.0) -> None:
        self.timeout_seconds = timeout_seconds

    def extract_many(self, candidates: list[DiscoveryCandidate]) -> tuple[list[OfferView], list[str]]:
        offers: list[OfferView] = []
        warnings: list[str] = []
        seen_urls: set[str] = set()
        for candidate in candidates:
            if candidate.url in seen_urls:
                continue
            seen_urls.add(candidate.url)
            offer = self.extract_one(candidate)
            if offer is None:
                warnings.append(f'Could not parse a priced product page for {candidate.platform}.')
                continue
            offers.append(offer)
        return offers, warnings

    def extract_one(self, candidate: DiscoveryCandidate) -> OfferView | None:
        try:
            response = httpx.get(
                candidate.url,
                timeout=self.timeout_seconds,
                headers={'User-Agent': 'pricing-compare-agent/1.0'},
                follow_redirects=True,
            )
            response.raise_for_status()
        except Exception:
            return None

        html = response.text
        soup = BeautifulSoup(html, 'html.parser')
        page_text = soup.get_text(' ', strip=True)
        jsonld_product = self._extract_product_jsonld(soup)
        meta = self._extract_meta(soup)

        title = self._first_non_empty(
            self._from_product(jsonld_product, 'name'),
            meta.get('og:title', ''),
            meta.get('twitter:title', ''),
            candidate.title,
            soup.title.get_text(strip=True) if soup.title else '',
        )
        brand = self._first_non_empty(
            self._brand_from_product(jsonld_product),
            infer_brand(title),
        )
        model = self._first_non_empty(
            self._from_product(jsonld_product, 'model'),
            self._from_product(jsonld_product, 'mpn'),
            self._from_product(jsonld_product, 'sku'),
            extract_model_identifier(title),
        )
        price = self._extract_price(jsonld_product, meta, soup, html)
        if price is None:
            return None
        currency = self._first_non_empty(
            self._offer_value(jsonld_product, 'priceCurrency'),
            meta.get('product:price:currency', ''),
            'USD',
        )
        promo_text = self._extract_promo_text(page_text, candidate.snippet)
        availability = self._normalize_availability(
            self._offer_value(jsonld_product, 'availability') or meta.get('product:availability', '') or page_text
        )
        canonical = self._first_non_empty(self._canonical_url(soup), response.url, candidate.url)
        image = self._first_non_empty(
            self._from_product(jsonld_product, 'image'),
            meta.get('og:image', ''),
        )
        parse_notes = []
        if jsonld_product:
            parse_notes.append('jsonld')
        else:
            parse_notes.append('fallback')
        if candidate.snippet and promo_text == candidate.snippet:
            parse_notes.append('promo-from-search-snippet')

        return OfferView(
            platform=candidate.platform,
            title=title or candidate.title or candidate.url,
            brand=brand,
            model=model,
            price=price,
            currency=currency.upper()[:3],
            promo_text=promo_text,
            availability=availability,
            url=canonical,
            image=image,
            match_confidence=0.0,
            source_domain=candidate.domain,
            match_key='',
            parse_notes=parse_notes,
        )

    def _extract_product_jsonld(self, soup: BeautifulSoup) -> dict[str, Any] | None:
        for script in soup.find_all('script', attrs={'type': 'application/ld+json'}):
            if not script.string:
                continue
            try:
                payload = json.loads(script.string)
            except json.JSONDecodeError:
                continue
            for node in self._iter_json_nodes(payload):
                type_value = node.get('@type')
                if type_value == 'Product' or (isinstance(type_value, list) and 'Product' in type_value):
                    return node
        return None

    def _iter_json_nodes(self, payload: Any):
        if isinstance(payload, list):
            for item in payload:
                yield from self._iter_json_nodes(item)
            return
        if isinstance(payload, dict):
            yield payload
            if '@graph' in payload:
                yield from self._iter_json_nodes(payload['@graph'])
            for value in payload.values():
                if isinstance(value, (dict, list)):
                    yield from self._iter_json_nodes(value)

    @staticmethod
    def _extract_meta(soup: BeautifulSoup) -> dict[str, str]:
        meta: dict[str, str] = {}
        for node in soup.find_all('meta'):
            key = node.get('property') or node.get('name') or node.get('itemprop')
            value = node.get('content')
            if key and value:
                meta[key.lower()] = value.strip()
        return meta

    @staticmethod
    def _from_product(product: dict[str, Any] | None, key: str) -> str:
        if not product:
            return ''
        value = product.get(key)
        if isinstance(value, list):
            if not value:
                return ''
            value = value[0]
        if isinstance(value, dict):
            value = value.get('name') or value.get('@id') or ''
        return str(value).strip()

    @staticmethod
    def _brand_from_product(product: dict[str, Any] | None) -> str:
        if not product:
            return ''
        brand = product.get('brand', '')
        if isinstance(brand, dict):
            return str(brand.get('name', '')).strip()
        if isinstance(brand, list) and brand:
            first = brand[0]
            if isinstance(first, dict):
                return str(first.get('name', '')).strip()
            return str(first).strip()
        return str(brand).strip()

    @staticmethod
    def _offer_value(product: dict[str, Any] | None, key: str) -> str:
        if not product:
            return ''
        offers = product.get('offers')
        if isinstance(offers, list):
            offers = offers[0] if offers else None
        if isinstance(offers, dict):
            value = offers.get(key, '')
            return str(value).strip()
        return ''

    def _extract_price(
        self,
        product: dict[str, Any] | None,
        meta: dict[str, str],
        soup: BeautifulSoup,
        html: str,
    ) -> float | None:
        candidates = [
            self._offer_value(product, 'price'),
            meta.get('product:price:amount', ''),
            meta.get('price', ''),
            meta.get('itemprop', ''),
        ]
        itemprop_price = soup.select_one('[itemprop="price"]')
        if itemprop_price:
            candidates.append(itemprop_price.get('content') or itemprop_price.get_text(' ', strip=True))
        candidates.append(self._regex_find_price(html))
        for candidate in candidates:
            value = self._parse_price(candidate)
            if value is not None:
                return value
        return None

    @staticmethod
    def _parse_price(value: str | None) -> float | None:
        if not value:
            return None
        match = re.search(r'([0-9]+(?:\.[0-9]{1,2})?)', str(value).replace(',', ''))
        if not match:
            return None
        try:
            return float(match.group(1))
        except ValueError:
            return None

    @staticmethod
    def _regex_find_price(html: str) -> str:
        patterns = [
            r'"price"\s*:\s*"?([0-9]+(?:\.[0-9]{1,2})?)',
            r'\$\s*([0-9]+(?:\.[0-9]{1,2})?)',
        ]
        for pattern in patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                return match.group(1)
        return ''

    @staticmethod
    def _extract_promo_text(page_text: str, snippet: str) -> str:
        lowered = page_text.lower()
        for token in PROMO_PATTERNS:
            idx = lowered.find(token)
            if idx >= 0:
                return page_text[max(0, idx - 40): idx + 120].strip()
        return snippet.strip()

    @staticmethod
    def _normalize_availability(value: str) -> str:
        lowered = value.lower().replace(' ', '')
        for key, label in AVAILABILITY_MAP.items():
            if key in lowered:
                return label
        if 'out of stock' in value.lower():
            return 'out of stock'
        if 'in stock' in value.lower():
            return 'in stock'
        return 'unknown'

    @staticmethod
    def _canonical_url(soup: BeautifulSoup) -> str:
        node = soup.find('link', attrs={'rel': 'canonical'})
        if node and node.get('href'):
            return str(node['href']).strip()
        return ''

    @staticmethod
    def _first_non_empty(*values: str) -> str:
        for value in values:
            if value and str(value).strip():
                return str(value).strip()
        return ''
