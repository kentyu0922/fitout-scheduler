import json

from core.intake_diff import compare_intake_snapshots, format_ascii_table


def test_compare_delivery_and_packages():
    base = {"delivery": "DBB", "bidding": "public", "area_sqm": 3000, "addon_a06": "excluded"}
    tgt = {"delivery": "DB", "bidding": "invite", "area_sqm": 3000, "addon_a06": "required"}
    delta = compare_intake_snapshots(base, tgt)
    assert delta["delivery_switch"]["base"] == "DBB"
    assert delta["procurement_switch"]["target"] == "invite"
    pack_blob = json.dumps(delta, ensure_ascii=False)
    assert "addon_a06" in pack_blob
    text = format_ascii_table(delta)
    assert "INTAKE DIFF" in text
    assert "PMP Scheduler Engine v4.0.0" in text
