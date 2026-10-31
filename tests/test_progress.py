import unittest

from image_library.progress import ScanEstimate, format_duration


class ProgressTests(unittest.TestCase):
    def test_wait_for_measurements_then_estimate_remaining_and_total(self):
        estimate = ScanEstimate(now=0)
        self.assertIsNone(estimate.observe(0, 10, now=0)['remaining'])
        value = estimate.observe(1, 10, now=5)
        self.assertEqual(value['remaining'], 45)
        self.assertEqual(value['estimated_total'], 50)

    def test_one_time_model_loading_is_excluded_after_warmup(self):
        estimate = ScanEstimate(now=0)
        estimate.observe(1, 10, now=20)
        estimate.observe(2, 10, now=22)
        value = estimate.observe(3, 10, now=24)
        self.assertEqual(value['remaining'], 14)
        self.assertEqual(value['elapsed'], 24)
        self.assertEqual(value['estimated_total'], 38)
        self.assertEqual(estimate.observe(3, 10, now=25)['remaining'], 14)

    def test_finished_and_empty_scans_have_no_remaining_time(self):
        estimate = ScanEstimate(now=0)
        self.assertEqual(estimate.observe(0, 0, now=1)['remaining'], 0)
        value = estimate.observe(5, 5, now=10)
        self.assertEqual(value['completed'], 5)
        self.assertEqual(value['remaining'], 0)

    def test_readable_durations_round_up_fractional_remaining_seconds(self):
        self.assertEqual(format_duration(0), '0초')
        self.assertEqual(format_duration(1.2), '2초')
        self.assertEqual(format_duration(62), '1분 02초')
        self.assertEqual(format_duration(3661), '1시간 01분 01초')


if __name__ == '__main__':
    unittest.main()
