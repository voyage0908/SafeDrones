import random
import unittest

from scripts.mqtt_lossy_proxy import parse_packets, should_drop


class ParsePacketsTest(unittest.TestCase):
    def test_single_complete_packet(self) -> None:
        buffer = bytearray([0x30, 0x02, 0x01, 0x02])
        packets = parse_packets(buffer)
        self.assertEqual(packets, [bytes([0x30, 0x02, 0x01, 0x02])])
        self.assertEqual(buffer, bytearray())

    def test_two_concatenated_packets(self) -> None:
        pingreq = bytes([0xC0, 0x00])
        publish = bytes([0x30, 0x02, 0x01, 0x02])
        buffer = bytearray(pingreq + publish)
        packets = parse_packets(buffer)
        self.assertEqual(packets, [pingreq, publish])
        self.assertEqual(buffer, bytearray())

    def test_partial_packet_waits_for_more_data(self) -> None:
        publish = bytes([0x30, 0x05, 0x01, 0x02, 0x03, 0x04, 0x05])
        buffer = bytearray(publish[:3])
        self.assertEqual(parse_packets(buffer), [])
        self.assertEqual(buffer, bytearray(publish[:3]))
        buffer.extend(publish[3:])
        self.assertEqual(parse_packets(buffer), [publish])
        self.assertEqual(buffer, bytearray())

    def test_incomplete_remaining_length_waits(self) -> None:
        # 0x80 has the continuation bit set, so the remaining-length field
        # needs a second byte that has not arrived yet.
        buffer = bytearray([0x30, 0x80])
        self.assertEqual(parse_packets(buffer), [])
        self.assertEqual(buffer, bytearray([0x30, 0x80]))

    def test_multi_byte_remaining_length(self) -> None:
        payload = bytes(range(200))
        packet = bytes([0x30, 0xC8, 0x01]) + payload  # remaining length 200
        buffer = bytearray(packet)
        self.assertEqual(parse_packets(buffer), [packet])
        self.assertEqual(buffer, bytearray())

    def test_malformed_remaining_length_raises(self) -> None:
        buffer = bytearray([0x30, 0xFF, 0xFF, 0xFF, 0xFF, 0x01])
        with self.assertRaises(ValueError):
            parse_packets(buffer)


class ShouldDropTest(unittest.TestCase):
    def test_zero_rate_never_drops(self) -> None:
        rng = random.Random(0)
        publish = bytes([0x30, 0x00])
        self.assertFalse(any(should_drop(publish, 0.0, rng) for _ in range(100)))

    def test_full_rate_always_drops_publish(self) -> None:
        rng = random.Random(0)
        publish = bytes([0x30, 0x00])
        self.assertTrue(all(should_drop(publish, 1.0, rng) for _ in range(100)))

    def test_non_publish_never_dropped(self) -> None:
        rng = random.Random(0)
        for packet_type in (1, 2, 6, 8, 12, 14):
            packet = bytes([packet_type << 4, 0x00])
            self.assertFalse(any(should_drop(packet, 1.0, rng) for _ in range(10)))

    def test_drop_decision_is_deterministic_with_seed(self) -> None:
        publish = bytes([0x30, 0x00])
        first = [should_drop(publish, 0.5, random.Random(42)) for _ in range(50)]
        second = [should_drop(publish, 0.5, random.Random(42)) for _ in range(50)]
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
