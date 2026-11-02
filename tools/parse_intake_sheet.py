"""Read client intake layouts; validation belongs to core.project_input."""
from __future__ import annotations
import argparse
import datetime as dt
import json
import sys
from pathlib import Path
import openpyxl
from openpyxl.utils.datetime import from_excel
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.project_input import normalize_project_inputs, ALIASES

def parse_intake_xlsx(file_path: Path) -> dict:
    """Read value columns by headers, never examples; honour Excel date epoch."""
    wb = openpyxl.load_workbook(file_path, data_only=True)
    formulas = openpyxl.load_workbook(file_path, data_only=False)
    data = {}
    try:
        ws = wb.active
        value_col = None
        note_col = None
        for row_number, row in enumerate(ws.iter_rows(values_only=True), 1):
            if not row or row[0] is None:
                continue
            texts = [str(v or "").strip() for v in row]
            if texts[0] in {"字段标识", "工作流分类", "管理维度", "字段键", "field"}:
                note_col = None
                for i, text in enumerate(texts):
                    if any(word in text for word in ("填值", "项目值", "确认状态")) or text.lower() == "value":
                        value_col = i
                    if text in {"来源/备注", "背景说明或既有凭据"}:
                        note_col = i
                continue
            key = texts[0]
            if key == "SEC" or " " in key or not key.isascii():
                continue
            col = value_col if value_col is not None else 3
            val = row[col] if col < len(row) else None
            formula = formulas[ws.title].cell(row_number, col + 1).value
            if val is None and isinstance(formula, str) and formula.startswith("="):
                raise ValueError(f"{key}: formula has no cached result; recalculate or supply a value")
            if val is None or val == "":
                continue
            canonical = ALIASES.get(key, key)
            if isinstance(val, (dt.date, dt.datetime)):
                val = val.strftime("%Y-%m-%d")
            elif canonical in {"start_date", "target_date", "authority_rule_date"}:
                if isinstance(val, (int, float)) or (isinstance(val, str) and val.isdigit()):
                    val = from_excel(float(val), epoch=wb.epoch).strftime("%Y-%m-%d")
                if isinstance(val, str) and val.strip() in {"待定", "待确认", "unknown"}:
                    continue
            note = row[note_col] if note_col is not None and note_col < len(row) else ""
            if key in data and data[key][0] != val:
                raise ValueError(f"conflicting intake rows for {key}")
            data[key] = (val, str(note or ""))
        return data
    finally:
        wb.close()
        formulas.close()

def build_facts_from_data(data: dict) -> dict:
    result = normalize_project_inputs([("user_intake_form", {k: v[0] for k, v in data.items()})])
    if result.blockers:
        raise ValueError("INTAKE_BLOCKED: " + json.dumps(result.blockers, ensure_ascii=False))
    return result.payload()

def main():
    p = argparse.ArgumentParser(description="Normalize client XLSX to shared project JSON")
    p.add_argument("--input", "-i", required=True)
    p.add_argument("--output", "-o", default="project_input.json")
    args = p.parse_args()
    payload = build_facts_from_data(parse_intake_xlsx(Path(args.input)))
    Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

if __name__ == "__main__":
    main()

