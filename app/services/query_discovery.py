from __future__ import annotations

from collections import defaultdict
import re
from urllib.parse import urlparse

from app.adapters.tavily_client import TavilyClient
from app.models import DiscoveryCandidate, PlatformCoverage

PRODUCT_PATH_HINTS = {
    'bestbuy.com': ['/site/'],
    'microcenter.com': ['/product/'],
    'amazon.com': ['/dp/', '/gp/product/'],
}
BLOCKED_TOKENS = ('review', 'blog', 'guide', 'news', 'category', 'search', 'deals')
NOISE_QUERY_TOKENS = {'buy', 'price', 'product', 'electronics', 'site'}


class QueryDiscoveryService:
    def __init__(self, tavily: TavilyClient, supported_domains: list[str], max_results_per_domain: int = 5) -> None:
        self.tavily = tavily
        self.supported_domains = supported_domains
        self.max_results_per_domain = max_results_per_domain

    @staticmethod
    def normalize_query(query: str) -> str:
        return ' '.join(query.strip().lower().split())

    def discover(self, query: str) -> tuple[str, list[DiscoveryCandidate], list[PlatformCoverage], list[str]]:
        normalized_query = self.normalize_query(query)
        warnings: list[str] = []
        if not self.tavily.enabled:
            platform_statuses = [
                PlatformCoverage(platform=self._platform_name(domain), status='missing', candidate_count=0, note='Tavily is not configured.')
                for domain in self.supported_domains
            ]
            warnings.append('Tavily is not configured, so product discovery is unavailable.')
            return normalized_query, [], platform_statuses, warnings

        raw_candidates = self.tavily.search_products(
            query=normalized_query,
            domains=self.supported_domains,
            max_results_per_domain=self.max_results_per_domain,
        )
        grouped: dict[str, list[DiscoveryCandidate]] = defaultdict(list)
        raw_counts: dict[str, int] = defaultdict(int)
        for candidate in raw_candidates:
            raw_counts[candidate.domain] += 1
            if self._is_product_candidate(candidate, normalized_query):
                grouped[candidate.domain].append(candidate)

        candidates: list[DiscoveryCandidate] = []
        platform_statuses: list[PlatformCoverage] = []
        for domain in self.supported_domains:
            domain_candidates = grouped.get(domain, [])[: self.max_results_per_domain]
            candidates.extend(domain_candidates)
            if domain_candidates:
                platform_statuses.append(
                    PlatformCoverage(
                        platform=self._platform_name(domain),
                        status='found',
                        candidate_count=len(domain_candidates),
                        note=f'Found {len(domain_candidates)} product-page candidates from {raw_counts.get(domain, len(domain_candidates))} discovered URLs.',
                    )
                )
            else:
                platform_statuses.append(
                    PlatformCoverage(
                        platform=self._platform_name(domain),
                        status='missing',
                        candidate_count=0,
                        note=f'No product-detail candidates found from {raw_counts.get(domain, 0)} discovered URLs.',
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


def platform_name_for_domain(domain: str) -> str:
    return QueryDiscoveryService._platform_name(domain)


def _normalize_token(token: str) -> str:
    return re.sub(r'[^a-z0-9]+', '', token.lower())
