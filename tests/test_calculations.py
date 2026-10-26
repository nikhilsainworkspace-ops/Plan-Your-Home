import unittest

from pyh.calculations import DEFAULT_CATEGORIES, area_from_plot, estimate_cost, positive_number, room_area, to_display_length
from pyh.security import hash_password, validate_email, verify_hmac_sha256, verify_password


class CalculationTests(unittest.TestCase):
    def test_default_allocations_sum_to_one_hundred(self):
        self.assertAlmostEqual(sum(pct for _, pct in DEFAULT_CATEGORIES), 100.0)

    def test_area_and_cost_math(self):
        self.assertEqual(room_area(4, 3), 12)
        self.assertEqual(area_from_plot(20, 15), 300)
        result = estimate_cost(100, 2000, 10, [("Build", 100)])
        self.assertEqual(result["base_cost"], 200000)
        self.assertEqual(result["contingency_amount"], 20000)
        self.assertEqual(result["total"], 220000)
        self.assertEqual(result["lines"][0]["amount"], 200000)

    def test_rejects_invalid_positive_numbers(self):
        for val in ("", "nan", "inf", "-inf", "-1"):
            with self.subTest(val=val), self.assertRaises(ValueError):
                positive_number(val, "Test amount", 100)
        self.assertEqual(positive_number("0", "Test amount", 100), 0)

    def test_display_length_metric_and_imperial(self):
        self.assertEqual(to_display_length(1, "metric"), 1)
        self.assertEqual(to_display_length(1, "imperial"), 3.28)


class SecurityHelperTests(unittest.TestCase):
    def test_password_hash_round_trip_and_rejection(self):
        encoded = hash_password("correct-horse-battery")
        self.assertTrue(verify_password("correct-horse-battery", encoded))
        self.assertFalse(verify_password("incorrect", encoded))

    def test_password_minimum_length(self):
        with self.assertRaises(ValueError):
            hash_password("short")

    def test_email_validation(self):
        self.assertTrue(validate_email("a.person@example.com"))
        self.assertFalse(validate_email("not-an-address"))
        self.assertFalse(validate_email("a@example.com\r\nBcc:bad@example.com"))

    def test_webhook_signature_uses_raw_body(self):
        import hmac, hashlib
        body = b'{"event":"subscription.activated"}'
        secret = "webhook-secret"
        signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        self.assertTrue(verify_hmac_sha256(body, signature, secret))
        self.assertFalse(verify_hmac_sha256(body + b" ", signature, secret))
        self.assertFalse(verify_hmac_sha256(body, signature, "wrong"))


if __name__ == "__main__":
    unittest.main()
