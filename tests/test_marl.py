import unittest

from swarm.marl import (
    MarlActionConfig,
    MarlObservationBuilder,
    MarlObservationConfig,
    RuleMarlPilot,
    action_to_velocity,
    action_to_waypoint,
    reward_for_transition,
)
from swarm.safety import DroneSnapshot


class MarlObservationTest(unittest.TestCase):
    def test_observation_has_fixed_size_and_neighbor_padding(self) -> None:
        config = MarlObservationConfig(max_neighbors=2)
        builder = MarlObservationBuilder(config)
        self_snapshot = DroneSnapshot(1, (1, 0, 1), target=(3, 0, 1), velocity=(0.5, 0, 0))
        peer = DroneSnapshot(2, (2, 0, 1), velocity=(-0.5, 0, 0))

        observation = builder.build(self_snapshot, [self_snapshot, peer])

        self.assertEqual(len(observation), config.observation_size)
        self.assertEqual(observation[0:3], [0.1, 0.0, 0.1])
        self.assertEqual(observation[6:9], [0.2, 0.0, 0.0])


class MarlActionTest(unittest.TestCase):
    def test_action_to_velocity_clamps_unit_action(self) -> None:
        velocity = action_to_velocity((2, 0, 0), MarlActionConfig(max_speed_mps=1.5))

        self.assertEqual(velocity, (1.5, 0.0, 0.0))

    def test_action_to_waypoint_uses_short_horizon(self) -> None:
        waypoint = action_to_waypoint((1, 1, 1), (1, 0, 0), MarlActionConfig(max_speed_mps=2, horizon_sec=0.25))

        self.assertEqual(waypoint, (1.5, 1.0, 1.0))


class MarlRewardTest(unittest.TestCase):
    def test_reward_is_positive_for_progress(self) -> None:
        reward = reward_for_transition(
            previous_position=(0, 0, 1),
            current_position=(0.5, 0, 1),
            target=(2, 0, 1),
            peer_positions=[],
            action=(1, 0, 0),
        )

        self.assertGreater(reward.total, 0)
        self.assertGreater(reward.progress, 0)

    def test_reward_penalizes_collision_distance(self) -> None:
        reward = reward_for_transition(
            previous_position=(0, 0, 1),
            current_position=(0.1, 0, 1),
            target=(2, 0, 1),
            peer_positions=[(0.2, 0, 1)],
            action=(1, 0, 0),
        )

        self.assertLess(reward.total, 0)
        self.assertLess(reward.collision, 0)


class RuleMarlPilotTest(unittest.TestCase):
    def test_rule_pilot_repels_from_close_peer(self) -> None:
        pilot = RuleMarlPilot()
        self_snapshot = DroneSnapshot(1, (0, 0, 1), target=(3, 0, 1), velocity=(0, 0, 0))
        close_peer = DroneSnapshot(2, (0, 0.4, 1), velocity=(0, 0, 0))

        action = pilot.predict(self_snapshot, [self_snapshot, close_peer], target=(3, 0, 1))

        self.assertGreater(action[0], 0)
        self.assertLess(action[1], 0)


if __name__ == "__main__":
    unittest.main()
