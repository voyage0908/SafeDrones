"""Offline ground-truth trajectory analysis.

Each mock drone can log its own true trajectory to a local JSONL file
(``--log-trajectory``), bypassing MQTT entirely. This module computes the
true pairwise minimum distance from those files, which is the trustworthy
collision criterion under packet loss: the MQTT-observed minimum only
reflects whatever samples survived the lossy link.
"""

from __future__ import annotations

import argparse
from bisect import bisect_left
import json
import math


def load_trajectory(path: str) -> tuple[list[float], list[list[float]]]:
    """Load a JSONL trajectory file into (timestamps_ms, positions)."""
    times: list[float] = []
    positions: list[list[float]] = []
    with open(path, encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            position = record.get("position")
            timestamp_ms = record.get("timestamp_ms")
            if position is None or timestamp_ms is None or len(position) != 3:
                continue
            times.append(float(timestamp_ms))
            positions.append([float(value) for value in position])
    return times, positions


def interpolate(
    times: list[float],
    positions: list[list[float]],
    timestamp_ms: float,
) -> list[float] | None:
    """Linear-interpolated position at timestamp_ms; None outside the logged range."""
    if not times or timestamp_ms < times[0] or timestamp_ms > times[-1]:
        return None
    index = bisect_left(times, timestamp_ms)
    if index < len(times) and times[index] == timestamp_ms:
        return positions[index]
    if index == 0 or index >= len(times):
        return None
    t0, t1 = times[index - 1], times[index]
    p0, p1 = positions[index - 1], positions[index]
    ratio = (timestamp_ms - t0) / (t1 - t0)
    return [a + (b - a) * ratio for a, b in zip(p0, p1)]


def pairwise_min_distance(
    trajectories: list[tuple[list[float], list[list[float]]]],
) -> float | None:
    """Minimum pairwise 3D distance across all trajectories.

    For every logged sample of every drone, the other drones' positions are
    linearly interpolated at the same timestamp, so drones logging at
    different rates or with jitter still compare correctly.
    """
    best: float | None = None
    for index_a in range(len(trajectories)):
        times_a, positions_a = trajectories[index_a]
        for index_b in range(index_a + 1, len(trajectories)):
            times_b, positions_b = trajectories[index_b]
            for timestamp_ms, position_a in zip(times_a, positions_a):
                position_b = interpolate(times_b, positions_b, timestamp_ms)
                if position_b is None:
                    continue
                distance = math.dist(position_a, position_b)
                if best is None or distance < best:
                    best = distance
    return best


def true_min_distance(
    paths: list[str],
    start_ms: float | None = None,
    end_ms: float | None = None,
) -> float | None:
    """True pairwise minimum distance from ground-truth trajectory files.

    start_ms/end_ms restrict the evaluation window. This matters because
    mock drones spawn at the shared origin: without a window, the initial
    overlap before takeoff would be reported as a zero-distance "collision".
    """
    trajectories = [load_trajectory(path) for path in paths]
    if start_ms is not None or end_ms is not None:
        windowed = []
        for times, positions in trajectories:
            pairs = [
                (t, p)
                for t, p in zip(times, positions)
                if (start_ms is None or t >= start_ms) and (end_ms is None or t <= end_ms)
            ]
            windowed.append(
                ([t for t, _ in pairs], [p for _, p in pairs])
            )
        trajectories = windowed
    trajectories = [trajectory for trajectory in trajectories if trajectory[0]]
    if len(trajectories) < 2:
        return None
    return pairwise_min_distance(trajectories)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compute true pairwise min distance from trajectory logs.")
    parser.add_argument("trajectories", nargs="+", help="JSONL trajectory files (one per drone)")
    args = parser.parse_args()

    if len(args.trajectories) < 2:
        raise SystemExit("need at least two trajectory files")

    result = true_min_distance(args.trajectories)
    if result is None:
        raise SystemExit("no overlapping samples found")
    print(f"true_min_distance_m={result:.4f}")


if __name__ == "__main__":
    main()
