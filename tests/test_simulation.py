import unittest

from swarm.simulation import MockDroneState, parse_command, step_towards


class SimulationTest(unittest.TestCase):
    def test_step_towards_stops_at_target(self) -> None:
        self.assertEqual(step_towards((0, 0, 0), (1, 0, 0), 2), (1, 0, 0))

    def test_step_towards_moves_proportionally(self) -> None:
        self.assertEqual(step_towards((0, 0, 0), (2, 0, 0), 0.5), (0.5, 0.0, 0.0))

    def test_parse_stage_one_command(self) -> None:
        command = parse_command('{"action":"move_to","target":[5,5,2],"speed_mps":2}')
        self.assertEqual(command.action, "move_to")
        self.assertEqual(command.target, (5.0, 5.0, 2.0))
        self.assertEqual(command.speed_mps, 2.0)

    def test_parse_commander_waypoint_command(self) -> None:
        command = parse_command('{"waypoint":[10,5,2],"command_id":"cmd-1"}')
        self.assertEqual(command.action, "move_to")
        self.assertEqual(command.target, (10.0, 5.0, 2.0))
        self.assertEqual(command.command_id, "cmd-1")

    def test_state_accelerates_and_reaches_target(self) -> None:
        state = MockDroneState(drone_id=1, speed_mps=1)
        state.apply_command(parse_command('{"action":"move_to","target":[1,0,0]}'))
        state.update(0.25)
        self.assertEqual(state.current_pos, (0.0625, 0.0, 0.0))
        self.assertEqual(state.velocity, (0.25, 0.0, 0.0))
        self.assertEqual(state.status, "flying")
        state.update(1)
        self.assertEqual(state.current_pos, (1.0, 0.0, 0.0))
        self.assertEqual(state.status, "idle")

    def test_hover_brakes_instead_of_stopping_instantly(self) -> None:
        state = MockDroneState(drone_id=1, speed_mps=2, max_accel_mps2=1)
        state.apply_command(parse_command('{"action":"move_to","target":[5,0,1]}'))
        state.update(1)
        self.assertGreater(state.velocity[0], 0)

        state.apply_command(parse_command('{"action":"hover","command_id":"stop-1"}'))
        state.update(0.5)

        self.assertEqual(state.status, "hovering")
        self.assertGreater(state.velocity[0], 0)
        self.assertLess(state.velocity[0], 1.0)

    def test_telemetry_contains_motion_constraints(self) -> None:
        state = MockDroneState(drone_id=1, speed_mps=1, max_accel_mps2=0.5, max_yaw_rate_dps=90)
        state.apply_command(parse_command('{"action":"move_to","target":[1,1,1]}'))
        state.update(0.1)

        telemetry = state.telemetry(timestamp_ms=1)

        self.assertIn("velocity", telemetry)
        self.assertIn("yaw_deg", telemetry)
        self.assertEqual(telemetry["max_accel_mps2"], 0.5)
        self.assertEqual(telemetry["max_yaw_rate_dps"], 90)


if __name__ == "__main__":
    unittest.main()
