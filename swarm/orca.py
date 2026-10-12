"""orca.py — Reciprocal Velocity Obstacles (ORCA) 预测式协同避障。

安全门的「怎么躲」升级：从「背离最坏单对」的独立启发式，升级到教科书级 ORCA
（van den Berg et al. "Reciprocal n-body Collision Avoidance", 2011）。ORCA 在
**速度空间**里为每架无人机求一个「对所有邻居都无碰撞、且最接近期望速度」的协同
速度——邻居间互惠分摊避让责任（默认各 1/2），避免对称相向时双方同向躲闪的振荡。

实现说明：
- 只求解 **水平 X-Y 平面**（教科书标准，无人机避碰常规做法），垂直分离由 safety 层
  的 `vertical_escape_gain` 正交叠加，本模块不处理 z。
- 采用与 RVO2 库等价的 **2D 线性规划**（linearProgram1/2/3，精确解而非迭代投影），
  对 benchmark 里 ≤7 个邻居的场景计算量可忽略、且结果确定。
- 纯函数、无 I/O、不 import swarm 其他模块（避免与 safety.py 循环导入）；输入输出
  均为 2 维 (x, y) 向量。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

Vec2 = tuple[float, float]

_EPS = 1e-6


def _add(a: Vec2, b: Vec2) -> Vec2:
    return (a[0] + b[0], a[1] + b[1])


def _sub(a: Vec2, b: Vec2) -> Vec2:
    return (a[0] - b[0], a[1] - b[1])


def _scale(a: Vec2, s: float) -> Vec2:
    return (a[0] * s, a[1] * s)


def _dot(a: Vec2, b: Vec2) -> float:
    return a[0] * b[0] + a[1] * b[1]


def _det(a: Vec2, b: Vec2) -> float:
    return a[0] * b[1] - a[1] * b[0]


def _abs_sq(a: Vec2) -> float:
    return _dot(a, a)


def _abs(a: Vec2) -> float:
    return math.sqrt(_abs_sq(a))


def _normalize(a: Vec2) -> Vec2:
    length = _abs(a)
    if length < _EPS:
        return (0.0, 0.0)
    return (a[0] / length, a[1] / length)


def _perp(a: Vec2) -> Vec2:
    """逆时针旋转 90°：(x, y) -> (-y, x)。"""
    return (-a[1], a[0])


def _perp_neg(a: Vec2) -> Vec2:
    """顺时针旋转 90°：(x, y) -> (y, -x)。"""
    return (a[1], -a[0])


@dataclass(frozen=True)
class _Line:
    """ORCA 半平面：可行侧为 det(direction, v - point) >= 0（等价于 (v-point)·u >= 0）。"""

    point: Vec2
    direction: Vec2


def _orca_line(
    position: Vec2,
    velocity: Vec2,
    other_position: Vec2,
    other_velocity: Vec2,
    combined_radius: float,
    inv_time_horizon: float,
) -> tuple[Vec2, Vec2]:
    """构造 agent(自身) 相对 other(邻居) 的单条 ORCA 半平面。

    返回 (direction, u)，其中：
    - direction：半平面边界方向（可行侧为 det(direction, v - point) >= 0）。
    - u：让相对速度脱离速度障碍所需的**最小相对速度修正**；
      半平面边界点 point = velocity + responsibility * u（责任分摊比例由调用方传入）。
    """
    rel_pos = _sub(other_position, position)  # p_other - p_self（自身指向邻居）
    rel_vel = _sub(velocity, other_velocity)  # v_self - v_other
    dist_sq = _abs_sq(rel_pos)
    combined_radius_sq = combined_radius * combined_radius

    direction: Vec2
    u: Vec2

    if dist_sq > combined_radius_sq:
        # 当前不重叠：截断速度障碍（VO 锥），求最小相对速度修正 u。
        w = _sub(rel_vel, _scale(rel_pos, inv_time_horizon))
        w_len_sq = _abs_sq(w)
        dot1 = _dot(w, rel_pos)
        if dot1 < 0.0 and dot1 * dot1 > combined_radius_sq * w_len_sq:
            # 投影到截断圆上（相对位置后方）。
            w_len = math.sqrt(w_len_sq)
            unit_w = _scale(w, 1.0 / w_len)
            direction = _perp_neg(unit_w)
            u = _scale(unit_w, combined_radius * inv_time_horizon - w_len)
        else:
            # 投影到 VO 锥的两条腿上。
            leg = math.sqrt(dist_sq - combined_radius_sq)
            if _det(rel_pos, w) > 0.0:
                # 左腿。
                direction = (
                    (rel_pos[0] * leg - rel_pos[1] * combined_radius) / dist_sq,
                    (rel_pos[0] * combined_radius + rel_pos[1] * leg) / dist_sq,
                )
            else:
                # 右腿。
                direction = (
                    -(rel_pos[0] * leg + rel_pos[1] * combined_radius) / dist_sq,
                    -(-rel_pos[0] * combined_radius + rel_pos[1] * leg) / dist_sq,
                )
            dot2 = _dot(rel_vel, direction)
            u = _sub(_scale(direction, dot2), rel_vel)
    else:
        # 已重叠/接触：投影到 τ 时刻截断圆上（紧急分离）。
        w = _sub(rel_vel, _scale(rel_pos, inv_time_horizon))
        w_len = _abs(w)
        if w_len < _EPS:
            # 退化（相对速度恰抵消 τ 内的接近，或完全共点）：直接沿相对位置背离。
            away = _normalize(rel_pos) if _abs(rel_pos) > _EPS else (1.0, 0.0)
            direction = _perp_neg(away)
            u = _scale(away, combined_radius * inv_time_horizon)
        else:
            unit_w = _scale(w, 1.0 / w_len)
            direction = _perp_neg(unit_w)
            u = _scale(unit_w, combined_radius * inv_time_horizon - w_len)

    return direction, u


def _linear_program_1(
    lines: list[_Line],
    line_no: int,
    radius: float,
    opt_velocity: Vec2,
    direction_opt: bool,
) -> Vec2 | None:
    """在 line_no 半平面边界（截于 max-speed 圆内）上求最优点；不可行返回 None。"""
    point = lines[line_no].point
    direction = lines[line_no].direction
    dot_product = _dot(point, direction)
    disc = dot_product * dot_product + radius * radius - _abs_sq(point)
    if disc < 0.0:
        # max-speed 圆完全落在半平面不可行侧。
        return None

    sqrt_disc = math.sqrt(disc)
    t_left = -dot_product - sqrt_disc
    t_right = -dot_product + sqrt_disc

    for i in range(line_no):
        denominator = _det(direction, lines[i].direction)
        numerator = _det(lines[i].direction, _sub(point, lines[i].point))
        if abs(denominator) <= _EPS:
            # 近乎平行。
            if numerator < 0.0:
                return None
            continue

        t = numerator / denominator
        if denominator >= 0.0:
            t_right = min(t_right, t)
        else:
            t_left = max(t_left, t)
        if t_left > t_right:
            return None

    if direction_opt:
        if _dot(opt_velocity, direction) > 0.0:
            result = _add(point, _scale(direction, t_right))
        else:
            result = _add(point, _scale(direction, t_left))
    else:
        t = _dot(direction, _sub(opt_velocity, point))
        if t < t_left:
            result = _add(point, _scale(direction, t_left))
        elif t > t_right:
            result = _add(point, _scale(direction, t_right))
        else:
            result = _add(point, _scale(direction, t))

    return result


def _linear_program_2(
    lines: list[_Line],
    radius: float,
    opt_velocity: Vec2,
    direction_opt: bool,
) -> tuple[Vec2, int]:
    """求 max-speed 圆内满足所有半平面、最接近 opt_velocity 的可行速度。

    返回 (result, fail)：fail == len(lines) 表示全部满足；否则为首个被违反的半平面下标。
    """
    if direction_opt:
        result = _scale(opt_velocity, radius)
    elif _abs_sq(opt_velocity) > radius * radius:
        result = _scale(_normalize(opt_velocity), radius)
    else:
        result = opt_velocity

    for i in range(len(lines)):
        if _det(lines[i].direction, _sub(lines[i].point, result)) > 0.0:
            temp = result
            new_result = _linear_program_1(lines, i, radius, opt_velocity, direction_opt)
            if new_result is None:
                return temp, i
            result = new_result

    return result, len(lines)


def _linear_program_3(
    lines: list[_Line],
    num_obstacle_lines: int,
    begin_line: int,
    radius: float,
    result: Vec2,
) -> Vec2:
    """处理 linear_program_2 不可行的退化情形（3D 推广到 2D 的精确解）。"""
    distance = 0.0
    for i in range(begin_line, len(lines)):
        if _det(lines[i].direction, _sub(lines[i].point, result)) > distance:
            proj_lines = list(lines[:num_obstacle_lines])
            for j in range(num_obstacle_lines, i):
                determinant = _det(lines[i].direction, lines[j].direction)
                if abs(determinant) <= _EPS:
                    if _dot(lines[i].direction, lines[j].direction) > 0.0:
                        continue
                    point = _scale(_add(lines[i].point, lines[j].point), 0.5)
                else:
                    point = _add(
                        lines[i].point,
                        _scale(
                            lines[i].direction,
                            _det(lines[j].direction, _sub(lines[i].point, lines[j].point))
                            / determinant,
                        ),
                    )
                direction = _normalize(_sub(lines[j].direction, lines[i].direction))
                proj_lines.append(_Line(point=point, direction=direction))

            temp = result
            opt_dir = _perp(lines[i].direction)
            new_result, fail = _linear_program_2(proj_lines, radius, opt_dir, True)
            if fail < len(proj_lines):
                result = temp
            else:
                result = new_result
            distance = _det(lines[i].direction, _sub(lines[i].point, result))

    return result


def orca_velocity(
    position: Vec2,
    velocity: Vec2,
    preferred: Vec2,
    neighbors: list[tuple[Vec2, Vec2]],
    combined_radius: float,
    time_horizon: float,
    responsibility: float = 0.5,
    max_speed: float = 2.0,
) -> Vec2:
    """计算 agent 的 ORCA 无碰撞速度（2D）。

    - position / velocity / preferred：自身位置、当前速度、期望速度（均为 (x, y)）。
    - neighbors：[(position, velocity), ...] 邻居列表。
    - combined_radius：双方几何半径之和（本项目用 safe_distance_m，语义「保持该间距」）。
    - time_horizon：预测时域 τ（秒）。
    - responsibility：互惠分摊比例（0.5 = 标准 ORCA 各承担一半）。
    - max_speed：速度上限（可行域为半径 max_speed 的圆）。

    返回最接近 preferred 的无碰撞速度；无邻居时直接返回 preferred（夹到 max_speed）。
    """
    inv_time_horizon = 1.0 / time_horizon
    lines: list[_Line] = []
    for other_position, other_velocity in neighbors:
        direction, u = _orca_line(
            position, velocity, other_position, other_velocity,
            combined_radius, inv_time_horizon,
        )
        lines.append(
            _Line(point=_add(velocity, _scale(u, responsibility)), direction=direction)
        )

    if not lines:
        pref = preferred
        if _abs(pref) > max_speed:
            pref = _scale(_normalize(pref), max_speed)
        return pref

    pref = preferred
    if _abs(pref) > max_speed:
        pref = _scale(_normalize(pref), max_speed)

    new_velocity, fail = _linear_program_2(lines, max_speed, pref, False)
    if fail < len(lines):
        new_velocity = _linear_program_3(lines, 0, fail, max_speed, new_velocity)

    return new_velocity


def orca_pair_risk(
    position: Vec2,
    velocity: Vec2,
    other_position: Vec2,
    other_velocity: Vec2,
    combined_radius: float,
    time_horizon: float,
) -> float:
    """预测性风险：速度障碍（VO）意义下的「到碰撞时间」，归一化到 0..1。

    相对轨迹 rel_pos + rel_vel·t 在 t∈[0, τ] 内首次进入碰撞盘（半径 combined_radius）
    的时间为 t_col，则风险 = 1 - t_col/τ（越早碰撞风险越高）；τ 内无碰撞返回 0。
    这是 ORCA/VO 的核心原语，比反应式标量 TTC 更准——它用完整 2D 相对速度向量，
    对非正对几何（垂直/斜向交叉）也能正确预判。单调、只增不减，供调用方与
    现有反应式风险取 max 做「提前预警」（--orca-early）。
    """
    rel_pos = _sub(other_position, position)  # p_other - p_self（other 相对 self 的位置）
    rel_vel = _sub(other_velocity, velocity)  # v_other - v_self（other 在 self 参考系下的速度）
    a = _abs_sq(rel_vel)
    if a < _EPS:
        # 相对静止：无碰撞趋势（若已重叠，由反应式距离风险兜底）。
        return 0.0

    b = 2.0 * _dot(rel_pos, rel_vel)
    c = _abs_sq(rel_pos) - combined_radius * combined_radius
    disc = b * b - 4.0 * a * c
    if disc < 0.0:
        return 0.0  # 相对轨迹永不相交。

    sqrt_disc = math.sqrt(disc)
    t_col = (-b - sqrt_disc) / (2.0 * a)  # 更小的根 = 首次进入碰撞盘的时间。
    if t_col < 0.0:
        return 0.0  # 正在远离（或已穿过），不构成前瞻风险。
    if t_col > time_horizon:
        return 0.0  # 碰撞发生在时域之外，交给反应式门。

    return 1.0 - t_col / time_horizon
