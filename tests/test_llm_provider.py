import unittest

from swarm.llm_provider import extract_json_object, heuristic_plan, parse_waypoint_plan


class LLMProviderTest(unittest.TestCase):
    def test_extract_json_from_plain_response(self) -> None:
        self.assertEqual(extract_json_object('{"drone":1,"waypoint":[1,2,3]}')["drone"], 1)

    def test_extract_json_from_markdown_response(self) -> None:
        data = extract_json_object('```json\n{"drone":2,"waypoint":[1,2,3]}\n```')
        self.assertEqual(data["drone"], 2)

    def test_parse_waypoint_plan_clamps_confidence(self) -> None:
        plan = parse_waypoint_plan({"waypoint": [1, 2, 3], "confidence": 2}, default_drone=1)
        self.assertEqual(plan.waypoint, (1.0, 2.0, 3.0))
        self.assertEqual(plan.confidence, 1.0)

    def test_heuristic_plan_supports_left(self) -> None:
        plan = heuristic_plan("去左前方", drone=3)
        self.assertEqual(plan.drone, 3)
        self.assertEqual(plan.waypoint, (5.0, 5.0, 2.0))


if __name__ == "__main__":
    unittest.main()
