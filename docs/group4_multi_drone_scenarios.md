# 组 4：4-5 机复杂场景实施方案

## 场景总览

| # | 名称 | 机数 | 几何特征 | 核心验收 |
|---|------|------|---------|---------|
| 1 | `four_way_crossing` | 4 | 四角同时对角穿越，中心点 6 对冲突 | 零碰撞，最近距离 ≥ 0.5m |
| 2 | `formation_crossing` | 5 | 蓝方 3 机 V 字编队 + 红方 2 机交错穿越 | 编队间距保持，蓝方零碰撞 |
| 3 | `pursuit_intercept` | 4 | 蓝方 2 机直飞 + 红方 2 机垂直切入追击 | C3 零碰撞，C2 有碰撞（体现 Gate 价值） |

---

## 通用改造：多机基础设施

三个场景共享以下改造，必须先完成。

### 0.1 Scenario 类泛化

```python
# scripts/stage4_benchmark.py

@dataclass(frozen=True)
class Scenario:
    name: str
    drone_count: int = 2               # 新增：总机数
    blue_ids: tuple[int, ...] = ()     # 新增：蓝方 id（默认空 = 全部为蓝方）
    red_ids: tuple[int, ...] = ()      # 新增：红方 id
    separation_timeout: float = 12.0
    cross_timeout: float = 30.0
    near_miss_distance_m: float = 0.8
    collision_distance_m: float = 0.25
```

`blue_ids` / `red_ids` 默认空表示无红蓝区分，全部视为蓝方（向后兼容 2 机场景）。红方不受 Pilot 控制、不受 Gate 保护。

### 0.2 Stage4Monitor 泛化

```python
# scripts/stage4_marl_safety_check.py

class Stage4Monitor:
    def __init__(self, host, port, qos, expected_drones: int = 2):  # 新增参数
        ...
        self.expected_drones = expected_drones

    def _record_telemetry(self, payload):
        # 改：min_distance 对所有上报无人机做全配对
        ...
        all_ids = set(self.telemetry.keys())
        if len(all_ids) >= 2:                    # 至少有 2 机才计算
            pos_list = [(did, self.telemetry[did].get("position"))
                        for did in sorted(all_ids)]
            for i in range(len(pos_list)):
                for j in range(i + 1, len(pos_list)):
                    if pos_list[i][1] and pos_list[j][1]:
                        d = dist(pos_list[i][1], pos_list[j][1])
                        if self.min_distance_m is None or d < self.min_distance_m:
                            self.min_distance_m = d
        # telemetry_ready 改为 >= expected_drones
        if len(self.telemetry) >= self.expected_drones:
            self.telemetry_ready.set()
```

### 0.3 marl_pilot.py / safety_gate.py 白名单

```python
# marl_pilot.py 新增参数
parser.add_argument("--drone-ids", type=int, nargs="*", default=[],
                    help="仅控制这些 drone id，默认空=全部")

# safety_gate.py 新增参数
parser.add_argument("--protect-ids", type=int, nargs="*", default=[],
                    help="仅保护这些 drone id，默认空=全部")
```

- Pilot 收到 telemetry 后，只对 `--drone-ids` 中的 drone 发 micro-waypoint。
- Gate 收到全部 telemetry 做风险评估（红方作为障碍物），但只对 `--protect-ids` 中的 drone 发 override。

### 0.4 build_services / run_trial 泛化

```python
def build_services(condition, scenario, log_dir):
    services = [ManagedService("broker", [...], log_dir)]
    for did in range(1, scenario.drone_count + 1):
        services.append(ManagedService(
            f"drone{did}",
            [python, "mock_drone.py", "--drone-id", str(did), *scenario.drone_args()],
            log_dir,
        ))
    pilot_cmd = [python, "marl_pilot.py", *scenario.pilot_args()]
    if scenario.blue_ids:
        pilot_cmd += ["--drone-ids"] + [str(d) for d in scenario.blue_ids]
    services.append(ManagedService("marl_pilot", pilot_cmd, log_dir))
    if condition in {"C3", "C4"}:
        gate_cmd = [python, "safety_gate.py"]
        if scenario.blue_ids:
            gate_cmd += ["--protect-ids"] + [str(d) for d in scenario.blue_ids]
        services.append(ManagedService("safety_gate", gate_cmd, log_dir))
    return services
```

`run_trial` 中 publish_command 改为对所有 drone 遍历：

```python
for did in range(1, scenario.drone_count + 1):
    monitor.publish_command(command(did, targets[f"drone{did}_start"], "separate"))
# 交叉阶段同理
for did in range(1, scenario.drone_count + 1):
    monitor.publish_command(command(did, targets[f"drone{did}_goal"], "cross"))
```

---

## 场景 1：四路交叉 `four_way_crossing`（4 机）

### 1.1 几何

```
起点（正方形四角）:              终点（对角）:
drone1 [-3, -3, h]      →       [ 3,  3, h]
drone2 [-3,  3, h]      →       [ 3, -3, h]
drone3 [ 3, -3, h]      →       [-3,  3, h]
drone4 [ 3,  3, h]      →       [-3, -3, h]

h = 1.0 + z 抖动（种子相关）
```

特点：4 条航线在原点 (0, 0) 交汇，共 C(4,2) = 6 对潜在冲突。

### 1.2 targets_for_seed

```python
if self.name == "four_way_crossing":
    rng = random.Random(seed)
    altitude = 1.0 + rng.uniform(-0.03, 0.03)
    jitter = 0.15  # 每个方向的小扰动
    return {
        "drone1_start": [-3.0, -3.0 + rng.uniform(-jitter, jitter), altitude],
        "drone2_start": [-3.0,  3.0 + rng.uniform(-jitter, jitter), altitude],
        "drone3_start": [ 3.0, -3.0 + rng.uniform(-jitter, jitter), altitude],
        "drone4_start": [ 3.0,  3.0 + rng.uniform(-jitter, jitter), altitude],
        "drone1_goal":  [ 3.0,  3.0 + rng.uniform(-jitter, jitter), altitude],
        "drone2_goal":  [ 3.0, -3.0 + rng.uniform(-jitter, jitter), altitude],
        "drone3_goal":  [-3.0,  3.0 + rng.uniform(-jitter, jitter), altitude],
        "drone4_goal":  [-3.0, -3.0 + rng.uniform(-jitter, jitter), altitude],
    }
```

### 1.3 状态判定

```python
def is_separated(self, snapshot):
    # 每机 x 坐标在各自起始半区内
    # drone1/2 从左侧出发: x < -2.0; drone3/4 从右侧出发: x > 2.0
    for did in (1, 2):
        if not snapshot.get(did, {}).get("position"):
            return False
        if snapshot[did]["position"][0] >= -2.0:
            return False
    for did in (3, 4):
        if not snapshot.get(did, {}).get("position"):
            return False
        if snapshot[did]["position"][0] <= 2.0:
            return False
    return True

def is_complete(self, snapshot):
    # drone1/2 到达右侧: x > 2.0; drone3/4 到达左侧: x < -2.0
    for did in (1, 2):
        if not snapshot.get(did, {}).get("position"):
            return False
        if snapshot[did]["position"][0] <= 2.0:
            return False
    for did in (3, 4):
        if not snapshot.get(did, {}).get("position"):
            return False
        if snapshot[did]["position"][0] >= -2.0:
            return False
    return True
```

### 1.4 Pilot 参数

4 机同时交叉，Pilot 需要更强排斥力保证分离：

```python
def pilot_args(self):
    if self.name == "four_way_crossing":
        return [
            "--rule-safe-distance", "0.5",
            "--repulsion-gain", "1.0",
            "--max-speed", "2.0",
            "--horizon-sec", "0.5",
        ]
    # ... else 保持原有
```

### 1.5 结果字段

```python
# 在 summary 中增加
{
    "pairwise_min_distances": {       # 所有 drone 对的最小距离
        "1-2": 0.72, "1-3": 0.55, "1-4": 0.81,
        "2-3": 0.68, "2-4": 0.49, "3-4": 0.63,
    },
    "crossing_center_pass_sec": 3.2,  # 4 机全部通过原点 ±1m 圈的时间
}
```

### 1.6 验收标准

- 所有条件 (C2/C3/C4) 下 swap_completed = true
- C3/C4: collision_count = 0，所有 pairwise 距离 ≥ 0.35m（安全余量）
- C2: 预期有碰撞或近失（无 Gate 保护），体现消融差异

---

## 场景 2：编队穿越 `formation_crossing`（5 机）

### 2.1 几何

```
蓝方（1-3）V 字编队从左→右:       红方（4-5）并排从右→左:
drone1 [-3,  0 , h] → [3,  0 , h]    drone4 [ 3,  1.5, h] → [-3, -1.5, h]
drone2 [-3, -1 , h] → [3, -1 , h]    drone5 [ 3, -1.5, h] → [-3,  1.5, h]
drone3 [-3,  1 , h] → [3,  1 , h]

h = 1.0 + z 抖动
```

蓝方编队间距 = 2m（y 方向），红方间距 = 3m。红蓝航线在 x≈0 处交错：蓝方 y 通道 -1/0/1，红方 y 通道 ±1.5，错开了 0.5m。

### 2.2 场景配置

```python
Scenario(
    name="formation_crossing",
    drone_count=5,
    blue_ids=(1, 2, 3),
    red_ids=(4, 5),
    separation_timeout=16.0,  # 5 机分离比 2 机慢
    cross_timeout=40.0,       # 编队保持 + 交叉更耗时
)
```

### 2.3 targets_for_seed

```python
if self.name == "formation_crossing":
    rng = random.Random(seed)
    altitude = 1.0 + rng.uniform(-0.03, 0.03)
    jitter = rng.uniform(-0.15, 0.15)
    return {
        # 蓝方 V 字编队起点
        "drone1_start": [-3.0,  0.0 + jitter, altitude],
        "drone2_start": [-3.0, -1.0 + jitter, altitude],
        "drone3_start": [-3.0,  1.0 + jitter, altitude],
        # 红方并排起点
        "drone4_start": [ 3.0,  1.5 + jitter, altitude],
        "drone5_start": [ 3.0, -1.5 + jitter, altitude],
        # 蓝方编队终点
        "drone1_goal":  [ 3.0,  0.0 + jitter, altitude],
        "drone2_goal":  [ 3.0, -1.0 + jitter, altitude],
        "drone3_goal":  [ 3.0,  1.0 + jitter, altitude],
        # 红方终点（交叉到对侧）
        "drone4_goal":  [-3.0, -1.5 + jitter, altitude],
        "drone5_goal":  [-3.0,  1.5 + jitter, altitude],
    }
```

### 2.4 状态判定

```python
def is_separated(self, snapshot):
    # 蓝方全部 x < -2.0，红方全部 x > 2.0
    for did in self.blue_ids:
        p = snapshot.get(did, {}).get("position")
        if p is None or p[0] >= -2.0:
            return False
    for did in self.red_ids:
        p = snapshot.get(did, {}).get("position")
        if p is None or p[0] <= 2.0:
            return False
    return True

def is_complete(self, snapshot):
    # 蓝方全部 x > 2.0
    for did in self.blue_ids:
        p = snapshot.get(did, {}).get("position")
        if p is None or p[0] <= 2.0:
            return False
    return True  # 红方到达不是完成条件（红方只需不撞）
```

### 2.5 红方控制

红方不需要格外控制器——在 GT 模式下，红方也接收固定 goal 指令。它们从右侧飞到左侧，恰好与蓝方交叉。差异在于：

- 红方不在 `blue_ids` 中 → Pilot 不给红方发 micro-waypoint
- 红方不在 `protect-ids` 中 → Gate 不给红方发 override
- 但红方的 telemetry 仍被 Gate 订阅 → Gate 将红方视为移动障碍物

### 2.6 Pilot 参数

```python
def pilot_args(self):
    if self.name == "formation_crossing":
        return [
            "--rule-safe-distance", "0.3",
            "--repulsion-gain", "1.5",   # 编队保持需要更强排斥
            "--max-speed", "2.0",
            "--horizon-sec", "0.5",
        ]
```

### 2.7 结果字段

```python
{
    "blue_min_formation_distance_m": 1.85,   # 蓝方 3 机之间最小间距
    "blue_max_formation_spread_m": 2.3,      # 蓝方 3 机最大扩散范围
    "red_blue_near_miss_count": 1,           # 红蓝之间的近失次数
    "blue_all_reached": true,                # 蓝方全部到达目标
}
```

### 2.8 验收标准

- C3/C4: 蓝方 3 机全部到达（swap_completed），零碰撞
- 编队间距全程 ≥ 1.5m（内部不互相碰撞）
- C2: 预期至少 1 次碰撞或近失

---

## 场景 3：追逃拦截 `pursuit_intercept`（4 机）

### 3.1 几何

```
蓝方（1-2）直飞:                   红方（3-4）垂直切入:
drone1 [-3, -0.5, h] → [3, -0.5, h]   drone3 [ 0,  3, h] → 动态追击 drone1/2
drone2 [-3,  0.5, h] → [3,  0.5, h]   drone4 [ 0, -3, h] → 动态追击 drone1/2
```

红方目标不是固定航点——每 2 秒重新计算最近蓝方位置，加提前量后作为追击指令。

### 3.2 场景配置

```python
Scenario(
    name="pursuit_intercept",
    drone_count=4,
    blue_ids=(1, 2),
    red_ids=(3, 4),
    separation_timeout=12.0,
    cross_timeout=40.0,    # 追逃场景，蓝方可能绕行
)
```

### 3.3 targets_for_seed

```python
if self.name == "pursuit_intercept":
    rng = random.Random(seed)
    altitude = 1.0 + rng.uniform(-0.03, 0.03)
    return {
        "drone1_start": [-3.0, -0.5 + rng.uniform(-0.15, 0.15), altitude],
        "drone2_start": [-3.0,  0.5 + rng.uniform(-0.15, 0.15), altitude],
        "drone3_start": [ 0.0 + rng.uniform(-0.3, 0.3),  3.0, altitude],
        "drone4_start": [ 0.0 + rng.uniform(-0.3, 0.3), -3.0, altitude],
        "drone1_goal":  [ 3.0, -0.5, altitude],
        "drone2_goal":  [ 3.0,  0.5, altitude],
        # 红方无固定 goal（由 RedPursuitController 动态生成）
        "drone3_goal":  [ 3.0, -0.5, altitude],  # 占位
        "drone4_goal":  [ 3.0,  0.5, altitude],  # 占位
    }
```

### 3.4 RedPursuitController

新增 `scripts/stage4_benchmark.py` 内嵌类：

```python
@dataclass
class RedPursuitController:
    """红方追击控制器：每 2 秒对每架红方发追击指令。"""
    scenario: Scenario
    monitor: Stage4Monitor
    interval_sec: float = 2.0
    lead_factor: float = 1.5      # 提前量系数（秒）
    pursuit_speed: float = 1.8    # 追击速度

    _last_tick: float = field(default=0.0, init=False)

    def tick(self) -> None:
        now = time.time()
        if now - self._last_tick < self.interval_sec:
            return
        self._last_tick = now

        snapshot = self.monitor.snapshot()
        for red_id in self.scenario.red_ids:
            # 找最近的蓝方
            target_blue = self._find_nearest_blue(red_id, snapshot)
            if target_blue is None:
                continue
            # 加提前量：蓝方当前位置 + 速度 × lead_factor
            blue_pos = snapshot[target_blue].get("position", [0, 0, 0])
            blue_vel = snapshot[target_blue].get("velocity", [0, 0, 0])
            intercept = [
                blue_pos[i] + blue_vel[i] * self.lead_factor
                for i in range(3)
            ]
            cmd = command(red_id, intercept, "red-pursuit")
            cmd["priority"] = "red"
            self.monitor.publish_command(cmd)

    def _find_nearest_blue(self, red_id, snapshot):
        red_pos = snapshot.get(red_id, {}).get("position")
        if red_pos is None:
            return None
        best_blue, best_dist = None, float("inf")
        for blue_id in self.scenario.blue_ids:
            bp = snapshot.get(blue_id, {}).get("position")
            if bp is None:
                continue
            d = math.dist(red_pos, bp)
            if d < best_dist:
                best_dist, best_blue = d, blue_id
        return best_blue
```

### 3.5 状态判定

```python
def is_separated(self, snapshot):
    # 蓝方在左侧，红方在远侧（上下）
    for did in self.blue_ids:
        p = snapshot.get(did, {}).get("position")
        if p is None or p[0] >= -2.0:
            return False
    for did in self.red_ids:
        p = snapshot.get(did, {}).get("position")
        if p is None:
            return False
        # 红方应在各自的起始半区
        if did == 3 and p[1] < 2.0:
            return False
        if did == 4 and p[1] > -2.0:
            return False
    return True

def is_complete(self, snapshot):
    # 蓝方至少 1 架到达目标区域即可（追逃场景可能有机被拦截）
    blue_reached = 0
    for did in self.blue_ids:
        p = snapshot.get(did, {}).get("position")
        if p is not None and p[0] > 2.0:
            blue_reached += 1
    return blue_reached >= 1
```

### 3.6 run_trial 改造

交叉阶段增加红方 tick：

```python
red_controller = RedPursuitController(scenario, monitor) if scenario.red_ids else None
# ...
while time.time() < deadline:
    if replanner:
        replanner.process_new_overrides(monitor)
    if red_controller:
        red_controller.tick()
    if scenario.is_complete(monitor.snapshot()):
        break
    time.sleep(0.1)
```

### 3.7 结果字段

```python
{
    "blue_reached_count": 2,           # 蓝方到达数
    "blue_intercepted_count": 0,       # 被红方拦截数（碰撞或迫近 < 0.5m）
    "red_pursuit_commands": 34,        # 红方追击指令总数
    "red_avg_intercept_error_m": 0.8,  # 红方拦截点 vs 蓝方实际位置的平均误差
    "min_blue_red_distance_m": 0.55,   # 蓝-红最近距离
}
```

### 3.8 验收标准

| 条件 | 预期结果 |
|------|---------|
| C2（Pilot，无 Gate） | collision_count ≥ 1（红方拦截成功，无 Gate 保护） |
| C3（Pilot + Gate） | collision_count = 0，Gate 强制蓝方绕飞，override_count ≥ 2 |
| C4（LLM + Gate） | collision_count = 0，LLM 在 Gate 接管后重新规划路径 |

---

## 实施顺序建议

```
第一轮：基础设施 0.1-0.4（1-2 天）
  ├── Scenario 泛化
  ├── Stage4Monitor 全配对
  ├── Pilot/Gate 白名单
  └── build_services/run_trial 多机遍历

第二轮：四路交叉（1 天）
  ├── targets / is_separated / is_complete
  ├── pilot_args
  └── C2/C3/C4 冒烟

第三轮：追逃拦截（1-2 天）
  ├── RedPursuitController
  ├── 红蓝分离/完成判定
  └── C2/C3 对比冒烟

第四轮：编队穿越（1 天）
  ├── 5 机配置
  └── 编队间距统计

第五轮：数据落盘 + 报告
```

## 向后兼容性

所有改造对现有 2 机 3 场景 (head_on/perpendicular/diagonal) **零影响**：
- `drone_count` 默认 2
- `blue_ids` / `red_ids` 默认空 → 全部蓝方（现有行为）
- `expected_drones` 默认 2
- `--drone-ids` / `--protect-ids` 默认空 → 全部
