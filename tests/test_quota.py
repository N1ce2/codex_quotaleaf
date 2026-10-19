import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quota_card import remaining, countdown, extract_bucket, DEFAULTS


class QuotaSemantics(unittest.TestCase):
    def test_used_percentage_is_converted_to_remaining(self):
        self.assertEqual(remaining({'usedPercent': 12}), 88)
        self.assertEqual(remaining({'usedPercent': 120}), 0)
        self.assertEqual(remaining({'usedPercent': -2}), 100)

    def test_unknown_never_becomes_full_or_empty(self):
        for value in (None, {}, {'usedPercent': None}, {'usedPercent': 'bad'}, {'usedPercent': float('nan')}):
            self.assertIsNone(remaining(value))

    def test_multi_bucket_selection(self):
        payload = {'rateLimits': {'primary': None}, 'rateLimitsByLimitId': {
            'codex': {'limitId': 'codex'}, 'other': {'limitId': 'other'}}}
        self.assertEqual(extract_bucket(payload)[0]['limitId'], 'codex')
        self.assertEqual(extract_bucket(payload, 'other')[0]['limitId'], 'other')

    def test_legacy_and_unavailable_limits(self):
        self.assertEqual(extract_bucket({'rateLimits': {'primary': None}})[0], {'primary': None})
        self.assertIsNone(extract_bucket({})[0])

    def test_expired_countdown_waits_for_server(self):
        self.assertEqual(countdown(999, now=1000), '待同步新额度')
        self.assertEqual(countdown(None), '重置时间未知')
        self.assertEqual(countdown(4661, now=1000), '01:01:01 后重置')


if __name__ == '__main__':
    unittest.main()
