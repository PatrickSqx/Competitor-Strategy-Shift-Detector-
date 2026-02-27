import unittest

from app.models import OfferView, ProductCluster
from app.services.differential_pricing import DifferentialPricingService
from app.services.product_matcher import ProductMatcherService
from app.services.query_discovery import QueryDiscoveryService


class CompareServicesTest(unittest.TestCase):
    def test_query_normalization(self) -> None:
        normalized = QueryDiscoveryService.normalize_query('  Sony   WH-1000XM5  ')
        self.assertEqual(normalized, 'sony wh-1000xm5')

    def test_exact_match_cluster(self) -> None:
        matcher = ProductMatcherService()
        offers = [
            OfferView(platform='Best Buy', title='Sony WH-1000XM5 Wireless Noise Canceling Headphones', brand='Sony', model='WH-1000XM5', price=349.99, url='https://example.com/bb'),
            OfferView(platform='Walmart', title='Sony WH-1000XM5 Wireless Headphones', brand='Sony', model='WH-1000XM5', price=329.99, url='https://example.com/wm'),
            OfferView(platform='Target', title='Sony WH-1000XM5 Bluetooth Headphones', brand='Sony', model='WH-1000XM5', price=339.99, url='https://example.com/tg'),
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
            platforms=['Best Buy', 'Walmart', 'Target'],
            offer_count=3,
        )
        offers = [
            OfferView(platform='Best Buy', title='Sony WH-1000XM5', brand='Sony', model='WH-1000XM5', price=349.99, promo_text='', availability='in stock', url='https://example.com/bb', match_confidence=0.96),
            OfferView(platform='Walmart', title='Sony WH-1000XM5', brand='Sony', model='WH-1000XM5', price=299.99, promo_text='Save 20% today', availability='in stock', url='https://example.com/wm', match_confidence=0.96),
            OfferView(platform='Target', title='Sony WH-1000XM5', brand='Sony', model='WH-1000XM5', price=339.99, promo_text='', availability='in stock', url='https://example.com/tg', match_confidence=0.96),
        ]
        finding = service.analyze('sony wh-1000xm5', offers, cluster, 'full')
        self.assertIn(finding.label, {'high', 'critical'})
        self.assertGreaterEqual(finding.spread_percent, 8.0)


if __name__ == '__main__':
    unittest.main()
