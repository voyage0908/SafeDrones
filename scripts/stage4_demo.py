#!/usr/bin/env python3
"""SafeDrones Stage-4 交互式演示（Unity 可视化 + 录屏）。

复用 stage4_benchmark 的 build_services / start_services / ManagedService /
RedPursuitController / C4Replanner，支持 2 机交叉、5 机编队交叉、8 机 4v4 混战。
与 stage4_benchmark 的区别：在「起始分离就位」与「注入穿越目标」之间加入交互暂停，
方便先开 Unity 按下 Play、再开始录屏后触发穿越/混战。

Unity 侧无需改动：DroneTelemetrySubscriber 订阅 swarm/drone/+/telemetry 与
swarm/commander/{status,override}，按 drone id 自动实例化（奇蓝偶红），天然支持 5/8 机。

用法：
  python scripts/stage4_demo.py --scenario formation_crossing --condition C3 --seed 0
  python scripts/stage4_demo.py --scenario four_v_four --condition C4 --seed 0
  python scripts/stage4_demo.py --scenario four_v_four --condition C4 --seed 0 --non-interactive --hold-sec 8

说明：
  - 编队交叉（formation_crossing）推荐 C3：反应式安全门即可 100% 无碰撞，无 LLM 延迟。
  - 4v4 混战（four_v_four）推荐 C4：展示「下行否决 + 上行反馈 + AGH 重规划」闭环
    （红方对称保护触发接管，AGH 把红方重规划到任务侧，打破蓝方被压制的死锁）。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# 强制 UTF-8 输出：Windows 控制台默认 GBK 会把中文打印成乱码。
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, OSError, ValueError):
    pass

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# dev_broker.py 用 subprocess.run(["amqtt", ...]) 拉起 broker，amqtt 需在 PATH 上。
# 直接把当前解释器所在目录（.venv/Scripts）加入 PATH，避免因未激活 venv 而找不到 amqtt。
_VENV_SCRIPTS = Path(sys.executable).resolve().parent
if str(_VENV_SCRIPTS) not in os.environ.get("PATH", ""):
    os.environ["PATH"] = str(_VENV_SCRIPTS) + os.pathsep + os.environ.get("PATH", "")

from scripts.stage4_benchmark import (  # noqa: E402
    C4Replanner,
    RedPursuitController,
    Scenario,
    build_services,
    start_services,
    stop_services,
    wait_for_crossing_completion,
    wait_with_republish,
)
from scripts.stage4_marl_safety_check import Stage4Monitor, command  # noqa: E402


SUPPORTED_CONDITIONS = {"C2", "C3", "C4"}
SCENARIO_CHOICES = [
    "head_on_crossing",
    "perpendicular_crossing",
    "diagonal_crossing",
    "four_way_crossing",
    "pursuit_intercept",
    "formation_crossing",
    "four_v_four",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run an interactive SafeDrones stage-4 demo for Unity recording.")
    parser.add_argument("--scenario", default="formation_crossing", choices=SCENARIO_CHOICES)
    parser.add_argument("--condition", default="C3", choices=sorted(SUPPORTED_CONDITIONS))
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--non-interactive", action="store_true", help="不等待 Enter，用 --hold-sec 定时自动开始穿越。")
    parser.add_argument("--hold-sec", type=float, default=8.0, help="非交互模式下，就位后等待多少秒再开始穿越（给录屏留出余量）。")
    return parser.parse_args()


def wait_for_go(non_interactive: bool, hold_sec: float) -> None:
    """就位后、开始穿越前的暂停点：让用户打开 Unity / 启动录屏。"""
    if non_interactive:
        print(f"[demo] 非交互模式：{hold_sec:.0f}s 后自动开始穿越/混战...")
        time.sleep(hold_sec)
        return

    try:
        input("[demo] 起始位置已就位。请在 Unity 中按下 Play，启动录屏后按 Enter 开始穿越/混战...")
    except EOFError:
        # stdin 不可读（如被重定向）时回退为定时等待。
        print("[demo] stdin 不可读，回退为 8s 定时等待后自动开始。")
        time.sleep(8.0)


def main() -> None:
    args = parse_args()
    scenario = Scenario(args.scenario)
    condition = args.condition
    seed = args.seed

    targets = scenario.targets_for_seed(seed)
    start_positions = {did: targets[f"drone{did}_start"] for did in scenario.all_ids()}
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_dir = ROOT_DIR / "logs" / "stage4_demo" / f"{scenario.name}_{condition}_seed{seed}_{run_id}"
    log_dir.mkdir(parents=True, exist_ok=True)

    print("[demo] ================================================")
    print(f"[demo] 场景={scenario.name} 条件={condition} 种子={seed}")
    print(f"[demo] 无人机数={scenario.drone_count} 蓝方={scenario.all_blue_ids()} "
          f"红方={scenario.red_ids or '无'} 红方追击={'是' if scenario.red_pursuit else '否'}")
    print("[demo] 提示：打开 unity/SwarmUnityDemo 并在穿越前按下 Play（遥测会自动接入）。")
    print(f"[demo] 日志目录={log_dir}")

    services, mqtt_port = build_services(
        condition,
        scenario,
        log_dir,
        start_positions=start_positions,
    )
    monitor = Stage4Monitor("127.0.0.1", mqtt_port, qos=0, expected_drones=scenario.drone_count)
    replanner = C4Replanner(scenario, targets) if condition == "C4" else None
    red_controller = RedPursuitController(scenario, monitor) if scenario.red_pursuit else None

    try:
        start_services(services, mqtt_port)
        monitor.start()
        monitor.wait_for_telemetry(timeout=8)
        print("[demo] 遥测就绪（Unity 此时应能看到全部无人机）。")

        # 起始分离：把无人机从同一原点拉开到各自起点。
        start_commands = [
            command(did, targets[f"drone{did}_start"], "separate")
            for did in scenario.all_ids()
        ]
        separated = wait_with_republish(
            monitor,
            lambda: scenario.is_separated(monitor.snapshot()),
            start_commands,
            timeout=scenario.separation_timeout,
            label="initial separation",
        )
        if separated:
            monitor.reset_min_distance()
            print("[demo] 起始分离完成。")
        else:
            print("[demo] 警告：起始分离超时，仍继续（用于展示）。")

        wait_for_go(args.non_interactive, args.hold_sec)

        # 穿越/混战：蓝方注入目标；红方追击场景由 RedPursuitController 驱动红方。
        goal_commands = [
            command(did, targets[f"drone{did}_goal"], "cross")
            for did in scenario.all_ids()
            if not (scenario.red_pursuit and did in scenario.red_ids)
        ]
        print("[demo] 注入穿越/混战目标...")
        swap_completed = separated and wait_for_crossing_completion(
            condition=condition,
            scenario=scenario,
            monitor=monitor,
            timeout=scenario.cross_timeout,
            replanner=replanner,
            red_controller=red_controller,
            republish_commands=goal_commands,
            targets=targets,
            arrival_radius_m=0.3,
        )
        if replanner is not None:
            replanner.process_new_overrides(monitor)

        summary = monitor.summary(swap_completed=swap_completed)
        min_distance = summary["min_distance_m"]
        collision = int(min_distance is not None and min_distance < scenario.collision_distance_m)

        print("[demo] ================================================")
        print("[demo] 结果摘要：")
        print(f"  - 任务完成：{'是' if swap_completed else '否'}")
        print(f"  - 最坏最小间距：{min_distance}m（碰撞阈值 {scenario.collision_distance_m}m）")
        print(f"  - 碰撞：{'是' if collision else '否'}")
        print(f"  - 安全门接管次数：{summary['override_count']}")
        print(f"  - 告警次数：{summary['warning_count']}")
        if replanner is not None:
            print(f"  - AGH 重规划次数：{replanner.stats.llm_replan_count} "
                  f"（失败 {replanner.stats.llm_replan_error_count}）")
        print("[demo] 演示结束（可停止 Unity 录屏）。")
    finally:
        monitor.stop()
        stop_services(services)


if __name__ == "__main__":
    main()
