import csv
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen
from unittest.mock import patch

import dashboard


class DashboardTests(unittest.TestCase):
    def test_local_dashboard_serves_events_and_only_jpeg_evidence(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            alert_dir = Path(temporary_directory)
            image_name = "event.jpg"
            (alert_dir / image_name).write_bytes(b"test-image")
            event_path = alert_dir / "incidents_2026-10-27.csv"
            with event_path.open("w", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, fieldnames=[
                    "timestamp", "action", "track_id", "identity", "status", "image_file",
                ])
                writer.writeheader()
                writer.writerow({
                    "timestamp": "2026-10-27T18:00:00", "action": "OPERATOR_CONFIRMED_NOTIFY_SECURITY",
                    "track_id": "4", "identity": "Test Person", "status": "UNREGISTERED",
                    "image_file": image_name,
                })

            with patch.object(dashboard, "ALERT_DIR", alert_dir):
                server = dashboard.LocalDashboardServer(("127.0.0.1", 0), dashboard.DashboardHandler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    base_url = f"http://127.0.0.1:{server.server_port}"
                    with urlopen(f"{base_url}/api/events") as response:
                        events = json.loads(response.read())
                    self.assertEqual(len(events), 1)
                    self.assertEqual(events[0]["event_type"], "OPERATOR INCIDENT")
                    self.assertEqual(events[0]["identity"], "Test Person")

                    with urlopen(f"{base_url}/evidence/{image_name}") as response:
                        self.assertEqual(response.headers["Content-Type"], "image/jpeg")
                        self.assertEqual(response.read(), b"test-image")
                    with self.assertRaises(HTTPError) as raised:
                        urlopen(f"{base_url}/evidence/%2e%2e%5csecret.jpg")
                    raised.exception.close()
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=1)


if __name__ == "__main__":
    unittest.main()
