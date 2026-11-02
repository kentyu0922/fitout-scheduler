"""Shanghai fit-out addon sequences, statutory clocks, and incomplete-data labels.

Source tags: 〔法〕 national statute/code, 〔沪〕 Shanghai policy, 〔惯〕 practice.
Statutory durations are fixed; construction occupancy may follow area/kW/cabinets.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.equipment_leads import apply_equipment_leads
from core.module_graph import add_pred, dependency_order


def _find(tasks, *needles):
    for needle in needles:
        hit = next((t for t in tasks if needle in str(t.get("name") or "")), None)
        if hit:
            return hit
    return None


def _life(tasks, addon, step):
    return next((t for t in tasks if t.get("module_id") == addon and t.get("lifecycle_step") == step), None)


def _step(tasks, pack, n):
    return next((t for t in tasks if t.get("module_id") == pack and t.get("module_step") == n), None)


def _insert(tasks, after, **node):
    uid = node["activity_uid"]
    hit = next((t for t in tasks if t.get("activity_uid") == uid), None)
    if hit:
        hit.update({k: v for k, v in node.items() if k != "id"})
        return hit
    node["id"] = max(t["id"] for t in tasks) + 1
    node.setdefault("outline_level", 3)
    node.setdefault("predecessors", "")
    # Append: inserting in the middle of a heading/child pair would make this node a summary.
    tasks.append(node)
    return node


def _wd(task, days, basis, calendar="5D"):
    task.update(duration_days=days, duration=days, statutory_locked=True,
                duration_authority="statutory_window", duration_basis=basis, calendar=calendar)
    if "日历" in basis and "工作日" not in basis[:20]:
        task.update(calendar="7D", ignore_holidays=True, elapsed_window=True, duration_unit="calendar_days")
    return task


def construction_driver_days(facts, b0, b_min=2, b_max=None):
    from core.l2_master_duration import occupancy_days, load_l2_duration_policy
    area = float(facts.get("area_sqm") or facts.get("area") or 3000)
    # 〔惯〕施工无法定天数：面积为驱动；kW/柜位放大系数不得冒充定额。
    factor = 1.0
    if facts.get("power_kva"):
        factor *= max(1.0, (float(facts["power_kva"]) / 400.0) ** 0.2)
    if facts.get("fume_hood_count"):
        factor *= max(1.0, (float(facts["fume_hood_count"]) / 4.0) ** 0.2)
    occ = occupancy_days(
        {"b0_days": float(b0) * factor, "b_min": b_min, "scale_mode": "plate",
         "role": "SLICE", "package_id": "addon_works", "overlap_group": "addon_works",
         **({"b_max": b_max} if b_max else {})},
        area_sqm=area, policy=load_l2_duration_policy())
    return int(occ["duration_days"])


def apply_addon_practices(tasks: List[Dict[str, Any]], facts: Dict[str, Any]) -> Dict[str, Any]:
    from core.module_catalog import resolve_modules
    plan = resolve_modules(facts)
    selected = set(plan.get("selected") or [])
    audit: List[Dict[str, Any]] = []
    kickoff = _find(tasks, "Kick Off")
    permit = next((t for t in tasks if t.get("permit_role") == "submit"), None) or _find(tasks, "施工许可行政审批")
    review = _find(tasks, "第三方图审机构审查", "施工图送审")
    physical = _find(tasks, "物理竣工")
    linkage = _find(tasks, "全系统 T&C 联动调试")
    fire_apply = _find(tasks, "特殊建设工程消防验收申请", "消防验收备案申报", "消防验收申报")
    fire_result = _find(tasks, "取得消防验收合格意见", "取得消防验收备案凭证", "消防备案完成")
    owner = _find(tasks, "业主组织工程竣工验收")
    completion = _find(tasks, "工程竣工验收备案", "竣工验收备案")
    site = _find(tasks, "测量放线与标高复核")

    # 保留委托15日历天场景；全国统一日历天解释尚未核实，明确规划假设。
    if fire_apply and facts.get("fire_classification") == "special":
        _wd(fire_apply, 15, '不完整（依据待补）：消防验收15日历天为本比赛规划假设；未确认全国统一日历天解释，不作为法定办结承诺；整改复查另计。', "7D")
        audit.append(dict(addon="fire", item="fire_acceptance_15_calendar_days"))
    # 〔法〕第36条：其他工程竣工验收合格之日起五个工作日内由建设单位报备案（申报义务，非主管部门办结）。
    if completion:
        completion["input_notes"] = str(completion.get("input_notes") or "") + (
            "\n〔法〕58号令第36条：建设单位应于竣工验收合格后5个工作日内备案（申报时限）。"
            "〔沪〕沪建规范〔2025〕12号第10条：主管部门办结2个工作日。"
        )
        if owner:
            add_pred(completion, owner)

    def eia_days():
        kind = facts.get("eia_document_type") or "unknown"
        if kind == "report_book":
            return 60, "〔法〕《环境影响评价法》第22条：报告书60日（听证/评估不计入）。"
        if kind == "report_form":
            return 30, "〔法〕《环境影响评价法》第22条：报告表30日（听证/评估不计入）。"
        if kind == "registration":
            return 1, "〔法〕登记表备案（规划占位1工作日）；细分不完整。"
        return 30, "〔惯/不完整〕环评文件类型未提供，暂按报告表30日占位，补充后按报告书60/报告表30重算。"

    if selected & {"A12", "A19"}:
        days, basis = eia_days()
        eia = _insert(tasks, kickoff, name="环境影响评价审批", activity_uid="pmp.addon.eia",
                      activity_class="AUTHORITY_APPROVAL", phase="前期策划", module_id="A19" if "A19" in selected else "A12")
        _wd(eia, days, basis, "7D")
        if kickoff:
            add_pred(eia, kickoff)
        if permit:
            add_pred(permit, eia)
        audit.append(dict(addon="A19" if "A19" in selected else "A12", item="eia", days=days))

    if "A14" in selected:
        haz = _insert(tasks, kickoff, name="危化建设项目安全条件审查", activity_uid="pmp.addon.hazchem_safety",
                      activity_class="AUTHORITY_APPROVAL", phase="前期策划", module_id="A14")
        _wd(haz, 45, "〔法〕安监总局令第45号：危化建设项目安全条件审查45日内。上海承诺常见20工作日，主计划按法定45日。", "7D")
        if kickoff:
            add_pred(haz, kickoff)
        notice = _insert(tasks, haz, name="压力管道施工告知", activity_uid="pmp.addon.pressure_pipe_notice",
                         activity_class="AUTHORITY_APPROVAL", phase="开工许可", module_id="A14")
        _wd(notice, 2, "〔沪〕特种设备施工告知法定2个工作日（一网通办）。", "5D")
        works = _life(tasks, "A14", "works") or _step(tasks, "cda_piping", 1)
        if works:
            add_pred(works, notice)
            add_pred(works, haz)
        audit.append(dict(addon="A14", item="hazchem_45_and_pipe_notice_2"))

    if "A15" in selected and "A14" not in selected:
        notice = _insert(tasks, kickoff, name="压力管道/储气罐施工告知（达特种设备范围时）",
                         activity_uid="pmp.addon.pressure_pipe_notice_cda",
                         activity_class="AUTHORITY_APPROVAL", phase="开工许可", module_id="A15")
        _wd(notice, 2, "〔沪〕特种设备施工告知法定2个工作日；未达特种设备范围时本节点为条件占位。", "5D")
        works = _life(tasks, "A15", "works") or _step(tasks, "cda_piping", 2)
        if works:
            add_pred(works, notice)

    if "A03" in selected:
        kind = facts.get("power_supply_type") or "unknown"
        # 〔沪〕高压单电源合计22工作日、双电源32工作日；低压三零无外线≤5、有外线≤15。
        table = {"hv_single": (22, "〔沪〕高压单电源报装合计办理22个工作日。"),
                 "hv_dual": (32, "〔沪〕高压双电源报装合计办理32个工作日。"),
                 "lv": (10, "〔沪〕低压报装5–15个工作日；未区分有无外线，暂按10工作日占位（不完整）。")}
        days, basis = table.get(kind, (22, "〔沪/不完整〕供电类型未提供，暂按高压单电源22个工作日；补充后按22/32或低压5–15重算。"))
        consent = _life(tasks, "A03", "consent")
        if consent:
            _wd(consent, days, basis, "5D")
            consent["name"] = "供电方案与报装办理"
        audit.append(dict(addon="A03", item="power_application", days=days, type=kind))

    if "A01" in selected:
        tdd_end = _step(tasks, "tdd", 5) or _step(tasks, "tdd", 2)
        if tdd_end:
            if review:
                add_pred(review, tdd_end)
            a03 = _life(tasks, "A03", "capacity")
            if a03:
                add_pred(a03, tdd_end)
        audit.append(dict(addon="A01", item="tdd_before_review_and_utility"))

    if "A02" in selected:
        inspect = _life(tasks, "A02", "inspection")
        if inspect and physical:
            add_pred(physical, inspect)
        audit.append(dict(addon="A02", item="structure_before_finish"))

    if "A04" in selected:
        # 〔法〕GB 50174-2017 / GB 50462-2024：围护→UPS→精密空调→气灭→动环→综合测试。
        dc1, dc2, dc3, dc4, dc7 = (_step(tasks, "datacenter", n) for n in (1, 2, 3, 4, 7))
        if dc1 and dc4:
            add_pred(dc4, dc1)
        if dc2 and dc1:
            add_pred(dc2, dc1)
        if dc3 and dc1:
            add_pred(dc3, dc1)
        if dc7:
            for n in (dc2, dc3, dc4):
                if n:
                    add_pred(dc7, n)
            if linkage:
                add_pred(linkage, dc7)
        for t in (dc1, dc2, dc3, dc4, dc7):
            if t:
                t["duration_days"] = t["duration"] = construction_driver_days(
                    facts, max(2, int(t.get("duration_days") or 4)), b_min=2, b_max=18)
                t["duration_basis"] = "〔惯〕GB 50174-2017按A/B/C级+IT负荷kW/主机房面积驱动施工占位，无法定时长；〔法〕施工验收GB 50462-2024。"
        audit.append(dict(addon="A04", item="dc_sequence_gb50174"))

    if "A05" in selected:
        cut = _step(tasks, "it_cutover", 2) or _step(tasks, "it_cutover", 3)
        dc_ready = _step(tasks, "datacenter", 8)
        for pred in (dc_ready, fire_result, fire_apply):
            if cut and pred:
                add_pred(cut, pred)
        audit.append(dict(addon="A05", item="cutover_after_dc_and_fire"))

    if "A06" in selected:
        for t in tasks:
            if t.get("module_id") == "mocap" or str(t.get("mocap_subtask") or "").startswith("mocap"):
                t["duration_basis"] = "〔不完整〕动捕无对应国家工程建设强制标准、无法定审批时限、无官方交货周数；仅排工序顺序，工期待项目确认。"
                t["duration_authority"] = "incomplete_pending_project"
                t["calibration_status"] = "incomplete"
        # 光学/桁架在吊顶封闭前；标定在精装完成、振动源停工后、竣工验收前。
        cam = next((t for t in tasks if t.get("mocap_subtask") == "mocap_camera_mount"), None)
        truss = next((t for t in tasks if t.get("mocap_subtask") == "mocap_truss_grid"), None)
        ceiling = _find(tasks, "天花隐蔽工程验收")
        cal = next((t for t in tasks if t.get("mocap_subtask") == "mocap_calibration_ue5"), None)
        if truss and ceiling:
            add_pred(ceiling, truss)
        if cam and ceiling:
            # 相机上架在消防后，不闸天花封闭。
            pass
        # 标定在相机上架之后；相机在消防验收之后，故不能再要求竣工验收等待标定（会环路）。
        audit.append(dict(addon="A06", item="sequence_only_incomplete"))

    if "A07" in selected:
        for t in tasks:
            if t.get("mocap_subtask") == "mocap_acoustic_base" or (t.get("module_id") == "mocap" and t.get("module_step") == 1):
                t["duration_basis"] = (str(t.get("duration_basis") or "") +
                                       " 〔法〕现场隔声测量GB/T 19889系列；浮筑面层验收GB 50209；法定养护天数不完整。")
        audit.append(dict(addon="A07", item="acoustic_gb_t_19889"))

    if "A08" in selected:
        works = _step(tasks, "dev_zone", 0)
        if works:
            works["duration_days"] = works["duration"] = construction_driver_days(facts, 6, 3, 16)
            works["duration_basis"] = "〔惯〕高密研发区按kW/机柜与面积驱动施工占位，无专项许可。"
        audit.append(dict(addon="A08", item="high_density_kw_driver"))

    if "A09" in selected:
        audit.append(dict(addon="A09", item="trading_after_a04_a05"))

    if "A10" in selected:
        # 忽略调研稿乱码「=======」。物防与技防同步；GB 50348-2018。
        for t in tasks:
            if t.get("module_id") == "vault_security" and "A11" not in selected:
                if "金库" in t.get("name", ""):
                    t["name"] = t["name"].replace("金库级防撬门", "防盗门、通道闸")
                t["duration_basis"] = "〔法〕GB 50348-2018；程序GA/T 75；验收规则〔惯〕GA 308。一般办公技防备案法定时限不完整。"
        audit.append(dict(addon="A10", item="physical_security_gb50348"))

    if "A11" in selected:
        for t in tasks:
            if t.get("module_id") == "vault_security":
                t["duration_basis"] = "〔法〕GA 38-2021 + GB 37481金库门；公安技防审批法定时限不完整。"
        audit.append(dict(addon="A11", item="vault_ga38"))

    if "A12" in selected:
        works = _step(tasks, "lab_hvac", 1)
        if works:
            works["duration_days"] = works["duration"] = construction_driver_days(facts, 8, 4, 20)
            works["duration_basis"] = "〔法〕GB 50346-2011；柜位数/排风量为驱动，无法定时长。排风柜现场性能〔惯〕EN 14175/ASHRAE 110。"
        if linkage and _step(tasks, "lab_hvac", 3):
            add_pred(linkage, _step(tasks, "lab_hvac", 3))
        audit.append(dict(addon="A12", item="lab_hvac_eia_and_hoods"))

    if "A13" in selected:
        for n in (1, 2, 3):
            t = _step(tasks, "cleanroom_iso", n)
            if t:
                t["duration_days"] = t["duration"] = construction_driver_days(facts, 6, 3, 18)
                t["duration_basis"] = "〔法〕GB 50073-2013设计、GB 50591-2010施工验收；洁净度等级×面积驱动，无法定时长。"
        audit.append(dict(addon="A13", item="cleanroom_gb50591"))

    if "A16" in selected:
        works = _life(tasks, "A16", "works")
        if works:
            works["duration_days"] = works["duration"] = construction_driver_days(facts, 8, 4, 18)
            works["duration_basis"] = "〔惯〕工艺水施工按面积驱动；排水许可/环评分项不完整。"
        audit.append(dict(addon="A16", item="upw"))

    if "A17" in selected:
        for t in tasks:
            name = str(t.get("name") or "")
            if "VC-C" in name or "VC－C" in name:
                t["name"] = name.replace("VC-C", "微振（IEST VC，行业惯例）").replace("Anti-vibration & VC-C", "隔振（GB 50463）")
            if t.get("module_id") == "anti_vibration":
                t["duration_basis"] = "〔法〕GB 50463-2019为隔振设计规范，不是VC标准。VC曲线为〔惯〕IEST。无行政许可。"
        audit.append(dict(addon="A17", item="gb50463_not_vc"))

    if "A18" in selected:
        for t in tasks:
            if t.get("module_id") == "esd_grounding":
                t["duration_basis"] = "〔法〕办公电子工程GB 50611-2010设计、GB 50944-2013施工验收。不引用GB/T 32304（航天电子）。ANSI/ESD S20.20为〔惯〕。"
        audit.append(dict(addon="A18", item="esd_gb50611_gb50944"))

    if "A20" in selected:
        iq, oq, pq = _life(tasks, "A20", "iq"), _life(tasks, "A20", "oq"), _life(tasks, "A20", "pq")
        iso = _step(tasks, "cleanroom_iso", 3)
        if iq and physical:
            add_pred(iq, physical)
        if oq and iso:
            add_pred(oq, iso)
        for t in (iq, oq, pq):
            if t:
                t["duration_basis"] = "〔法〕医药厂房GB 50457-2019、GB/T 51466-2025；IQ/OQ/PQ为〔惯〕/持证要求，药监许可不在装修发证链。"
        audit.append(dict(addon="A20", item="iq_after_install"))

    if "A21" in selected:
        fm = _life(tasks, "A21", "assets") or _life(tasks, "A21", "release")
        if fm and completion:
            add_pred(fm, completion)
        audit.append(dict(addon="A21", item="fm_after_filing"))

    if "A22" in selected:
        place = _life(tasks, "A22", "placement")
        if place:
            place["duration_days"] = place["duration"] = construction_driver_days(facts, 4, 2, 12)
            place["duration_basis"] = "〔惯〕专业设备就位按面积/荷载驱动；交货周数按合同，检索无统一值。"
        test = _life(tasks, "A22", "performance")
        if test and fire_result:
            add_pred(test, fire_result)
        audit.append(dict(addon="A22", item="equipment_after_structure"))

    leads = apply_equipment_leads(tasks, facts, selected)
    dependency_order(tasks)
    if tasks:
        tasks[0]["addon_practices"] = dict(selected=sorted(selected), audit=audit, leads=leads)
    return dict(selected=sorted(selected), audit=audit, leads=leads)
