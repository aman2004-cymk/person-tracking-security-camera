import csv
import hashlib
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import numpy as np

from multi_person_surveillance import (
    daily_incident_path, model_reference, record_incident, run,
)


class OperatorIncidentReportingTests(unittest.TestCase):
    def test_model_reference_uses_local_weights_or_downloadable_asset_name(self):
        with patch("multi_person_surveillance.MODEL_PATH") as model_path:
            model_path.is_file.return_value = True
            model_path.__str__.return_value = "C:/project/yolo11n.pt"
            self.assertEqual(model_reference(), "C:/project/yolo11n.pt")
            model_path.is_file.return_value = False
            model_path.name = "yolo11n.pt"
            self.assertEqual(model_reference(), "yolo11n.pt")

    def test_real_hardware_requires_port_before_camera_or_model_startup(self):
        with patch("multi_person_surveillance.open_video_source") as open_camera:
            with self.assertRaisesRegex(ValueError, "--esp32-port is required"):
                run(0, hardware_backend="real")
        open_camera.assert_not_called()

    def test_operator_confirmed_incident_saves_image_and_integrity_record(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            alert_dir = Path(temporary_directory)
            frame = np.zeros((80, 120, 3), dtype=np.uint8)
            with patch("multi_person_surveillance.ALERT_DIR", alert_dir):
                image_path = record_incident(frame, 7, "Consented Person", "UNAUTHORIZED", 42.5)
                log_path = daily_incident_path(datetime.now())

            self.assertTrue(image_path.is_file())
            with log_path.open(newline="", encoding="utf-8") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["action"], "OPERATOR_CONFIRMED_NOTIFY_SECURITY")
            self.assertEqual(rows[0]["track_id"], "7")
            self.assertEqual(rows[0]["identity"], "Consented Person")
            self.assertEqual(rows[0]["status"], "UNAUTHORIZED")
            self.assertEqual(rows[0]["sha256"], hashlib.sha256(image_path.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
