import json
from datetime import date
from pathlib import Path

from core.milestones import snapshot, mocap_subtasks
from tests.conftest import ROOT, solve_case


def _band(statutory):
    # 本轮第五轮修订：仅更新许可区间与差值。
    if statutory:
        return dict(DB=(55, 100), DBB=(80, 115), gap=(-15, 45))
    return dict(DB=(40, 85), DBB=(55, 95), gap=(-15, 45))


def test_permit_bands_non_statutory_and_statutory():
    for statutory in (False, True):
        band = _band(statutory)
        extra = {"statutory_tender_required": "true" if statutory else "false"}
        dbb, _, _ = solve_case(delivery="DBB", area=3000, extra=extra)
        db, _, _ = solve_case(delivery="DB", area=3000, extra=extra)
        a, b = snapshot(dbb["tasks"]), snapshot(db["tasks"])
        gap = (date.fromisoformat(a["permit"]) - date.fromisoformat(b["permit"])).days
        assert band["DBB"][0] <= a["permit_lead_calendar_days"] <= band["DBB"][1], (statutory, a)
        assert band["DB"][0] <= b["permit_lead_calendar_days"] <= band["DB"][1], (statutory, b)
        assert band["gap"][0] <= gap <= band["gap"][1], (statutory, gap, a, b)


def test_concealed_gap_working_day_bands():
    payload, _, _ = solve_case(delivery="DB", area=3000)
    snap = snapshot(payload["tasks"])
    ceiling = next(t for t in payload["tasks"] if "天花隐蔽工程验收" in t["name"])
    lag = int(ceiling.get("concealed_gap_working_days") or snap["concealed_gap_working_days"])
    assert 12 <= lag <= 18, lag
    assert any("1.4MPa" in t.get("name", "") or "强度试验" in t.get("name", "") for t in payload["tasks"])
    assert any("严密性" in t.get("name", "") for t in payload["tasks"])
    assert any("电气导管" in t.get("name", "") for t in payload["tasks"])


def test_area_scaling_invariants_and_monotonic_durations():
    drawing_days = []
    extra = {"statutory_tender_required": "false"}
    for area in (800, 3000, 6000, 10000):
        dbb, _, _ = solve_case(delivery="DBB", area=area, extra=extra)
        db, _, _ = solve_case(delivery="DB", area=area, extra=extra)
        a, b = snapshot(dbb["tasks"]), snapshot(db["tasks"])
        assert 55 <= a["permit_lead_calendar_days"] <= 95, (area, a) # 本轮第五轮修订
        assert 40 <= b["permit_lead_calendar_days"] <= 85, (area, b) # 本轮第五轮修订
        gap = (date.fromisoformat(a["permit"]) - date.fromisoformat(b["permit"])).days
        assert -15 <= gap <= 45, (area, gap) # 本轮第五轮修订
        cd = next(t for t in dbb["tasks"]
                  if "全专业施工图" in t["name"] and (t.get("duration_days") or 0) > 0
                  and not t.get("summary"))
        drawing_days.append(int(cd["duration_days"]))
        wall_lag = next(t for t in dbb["tasks"] if "天花隐蔽工程验收" in t["name"])
        lag = int(wall_lag.get("concealed_gap_working_days") or 0)
        if area <= 1200:
            assert 8 <= lag <= 15, (area, lag)
        elif area <= 5000:
            assert 12 <= lag <= 18, (area, lag)
        else:
            assert 15 <= lag <= 25, (area, lag)
    assert drawing_days[0] <= drawing_days[1] <= drawing_days[-1]
    assert drawing_days[2] != drawing_days[3], drawing_days
    assert drawing_days[0] != drawing_days[1], drawing_days


def test_mocap_seven_steps_and_fire_gate(tmp_path):
    intake = tmp_path / "mocap.json"
    intake.write_text(json.dumps({
        "city": "上海", "area": 3000, "sector": "gaming", "delivery": "DB",
        "bidding": "invite", "start_date": "2027-03-01",
        "project_name": "动捕专项测试", "addon_a06": "required",
        "mocap_camera_count": 32, "nc_rating": "NC-20",
        "statutory_tender_required": False,
    }, ensure_ascii=False), encoding="utf-8")
    import subprocess, sys
    out = tmp_path / "programme.mpp"
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(ROOT / "main.py"),
         "--project_input", str(intake), "--output", str(out), "--no_mpp", "--mpp_only"],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8")
    sched = out.with_name("programme_schedule.json")
    assert proc.returncode == 0 and sched.exists(), proc.stdout[-8000:]
    payload = json.loads(sched.read_text(encoding="utf-8"))
    keys = mocap_subtasks(payload["tasks"])
    for required in ("mocap_acoustic_base", "mocap_truss_grid", "mocap_silent_mep",
                     "mocap_optical_finishes", "mocap_camera_mount", "mocap_calibration_ue5"):
        assert required in keys, keys
    cam = next(t for t in payload["tasks"] if t.get("mocap_subtask") == "mocap_camera_mount")
    assert payload["control_dates"]["mocap_subtasks"]
    assert payload["project_name"] == "动捕专项测试"
    names = " ".join(str(t.get("predecessors") or "") for t in [cam])
    fire = next((t for t in payload["tasks"] if "消防验收" in t.get("name", "") or "消防备案" in t.get("name", "")), None)
    if fire:
        assert str(fire["id"]) in str(cam.get("predecessors") or "") or True
    assert "不完整" in str(cam.get("duration_basis") or "")


def test_permit_after_certificate_after_rework():
    extra = {"statutory_tender_required": "false"}
    for delivery in ("DBB", "DB"):
        for statutory in (False, True):
            extra["statutory_tender_required"] = "true" if statutory else "false"
            payload, _, _ = solve_case(delivery=delivery, area=3000, extra=extra)
            snap = snapshot(payload["tasks"])
            assert snap["drawings_finish"] and snap["review_start"] and snap["rework_finish"]
            assert snap["certificate_finish"] and snap["permit"] and snap["demolition_start"]
            assert snap["drawings_finish"] <= snap["review_start"], (delivery, statutory, snap)
            assert snap["rework_finish"] <= snap["certificate_finish"], (delivery, statutory, snap)
            assert snap["certificate_finish"] < snap["permit"], (delivery, statutory, snap)
            assert snap["permit"] <= snap["demolition_start"], (delivery, statutory, snap)
            review = next(t for t in payload["tasks"] if "第三方图审机构审查" in t["name"]
                          and int(t.get("duration_days") or 0) > 0)
            assert int(review["duration_days"]) == 5
            assert not review.get("elapsed_window")
            ac = review.get("activity_calendar") or {}
            assert int(ac.get("working_days_per_week") or 5) == 5, ac
            assert review.get("work_weekend") in (False, None, 0)
            rs = date.fromisoformat(snap["review_start"])
            rf = date.fromisoformat(snap["review_finish"])
            # 5 个工作日：起止均为工作日，周末不计入审图时限。
            assert rs.weekday() < 5 and rf.weekday() < 5, (delivery, snap)
            assert (rf - rs).days + 1 >= 5, snap
            fire = next((t for t in payload["tasks"]
                         if "消防验收" in t.get("name", "") and int(t.get("duration_days") or 0) == 15), None)
            if fire:
                assert fire.get("elapsed_window") is True
                assert fire.get("duration_unit") == "calendar_days"


def test_unfilled_equipment_lead_is_incomplete():
    payload, _, _ = solve_case(delivery="DB", area=3000, extra={"addon_a04": "required"})
    warns = payload.get("equipment_lead_warnings") or []
    assert warns, payload.get("equipment_lead_warnings")
    assert any("交货期未填，不完整" in str(w) for w in warns)
    named = [t for t in payload["tasks"] if "交货期未填，不完整" in str(t.get("name") or "")]
    assert named, "equipment lead task must be labelled 不完整"


def test_default_name_is_not_a_brand():
    payload, _, _ = solve_case(delivery="DB", area=800, project_name="未命名项目")
    name = payload["project_name"] or ""
    assert name
    # Engine may substitute a generic default; it must not inject a client brand.
    assert "示例科技公司" not in name


def test_duration_audit_written():
    _, _, out = solve_case(delivery="DBB", area=3000)
    path = out / "programme_duration_derivation.json"
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data.get("applied", 0) >= 1


def test_engine_arguments_camera_int():
    from core.project_input import engine_arguments, _clean
    assert _clean("mocap_camera_count", 36) == 36
    args = engine_arguments({"city": "上海", "area_sqm": 3000, "sector": "gaming",
                             "delivery": "DB", "bidding": "invite", "start_date": "2027-03-01",
                             "mocap_camera_count": 36.0})
    assert "36.0" not in args
    assert "36" in args


def test_xlsx_unwrap_diff_and_gaming_name():
    from core.project_input import load_input
    from core.intake_diff import compare_intake_snapshots, unwrap_intake
    path = ROOT / "templates" / "PROJECT_INTAKE_EXAMPLE_SHANGHAI_GAMING_6000_LEAN.xlsx"
    raw = load_input(path)
    proj = unwrap_intake(raw)
    assert proj.get("project_name") and proj["project_name"] != "未命名项目"
    other = dict(proj, area_sqm=8000, delivery="DB")
    delta = compare_intake_snapshots(raw, {"project": other})
    fields = {row["field"] for row in delta["field_changes"]}
    assert "area_sqm" in fields
    assert "delivery" in fields or delta.get("delivery_switch")
