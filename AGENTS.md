# Agent Notes

## Python Environment

This project uses the Conda environment `eai-swarm`.

Use it for project commands:

```bash
conda activate eai-swarm
```

For non-interactive agent commands, prefer:

```bash
conda run -n eai-swarm python -m unittest discover -s tests
```

The environment was checked with:

```text
Conda env: eai-swarm
Python: 3.14.6
Path: /home/vitalrubbish/miniconda3/envs/eai-swarm
```

Key dependencies are installed in `eai-swarm`:

```text
fastapi 0.140.7
paho-mqtt 2.1.0
amqtt 0.11.3
uvicorn 0.51.0
httpx 0.28.1
```

The default shell may still start in Conda `base`, which currently uses Python 3.13.11 and does not necessarily contain the project dependencies. Do not rely on `base` for tests or runtime commands.

## Common Commands

Run the test suite:

```bash
conda run -n eai-swarm python -m unittest discover -s tests
```

Start the development MQTT broker:

```bash
conda run -n eai-swarm python scripts/dev_broker.py
```

Start one mock drone:

```bash
conda run -n eai-swarm python mock_drone.py --drone-id 1
```

Start the FastAPI gateway:

```bash
conda run -n eai-swarm uvicorn gateway:app --host 127.0.0.1 --port 8000
```

## Unity Project

The Unity visualization project lives at:

```text
unity/SwarmUnityDemo
```

Keep the Python package directory `swarm/` separate from the Unity project. Do not place Unity `Assets/`, `Packages/`, `ProjectSettings/`, or `Library/` under the Python `swarm/` package.

## Additional Commands

Smoke-test the MQTT → MockDrone loop:

```bash
conda run -n eai-swarm python scripts/smoke_stage1.py
```

Publish a manual waypoint without the gateway:

```bash
conda run -n eai-swarm python scripts/publish_command.py --drone-id 1 --target 5 5 2
```

Start the MARL pilot (rule-based fallback):

```bash
conda run -n eai-swarm python marl_pilot.py
```

Start the Safety Gate:

```bash
conda run -n eai-swarm python safety_gate.py
```

Run the stage-four ablation benchmark:

```bash
conda run -n eai-swarm python scripts/stage4_benchmark.py \
  --scenario head_on_crossing --conditions C2,C3 --seeds 10 --out results/stage4
```

## Platform Notes

- The benchmark runner (`scripts/stage4_benchmark.py`) uses `os.killpg` / `start_new_session`, which behave differently on Windows. It has been adapted with platform-aware process management (fallback to `process.terminate()` / `subprocess.CREATE_NEW_PROCESS_GROUP`).
- When cleaning up after a run on Windows, use `taskkill //F //IM python.exe` (or equivalent) if subprocesses persist.
- On Linux the full C2/C3/C4 ablation suite has been verified (see `docs/stage4/RESULTS.md`).

## Documents writing
Write all the documents in Chinese.
