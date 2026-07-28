from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the development MQTT broker.")
    parser.add_argument(
        "--config",
        default="config/amqtt.yml",
        help="Path to the amqtt YAML config file.",
    )
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        raise SystemExit(f"broker config not found: {config_path}")

    try:
        subprocess.run(["amqtt", "-c", str(config_path)], check=True)
    except FileNotFoundError as exc:
        raise SystemExit(
            "Missing dependency: amqtt. Install it in your active conda env with "
            "`python -m pip install -r requirements.txt`."
        ) from exc


if __name__ == "__main__":
    main()
