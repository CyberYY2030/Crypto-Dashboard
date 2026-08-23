import unittest

import db as dbm


class MacroDbTests(unittest.TestCase):
    def setUp(self):
        self.conn = dbm.connect(":memory:")

    def tearDown(self):
        self.conn.close()

    def test_fetch_metric_detail_for_date_returns_extra_payload(self):
        dbm.upsert_metric(
            self.conn,
            "2026-04-17",
            "fear_greed",
            21.0,
            "alternative.me",
            {"class": "Extreme Fear"},
        )
        row = dbm.fetch_metric_detail_for_date(self.conn, "2026-04-17", "fear_greed")
        self.assertEqual(row["value"], 21.0)
        self.assertEqual(row["source"], "alternative.me")
        self.assertEqual(row["extra"]["class"], "Extreme Fear")


if __name__ == "__main__":
    unittest.main()
