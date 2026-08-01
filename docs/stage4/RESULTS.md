# Stage 4 Results

## Run Metadata

- Date: 2026-08-01
- Environment: `conda run -n eai-swarm`
- Command:

```bash
conda run -n eai-swarm python scripts/stage4_benchmark.py \
  --scenario all \
  --conditions C2,C3,C4 \
  --seeds 10 \
  --out results/stage4
```

- Result directory: `results/stage4/all_scenarios/20260801-140557`
- Raw files:
  - `runs.jsonl`
  - `summary.csv`
  - `aggregate.csv`

`results/` is intentionally git-ignored. This document records the reproducible command and the aggregate results needed for stage-four review.

## Conditions

| Condition | Meaning |
| --- | --- |
| C2 | Rule Pilot follows high-level crossing waypoints, no Safety Gate |
| C3 | C2 + Safety Gate, no LLM feedback replanning |
| C4 | C3 + real DeepSeek LLM replanner after `safety_override` |

C4 keeps the control boundary explicit: Safety Gate performs high-frequency emergency separation; DeepSeek only consumes structured override events and returns low-frequency mission-level recovery waypoints.

## Overall Results

| Condition | Runs | Success Rate | Collision Rate | Near-Miss Rate | Avg Min Distance (m) | Avg Overrides | Avg LLM Replans | Avg LLM Errors | Avg LLM Latency (ms) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| C2 | 30 | 0.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| C3 | 30 | 1.0000 | 0.0000 | 0.2333 | 0.8875 | 2.1333 | 0.0000 | 0.0000 | 0.0000 |
| C4 | 30 | 0.9667 | 0.0000 | 0.1333 | 0.9186 | 2.1333 | 1.9667 | 0.0333 | 2634.4760 |

## Scenario Results

| Scenario | Condition | Runs | Success Rate | Collision Rate | Near-Miss Rate | Avg Min Distance (m) | Avg Overrides | Avg LLM Replans | Avg LLM Errors | Avg Revision Rate | Avg LLM Latency (ms) | Max LLM Latency (ms) |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `head_on_crossing` | C2 | 10 | 0.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| `head_on_crossing` | C3 | 10 | 1.0000 | 0.0000 | 0.4000 | 0.8200 | 2.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| `head_on_crossing` | C4 | 10 | 1.0000 | 0.0000 | 0.2000 | 0.8370 | 2.0000 | 2.0000 | 0.0000 | 1.0000 | 3367.8493 | 12976.7725 |
| `perpendicular_crossing` | C2 | 10 | 0.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| `perpendicular_crossing` | C3 | 10 | 1.0000 | 0.0000 | 0.2000 | 0.8889 | 2.4000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| `perpendicular_crossing` | C4 | 10 | 0.9000 | 0.0000 | 0.1000 | 0.9226 | 2.4000 | 1.9000 | 0.1000 | 0.8500 | 2365.8994 | 5269.2699 |
| `diagonal_crossing` | C2 | 10 | 0.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| `diagonal_crossing` | C3 | 10 | 1.0000 | 0.0000 | 0.1000 | 0.9536 | 2.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| `diagonal_crossing` | C4 | 10 | 1.0000 | 0.0000 | 0.1000 | 0.9963 | 2.0000 | 2.0000 | 0.0000 | 1.0000 | 2169.6794 | 4698.6108 |

## Findings

1. C2 is the intended unsafe baseline: all 30 runs collide, with `avg_min_distance_m = 0.0`.
2. C3 validates the Safety Gate contribution: all 30 runs finish without collision, across all three crossing scenarios.
3. C4 validates the real DeepSeek feedback path, but not as a hard-real-time safety dependency. It completed 29/30 strict trials without collision, with 59 successful LLM replans out of 60 expected replan opportunities.
4. The single C4 strict failure was `perpendicular_crossing`, seed 3. The physical task still completed and no collision occurred (`min_distance_m = 1.0039`), but one DeepSeek replan failed after 3 attempts with `LLM response did not contain a JSON object`, so the run is counted as a C4 protocol failure.
5. LLM latency is non-trivial: C4 averaged about 2.63 seconds per successful replan, with a worst observed successful call of 12.98 seconds. This supports the architecture decision that Safety Gate must remain local and high-frequency.

## Stage-Four Status

Stage four is closed for the local protocol benchmark:

- C2/C3/C4 runner exists and is reproducible.
- C2 vs C3 isolates the Safety Gate contribution.
- C4 uses a real DeepSeek API feedback loop rather than a fake replanner.
- Logs and CSV metrics capture collision, near miss, override, command revision, LLM error, and LLM latency behavior.

Remaining work moves to the next stage: MARL/ONNX policy training, public benchmark alignment, and larger Unity multi-agent scenarios.
