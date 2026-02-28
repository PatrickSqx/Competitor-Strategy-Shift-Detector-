import unittest

from app.models import ComparisonCluster, DiscoveryCandidate, PurchaseOption
from app.services.differential_pricing import DifferentialPricingService
from app.services.product_matcher import ProductMatcherService, extract_model_identifier
from app.services.query_discovery import QueryDiscoveryService
from app.services.relevance_ranker import RelevanceRanker


class FakeTavily:
    def __init__(self, candidates):
        self.enabled = True
        self._candidates = candidates

    def search_products(self, query, max_results=8, query_variants=None, include_domains=None):
        return list(self._candidates)


class OfflineDiscoveryService(QueryDiscoveryService):
    def _site_search_fallback(self, normalized_query: str, domains: list[str]):
        return [], []


class CompareServicesTest(unittest.TestCase):
    def test_query_normalization(self) -> None:
        normalized = QueryDiscoveryService.normalize_query('  Sony   WH-1000XM5  ')
        self.assertEqual(normalized, 'sony wh-1000xm5')

    def test_discovery_filters_to_product_pages(self) -> None:
        service = OfflineDiscoveryService(
            tavily=FakeTavily(
                [
                    DiscoveryCandidate(
                        domain='bestbuy.com',
                        title='Sony WH-1000XM5 Wireless Noise Canceling Headphones - Black',
                        url='https://www.bestbuy.com/site/sony-wh-1000xm5-wireless-noise-canceling-headphones-black/6505727.p',
                        snippet='Buy Sony WH-1000XM5 wireless noise canceling headphones. In stock now.',
                    ),
                    DiscoveryCandidate(
                        domain='reddit.com',
                        title='Best headphones review thread',
                        url='https://www.reddit.com/r/headphones/comments/xyz',
                        snippet='Review discussion thread',
                    ),
                    DiscoveryCandidate(
                        domain='amazon.com',
                        title='Amazon search results for sony wh-1000xm5',
                        url='https://www.amazon.com/s?k=sony+wh-1000xm5',
                        snippet='Search results page',
                    ),
                ]
            ),
            supported_domains=['bestbuy.com', 'microcenter.com', 'amazon.com'],
            max_results_per_domain=2,
        )
        result = service.discover('sony wh-1000xm5')
        self.assertEqual(result.normalized_query, 'sony wh-1000xm5')
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].domain, 'bestbuy.com')
        self.assertIn('bestbuy.com', result.sources_seen)

    def test_site_search_html_fallback_extracts_product_links(self) -> None:
        service = QueryDiscoveryService(
            tavily=FakeTavily([]),
            supported_domains=['bestbuy.com', 'microcenter.com', 'amazon.com'],
            max_results_per_domain=2,
        )
        html = """
        <html>
          <body>
            <a href="/site/sony-wh-1000xm5-wireless-noise-canceling-headphones-black/6505727.p">
              Buy Sony WH-1000XM5 Wireless Noise Canceling Headphones - Black
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
        self.assertEqual(candidates[0].source, 'site_search')

    def test_discovery_token_overlap_handles_hyphenated_models(self) -> None:
        service = OfflineDiscoveryService(
            tavily=FakeTavily([]),
            supported_domains=['bestbuy.com'],
            max_results_per_domain=2,
        )
        query_tokens = service._query_tokens('sony wh-1000xm5')
        overlap = service._token_overlap(
            query_tokens,
            'https://example.com/sony-wh-1000xm5-wireless-noise-canceling-headphones'
        )
        self.assertGreaterEqual(overlap, 0.5)

    def test_relevance_ranker_rejects_used_and_accessories(self) -> None:
        ranker = RelevanceRanker()
        offers = [
            PurchaseOption(
                offer_id='1', seller_name='Best Buy', source_domain='bestbuy.com',
                title='Sony WH-1000XM5 Wireless Noise Canceling Headphones', brand='Sony', model='WH-1000XM5', variant='',
                price=349.99, url='https://example.com/1', currency='USD', condition='new',
                parse_notes=['jsonld', 'product-page']
            ),
            PurchaseOption(
                offer_id='2', seller_name='Accessory Shop', source_domain='accessoryshop.com',
                title='Sony WH-1000XM5 Replacement Ear Pads', brand='Sony', model='WH-1000XM5', variant='',
                price=19.99, url='https://example.com/2', currency='USD', condition='new',
                parse_notes=['product-page']
            ),
            PurchaseOption(
                offer_id='3', seller_name='Used Gear', source_domain='usedgear.com',
                title='Sony WH-1000XM5 Used Headphones', brand='Sony', model='WH-1000XM5', variant='',
                price=199.99, url='https://example.com/3', currency='USD', condition='used',
                parse_notes=['product-page']
            ),
        ]
        ranked = ranker.rank('sony wh-1000xm5', offers)
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0].offer_id, '1')
        self.assertGreaterEqual(ranked[0].relevance_score, 0.55)

    def test_extract_model_identifier_handles_spaced_fragments(self) -> None:
        self.assertEqual(
            extract_model_identifier('Razer Viper V3 Pro Wireless Esports Mouse - Black'),
            'V3PRO',
        )
        self.assertEqual(
            extract_model_identifier('Sony WH 1000XM5 Wireless Noise Canceling Headphones'),
            'WH1000XM5',
        )

    def test_relevance_ranker_keeps_brandless_query_with_model_match(self) -> None:
        ranker = RelevanceRanker()
        offer = PurchaseOption(
            offer_id='v3p',
            seller_name='Micro Center',
            source_domain='microcenter.com',
            title='Razer Viper V3 Pro Wireless Esports Mouse - Black',
            brand='Razer',
            model='V3PRO',
            variant='',
            price=159.99,
            url='https://example.com/v3p',
            currency='USD',
            condition='new',
            parse_notes=['jsonld', 'product-page'],
        )
        ranked = ranker.rank('viper v3pro', [offer])
        self.assertEqual(len(ranked), 1)
        self.assertGreaterEqual(ranked[0].relevance_score, 0.55)

    def test_exact_match_cluster(self) -> None:
        matcher = ProductMatcherService()
        offers = [
            PurchaseOption(
                offer_id='1', seller_name='Best Buy', source_domain='bestbuy.com', title='Sony WH-1000XM5 Wireless Noise Canceling Headphones',
                brand='Sony', model='WH-1000XM5', variant='', price=349.99, url='https://example.com/bb',
                condition='new', relevance_score=0.95
            ),
            PurchaseOption(
                offer_id='2', seller_name='Micro Center', source_domain='microcenter.com', title='Sony WH-1000XM5 Wireless Headphones',
                brand='Sony', model='WH-1000XM5', variant='', price=329.99, url='https://example.com/mc',
                condition='new', relevance_score=0.92
            ),
            PurchaseOption(
                offer_id='3', seller_name='Amazon', source_domain='amazon.com', title='Sony WH-1000XM5 Bluetooth Headphones',
                brand='Sony', model='WH-1000XM5', variant='', price=339.99, url='https://example.com/amz',
                condition='new', relevance_score=0.91
            ),
        ]
        cluster, matched, warnings = matcher.match('sony wh-1000xm5', offers)
        self.assertIsNotNone(cluster)
        self.assertEqual(cluster.match_method, 'exact_model')
        self.assertEqual(cluster.offer_count, 3)
        self.assertEqual(len(matched), 3)
        self.assertEqual(warnings, [])

    def test_two_offer_cluster_does_not_alert(self) -> None:
        service = DifferentialPricingService()
        cluster = ComparisonCluster(
            cluster_id='exact::sony::wh-1000xm5::',
            brand='Sony',
            model='WH-1000XM5',
            variant='',
            match_method='exact_model',
            confidence=0.96,
            offer_count=2,
            domains=['bestbuy.com', 'microcenter.com'],
            offers=[
                PurchaseOption(
                    offer_id='1', seller_name='Best Buy', source_domain='bestbuy.com', title='Sony WH-1000XM5',
                    brand='Sony', model='WH-1000XM5', variant='', price=349.99, url='https://example.com/bb',
                    condition='new', relevance_score=0.95, match_confidence=0.96
                ),
                PurchaseOption(
                    offer_id='2', seller_name='Micro Center', source_domain='microcenter.com', title='Sony WH-1000XM5',
                    brand='Sony', model='WH-1000XM5', variant='', price=299.99, url='https://example.com/mc',
                    condition='new', relevance_score=0.92, match_confidence=0.96
                ),
            ],
        )
        finding = service.analyze('sony wh-1000xm5', cluster)
        self.assertEqual(finding.label, 'none')
        self.assertFalse(finding.alert_eligible)
        self.assertGreaterEqual(finding.spread_percent, 8.0)

    def test_three_offer_cluster_can_alert(self) -> None:
        service = DifferentialPricingService()
        cluster = ComparisonCluster(
            cluster_id='exact::sony::wh-1000xm5::',
            brand='Sony',
            model='WH-1000XM5',
            variant='',
            match_method='exact_model',
            confidence=0.96,
            offer_count=3,
            domains=['bestbuy.com', 'microcenter.com', 'amazon.com'],
            offers=[
                PurchaseOption(
                    offer_id='1', seller_name='Best Buy', source_domain='bestbuy.com', title='Sony WH-1000XM5',
                    brand='Sony', model='WH-1000XM5', variant='', price=349.99, url='https://example.com/bb',
                    condition='new', relevance_score=0.95, match_confidence=0.96
                ),
                PurchaseOption(
                    offer_id='2', seller_name='Micro Center', source_domain='microcenter.com', title='Sony WH-1000XM5',
                    brand='Sony', model='WH-1000XM5', variant='', price=299.99, url='https://example.com/mc',
                    condition='new', relevance_score=0.92, match_confidence=0.96
                ),
                PurchaseOption(
                    offer_id='3', seller_name='Amazon', source_domain='amazon.com', title='Sony WH-1000XM5',
                    brand='Sony', model='WH-1000XM5', variant='', price=339.99, url='https://example.com/amz',
                    condition='new', relevance_score=0.91, match_confidence=0.96
                ),
            ],
        )
        finding = service.analyze('sony wh-1000xm5', cluster)
        self.assertIn(finding.label, {'watch', 'high', 'critical'})
        self.assertTrue(finding.alert_eligible)


if __name__ == '__main__':
    unittest.main()
