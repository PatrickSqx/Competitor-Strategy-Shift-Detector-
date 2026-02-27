from __future__ import annotations

from collections import defaultdict
from urllib.parse import urlparse

from app.adapters.tavily_client import TavilyClient
from app.models import DiscoveryCandidate, PlatformCoverage

PRODUCT_PATH_HINTS = {
    'bestbuy.com': ['/site/'],
    'microcenter.com': ['/product/'],
    'amazon.com': ['/dp/', '/gp/product/'],
}
BLOCKED_TOKENS = ('review', 'blog', 'guide', 'news', 'category', 'search', 'deals')


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
        for candidate in raw_candidates:
            if self._is_product_candidate(candidate):
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
                        note=f'Found {len(domain_candidates)} product-page candidates.',
                    )
                )
            else:
                platform_statuses.append(
                    PlatformCoverage(
                        platform=self._platform_name(domain),
                        status='missing',
                        candidate_count=0,
                        note='No product-detail candidates found for this platform.',
                    )
                )

        if not candidates:
            warnings.append('No product pages were discovered. Try a more specific electronics model query.')
        return normalized_query, candidates, platform_statuses, warnings

    def _is_product_candidate(self, candidate: DiscoveryCandidate) -> bool:
        parsed = urlparse(candidate.url)
        hostname = parsed.netloc.lower()
        path = parsed.path.lower()
        if candidate.domain not in hostname:
            return False
        if any(token in path for token in BLOCKED_TOKENS):
            return False
        hints = PRODUCT_PATH_HINTS.get(candidate.domain, [])
        if hints and not any(hint in path for hint in hints):
            return False
        title = candidate.title.lower()
        if any(token in title for token in BLOCKED_TOKENS):
            return False
        return True

    @staticmethod
    def _platform_name(domain: str) -> str:
        if 'bestbuy' in domain:
            return 'Best Buy'
        if 'microcenter' in domain:
            return 'Micro Center'
        if 'amazon' in domain:
            return 'Amazon'
        return domain


def platform_name_for_domain(domain: str) -> str:
    return QueryDiscoveryService._platform_name(domain)
