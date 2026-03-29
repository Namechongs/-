from __future__ import annotations

"""
JSON 校验模块：对 LLM 生成的配方 JSON 进行分层校验。

设计要点：
- 四层校验：顶层结构 -> plan 层 -> materials 局部 -> steps 层
- 外层失败时不再深入，内部错误尽量全部收集，以便一次性修正
- 返回值为 (is_valid, errors: List[str])，错误信息包含层级定位
- 供前端调用，在执行前对 plans 做完整性与一致性检查
"""

from typing import Any, Dict, List
import logging

logger = logging.getLogger(__name__)

required_top_keys = {"task_name", "requirement", "formula_reasoning", "plans"}

# 每种 action 需要的字段定义，方便扩展
action_required_fields: Dict[str, set] = {
    "move": {"target"},
    "grip": {"state"},
    "pump": {"pump_id", "amount_ml"},
    "stir": {"duration_seconds"},
    "print": {"content"},
}


def validate_formula(data: Dict[str, Any]) -> tuple[bool, List[str]]:
    errors: List[str] = []
    # 顶层结构校验
    if not isinstance(data, dict):
        errors.append("顶层数据应为对象（字典）")
        return False, errors
    top_keys = set(data.keys())
    missing_top = required_top_keys - top_keys
    if missing_top:
        errors.append("顶层缺少字段: " + ", ".join(sorted(missing_top)))
        # 顶层问题直接返回，后续依赖 data["plans"] 的逻辑会崩溃
        return False, errors

    plans = data.get("plans")
    if not isinstance(plans, list):
        errors.append("plans 字段应为列表")
        return False, errors

    # 针对每个 plan 进行分层校验
    for plan in plans:
        plan_id = plan.get("plan_id") if isinstance(plan, dict) else None
        plan_id_str = str(plan_id) if plan_id is not None else "unknown"
        if not isinstance(plan, dict):
            errors.append(f"[plan_id={plan_id_str}] 计划项应为对象字典")
            continue

        # materials 处理
        materials = plan.get("materials", [])
        if not isinstance(materials, list):
            errors.append(f"[plan_id={plan_id_str}] materials 应为列表")
            materials = []

        mat_map: Dict[int, float] = {}
        total_ml = 0.0
        for idx, m in enumerate(materials):
            if not isinstance(m, dict):
                errors.append(f"[plan_id={plan_id_str}] materials[{idx}] 应为对象字典")
                continue
            p_id_raw = m.get("pump_id")
            amt_raw = m.get("amount_ml")
            if p_id_raw is None or amt_raw is None:
                errors.append(f"[plan_id={plan_id_str}] materials[{idx}] 缺少 pump_id 或 amount_ml 字段")
                continue
            try:
                pid = int(p_id_raw)
                amt = float(amt_raw)
            except Exception:
                errors.append(f"[plan_id={plan_id_str}] materials[{idx}] pump_id/amount_ml 类型错误，应为整数/数字")
                continue
            if pid in mat_map:
                errors.append(f"[plan_id={plan_id_str}] pump_id={pid} 在 materials 中重复出现")
            mat_map[pid] = amt
            if amt > 0:
                total_ml += amt
        # 总量限制
        if total_ml > 400:
            errors.append(f"[plan_id={plan_id_str}] materials 总量 {total_ml}ml 超过上限 400ml")

        # stir_duration 与 steps 的检查需要 plan 级别信息
        stir_duration_plan = plan.get("stir_duration_seconds")
        steps = plan.get("steps")
        if steps is None:
            errors.append(f"[plan_id={plan_id_str}] steps 字段缺失")
            continue
        if not isinstance(steps, list):
            errors.append(f"[plan_id={plan_id_str}] steps 应为列表")
            continue

        # step_id 连续性检查
        step_ids = [s.get("step_id") for s in steps if isinstance(s, dict)]
        expected_ids = list(range(1, len(steps) + 1))
        if step_ids != expected_ids:
            errors.append(f"[plan_id={plan_id_str}] steps 的 step_id 不连续或起始不是 1")

        # 针对每一个 step 做具体检查
        for s_idx, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            step_id = step.get("step_id", s_idx + 1)
            action = step.get("action")
            if action not in action_required_fields:
                errors.append(f"[plan_id={plan_id_str}][step_id={step_id}] 非法的 action：{action}")
                continue
            required = action_required_fields[action]
            missing = [f for f in required if f not in step]
            if missing:
                errors.append(f"[plan_id={plan_id_str}][step_id={step_id}] action '{action}' 缺少字段: {', '.join(missing)}")
                continue

            # 特定字段的一致性检查
            if action == "pump":
                sp_id_raw = step.get("pump_id")
                sp_amt_raw = step.get("amount_ml")
                try:
                    sp_id = int(sp_id_raw)
                    sp_amt = float(sp_amt_raw)
                except Exception:
                    continue
                if sp_id not in mat_map:
                    errors.append(f"[plan_id={plan_id_str}][step_id={step_id}] pump_id={sp_id} 在 materials 中未定义")
                else:
                    mat_amt = mat_map[sp_id]
                    if mat_amt != sp_amt:
                        errors.append(
                            f"[plan_id={plan_id_str}][step_id={step_id}] pump amount 不一致：steps={sp_amt}，materials={mat_amt}"
                        )
            if action == "stir":
                # 与 plan 级 stir_duration_seconds 的一致性若提供
                if stir_duration_plan is not None:
                    try:
                        dur = float(step.get("duration_seconds"))
                        if dur != float(stir_duration_plan):
                            errors.append(
                                f"[plan_id={plan_id_str}][step_id={step_id}] stir duration 不一致：step={dur}，plan={stir_duration_plan}"
                            )
                    except Exception:
                        pass

    is_valid = len(errors) == 0
    if not is_valid:
        for e in errors:
            logger.warning(e)
    return is_valid, errors
