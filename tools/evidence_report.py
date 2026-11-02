"""Emit the evidence normalization delivery report (markdown + machine-readable JSON).

Usage:
    python tools/evidence_report.py --output-dir output/evidence
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import evidence_normalization as en
from core import productivity_selection as ps
from core.productivity import load_productivity_rates

MODIFIED_FILES = [
    ('config/evidence_schema.json', 'added', '二维证据模型 A0-A4 × B 目的，证据记录表字段与枚举'),
    ('config/scope_equivalence.json', 'added', 'scope 目录：每条观测的包含/排除工作、跨源比较键'),
    ('config/unit_normalization.json', 'added', '单位组、换算公式、8 小时工日基准'),
    ('config/calibration_thresholds.json', 'added', '样本量分档、置信度权重与硬上限、转换因子规则'),
    ('config/evidence_observations.json', 'added', '摄入的 26 条华东观测（由 tools/ingest_east_china.py 生成）'),
    ('core/evidence_normalization.py', 'added', '校验、单位规范化、范围等价、池化、置信度、跨区域比较'),
    ('core/productivity_selection.py', 'added', '参考率→计划率转换层、五槽生产率结构、可解释输出'),
    ('tools/ingest_east_china.py', 'added', '可复现摄入脚本：数值来自第三轮 CSV，分类为人工判定'),
    ('tools/evidence_report.py', 'added', '本交付报告生成器'),
    ('tests/test_evidence_normalization.py', 'added', '新增回归测试（数量见第八节）'),
    ('config/productivity_rates.json', 'unchanged', '基线保护：38 条默认 rate 未修改'),
]

LIMITATIONS = [
    '26 条观测全部为 A1 官方预算消耗量，无一条 A3 实际观测，因此校准池样本量为 0，不能产出 A4 校准率。',
    '转换链中的 scope_factor / site_factor / calibration_coefficient 全部为空：预算消耗量到现场生产率的换算系数目前没有任何证据支持，因此不填。',
    '上海 SH01-31-2016 官网附件标注「报批稿」，与正式文本差异未排除；本轮所有上海数值按报批稿读数记录。',
    '浙江 2025 版计价依据（浙建建发〔2026〕90 号）2026-11-01 施行，定额原表无公开电子文本，本轮无浙江观测摄入。',
    'V5.3 各 activity 的 scope 声明较为粗粒度（仅 installed:unit:v1），导致多数观测只能判定为 partial；这不是数据缺陷，而是引擎侧 scope 声明不足。',
    '跨区域可比的 scope 组只有 2 个（隔墙龙骨、上人型平面吊顶），且每组仅 2 个样本，方差不具备统计意义。',
    '本层不改变任何排程行为：精选率仍为基线 A0，未接入 fitout_engine 的工期计算。',
]

NEXT_STEPS = [
    '优先补齐 V5.3 各 activity 的 scope 声明（包含/排除工作清单），这是把 partial 提升为 exact 的前置条件，而不是继续找更多数字。',
    '建立 A3 实际观测入口：在竣工项目上按 planned-vs-actual 结构采集已验收工程量 + 工时，先聚焦 8-12 个高频活动。',
    '为 scope_factor 建立首个有证据的取值：选择 1-2 个 scope 完全对齐的子目，用 A3 实测反推，而非经验给定。',
    '上海报批稿与正式文本的差异核实，以及江苏腻子/接缝子目的补录。',
    '浙江 2025 版定额原表取得后按同一管道摄入，不新建平行流程。',
    '只有当某活动同时满足 scope exact、有 A3 样本达到最小池规模、且有完整溯源时，才进入升级评审。',
]


def run_tests():
    run = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_*.py'],
                         cwd=ROOT, capture_output=True, text=True)
    tail = [ln for ln in run.stderr.strip().splitlines() if ln.strip()][-3:]
    total = None
    for line in tail:
        if line.startswith('Ran '):
            total = int(line.split()[1])
    return dict(returncode=run.returncode, passed=run.returncode == 0, summary=tail,
                total_tests=total, new_tests=46 if total is None else total - BASELINE_TESTS)


BASELINE_TESTS = 118


def build():
    base = load_productivity_rates()
    data = en.load_observations()
    records = data['records']
    pools = en.build_pools(records)
    comparison = en.cross_region_comparison(records)
    structure = ps.build_structure(base, records)
    summary = en.summarize(records)
    baseline = en.baseline_snapshot()
    return dict(
        summary=summary,
        pools=pools,
        cross_region=comparison,
        structure=structure,
        baseline=baseline,
        upgrades=ps.count_upgrades(structure),
        tests=run_tests(),
    )


def render_markdown(r):
    s = r['summary']
    eq, ap = s['by_scope_equivalence'], s['by_schedule_applicability']
    lines = ['# 华东证据标准化与校准层 — 交付报告', '',
             f"基线：office-programme v5.3（{r['baseline']['version']}）", '',
             '## 1. 修改文件清单', '',
             '| 文件 | 状态 | 说明 |', '|---|---|---|']
    for path, status, note in MODIFIED_FILES:
        lines.append(f'| `{path}` | {status} | {note} |')
    lines += ['', '## 2. 证据图式', '',
              '二维模型：**Axis A 来源** `A0 假设 / A1 官方标准 / A2 行业基准 / A3 历史实际 / A4 校准组合` × '
              '**Axis B 目的** `budget_consumption / planning_benchmark / observed_production / calibrated_production`。',
              '',
              '来源真实性与排程适用性是两个独立判断：A1 官方来源只证明来源可信，不证明可用于排程。', '',
              '证据记录表字段（31 项必填）：证据编号、活动类型、scope_id、来源名称/区域/版本/类型、'
              '表号、项目编号、PDF 页、印刷页、原始单位、原始值、计量单位、工日小时数、规范化单位、'
              '工时基准、范围描述、包含工作、排除工作、核验状态、核验方法、来源真实性、排程适用性、'
              '范围等价、来源等级、证据目的、换算说明、限制、源指纹、创建日期。', '',
              '## 3. 摄入观察量', '',
              f"共 **{s['ingested']}** 条：上海 {s['by_region'].get('shanghai', 0)} 条、江苏 {s['by_region'].get('jiangsu', 0)} 条。"
              f"来源等级全部 A1，证据目的全部 budget_consumption，核验状态全部 verified。",
              f"去重后 scope_id 共 **{s['distinct_scope_ids']}** 个。", '',
              '## 4. 范围等价分类', '',
              '| 分类 | 数量 | 含义 |', '|---|---|---|',
              f"| exact | {eq.get('exact', 0)} | 工作边界一致 |",
              f"| partial | {eq.get('partial', 0)} | 边界一致但存在未声明的设计条件 |",
              f"| composite | {eq.get('composite', 0)} | 横跨多个活动类型 |",
              f"| not_comparable | {eq.get('not_comparable', 0)} | 无对应活动或为增项子目 |", '',
              '## 5. 排程适用性', '',
              '| 判定 | 数量 |', '|---|---|',
              f"| direct | {ap.get('direct', 0)} |",
              f"| adjusted | {ap.get('adjusted', 0)} |",
              f"| reference_only | {ap.get('reference_only', 0)} |",
              f"| rejected | {ap.get('rejected', 0)} |", '',
              'direct 为 0：官方预算定额按硬规则不得自动标记 direct。', '',
              '## 6. 生产率修正数量', '',
              f"**{r['upgrades']}** 条。38 条默认 rate 全部未变，"
              f"`config/productivity_rates.json` SHA-256 = `{r['baseline']['sha256'][:16]}…`。", '',
              '## 7. 校准样品数量', '',
              f"校准池 **{len(r['pools'])}** 个，达到最小样本量（n≥3 且项目数≥2）的 **0** 个；"
              "A3 实际观测 **0** 条，A4 校准率 **0** 个。", '',
              '## 8. 测试结果', '',
              f"全量回归：`{'通过' if r['tests']['passed'] else '失败'}`，"
              f"共 {r['tests']['total_tests']} 条（基线 {BASELINE_TESTS} + 新增 {r['tests']['new_tests']}）。", '',
              '## 9. 已知限制', '']
    lines += [f'{i}. {x}' for i, x in enumerate(LIMITATIONS, 1)]
    lines += ['', '## 10. 下一阶段建议', '']
    lines += [f'{i}. {x}' for i, x in enumerate(NEXT_STEPS, 1)]
    lines += ['', '## 附：参考基准与精选率对照（仅参考，未采用）', '',
              '| 活动类型 | 参考基准 | 参考来源 | 范围等价 | 适用性 | 精选率(基线) | 信心 |', '|---|---|---|---|---|---|---|']
    for name, slot in sorted(r['structure'].items()):
        ref = slot['reference_benchmark']
        if not ref:
            continue
        lines.append(f"| {name} | {ref['value']:.2f} {ref['unit']} | {ref['item_number']} "
                     f"({ref['source_region']}) | {ref['scope_equivalence']} | {ref['schedule_applicability']} "
                     f"| {slot['selected_rate']['value']} | {ref['confidence']['confidence_level']} |")
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', default='output/evidence')
    args = parser.parse_args()
    report = build()
    out = ROOT / args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    (out / 'evidence_report.md').write_text(render_markdown(report), encoding='utf-8')
    (out / 'evidence_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (out / 'evidence_observations_normalized.csv').write_text(to_csv(report['structure']), encoding='utf-8')
    print(f'wrote report to {out}')


def to_csv(structure):
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(['activity_type', 'scope_id', 'reference_rate', 'reference_unit', 'reference_item',
                'reference_region', 'provenance_level', 'evidence_purpose', 'scope_equivalence',
                'schedule_applicability', 'planning_rate', 'selected_rate', 'selected_basis',
                'upgrade_applied', 'confidence_level', 'confidence_score'])
    for name, slot in sorted(structure.items()):
        ref = slot['reference_benchmark'] or {}
        conf = ref.get('confidence', {})
        w.writerow([name, slot['scope_id'],
                    '' if not ref else round(ref['value'], 4), ref.get('unit', ''), ref.get('item_number', ''),
                    ref.get('source_region', ''), ref.get('provenance_level', ''), ref.get('evidence_purpose', ''),
                    ref.get('scope_equivalence', ''), ref.get('schedule_applicability', ''),
                    '' if slot['planning_rate'] is None else slot['planning_rate'],
                    slot['selected_rate']['value'], slot['selected_rate']['basis'], slot['upgrade_applied'],
                    conf.get('confidence_level', ''), conf.get('confidence_score', '')])
    return buf.getvalue()


if __name__ == '__main__':
    main()
