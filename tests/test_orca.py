"""ORCA（互惠速度障碍）纯算法层单测。

覆盖 swarm/orca.py 的速度求解与预测性风险，以及 swarm/safety.py 的集成
（config 门控 + orca_diversion_waypoint 分流航点）。纯函数层，无 MQTT/进程。
"""

import unittest

from swarm.orca import orca_pair_risk, orca_velocity
from swarm.safety import (
    DroneSnapshot,
    SafetyConfig,
    SafetyGate,
    orca_diversion_waypoint,
)


class OrcaVelocityTest(unittest.TestCase):
    def test_symmetric_head_on_diverges_laterally(self) -> None:
        """相向对称对：ORCA 协同速度应带横向分量（对称分流），而非「笔直相撞」。"""
        v_new = orca_velocity(
            (0.0, 0.0),
            (1.0, 0.0),
            (1.0, 0.0),
            [((1.0, 0.0), (-1.0, 0.0))],
            combined_radius=0.25,
            time_horizon=2.0,
        )
        # 横向偏转（-y 侧），但前向分量仍为正（继续前进而非折返）。
        self.assertNotEqual(v_new[1], 0.0)
        self.assertGreater(v_new[0], 0.0)

    def test_overlap_collision_branch_keeps_straight(self) -> None:
        """已重叠（< combined_radius）时 ORCA 的对称碰撞分支退化，返回「保持直行」。

        纯函数层测试：显式取 combined_radius=0.25，验证已重叠的对称相向对会退化为「笔直前进」
        （互惠假设下双方都指望对方担责）。系统默认 combined_radius=1.6（舒适间距），反应式门
        在 ttc≈1.8m 就 override、不会走到 d≤1.6 的重叠区；此处只断言该退化分支不崩溃、结果确定。
        """
        v_new = orca_velocity(
            (0.0, 0.0),
            (1.0, 0.0),
            (1.0, 0.0),
            [((0.1, 0.0), (-1.0, 0.0))],
            combined_radius=0.25,
            time_horizon=2.0,
        )
        self.assertEqual(v_new, (1.0, 0.0))

    def test_far_same_direction_preserves_preferred(self) -> None:
        """远处同向无碰撞：ORCA 保留期望速度。"""
        v_new = orca_velocity(
            (0.0, 0.0),
            (1.0, 0.0),
            (1.0, 0.0),
            [((10.0, 0.0), (1.0, 0.0))],
            combined_radius=0.25,
            time_horizon=2.0,
        )
        self.assertEqual(v_new, (1.0, 0.0))

    def test_no_neighbors_returns_preferred(self) -> None:
        v_new = orca_velocity(
            (0.0, 0.0),
            (1.0, 0.0),
            (1.0, 0.0),
            [],
            combined_radius=0.25,
            time_horizon=2.0,
        )
        self.assertEqual(v_new, (1.0, 0.0))

    def test_respects_max_speed(self) -> None:
        v_new = orca_velocity(
            (0.0, 0.0),
            (0.0, 0.0),
            (5.0, 0.0),
            [],
            combined_radius=0.25,
            time_horizon=2.0,
            max_speed=2.0,
        )
        self.assertEqual(v_new, (2.0, 0.0))

    def test_stationary_neighbor_no_crash(self) -> None:
        """静止邻居（相对速度即自身速度）应正常求解、不崩溃。"""
        v_new = orca_velocity(
            (0.0, 0.0),
            (1.0, 0.0),
            (1.0, 0.0),
            [((1.0, 0.0), (0.0, 0.0))],
            combined_radius=0.25,
            time_horizon=2.0,
        )
        self.assertEqual(len(v_new), 2)


class OrcaPairRiskTest(unittest.TestCase):
    def test_head_on_risk_monotonic(self) -> None:
        """相向风险随距离缩小单调上升（预测式 TTC 归一化）。"""
        risks = [
            orca_pair_risk((0.0, 0.0), (1.0, 0.0), (d, 0.0), (-1.0, 0.0), 0.25, 2.0)
            for d in (4.0, 2.0, 1.0, 0.5, 0.3)
        ]
        for risk in risks:
            self.assertGreater(risk, 0.0)
        for earlier, later in zip(risks, risks[1:]):
            self.assertLess(earlier, later)

    def test_receding_risk_zero(self) -> None:
        """同向远离：无前瞻碰撞风险。"""
        risk = orca_pair_risk((0.0, 0.0), (1.0, 0.0), (1.0, 0.0), (1.0, 0.0), 0.25, 2.0)
        self.assertEqual(risk, 0.0)

    def test_far_risk_zero(self) -> None:
        """远处相向但碰撞发生在时域之外：风险 0（交给反应式门）。"""
        risk = orca_pair_risk((0.0, 0.0), (1.0, 0.0), (10.0, 0.0), (-1.0, 0.0), 0.25, 2.0)
        self.assertEqual(risk, 0.0)


class OrcaDiversionWaypointTest(unittest.TestCase):
    def test_no_neighbors_returns_none(self) -> None:
        """无邻居时 ORCA 分流航点返回 None（由调用方回退反应式启发式）。"""
        cfg = SafetyConfig(orca_enabled=True)
        a = DroneSnapshot(1, (0, 0, 1), target=(10, 0, 1), speed_mps=1)
        self.assertIsNone(orca_diversion_waypoint(a, [a], cfg, None))

    def test_gate_uses_orca_waypoint_when_enabled(self) -> None:
        """orca_enabled 时 override/warning 分流航点带横向偏转（相向正对）。"""
        cfg = SafetyConfig(orca_enabled=True, vertical_escape_gain=0.0)
        a = DroneSnapshot(1, (0, 0, 1), target=(10, 0, 1), speed_mps=1)
        b = DroneSnapshot(2, (2, 0, 1), target=(-10, 0, 1), speed_mps=1)
        decisions = SafetyGate(cfg).evaluate([a, b])
        diverted = [d for d in decisions if d.safety_waypoint is not None]
        self.assertTrue(diverted)
        wp = diverted[0].safety_waypoint
        self.assertNotEqual(wp[1], 0.0)  # 横向 y 分量非零
        self.assertEqual(len(wp), 3)

    def test_gate_default_off_uses_reactive(self) -> None:
        """默认（orca_enabled=False）保持反应式行为，不改关键路径。"""
        cfg = SafetyConfig(orca_enabled=False, vertical_escape_gain=0.0)
        a = DroneSnapshot(1, (0, 0, 1), target=(10, 0, 1), speed_mps=1)
        b = DroneSnapshot(2, (0.5, 0, 1), target=(-10, 0, 1), speed_mps=1)
        decisions = SafetyGate(cfg).evaluate([a, b])
        overrides = [d for d in decisions if d.mode == "override"]
        self.assertTrue(overrides)
        for d in overrides:
            self.assertIsNotNone(d.safety_waypoint)


if __name__ == "__main__":
    unittest.main()
