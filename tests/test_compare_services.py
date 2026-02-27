import unittest

from app.models import DiscoveryCandidate, OfferView, ProductCluster
from app.services.differential_pricing import DifferentialPricingService
from app.services.product_matcher import ProductMatcherService
from app.services.query_discovery import QueryDiscoveryService


class FakeTavily:
    def __init__(self, candidates):
        self.enabled = True
        self._candidates = candidates

    def search_products(self, query, domains, max_results_per_domain=5):
        return list(self._candidates)


class OfflineDiscoveryService(QueryDiscoveryService):
    def _discover_from_site_search(self, domain: str, normalized_query: str):
        return [], ''


class CompareServicesTest(unittest.TestCase):
    def test_query_normalization(self) -> None:
        normalized = QueryDiscoveryService.normalize_query('  Sony   WH-1000XM5  ')
        self.assertEqual(normalized, 'sony wh-1000xm5')

    def test_exact_match_cluster(self) -> None:
        matcher = ProductMatcherService()
        offers = [
            OfferView(platform='Best Buy', title='Sony WH-1000XM5 Wireless Noise Canceling Headphones', brand='Sony', model='WH-1000XM5', price=349.99, url='https://example.com/bb'),
            OfferView(platform='Micro Center', title='Sony WH-1000XM5 Wireless Headphones', brand='Sony', model='WH-1000XM5', price=329.99, url='https://example.com/mc'),
            OfferView(platform='Amazon', title='Sony WH-1000XM5 Bluetooth Headphones', brand='Sony', model='WH-1000XM5', price=339.99, url='https://example.com/amz'),
        ]
        cluster, matched, warnings = matcher.match('sony wh-1000xm5', offers)
        self.assertIsNotNone(cluster)
        self.assertEqual(cluster.match_method, 'exact_model')
        self.assertEqual(len(matched), 3)
        self.assertEqual(warnings, [])

    def test_differential_pricing_threshold(self) -> None:
        service = DifferentialPricingService()
        cluster = ProductCluster(
            cluster_id='exact::sony::wh-1000xm5',
            brand='Sony',
            model='WH-1000XM5',
            match_method='exact_model',
            confidence=0.96,
            platforms=['Best Buy', 'Micro Center', 'Amazon'],
            offer_count=3,
        )
        offers = [
            OfferView(platform='Best Buy', title='Sony WH-1000XM5', brand='Sony', model='WH-1000XM5', price=349.99, promo_text='', availability='in stock', url='https://example.com/bb', match_confidence=0.96),
            OfferView(platform='Micro Center', title='Sony WH-1000XM5', brand='Sony', model='WH-1000XM5', price=299.99, promo_text='Save 20% today', availability='in stock', url='https://example.com/mc', match_confidence=0.96),
            OfferView(platform='Amazon', title='Sony WH-1000XM5', brand='Sony', model='WH-1000XM5', price=339.99, promo_text='', availability='in stock', url='https://example.com/amz', match_confidence=0.96),
        ]
        finding = service.analyze('sony wh-1000xm5', offers, cluster, 'full')
        self.assertIn(finding.label, {'high', 'critical'})
        self.assertGreaterEqual(finding.spread_percent, 8.0)

    def test_discovery_filters_to_product_pages(self) -> None:
        tavily = FakeTavily(
            [
                DiscoveryCandidate(
                    platform='Best Buy',
                    domain='bestbuy.com',
                    title='Sony WH-1000XM5 Wireless Noise Canceling Headphones - Black',
                    url='https://www.bestbuy.com/site/sony-wh-1000xm5-wireless-noise-canceling-headphones-black/6505727.p',
                    snippet='Shop Sony WH-1000XM5 wireless noise canceling headphones.',
                ),
                DiscoveryCandidate(
                    platform='Amazon',
                    domain='amazon.com',
                    title='Amazon search results for sony wh-1000xm5',
                    url='https://www.amazon.com/s?k=sony+wh-1000xm5',
                    snippet='Search results page',
                ),
                DiscoveryCandidate(
                    platform='Micro Center',
                    domain='microcenter.com',
                    title='Sony WH-1000XM5 Wireless Headphones',
                    url='https://www.microcenter.com/product/123456/sony-wh-1000xm5-wireless-headphones',
                    snippet='Sony WH-1000XM5 in stock now.',
                ),
            ]
        )
        service = OfflineDiscoveryService(
            tavily=tavily,
            supported_domains=['bestbuy.com', 'microcenter.com', 'amazon.com'],
            max_results_per_domain=5,
        )
        normalized, candidates, statuses, warnings = service.discover('sony wh-1000xm5')
        self.assertEqual(normalized, 'sony wh-1000xm5')
        self.assertEqual(len(candidates), 2)
        self.assertEqual(sorted(candidate.platform for candidate in candidates), ['Best Buy', 'Micro Center'])
        self.assertEqual(len(warnings), 0)
        amazon = next(item for item in statuses if item.platform == 'Amazon')
        self.assertEqual(amazon.status, 'missing')
        self.assertIn('1 Tavily URLs', amazon.note)

    def test_site_search_html_fallback_extracts_product_links(self) -> None:
        service = QueryDiscoveryService(
            tavily=FakeTavily([]),
            supported_domains=['bestbuy.com', 'microcenter.com', 'amazon.com'],
            max_results_per_domain=5,
        )
        html = """
        <html>
          <body>
            <a href="/site/sony-wh-1000xm5-wireless-noise-canceling-headphones-black/6505727.p">
              Sony WH-1000XM5 Wireless Noise Canceling Headphones - Black
            </a>
            <a href="/site/searchpage.jsp?st=sony+wh-1000xm5">Search results</a>
          </body>
        </html>
        """
        candidates, scanned = service._extract_site_search_candidates(
            domain='bestbuy.com',
            normalized_query='sony wh-1000xm5',
            base_url='https://www.bestbuy.com/site/searchpage.jsp?st=sony+wh-1000xm5',
            html=html,
        )
        self.assertEqual(scanned, 2)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].platform, 'Best Buy')
        self.assertIn('/site/sony-wh-1000xm5', candidates[0].url)


if __name__ == '__main__':
    unittest.main()
