import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.trajectory_analysis import (
    interpolate,
    load_trajectory,
    pairwise_min_distance,
    true_min_distance,
)


def write_trajectory(path: Path, samples: list[tuple[int, list[float]]]) -> None:
    with path.open("w", encoding="utf-8") as file:
        for timestamp_ms, position in samples:
            file.write(
                json.dumps(
                    {"drone": 1, "timestamp_ms": timestamp_ms, "position": position},
                    separators=(",", ":"),
                )
                + "\n"
            )


class TrajectoryAnalysisTest(unittest.TestCase):
    def test_head_on_true_min_distance(self) -> None:
        # Two drones fly head-on along the x axis, separated by 0.5m in y.
        # Both log at 10 Hz for 6 seconds: the true minimum is exactly 0.5.
        samples_a = [(t, [-3.0 + t / 1000.0, 0.0, 1.0]) for t in range(0, 6001, 100)]
        samples_b = [(t, [3.0 - t / 1000.0, 0.5, 1.0]) for t in range(0, 6001, 100)]
        with TemporaryDirectory() as tmp:
            path_a = Path(tmp) / "a.jsonl"
            path_b = Path(tmp) / "b.jsonl"
            write_trajectory(path_a, samples_a)
            write_trajectory(path_b, samples_b)
            result = true_min_distance([str(path_a), str(path_b)])
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result, 0.5, places=3)

    def test_interpolation_covers_unaligned_timestamps(self) -> None:
        # Drone B logs at 1 Hz; drone A at 10 Hz. The closest approach of A
        # falls between B's samples and must be found via interpolation.
        samples_a = [(t, [t / 1000.0, 0.0, 0.0]) for t in range(0, 1001, 100)]
        samples_b = [(t, [0.5, 0.2, 0.0]) for t in (0, 1000)]
        with TemporaryDirectory() as tmp:
            path_a = Path(tmp) / "a.jsonl"
            path_b = Path(tmp) / "b.jsonl"
            write_trajectory(path_a, samples_a)
            write_trajectory(path_b, samples_b)
            result = true_min_distance([str(path_a), str(path_b)])
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result, 0.2, places=3)

    def test_interpolate_returns_none_outside_range(self) -> None:
        times = [100.0, 200.0]
        positions = [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]
        self.assertIsNone(interpolate(times, positions, 50.0))
        self.assertIsNone(interpolate(times, positions, 250.0))
        self.assertEqual(interpolate(times, positions, 150.0), [0.5, 0.0, 0.0])

    def test_empty_or_missing_data_returns_none(self) -> None:
        with TemporaryDirectory() as tmp:
            path_a = Path(tmp) / "a.jsonl"
            path_b = Path(tmp) / "b.jsonl"
            path_a.write_text("", encoding="utf-8")
            write_trajectory(path_b, [(0, [0.0, 0.0, 0.0])])
            self.assertIsNone(true_min_distance([str(path_a), str(path_b)]))

    def test_load_trajectory_skips_malformed_lines(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "traj.jsonl"
            path.write_text(
                '{"timestamp_ms": 1, "position": [0, 0, 0]}\n'
                "\n"
                '{"timestamp_ms": 2}\n',
                encoding="utf-8",
            )
            times, positions = load_trajectory(str(path))
        self.assertEqual(times, [1.0])
        self.assertEqual(positions, [[0.0, 0.0, 0.0]])

    def test_evaluation_window_excludes_spawn_overlap(self) -> None:
        # Both drones spawn at the shared origin (distance 0) before
        # separating; a windowed evaluation must skip that overlap.
        samples_a = [(0, [0.0, 0.0, 0.0])] + [
            (t, [-1.0, 0.0, 1.0]) for t in range(1000, 2001, 100)
        ]
        samples_b = [(0, [0.0, 0.0, 0.0])] + [
            (t, [1.0, 0.0, 1.0]) for t in range(1000, 2001, 100)
        ]
        with TemporaryDirectory() as tmp:
            path_a = Path(tmp) / "a.jsonl"
            path_b = Path(tmp) / "b.jsonl"
            write_trajectory(path_a, samples_a)
            write_trajectory(path_b, samples_b)
            full = true_min_distance([str(path_a), str(path_b)])
            windowed = true_min_distance([str(path_a), str(path_b)], start_ms=1000)
        self.assertEqual(full, 0.0)
        self.assertIsNotNone(windowed)
        self.assertAlmostEqual(windowed, 2.0, places=3)

    def test_pairwise_min_distance_single_pair(self) -> None:
        trajectories = [
            ([0.0, 100.0], [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]),
            ([0.0, 100.0], [[0.0, 3.0, 0.0], [1.0, 3.0, 0.0]]),
        ]
        self.assertAlmostEqual(pairwise_min_distance(trajectories), 3.0)


if __name__ == "__main__":
    unittest.main()
