import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import monitor


class MonitorTests(unittest.TestCase):
    def test_add_months_clamps_end_of_month(self):
        self.assertEqual(monitor.add_months(date(2026, 8, 31), 6), date(2027, 2, 28))

    @patch("monitor.fetch_json")
    def test_fetch_openings_excludes_day_use_and_reserved(self, fetch_json):
        fetch_json.return_value = {
            "campsites": {
                "overnight-open": {
                    "site": "001",
                    "type_of_use": "Overnight",
                    "availabilities": {"2026-09-20T00:00:00Z": "Available"},
                },
                "overnight-reserved": {
                    "site": "002",
                    "type_of_use": "Overnight",
                    "availabilities": {"2026-09-20T00:00:00Z": "Reserved"},
                },
                "day-use-open": {
                    "site": "Day Use",
                    "type_of_use": "Day",
                    "availabilities": {"2026-09-20T00:00:00Z": "Available"},
                },
            }
        }
        openings = monitor.fetch_openings(date(2026, 9, 15))
        self.assertEqual(
            openings,
            [monitor.Opening(date(2026, 9, 20), "001", "overnight-open")],
        )

    def test_state_round_trip(self):
        openings = [monitor.Opening(date(2026, 10, 1), "005", "96745")]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            monitor.save_state(path, openings)
            self.assertEqual(monitor.load_previous_keys(path), {"2026-10-01|96745"})
            payload = json.loads(path.read_text())
            self.assertEqual(payload["facility_id"], "232491")


if __name__ == "__main__":
    unittest.main()
