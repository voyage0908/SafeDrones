import unittest

from scripts.ego_bridge import EgoBridge


def sighting(position, camera_drone=1, timestamp_ms=1000):
    return {
        "target": 1,
        "position": list(position),
        "pixel": [320.0, 180.0],
        "estimated_depth": 3.0,
        "source": "sim_cam",
        "camera_drone": camera_drone,
        "timestamp_ms": timestamp_ms,
    }


class EgoBridgeTest(unittest.TestCase):
    def test_sighting_assigned_to_only_enemy(self) -> None:
        bridge = EgoBridge(self_ids={1}, enemy_ids={2}, state_ttl_sec=1.0, publish_interval_sec=0.1)
        assigned = bridge.handle_sighting(sighting((2.0, 0.0, 1.0)), timestamp_ms=1000)
        self.assertEqual(assigned, 2)

        messages = dict(bridge.outgoing_messages(timestamp_ms=1100))
        ego = messages["swarm/ego/drone/2/telemetry"]
        self.assertEqual(ego["position"], [2.0, 0.0, 1.0])
        self.assertEqual(ego["status"], "perceived")
        self.assertEqual(ego["source"], "sim_cam")

    def test_self_telemetry_passes_through(self) -> None:
        bridge = EgoBridge(self_ids={1}, enemy_ids={2}, state_ttl_sec=1.0, publish_interval_sec=0.1)
        bridge.handle_telemetry(1, {"drone": 1, "position": [-3.0, 0.0, 1.0], "status": "flying"})
        bridge.handle_telemetry(2, {"drone": 2, "position": [3.0, 0.0, 1.0], "status": "flying"})

        messages = dict(bridge.outgoing_messages(timestamp_ms=1000))
        self.assertIn("swarm/ego/drone/1/telemetry", messages)
        self.assertEqual(messages["swarm/ego/drone/1/telemetry"]["status"], "flying")
        self.assertNotIn("swarm/ego/drone/2/telemetry", messages)

    def test_stale_after_ttl(self) -> None:
        bridge = EgoBridge(self_ids={1}, enemy_ids={2}, state_ttl_sec=1.0, publish_interval_sec=0.1)
        bridge.handle_sighting(sighting((2.0, 0.0, 1.0)), timestamp_ms=1000)

        fresh = dict(bridge.outgoing_messages(timestamp_ms=1500))["swarm/ego/drone/2/telemetry"]
        stale = dict(bridge.outgoing_messages(timestamp_ms=3000))["swarm/ego/drone/2/telemetry"]
        self.assertEqual(fresh["status"], "perceived")
        self.assertEqual(stale["status"], "stale")
        self.assertEqual(stale["position"], [2.0, 0.0, 1.0])

    def test_velocity_from_finite_difference(self) -> None:
        bridge = EgoBridge(self_ids={1}, enemy_ids={2}, state_ttl_sec=1.0, publish_interval_sec=0.1)
        bridge.handle_sighting(sighting((0.0, 0.0, 1.0)), timestamp_ms=1000)
        bridge.handle_sighting(sighting((1.0, 0.0, 1.0)), timestamp_ms=1500)

        ego = dict(bridge.outgoing_messages(timestamp_ms=1600))["swarm/ego/drone/2/telemetry"]
        self.assertAlmostEqual(ego["velocity"][0], 2.0, places=3)
        self.assertAlmostEqual(ego["velocity"][1], 0.0, places=3)

    def test_sighting_from_enemy_camera_is_ignored(self) -> None:
        bridge = EgoBridge(self_ids={1}, enemy_ids={2}, state_ttl_sec=1.0, publish_interval_sec=0.1)
        assigned = bridge.handle_sighting(sighting((0.0, 0.0, 1.0), camera_drone=2), timestamp_ms=1000)
        self.assertIsNone(assigned)
        self.assertEqual(dict(bridge.outgoing_messages(timestamp_ms=1100)), {})

    def test_multi_enemy_nearest_track_wins(self) -> None:
        bridge = EgoBridge(self_ids={1}, enemy_ids={2, 4}, state_ttl_sec=1.0, publish_interval_sec=0.1)
        bridge.handle_sighting(sighting((5.0, 0.0, 1.0)), timestamp_ms=1000)
        # 第二次目击离 track 2 更远，应分配给另一架（track 4）
        assigned = bridge.handle_sighting(sighting((-5.0, 0.0, 1.0)), timestamp_ms=1100)
        self.assertEqual(assigned, 4)
        # 离 track 2 近，仍归 track 2
        assigned = bridge.handle_sighting(sighting((5.2, 0.0, 1.0)), timestamp_ms=1200)
        self.assertEqual(assigned, 2)


if __name__ == "__main__":
    unittest.main()
