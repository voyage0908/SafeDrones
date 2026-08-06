import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from mock_drone import build_self_snapshot, onboard_gate_tick, read_peer_snapshots
from swarm.safety import SafetyGate
from swarm.simulation import MockDroneState


def write_lines(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def telemetry_line(drone: int, position: list[float], timestamp_ms: int) -> str:
    return json.dumps(
        {
            "drone": drone,
            "position": position,
            "velocity": [0.0, 0.0, 0.0],
            "speed_mps": 0.0,
            "status": "flying",
            "timestamp_ms": timestamp_ms,
        }
    )


class SelfSnapshotTest(unittest.TestCase):
    def test_build_self_snapshot(self) -> None:
        state = MockDroneState(drone_id=3, current_pos=(1.0, 2.0, 1.5), target_pos=(4.0, 2.0, 1.5))
        snapshot = build_self_snapshot(state, timestamp_ms=123)
        self.assertEqual(snapshot.drone_id, 3)
        self.assertEqual(snapshot.position, (1.0, 2.0, 1.5))
        self.assertEqual(snapshot.target, (4.0, 2.0, 1.5))
        self.assertEqual(snapshot.timestamp_ms, 123)


class ReadPeerSnapshotsTest(unittest.TestCase):
    def test_reads_latest_record(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "peer.jsonl"
            write_lines(
                path,
                [
                    telemetry_line(2, [1.0, 0.0, 1.0], 100),
                    telemetry_line(2, [2.0, 0.0, 1.0], 200),
                ],
            )
            snapshots = read_peer_snapshots({2: str(path)})
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0].drone_id, 2)
        self.assertEqual(snapshots[0].position, (2.0, 0.0, 1.0))

    def test_missing_file_is_skipped(self) -> None:
        snapshots = read_peer_snapshots({2: "/nonexistent/peer.jsonl"})
        self.assertEqual(snapshots, [])


class OnboardGateTickTest(unittest.TestCase):
    def test_override_applies_safety_command_locally(self) -> None:
        with TemporaryDirectory() as tmp:
            peer_path = Path(tmp) / "peer.jsonl"
            # Peer is 0.3m ahead on the collision course: risk must exceed the
            # override threshold.
            write_lines(peer_path, [telemetry_line(2, [0.3, 0.0, 1.0], 100)])

            state = MockDroneState(
                drone_id=1,
                current_pos=(0.0, 0.0, 1.0),
                target_pos=(5.0, 0.0, 1.0),
                speed_mps=1.0,
            )
            decision = onboard_gate_tick(
                state, SafetyGate(), {2: str(peer_path)}, timestamp_ms=200,
                follow_target=(5.0, 0.0, 1.0),
            )

        self.assertIsNotNone(decision)
        self.assertEqual(decision.mode, "override")
        self.assertNotEqual(state.target_pos, (5.0, 0.0, 1.0))
        self.assertTrue(str(state.last_command_id).startswith("safety-"))

    def test_no_override_when_peer_is_far(self) -> None:
        with TemporaryDirectory() as tmp:
            peer_path = Path(tmp) / "peer.jsonl"
            write_lines(peer_path, [telemetry_line(2, [4.0, 0.0, 1.0], 100)])

            state = MockDroneState(
                drone_id=1,
                current_pos=(0.0, 0.0, 1.0),
                target_pos=(5.0, 0.0, 1.0),
                speed_mps=1.0,
            )
            decision = onboard_gate_tick(
                state, SafetyGate(), {2: str(peer_path)}, timestamp_ms=200,
                follow_target=(5.0, 0.0, 1.0),
            )

        self.assertIsNotNone(decision)
        self.assertEqual(decision.mode, "normal")
        self.assertEqual(state.target_pos, (5.0, 0.0, 1.0))
        self.assertEqual(state.last_command_id, "onboard-follow-1")

    def test_warning_mode_blends_follow_and_safety(self) -> None:
        with TemporaryDirectory() as tmp:
            peer_path = Path(tmp) / "peer.jsonl"
            # Peer at 1.0m with zero velocities: risk = 1 - 1.0/1.6 = 0.375,
            # inside the warning band (0.32, 0.70).
            write_lines(peer_path, [telemetry_line(2, [1.0, 0.0, 1.0], 100)])

            follow = (5.0, 0.0, 1.0)
            state = MockDroneState(
                drone_id=1,
                current_pos=(0.0, 0.0, 1.0),
                target_pos=follow,
                speed_mps=1.0,
            )
            gate = SafetyGate()
            decision = onboard_gate_tick(
                state, gate, {2: str(peer_path)}, timestamp_ms=200, follow_target=follow,
            )

        self.assertIsNotNone(decision)
        self.assertEqual(decision.mode, "warning")
        self.assertIsNotNone(decision.safety_waypoint)

        config = gate.config
        beta = (config.high_threshold - decision.risk_level) / (
            config.high_threshold - config.low_threshold
        )
        expected = tuple(
            beta * f + (1.0 - beta) * s
            for f, s in zip(follow, decision.safety_waypoint)
        )
        self.assertTrue(str(state.last_command_id).startswith("onboard-blend-"))
        for actual, want in zip(state.target_pos, expected):
            self.assertAlmostEqual(actual, want, places=3)
        # blended target must differ from both endpoints
        self.assertNotEqual(tuple(state.target_pos), follow)
        self.assertNotEqual(tuple(state.target_pos), tuple(decision.safety_waypoint))


if __name__ == "__main__":
    unittest.main()
