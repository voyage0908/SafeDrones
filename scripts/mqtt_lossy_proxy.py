"""Lossy MQTT TCP proxy for packet-loss fault injection.

Listens on a local port and forwards MQTT traffic to the real broker,
randomly dropping whole PUBLISH packets in both directions. Business
processes keep their usual MQTT settings and simply point at the proxy
port instead of the broker port.
"""

from __future__ import annotations

import argparse
import logging
import random
import socket
import threading
import time


LOGGER = logging.getLogger("mqtt_lossy_proxy")

MQTT_PACKET_PUBLISH = 3
MAX_REMAINING_LENGTH_BYTES = 4


def parse_packets(buffer: bytearray) -> list[bytes]:
    """Extract complete MQTT packets from buffer; an incomplete tail stays.

    Raises ValueError on a malformed remaining-length field.
    """
    packets: list[bytes] = []
    offset = 0
    while True:
        if len(buffer) < offset + 2:
            break

        remaining_length = 0
        multiplier = 1
        index = offset + 1
        incomplete = False
        for _ in range(MAX_REMAINING_LENGTH_BYTES):
            if index >= len(buffer):
                incomplete = True
                break
            byte = buffer[index]
            remaining_length += (byte & 0x7F) * multiplier
            multiplier *= 128
            index += 1
            if byte & 0x80 == 0:
                break
        else:
            raise ValueError("malformed MQTT remaining length (more than 4 bytes)")
        if incomplete:
            break

        packet_end = index + remaining_length
        if len(buffer) < packet_end:
            break
        packets.append(bytes(buffer[offset:packet_end]))
        offset = packet_end

    if offset:
        del buffer[:offset]
    return packets


def should_drop(packet: bytes, drop_rate: float, rng: random.Random) -> bool:
    """Decide whether a packet is dropped. Only PUBLISH packets are droppable."""
    if drop_rate <= 0.0 or not packet:
        return False
    packet_type = packet[0] >> 4
    if packet_type != MQTT_PACKET_PUBLISH:
        return False
    return rng.random() < drop_rate


def pump(
    source: socket.socket,
    destination: socket.socket,
    drop_rate: float,
    rng: random.Random,
    stats: dict[str, int],
    stats_lock: threading.Lock,
    label: str,
) -> None:
    buffer = bytearray()
    try:
        while True:
            chunk = source.recv(65536)
            if not chunk:
                break
            buffer.extend(chunk)
            for packet in parse_packets(buffer):
                if should_drop(packet, drop_rate, rng):
                    with stats_lock:
                        stats["dropped"] += 1
                    LOGGER.debug("dropped PUBLISH (%s, %d bytes)", label, len(packet))
                    continue
                destination.sendall(packet)
                with stats_lock:
                    stats["forwarded"] += 1
    except ValueError as exc:
        LOGGER.warning("closing %s after protocol error: %s", label, exc)
    except OSError:
        pass
    finally:
        for sock in (source, destination):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def handle_client(
    client_sock: socket.socket,
    target_host: str,
    target_port: int,
    drop_rate: float,
    seed: int,
) -> None:
    try:
        upstream = socket.create_connection((target_host, target_port), timeout=10)
    except OSError as exc:
        LOGGER.warning("failed to reach broker %s:%s: %s", target_host, target_port, exc)
        client_sock.close()
        return

    stats = {"forwarded": 0, "dropped": 0}
    stats_lock = threading.Lock()
    threads = [
        threading.Thread(
            target=pump,
            args=(client_sock, upstream, drop_rate, random.Random(seed), stats, stats_lock, "downlink"),
            daemon=True,
        ),
        threading.Thread(
            target=pump,
            args=(upstream, client_sock, drop_rate, random.Random(seed + 1), stats, stats_lock, "uplink"),
            daemon=True,
        ),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    client_sock.close()
    upstream.close()
    LOGGER.info(
        "connection closed: forwarded=%d dropped=%d",
        stats["forwarded"],
        stats["dropped"],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a lossy MQTT TCP proxy for fault injection.")
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, default=1884)
    parser.add_argument("--target-host", default="127.0.0.1")
    parser.add_argument("--target-port", type=int, default=1883)
    parser.add_argument("--drop-rate", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    if not 0.0 <= args.drop_rate <= 1.0:
        raise SystemExit("--drop-rate must be between 0 and 1")

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((args.listen_host, args.listen_port))
    listener.listen(16)
    LOGGER.info(
        "listening on %s:%s, forwarding to %s:%s, drop_rate=%.2f seed=%d",
        args.listen_host,
        args.listen_port,
        args.target_host,
        args.target_port,
        args.drop_rate,
        args.seed,
    )

    connection_index = 0
    try:
        while True:
            client_sock, address = listener.accept()
            connection_index += 1
            LOGGER.info("client connected from %s", address)
            threading.Thread(
                target=handle_client,
                args=(
                    client_sock,
                    args.target_host,
                    args.target_port,
                    args.drop_rate,
                    args.seed + connection_index * 2,
                ),
                daemon=True,
            ).start()
    except KeyboardInterrupt:
        LOGGER.info("stopping proxy")
    finally:
        listener.close()
        time.sleep(0.2)


if __name__ == "__main__":
    main()
