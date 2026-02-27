from __future__ import annotations

from collections import defaultdict
import re
from urllib.parse import quote_plus, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.adapters.tavily_client import TavilyClient
from app.models import DiscoveryCandidate, PlatformCoverage

PRODUCT_PATH_HINTS = {
    'bestbuy.com': ['/site/'],
    'microcenter.com': ['/product/'],
    'amazon.com': ['/dp/', '/gp/product/'],
}
BLOCKED_TOKENS = ('review', 'blog', 'guide', 'news', 'category', 'search', 'deals')
NOISE_QUERY_TOKENS = {'buy', 'price', 'product', 'electronics', 'site'}
SEARCH_URLS = {
    'bestbuy.com': 'https://www.bestbuy.com/site/searchpage.jsp?st={query}',
    'microcenter.com': 'https://www.microcenter.com/search/search_results.aspx?Ntt={query}',
    'amazon.com': 'https://www.amazon.com/s?k={query}',
}


class QueryDiscoveryService:
    def __init__(
        self,
        tavily: TavilyClient,
        supported_domains: list[str],
        max_results_per_domain: int = 5,
        timeout_seconds: float = 8.0,
    ) -> None:
        self.tavily = tavily
        self.supported_domains = supported_domains
        self.max_results_per_domain = max_results_per_domain
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def normalize_query(query: str) -> str:
        return ' '.join(query.strip().lower().split())

    def discover(self, query: str) -> tuple[str, list[DiscoveryCandidate], list[PlatformCoverage], list[str]]:
        normalized_query = self.normalize_query(query)
        warnings: list[str] = []
        raw_candidates: list[DiscoveryCandidate] = []
        if self.tavily.enabled:
            raw_candidates = self.tavily.search_products(
                query=normalized_query,
                domains=self.supported_domains,
                max_results_per_domain=self.max_results_per_domain,
            )
        else:
            warnings.append('Tavily is not configured. Falling back to retailer site search only.')

        grouped: dict[str, list[DiscoveryCandidate]] = defaultdict(list)
        raw_counts: dict[str, int] = defaultdict(int)
        source_notes: dict[str, str] = {}
        for candidate in raw_candidates:
            raw_counts[candidate.domain] += 1
            if self._is_product_candidate(candidate, normalized_query):
                grouped[candidate.domain].append(candidate)

        candidates: list[DiscoveryCandidate] = []
        platform_statuses: list[PlatformCoverage] = []
        for domain in self.supported_domains:
            domain_candidates = grouped.get(domain, [])[: self.max_results_per_domain]
            if not domain_candidates:
                fallback_candidates, fallback_note = self._discover_from_site_search(domain, normalized_query)
                if fallback_candidates:
                    domain_candidates = fallback_candidates[: self.max_results_per_domain]
                    source_notes[domain] = fallback_note
                elif fallback_note:
                    source_notes[domain] = fallback_note
            candidates.extend(domain_candidates)
            if domain_candidates:
                platform_statuses.append(
                    PlatformCoverage(
                        platform=self._platform_name(domain),
                        status='found',
                        candidate_count=len(domain_candidates),
                        note=source_notes.get(
                            domain,
                            f'Found {len(domain_candidates)} product-page candidates from {raw_counts.get(domain, len(domain_candidates))} Tavily URLs.',
                        ),
                    )
                )
            else:
                platform_statuses.append(
                    PlatformCoverage(
                        platform=self._platform_name(domain),
                        status='missing',
                        candidate_count=0,
                        note=source_notes.get(
                            domain,
                            f'No product-detail candidates found from {raw_counts.get(domain, 0)} Tavily URLs.',
                        ),
                    )
                )

        if not candidates:
            warnings.append('No product pages were discovered. Try a more specific electronics model query.')
        return normalized_query, candidates, platform_statuses, warnings

    def _is_product_candidate(self, candidate: DiscoveryCandidate, normalized_query: str) -> bool:
        parsed = urlparse(candidate.url)
        hostname = parsed.netloc.lower()
        path = parsed.path.lower()
        if candidate.domain not in hostname:
            return False
        if any(token in path for token in BLOCKED_TOKENS):
            return False
        hints = PRODUCT_PATH_HINTS.get(candidate.domain, [])
        query_tokens = self._query_tokens(normalized_query)
        has_hint = any(hint in path for hint in hints) if hints else False
        title = candidate.title.lower()
        if any(token in title for token in BLOCKED_TOKENS):
            return False
        overlap = self._token_overlap(query_tokens, f"{candidate.title} {candidate.snippet} {path}")
        if has_hint:
            return overlap >= 0.2
        return overlap >= 0.45

    @staticmethod
    def _platform_name(domain: str) -> str:
        if 'bestbuy' in domain:
            return 'Best Buy'
        if 'microcenter' in domain:
            return 'Micro Center'
        if 'amazon' in domain:
            return 'Amazon'
        return domain

    @staticmethod
    def _query_tokens(normalized_query: str) -> set[str]:
        tokens = {_normalize_token(token) for token in normalized_query.split()}
        return {
            token
            for token in tokens
            if token and token not in NOISE_QUERY_TOKENS and (len(token) >= 3 or any(char.isdigit() for char in token))
        }

    @staticmethod
    def _token_overlap(query_tokens: set[str], haystack: str) -> float:
        if not query_tokens:
            return 0.0
        haystack_tokens = {_normalize_token(token) for token in haystack.split()}
        hits = sum(1 for token in query_tokens if token in haystack_tokens)
        return hits / max(len(query_tokens), 1)

    def _discover_from_site_search(self, domain: str, normalized_query: str) -> tuple[list[DiscoveryCandidate], str]:
        search_url = SEARCH_URLS.get(domain)
        if not search_url:
            return [], ''
        url = search_url.format(query=quote_plus(normalized_query))
        try:
            response = httpx.get(
                url,
                timeout=self.timeout_seconds,
                follow_redirects=True,
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36',
                    'Accept-Language': 'en-US,en;q=0.9',
                },
            )
            response.raise_for_status()
        except Exception as exc:
            return [], f'Site-search fallback failed: {exc.__class__.__name__}.'

        candidates, scanned_count = self._extract_site_search_candidates(
            domain=domain,
            normalized_query=normalized_query,
            base_url=str(response.url),
            html=response.text,
        )
        if candidates:
            return (
                candidates[: self.max_results_per_domain],
                f'Site-search fallback found {len(candidates[: self.max_results_per_domain])} product-page candidates from {scanned_count} scanned links.',
            )
        return [], f'Site-search fallback found no product-detail candidates in {scanned_count} scanned links.'

    def _extract_site_search_candidates(
        self,
        domain: str,
        normalized_query: str,
        base_url: str,
        html: str,
    ) -> tuple[list[DiscoveryCandidate], int]:
        soup = BeautifulSoup(html, 'html.parser')
        candidates: list[DiscoveryCandidate] = []
        seen_urls: set[str] = set()
        scanned_count = 0
        query_tokens = self._query_tokens(normalized_query)

        for node in soup.find_all('a', href=True):
            href = str(node.get('href', '')).strip()
            if not href:
                continue
            absolute_url = urljoin(base_url, href)
            parsed = urlparse(absolute_url)
            hostname = parsed.netloc.lower()
            if domain not in hostname:
                continue

            scanned_count += 1
            title = (
                node.get_text(' ', strip=True)
                or str(node.get('aria-label', '')).strip()
                or str(node.get('title', '')).strip()
            )
            snippet = str(node.get('aria-label', '')).strip()
            if not title and not snippet:
                continue
            if absolute_url in seen_urls:
                continue

            overlap = self._token_overlap(query_tokens, f'{title} {snippet} {absolute_url}')
            candidate = DiscoveryCandidate(
                platform=self._platform_name(domain),
                domain=domain,
                title=title[:240],
                url=absolute_url,
                snippet=snippet[:360],
                score=overlap,
            )
            if not self._is_product_candidate(candidate, normalized_query):
                continue

            seen_urls.add(absolute_url)
            candidates.append(candidate)

        return candidates, scanned_count


def platform_name_for_domain(domain: str) -> str:
    return QueryDiscoveryService._platform_name(domain)


def _normalize_token(token: str) -> str:
    return re.sub(r'[^a-z0-9]+', '', token.lower())
