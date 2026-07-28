import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from swarm.mqtt_gateway import MqttGateway, MqttSettings


class MqttGatewayTest(unittest.TestCase):
    def test_append_event_log_writes_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "events.jsonl"
            with patch.object(MqttGateway, "_build_client", return_value=object()):
                gateway = MqttGateway(MqttSettings(), event_log_path=str(log_path))

            event = {"topic": "swarm/commander/status", "payload": {"drone": 1}, "timestamp_ms": 1}
            gateway._append_event_log(event)

            lines = log_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            self.assertEqual(json.loads(lines[0]), event)


if __name__ == "__main__":
    unittest.main()
