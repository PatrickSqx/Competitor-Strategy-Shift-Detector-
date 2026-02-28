from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import re
from urllib.parse import quote_plus, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.adapters.tavily_client import TavilyClient
from app.models import DiscoveryCandidate

BLOCKED_TOKENS = {
    "review",
    "reviews",
    "blog",
    "guide",
    "news",
    "forum",
    "community",
    "reddit",
    "youtube",
    "video",
    "manual",
}
SEARCH_PAGE_MARKERS = ["/search", "search?", "?s=", "?k=", "&k=", "category", "collections"]
PRODUCT_PATH_HINTS = ["/dp/", "/gp/product/", "/product/", "/products/", "/item/", "/site/"]
COMMERCE_HINTS = ["buy", "shop", "price", "cart", "in stock", "pickup", "$", "shipping"]
SEARCH_URLS = {
    "bestbuy.com": "https://www.bestbuy.com/site/searchpage.jsp?st={query}",
    "microcenter.com": "https://www.microcenter.com/search/search_results.aspx?Ntt={query}",
    "amazon.com": "https://www.amazon.com/s?k={query}",
}


@dataclass
class DiscoveryResult:
    normalized_query: str
    candidates: list[DiscoveryCandidate]
    scanned_candidates: int
    sources_seen: list[str]
    warnings: list[str]
    degraded: bool = False


class QueryDiscoveryService:
    def __init__(
        self,
        tavily: TavilyClient,
        supported_domains: list[str],
        max_results_per_domain: int = 2,
        timeout_seconds: float = 2.5,
    ) -> None:
        self.tavily = tavily
        self.supported_domains = supported_domains
        self.max_results_per_domain = max_results_per_domain
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def normalize_query(query: str) -> str:
        return " ".join(query.strip().lower().split())

    def discover(self, query: str) -> DiscoveryResult:
        normalized_query = self.normalize_query(query)
        warnings: list[str] = []
        degraded = False
        raw_candidates: list[DiscoveryCandidate] = []
        all_seen_candidates: list[DiscoveryCandidate] = []
        source_counter: Counter[str] = Counter()

        if self.tavily.enabled:
            raw_candidates = self.tavily.search_products(
                query=normalized_query,
                max_results=max(8, self.max_results_per_domain * 4),
                query_variants=self._query_variants(normalized_query),
            )
            all_seen_candidates.extend(raw_candidates)
        else:
            degraded = True
            warnings.append("Tavily is not configured. Falling back to retailer site search only.")

        qualified = self.qualify_candidates(raw_candidates, normalized_query)
        for candidate in raw_candidates:
            source_counter[self._root_domain(candidate.domain)] += 1

        existing_known_domains = {
            root for root in {self._root_domain(candidate.domain) for candidate in qualified} if root in SEARCH_URLS
        }
        fallback_domains = [domain for domain in SEARCH_URLS if domain not in existing_known_domains]
        if fallback_domains:
            fallback_candidates, fallback_warnings = self._site_search_fallback(normalized_query, fallback_domains)
            if fallback_warnings:
                degraded = True
                warnings.extend(fallback_warnings)
            for candidate in fallback_candidates:
                source_counter[self._root_domain(candidate.domain)] += 1
            all_seen_candidates.extend(fallback_candidates)
            qualified.extend(self.qualify_candidates(fallback_candidates, normalized_query))

        deduped = self._dedupe_candidates(qualified)
        deduped.sort(key=lambda item: (item.score is not None, item.score or 0.0, len(item.title)), reverse=True)

        sources_seen = [domain for domain, _ in source_counter.most_common(12)]
        if not deduped:
            warnings.append("No product pages were discovered. Try a more specific electronics model query.")

        return DiscoveryResult(
            normalized_query=normalized_query,
            candidates=deduped,
            scanned_candidates=len(self._dedupe_candidates(all_seen_candidates)),
            sources_seen=sources_seen,
            warnings=self._dedupe_strings(warnings),
            degraded=degraded,
        )

    def qualify_candidates(self, candidates: list[DiscoveryCandidate], normalized_query: str) -> list[DiscoveryCandidate]:
        qualified: list[DiscoveryCandidate] = []
        query_tokens = self._query_tokens(normalized_query)
        for candidate in candidates:
            if self._reject_candidate(candidate):
                continue
            overlap = self._token_overlap(query_tokens, f"{candidate.title} {candidate.snippet} {candidate.url}")
            product_hint = self._has_product_hint(candidate)
            commerce_hint = self._has_commerce_hint(candidate)
            domain_hint = self._root_domain(candidate.domain) in SEARCH_URLS
            if not product_hint and overlap < (0.35 if domain_hint else 0.5):
                continue
            if not commerce_hint and overlap < (0.4 if product_hint or domain_hint else 0.65):
                continue
            score = max(candidate.score or 0.0, overlap)
            qualified.append(candidate.model_copy(update={"score": round(score, 4)}))
        return qualified

    def _site_search_fallback(self, normalized_query: str, domains: list[str]) -> tuple[list[DiscoveryCandidate], list[str]]:
        candidates: list[DiscoveryCandidate] = []
        warnings: list[str] = []
        with ThreadPoolExecutor(max_workers=min(len(domains), 3)) as executor:
            future_map = {
                executor.submit(self._discover_from_site_search, domain, normalized_query): domain
                for domain in domains
            }
            for future in as_completed(future_map):
                domain = future_map[future]
                try:
                    domain_candidates, note = future.result()
                except Exception as exc:
                    domain_candidates, note = [], f"Site-search fallback failed for {domain}: {exc.__class__.__name__}."
                if note:
                    warnings.append(note)
                candidates.extend(domain_candidates)
        return candidates, warnings

    def _discover_from_site_search(self, domain: str, normalized_query: str) -> tuple[list[DiscoveryCandidate], str]:
        search_url = SEARCH_URLS.get(domain)
        if not search_url:
            return [], ""
        url = search_url.format(query=quote_plus(normalized_query))
        try:
            response = httpx.get(
                url,
                timeout=self.timeout_seconds,
                follow_redirects=True,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
            response.raise_for_status()
        except Exception as exc:
            return [], f"Site-search fallback failed for {domain}: {exc.__class__.__name__}."

        candidates, scanned_count = self._extract_site_search_candidates(
            domain=domain,
            normalized_query=normalized_query,
            base_url=str(response.url),
            html=response.text,
        )
        if candidates:
            return candidates[: self.max_results_per_domain], f"Site-search fallback found {len(candidates[: self.max_results_per_domain])} purchase candidates from {scanned_count} scanned links on {domain}."
        return [], f"Site-search fallback found no purchase candidates in {scanned_count} scanned links on {domain}."

    def _extract_site_search_candidates(
        self,
        domain: str,
        normalized_query: str,
        base_url: str,
        html: str,
    ) -> tuple[list[DiscoveryCandidate], int]:
        soup = BeautifulSoup(html, "html.parser")
        candidates: list[DiscoveryCandidate] = []
        seen_urls: set[str] = set()
        scanned_count = 0
        query_tokens = self._query_tokens(normalized_query)

        for node in soup.find_all("a", href=True):
            href = str(node.get("href", "")).strip()
            if not href:
                continue
            absolute_url = urljoin(base_url, href)
            parsed = urlparse(absolute_url)
            hostname = parsed.netloc.lower().replace("www.", "")
            if domain not in hostname:
                continue
            scanned_count += 1

            title = (
                node.get_text(" ", strip=True)
                or str(node.get("aria-label", "")).strip()
                or str(node.get("title", "")).strip()
            )
            context_text = self._site_search_context_text(node)
            snippet = (str(node.get("aria-label", "")).strip() or context_text)[:360]
            if not title and not snippet:
                continue
            if absolute_url in seen_urls:
                continue

            preview_price = self._parse_preview_price(context_text)
            preview_availability = self._extract_preview_availability(context_text)
            preview_condition = self._infer_preview_condition(f"{title} {snippet}")

            candidate = DiscoveryCandidate(
                domain=self._root_domain(hostname),
                title=title[:240],
                url=absolute_url,
                snippet=snippet[:360],
                score=round(self._token_overlap(query_tokens, f"{title} {snippet} {absolute_url}"), 4),
                source="site_search",
                preview_price=preview_price,
                preview_availability=preview_availability,
                preview_condition=preview_condition,
            )
            if self._reject_candidate(candidate):
                continue
            seen_urls.add(absolute_url)
            candidates.append(candidate)
        return candidates, scanned_count

    def _query_variants(self, normalized_query: str) -> list[str]:
        return [
            normalized_query,
            f"buy {normalized_query}",
            f"{normalized_query} price",
            f"{normalized_query} in stock",
        ]

    def _reject_candidate(self, candidate: DiscoveryCandidate) -> bool:
        parsed = urlparse(candidate.url)
        hostname = parsed.netloc.lower().replace("www.", "")
        path = parsed.path.lower()
        text = f"{candidate.title} {candidate.snippet} {candidate.url}".lower()
        if not hostname:
            return True
        if any(token in text for token in BLOCKED_TOKENS):
            return True
        if any(marker in path or marker in candidate.url.lower() for marker in SEARCH_PAGE_MARKERS):
            return True
        if "accessories" in path or "replacement" in path:
            return True
        return False

    def _has_product_hint(self, candidate: DiscoveryCandidate) -> bool:
        lowered_url = candidate.url.lower()
        lowered_text = f"{candidate.title} {candidate.snippet}".lower()
        if any(hint in lowered_url for hint in PRODUCT_PATH_HINTS):
            return True
        return any(token in lowered_text for token in COMMERCE_HINTS)

    def _has_commerce_hint(self, candidate: DiscoveryCandidate) -> bool:
        lowered_text = f"{candidate.title} {candidate.snippet}".lower()
        return any(token in lowered_text for token in COMMERCE_HINTS) or "$" in lowered_text

    @staticmethod
    def _dedupe_candidates(candidates: list[DiscoveryCandidate]) -> list[DiscoveryCandidate]:
        by_url: dict[str, DiscoveryCandidate] = {}
        for candidate in candidates:
            current = by_url.get(candidate.url)
            if current is None or (candidate.score or 0.0) > (current.score or 0.0):
                by_url[candidate.url] = candidate
        return list(by_url.values())

    @staticmethod
    def _query_tokens(normalized_query: str) -> set[str]:
        return _tokenize_text(normalized_query)

    @staticmethod
    def _token_overlap(query_tokens: set[str], haystack: str) -> float:
        if not query_tokens:
            return 0.0
        haystack_tokens = _tokenize_text(haystack)
        hits = sum(1 for token in query_tokens if token in haystack_tokens)
        return hits / max(len(query_tokens), 1)

    @staticmethod
    def _root_domain(domain: str) -> str:
        cleaned = domain.lower().replace("www.", "")
        parts = cleaned.split(".")
        if len(parts) >= 2:
            return ".".join(parts[-2:])
        return cleaned

    @staticmethod
    def _dedupe_strings(items: list[str]) -> list[str]:
        seen: set[str] = set()
        output: list[str] = []
        for item in items:
            if item and item not in seen:
                seen.add(item)
                output.append(item)
        return output

    @staticmethod
    def _site_search_context_text(node) -> str:
        candidates: list[str] = []
        current = node
        for _ in range(4):
            if current is None:
                break
            text = current.get_text(" ", strip=True)
            if 24 <= len(text) <= 1200:
                candidates.append(text)
            current = current.parent
        for text in candidates:
            if re.search(r"\$\s*[0-9]", text):
                return text
        return candidates[0] if candidates else ""

    @staticmethod
    def _parse_preview_price(text: str) -> float | None:
        if not text:
            return None
        match = re.search(r"\$\s*([0-9]+(?:\.[0-9]{1,2})?)", text.replace(",", ""))
        if not match:
            return None
        try:
            return float(match.group(1))
        except ValueError:
            return None

    @staticmethod
    def _extract_preview_availability(text: str) -> str:
        lowered = text.lower()
        if "out of stock" in lowered:
            return "out of stock"
        if "in stock" in lowered:
            return "in stock"
        if "pickup" in lowered:
            return "pickup available"
        return ""

    @staticmethod
    def _infer_preview_condition(text: str) -> str:
        lowered = text.lower()
        if "open box" in lowered or "open-box" in lowered:
            return "open_box"
        if "renewed" in lowered or "refurbished" in lowered:
            return "refurbished"
        if "used" in lowered or "pre-owned" in lowered or "pre owned" in lowered:
            return "used"
        if "new" in lowered:
            return "new"
        return "unknown"


def _normalize_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _tokenize_text(value: str) -> set[str]:
    tokens: set[str] = set()
    split_parts: list[str] = []
    for raw in re.findall(r"[a-z0-9][a-z0-9/-]*", value.lower()):
        cleaned = _normalize_token(raw)
        if cleaned and (len(cleaned) >= 3 or any(char.isdigit() for char in cleaned)):
            tokens.add(cleaned)
        for part in re.split(r"[^a-z0-9]+", raw):
            normalized = _normalize_token(part)
            if normalized and (len(normalized) >= 3 or any(char.isdigit() for char in normalized)):
                tokens.add(normalized)
                split_parts.append(normalized)

    for window in (2, 3):
        for index in range(len(split_parts) - window + 1):
            combined = "".join(split_parts[index:index + window])
            if combined and re.search(r"\d", combined) and len(combined) >= 4:
                tokens.add(combined)
    return tokens
