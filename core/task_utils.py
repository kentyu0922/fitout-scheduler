# -*- coding: utf-8 -*-
"""
core/task_utils.py — 任务工具函数

从 main.py 提取的两个工具函数：
  * clean_procurement_terminology — 采购术语清洗
  * renumber_tasks_contiguously   — Task ID 连续重编号
"""
import logging
from typing import List, Dict, Any

# 复用 _common 的前置任务拆分函数，消除重复正则
try:
    from core._common import split_predecessor_id_suffix
except ImportError:
    try:
        from _common import split_predecessor_id_suffix
    except ImportError:
        split_predecessor_id_suffix = None

logger = logging.getLogger(__name__)


def clean_procurement_terminology(
    tasks: List[Dict[str, Any]],
    procurement_mode_str: str,
    log: logging.Logger = None,
) -> List[Dict[str, Any]]:
    """SOP-清洗: 当检测到非公开招标模式时，扫描全部任务名称，
    将公招专属词汇硬替换为邀请招标术语，并彻底去除公招等标期字段。

    从 main.py L37-66 迁入。
    """
    if log is None:
        log = logger

    is_invite_tender = any(kw in procurement_mode_str for kw in [
        "邀请", "Invite", "短名单"
    ])
    is_open_tender = any(kw in procurement_mode_str for kw in [
        "Public", "SOE", "公开", "Financial"
    ])

    # 只要不是强制公招，就做清洗 (邀请招标 或 非明确公招 均清洗)
    if is_invite_tender and not is_open_tender:
        TERM_MAP = {
            "公开招标": "邀请招标",
            "招标公示": "短名单发标",
            "中标公示": "述标定标",
            "施工招标阶段 (DBB模式)": "施工邀请招标阶段",
            "DBB模式": "邀请招标模式",
            "招标投标": "邀请招标",
            "招标文件编制": "招标文件 (RFP) 编制",
        }
        for t in tasks:
            name = str(t.get("name", ""))
            for old_term, new_term in TERM_MAP.items():
                name = name.replace(old_term, new_term)
            t["name"] = name
        log.info("  -> [采购术语清洗] 已将残留公开招标词汇替换为邀请招标术语。")
    return tasks


def renumber_tasks_contiguously(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """清洗并重新连续编号所有 Task ID (1..N)，自动同步修正所有的 Predecessors 前置关联字符串，
    防止因节点删除或插入导致的离散 ID 破坏 MS Project COM 渲染。

    从 main.py L68-97 迁入。使用 _common.split_predecessor_id_suffix 消除重复正则。
    """
    id_map: Dict[int, int] = {}
    for idx, t in enumerate(tasks):
        old_id = t["id"]
        new_id = idx + 1
        id_map[old_id] = new_id
        t["id"] = new_id

    for t in tasks:
        if 'inspection_gate_ids' in t:
            t['inspection_gate_ids'] = [id_map[i] for i in t['inspection_gate_ids']]
        preds = str(t.get("predecessors", ""))
        if not preds:
            continue
        if split_predecessor_id_suffix is not None:
            # 使用共享函数
            pairs = split_predecessor_id_suffix(preds)
            new_parts = []
            for old_pid, suffix in pairs:
                if old_pid in id_map:
                    new_parts.append(f"{id_map[old_pid]}{suffix}")
                else:
                    new_parts.append(f"{old_pid}{suffix}")
            t["predecessors"] = ",".join(new_parts)
        else:
            # 兜底：内联正则（与 _common 行为一致）
            import re
            _re = re.compile(r"^(\d+)(.*)$")
            parts = preds.split(",")
            new_parts = []
            for p in parts:
                p_clean = p.strip()
                if not p_clean:
                    continue
                match = _re.match(p_clean)
                if match:
                    old_pid = int(match.group(1))
                    suffix = match.group(2)
                    if old_pid in id_map:
                        new_parts.append(f"{id_map[old_pid]}{suffix}")
                    else:
                        new_parts.append(p_clean)
                else:
                    new_parts.append(p_clean)
            t["predecessors"] = ",".join(new_parts)
    for t in tasks:
        contract=t.get('industry_work_contract')
        if contract is not None and split_predecessor_id_suffix is not None:
            contract['input_task_ids']=[pid for pid,_ in split_predecessor_id_suffix(str(t.get('predecessors','') or ''))]
    return tasks



# 免办施工许可阶段：名称命中则折叠（保留物业送审等）
_PERMIT_FOLD_KEYWORDS = (
    "施工许可证办理",
    "政府施工许可证申报",
    "质安监报监及政府施工许可证",
    "正式取得施工许可证",
)
_PERMIT_KEEP_KEYWORDS = (
    "物业",
    "二次装修",
    "安全协议",
)


def _is_construction_permit_fold_task(name: str) -> bool:
    n = str(name or "")
    if any(k in n for k in _PERMIT_KEEP_KEYWORDS) and "施工许可证" not in n:
        return False
    return any(k in n for k in _PERMIT_FOLD_KEYWORDS)


def _parse_pred_token(token: str):
    """Return (task_id, suffix) for a single predecessor token like 12SS+3d."""
    token = (token or "").strip()
    if not token:
        return None, ""
    i = 0
    while i < len(token) and token[i].isdigit():
        i += 1
    if i == 0:
        return None, ""
    return int(token[:i]), token[i:]


def fold_exempt_construction_permit(
    tasks: List[Dict[str, Any]],
    is_exempt: bool,
    log: logging.Logger = None,
) -> List[Dict[str, Any]]:
    """属地免办《施工许可证》时，折叠政府施工许可阶段节点，并重映射前置关系。

    保留物业装修图纸送审/安全协议等。被删节点的前置传递给下游。
    不联网；仅消费 permit_expert 算出的 is_exempt。
    """
    if log is None:
        log = logger
    if not is_exempt:
        return tasks

    by_id = {int(t["id"]): t for t in tasks}
    remove_ids = {int(t["id"]) for t in tasks if _is_construction_permit_fold_task(t.get("name", ""))}
    if not remove_ids:
        log.info("  -> [免办折叠] is_exempt=True，但未命中施工许可节点（模板可能已无该阶段）。")
        return tasks

    def expand_preds(pred_str: str) -> list:
        parts = []
        for token in str(pred_str or "").replace(" ", "").split(","):
            if not token:
                continue
            pid, suffix = _parse_pred_token(token)
            if pid is None:
                continue
            if pid in remove_ids:
                parent = by_id.get(pid)
                if parent:
                    parts.extend(expand_preds(parent.get("predecessors", "")))
            else:
                parts.append(f"{pid}{suffix}")
        seen = set()
        out = []
        for p in parts:
            if p not in seen:
                seen.add(p)
                out.append(p)
        return out

    kept = []
    for t in tasks:
        tid = int(t["id"])
        if tid in remove_ids:
            continue
        nt = dict(t)
        nt["predecessors"] = ",".join(expand_preds(t.get("predecessors", "")))
        kept.append(nt)

    log.info(
        f"  -> [免办折叠] 已移除政府施工许可相关节点 {sorted(remove_ids)}，"
        f"保留物业审查路径；剩余 {len(kept)} 节点。免办≠无审批。"
    )
    return kept
