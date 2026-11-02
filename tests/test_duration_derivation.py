from core.duration_derivation import scale_role, camera_count


def test_durations_increase_with_area():
    rows = []
    for area in (800, 3000, 10000):
        facts = dict(area_sqm=area, sector="finance", delivery="DBB", bidding="invite", city="上海")
        rows.append(scale_role("detailed_drawings", facts)["duration_days"])
    assert rows[0] <= rows[1] <= rows[2]
    assert rows[0] >= 8
    assert rows[1] >= 8


def test_statutory_tender_not_compressed():
    from core.duration_derivation import procurement_step_days
    small = procurement_step_days("prepare", dict(area_sqm=500, delivery="DBB", bidding="public"),
                                  design=False, public=True, formal=True)
    big = procurement_step_days("prepare", dict(area_sqm=20000, delivery="DBB", bidding="public"),
                                design=False, public=True, formal=True)
    assert small["duration_days"] >= 20
    assert big["duration_days"] >= 20
    assert small["statutory_locked"]


def test_camera_count_clamped():
    assert camera_count({}) == 24
    assert camera_count({"mocap_camera_count": 60}) == 48
    assert camera_count({"mocap_camera_count": 36}) == 36
