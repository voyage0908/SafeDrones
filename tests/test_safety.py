import unittest

from swarm.safety import (
    DroneSnapshot,
    SafetyConfig,
    SafetyGate,
    build_hover_command,
    build_override_event,
    pair_collision_risk,
)


class SafetyRiskTest(unittest.TestCase):
    def test_close_pair_has_high_risk(self) -> None:
        config = SafetyConfig(safe_distance_m=1.5)
        a = DroneSnapshot(1, (0, 0, 1), target=(5, 0, 1), speed_mps=1)
        b = DroneSnapshot(2, (0.5, 0, 1), target=(-5, 0, 1), speed_mps=1)

        risk = pair_collision_risk(a, b, config)

        self.assertGreaterEqual(risk.risk, 0.75)
        self.assertEqual(risk.target_drone, 2)

    def test_far_pair_has_low_risk(self) -> None:
        config = SafetyConfig(safe_distance_m=1.5)
        a = DroneSnapshot(1, (0, 0, 1), target=(1, 0, 1), speed_mps=1)
        b = DroneSnapshot(2, (10, 0, 1), target=(11, 0, 1), speed_mps=1)

        risk = pair_collision_risk(a, b, config)

        self.assertEqual(risk.risk, 0)

    def test_approaching_pair_uses_ttc_risk(self) -> None:
        config = SafetyConfig(safe_distance_m=1.0, ttc_safe_sec=3.0)
        a = DroneSnapshot(1, (0, 0, 1), target=(10, 0, 1), speed_mps=1)
        b = DroneSnapshot(2, (2, 0, 1), target=(-10, 0, 1), speed_mps=1)

        risk = pair_collision_risk(a, b, config)

        self.assertGreater(risk.risk, 0)
        self.assertLess(risk.ttc_sec, config.ttc_safe_sec)

    def test_telemetry_velocity_overrides_target_inference(self) -> None:
        config = SafetyConfig(safe_distance_m=1.0, ttc_safe_sec=3.0)
        a = DroneSnapshot(1, (0, 0, 1), target=(10, 0, 1), velocity=(0, 0, 0), speed_mps=1)
        b = DroneSnapshot(2, (2, 0, 1), target=(-10, 0, 1), velocity=(0, 0, 0), speed_mps=1)

        risk = pair_collision_risk(a, b, config)

        self.assertEqual(risk.approach_mps, 0)
        self.assertIsNone(risk.ttc_sec)
        self.assertEqual(risk.risk, 0)


class SafetyGateTest(unittest.TestCase):
    def test_gate_emits_override_decision(self) -> None:
        gate = SafetyGate(SafetyConfig(safe_distance_m=1.5, high_threshold=0.75))
        snapshots = [
            DroneSnapshot(1, (0, 0, 1), target=(5, 0, 1), speed_mps=1, last_command_id="cmd-1"),
            DroneSnapshot(2, (0.5, 0, 1), target=(-5, 0, 1), speed_mps=1, last_command_id="cmd-2"),
        ]

        decisions = gate.evaluate(snapshots, now=1.0)

        self.assertEqual({decision.mode for decision in decisions}, {"override"})
        event = build_override_event(decisions[0])
        self.assertEqual(event["event"], "safety_override")
        self.assertEqual(event["reason"], "collision_risk_exceeded")
        self.assertEqual(event["target_drone"], 2)
        self.assertEqual(event["command_id"], "cmd-1")

    def test_hover_command_targets_drone(self) -> None:
        decision = SafetyGate(SafetyConfig()).evaluate(
            [DroneSnapshot(1, (0, 0, 1), target=(1, 0, 1), speed_mps=1)],
            now=1.0,
        )[0]

        command = build_hover_command(decision)

        self.assertEqual(command["drone"], 1)
        self.assertEqual(command["action"], "hover")
        self.assertEqual(command["priority"], "safety")


if __name__ == "__main__":
    unittest.main()
