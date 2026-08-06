import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.stage4_benchmark import (
    SCENARIO_NAMES,
    Scenario,
    build_c4_replan_text,
    build_llm_replan_command,
    parse_conditions,
    validate_stage4_replan,
    write_aggregate_csv,
)
from swarm.llm_provider import LLMProviderError, WaypointPlan


class Stage4BenchmarkScenarioTest(unittest.TestCase):
    def test_head_on_crossing_targets_and_predicates(self) -> None:
        scenario = Scenario("head_on_crossing")
        targets = scenario.targets_for_seed(0)

        self.assertLess(targets["drone1_start"][0], 0)
        self.assertGreater(targets["drone2_start"][0], 0)
        self.assertGreater(targets["drone1_goal"][0], 0)
        self.assertLess(targets["drone2_goal"][0], 0)
        self.assertEqual(targets["drone1_goal"], targets["drone2_start"])
        self.assertEqual(targets["drone2_goal"], targets["drone1_start"])
        self.assertAlmostEqual(targets["drone1_start"][1], -targets["drone2_start"][1])
        self.assertAlmostEqual(targets["drone1_start"][2], targets["drone2_start"][2])
        self.assertTrue(
            scenario.is_separated(
                {
                    1: {"position": targets["drone1_start"]},
                    2: {"position": targets["drone2_start"]},
                }
            )
        )
        self.assertTrue(
            scenario.is_complete(
                {
                    1: {"position": [2.1, 0.0, 1.0]},
                    2: {"position": [-2.1, 0.0, 1.0]},
                }
            )
        )

    def test_perpendicular_crossing_predicates(self) -> None:
        scenario = Scenario("perpendicular_crossing")

        self.assertIn("--repulsion-gain", scenario.pilot_args())
        self.assertTrue(
            scenario.is_separated(
                {
                    1: {"position": [-2.1, 0.0, 1.0]},
                    2: {"position": [0.0, -2.1, 1.0]},
                }
            )
        )
        self.assertTrue(
            scenario.is_complete(
                {
                    1: {"position": [2.1, 0.0, 1.0]},
                    2: {"position": [0.0, 2.1, 1.0]},
                }
            )
        )

    def test_diagonal_crossing_predicates(self) -> None:
        scenario = Scenario("diagonal_crossing")

        self.assertTrue(
            scenario.is_separated(
                {
                    1: {"position": [-2.1, -2.1, 1.0]},
                    2: {"position": [-2.1, 2.1, 1.0]},
                }
            )
        )
        self.assertTrue(
            scenario.is_complete(
                {
                    1: {"position": [2.1, 2.1, 1.0]},
                    2: {"position": [2.1, -2.1, 1.0]},
                }
            )
        )

    def test_parse_conditions_normalizes_and_rejects_unknown(self) -> None:
        self.assertEqual(parse_conditions("c2,C3,c4"), ["C2", "C3", "C4"])
        with self.assertRaises(SystemExit):
            parse_conditions("C2,C5")

    def test_all_scenario_names_are_supported(self) -> None:
        for scenario_name in SCENARIO_NAMES:
            scenario = Scenario(scenario_name)
            self.assertEqual(scenario.targets_for_seed(0)["drone1_start"][2], scenario.targets_for_seed(0)["drone2_start"][2])

    def test_write_aggregate_csv_groups_by_scenario_and_condition(self) -> None:
        results = [
            {
                "scenario": "head_on_crossing",
                "condition": "C2",
                "task_success": False,
                "collision_count": 1,
                "near_miss_count": 1,
                "min_distance_m": 0.0,
                "override_count": 0,
                "warning_count": 0,
                "crossing_duration_sec": 7.0,
            },
            {
                "scenario": "head_on_crossing",
                "condition": "C3",
                "task_success": True,
                "collision_count": 0,
                "near_miss_count": 1,
                "min_distance_m": 0.7,
                "override_count": 2,
                "warning_count": 4,
                "crossing_duration_sec": 12.0,
            },
        ]

        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "aggregate.csv"
            write_aggregate_csv(path, results)
            text = path.read_text(encoding="utf-8")

        self.assertIn("scenario,condition,runs,success_rate", text)
        self.assertIn("avg_llm_replan_count", text)
        self.assertIn("head_on_crossing,C2,1,0.0,1.0", text)
        self.assertIn("head_on_crossing,C3,1,1.0,0.0", text)

    def test_c4_replan_prompt_includes_override_and_mission_goal(self) -> None:
        scenario = Scenario("head_on_crossing")
        targets = scenario.targets_for_seed(0)
        event = {"event_name": "safety_override", "drone": 1, "risk_level": 0.9}
        prompt = build_c4_replan_text(
            scenario,
            targets,
            event,
            {1: {"position": [0.0, 0.0, 1.0]}},
        )

        self.assertIn("Safety Gate", prompt)
        self.assertIn("head_on_crossing", prompt)
        self.assertIn(str(targets["drone1_goal"]), prompt)

    def test_llm_replan_command_is_high_priority_recovery_command(self) -> None:
        event = {"command_id": "safety-divert-1"}
        plan = WaypointPlan(
            drone=1,
            waypoint=(3.0, 0.1, 1.0),
            priority="high",
            confidence=0.7,
            rationale="恢复任务",
        )

        command = build_llm_replan_command(plan, event, timestamp_ms=123)

        self.assertEqual(command["priority"], "high")
        self.assertEqual(command["source_event"], "safety_override")
        self.assertEqual(command["source_command_id"], "safety-divert-1")
        self.assertEqual(command["target"], [3.0, 0.1, 1.0])

    def test_validate_stage4_replan_rejects_wrong_drone_or_out_of_bounds(self) -> None:
        validate_stage4_replan(WaypointPlan(drone=1, waypoint=(3.0, 0.0, 1.0)), expected_drone=1)

        with self.assertRaises(LLMProviderError):
            validate_stage4_replan(WaypointPlan(drone=2, waypoint=(3.0, 0.0, 1.0)), expected_drone=1)

        with self.assertRaises(LLMProviderError):
            validate_stage4_replan(WaypointPlan(drone=1, waypoint=(30.0, 0.0, 1.0)), expected_drone=1)


class LlmTimeoutScenarioTest(unittest.TestCase):
    def test_llm_timeout_is_registered_and_injects_delay(self) -> None:
        self.assertIn("llm_timeout", SCENARIO_NAMES)
        scenario = Scenario("llm_timeout")
        self.assertEqual(scenario.llm_delay_sec, 4.0)

    def test_llm_timeout_reuses_head_on_geometry(self) -> None:
        timeout_scenario = Scenario("llm_timeout")
        head_on_scenario = Scenario("head_on_crossing")
        for seed in range(3):
            self.assertEqual(
                timeout_scenario.targets_for_seed(seed),
                head_on_scenario.targets_for_seed(seed),
            )

        targets = timeout_scenario.targets_for_seed(0)
        snapshot_start = {
            1: {"position": targets["drone1_start"]},
            2: {"position": targets["drone2_start"]},
        }
        snapshot_goal = {
            1: {"position": targets["drone1_goal"]},
            2: {"position": targets["drone2_goal"]},
        }
        self.assertTrue(timeout_scenario.is_separated(snapshot_start))
        self.assertTrue(timeout_scenario.is_complete(snapshot_goal))

    def test_explicit_delay_is_not_overwritten(self) -> None:
        scenario = Scenario("llm_timeout", llm_delay_sec=2.5)
        self.assertEqual(scenario.llm_delay_sec, 2.5)
        self.assertEqual(Scenario("head_on_crossing").llm_delay_sec, 0.0)


if __name__ == "__main__":
    unittest.main()
