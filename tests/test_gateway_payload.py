import unittest

from gateway import command_payload


class GatewayPayloadTest(unittest.TestCase):
    def test_command_payload_contains_stage_one_target(self) -> None:
        payload = command_payload(
            drone=1,
            waypoint=(1, 2, 3),
            priority="normal",
            confidence=0.8,
            ttl_sec=5,
        )
        self.assertEqual(payload["action"], "move_to")
        self.assertEqual(payload["target"], [1.0, 2.0, 3.0])
        self.assertEqual(payload["waypoint"], [1.0, 2.0, 3.0])
        self.assertTrue(payload["command_id"].startswith("cmd-"))


if __name__ == "__main__":
    unittest.main()
