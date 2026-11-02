"""What the PM reads first: a short brief after a run, or one plain question when stopped.

The agent pastes these lines to the user as they are, before listing files, so the
answer does not depend on how the agent happens to summarise the JSON.
"""
from __future__ import annotations

import datetime
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

QUESTIONS = {
    'city': '项目在哪个城市？',
    'area_sqm': '建筑面积大约多少平方米？',
    'sector': '属于哪个行业：通用办公、游戏、金融、生物医药还是半导体？',
    'delivery': '设计和施工是一家做（设计施工一体），还是分开招（设计施工分开）？',
    'bidding': '邀请招标还是公开招标？',
    'start_date_or_target_date': '计划什么时候启动？或者客户希望什么时候搬进去？',
}


# Only these decide a schedule; guide material and docs do not change the revision.
CODE_PATHS = ['core', 'experts', 'exporters', 'config', 'templates', 'main.py', 'tools/run_contract.py',
              'tools/pm_brief.py', 'tools/delivery_note.py', 'tools/check_env.py', 'SKILL.md']


def revision() -> str:
    """Pack revision (written by build_pack) or the last commit that touched the schedule code."""
    stamp = ROOT / 'REVISION'
    if stamp.exists():
        return stamp.read_text(encoding='utf-8').strip()
    try:
        commit = subprocess.run(['git', 'log', '-1', '--format=%h', '--', *CODE_PATHS], cwd=ROOT, capture_output=True,
                                text=True, timeout=5).stdout.strip()
        dirty = subprocess.run(['git', 'status', '--porcelain', '--', *CODE_PATHS], cwd=ROOT, capture_output=True,
                               text=True, timeout=5).stdout.strip()
        return (commit + ('-改动中' if dirty else '')) if commit else '未记录'
    except (OSError, subprocess.SubprocessError):
        return '未记录'


def stamp_line() -> str:
    version = next((line.split(':', 1)[1].strip().strip('"') for line in (ROOT / 'SKILL.md').read_text(encoding='utf-8').splitlines()
                    if line.strip().startswith('version:')), '')
    return f'版本 {version} · 修订 {revision()} · 运行于 {datetime.datetime.now():%Y-%m-%d %H:%M}'


def label(key: str) -> str:
    from core.programme_delivery import plain_field
    return plain_field(key).split('（')[0].split('：')[0] if not key.startswith('addon_') else plain_field(key)


def value_text(key: str, value) -> str:
    from core.programme_delivery import plain_value
    text = plain_value(key, value)
    return f'{text}㎡' if key == 'area_sqm' else str(text)


def source_text(source: str) -> str:
    if source == 'explicit_cli_or_prompt_facts':
        return '对话中'
    if source.startswith('上次运行'):
        return source
    name = Path(source).name
    return f'需求表（{name}）' if name.lower().endswith(('.xlsx', '.xlsm', '.xls')) else f'资料（{name}）'


def blocked_message(blockers: list) -> list[str]:
    """One plain question per blocker; conflicts show both sources."""
    lines = []
    for b in blockers:
        field = b.get('field', '')
        if b.get('values') and b.get('sources'):
            (a, c), (sa, sc) = b['values'][:2], b['sources'][:2]
            lines.append(f'· {label(field)}：{source_text(sa)}是 {value_text(field, a)}，'
                         f'{source_text(sc)}是 {value_text(field, c)}，以哪个为准？')
        elif field in QUESTIONS:
            lines.append('· ' + QUESTIONS[field])
        elif b.get('question'):
            lines.append('· ' + b['question'])
        elif any('一' <= ch <= '鿿' for ch in str(b.get('reason', ''))):
            lines.append('· ' + str(b['reason']))
        else:
            lines.append(f'· {label(field)}：请确认。')
    return ['【请一起确认以下排程条件】'] + lines + ['其余默认条件会列在《本次排程条件》中，确认后即可排程。'] if lines else []


# Add-ons worth offering by industry (only offered, never added). Codes as in core/module_catalog.
ADDON_HINTS = {
    'biopharma': ('A12', 'A13', 'A20', 'A04'),
    'semiconductor': ('A13', 'A14', 'A15', 'A16', 'A18'),
    'finance': ('A09', 'A10', 'A11', 'A04'),
    'gaming': ('A06', 'A07', 'A08', 'A04'),
    'office': ('A04', 'A05', 'A03'),
}


def addon_offer(project: dict) -> list[str]:
    """Add-ons this industry usually needs, skipping any the PM already mentioned (offered, never added)."""
    from core.module_catalog import CATALOG
    sector = project.get('sector') or 'office'
    named = {k[6:].upper() for k in project if k.startswith('addon_')}
    codes = [c for c in ADDON_HINTS.get(sector, ADDON_HINTS['office']) if c not in named][:4]
    return [CATALOG[c]['label'] for c in codes]


def choices(project: dict, route: dict, decision: dict) -> list[str]:
    """What the PM decides next, as numbered questions answered by letter."""
    from core.industry_base import PROFILES
    asks = []
    if decision.get('decision_required'):
        asks.append(f'目标晚 {decision.get("variance_calendar_days")} 天（工期硬性约束），怎么处理：A 保留推演计划　B 评估压缩方案（另算一份，原计划保留）')
    names = addon_offer(project)
    if names:
        label = PROFILES.get(project.get('sector') or 'office', PROFILES['office'])['label']
        options = '　'.join(f'{chr(65 + i)} {n}' for i, n in enumerate(names))
        asks.append(f'{label}常加的专项（关键功能空间），这次要不要加（没有自动加入）：{options}（可多选，或回“都不要”）')
    if route.get('route') != 'mpp':
        if route.get('mpp_ready'):
            asks.append('要可编辑的 MPP 吗（交付档案格式，约 1–2 分钟，日期与 PDF 相同）：A 要　B 先不用')
        elif route.get('failed'):
            asks.append('MPP 这次没生成：A 我已手动打开过一次 Project，重试　B 先不用')
    if not asks:
        return []
    return (['下一步（可一起回复，例如“1A 2B”）：'] + [f'{i}. {a}' for i, a in enumerate(asks, 1)]
            + [say_line(project)])


def critical_line(tasks: list) -> str | None:
    """The driving chain in a few steps: the longest piece of work in each phase, in time order."""
    leaves = [t for t in tasks if not t.get('summary') and (t.get('duration_days') or 0) > 0]
    chain = [t for t in leaves if t.get('on_driving_chain')] or [t for t in leaves if t.get('critical')]
    if not chain:
        return None
    longest = {}
    for t in chain:
        phase = t.get('master_phase') or t.get('id')
        if (t.get('duration_days') or 0) > (longest.get(phase, {}).get('duration_days') or 0):
            longest[phase] = t
    picked = sorted(longest.values(), key=lambda t: -(t.get('duration_days') or 0))[:5]
    picked.sort(key=lambda t: (t.get('start', ''), t.get('finish', '')))
    names = [_driving_chain_name(t) for t in picked]
    return '主要控制工作：' + ' → '.join(names) + '。调整后需重算，确认对最终日期的影响。'


def _driving_chain_name(t: dict) -> str:
    """Display name for the '卡在哪' line.

    ``client_task_name`` drops a "采购包名：" prefix that the section heading already
    shows in the flattened schedule, but in this one-line summary two same-core tasks
    from different procurement packages (e.g. 设计服务 vs 施工总包) would otherwise
    collapse to the same name. Restore the package prefix so they stay distinct."""
    client = t.get('client_task_name') or t.get('name') or ''
    raw = t.get('name') or client
    if '：' in raw:
        prefix, core = raw.split('：', 1)
        if prefix and client == core:
            client = prefix + '：' + client
    return ' '.join(client.split('（')[0].split())


def say_line(project: dict) -> str:
    """Two or three things the PM can say instead, fitted to this project."""
    says = []
    if project.get('delivery') == 'DBB':
        says.append('其他不变，改成设计施工一体')
    elif project.get('delivery') == 'DB':
        says.append('其他不变，改成设计施工分开')
    if project.get('bidding') == 'invite':
        says.append('改成公开招标')
    says.append('晚一个月启动')
    return '也可以直接说，例如' + '、'.join(f'“{s}”' for s in says[:3]) + '。'


def supply_notes(project: dict) -> list[str]:
    """Specialist scope reused or supplied by others: not re-procured, but its dates are not known."""
    from core.module_catalog import CATALOG
    notes = []
    for key, value in project.items():
        if key.startswith('addon_') and value in ('existing_claimed', 'provided_by_others'):
            name = CATALOG.get(key[6:].upper(), {}).get('label', key)
            how = '沿用现有' if value == 'existing_claimed' else '由他方提供'
            notes.append(f'· {name}（{how}）：计划不重复排采购和施工，只排复用核验；到货、可用及接入日期未提供，'
                         '计划假设按时可用，晚了会影响安装、调试和搬迁，请向提供方确认。')
    return notes


def change_line(changes: dict) -> str:
    parts = []
    for k, (a, b) in changes['changed'].items():
        if k.startswith('addon_') and a == '未提供' and b == 'required':
            parts.append('加上' + label(k).replace('专项：', ''))
        elif a == '未提供':
            parts.append(f'{label(k)}定为{value_text(k, b)}')
        else:
            parts.append(f'{label(k)}由{value_text(k, a)}改为{value_text(k, b)}')
    if not parts:
        return f'本次条件没有改动，{changes["kept"]} 项全部沿用{changes["base"]}。'
    return f'本次改了：{"；".join(parts)}。其余 {changes["kept"]} 项沿用{changes["base"]}。'


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def delivered_brief(output: Path, payload: dict, route: dict, changes: dict | None = None) -> list[str]:
    """Five or six lines: result, target, approvals, what to confirm, what changed, files."""
    from core.authority_reference import _key_dates
    schedule = _load(output.with_name(output.stem + '_schedule.json'))
    project = payload.get('project', {})
    tasks = schedule.get('tasks', [])
    key = _key_dates(tasks) if tasks else {}
    start, finish = schedule.get('start_date') or project.get('start_date'), schedule.get('finish_date')
    days = (datetime.date.fromisoformat(finish) - datetime.date.fromisoformat(start)).days + 1 if start and finish else None
    attrs = []
    if project.get("area_sqm") or project.get("area"):
        attrs.append(f'GFA {project.get("area_sqm") or project.get("area")}㎡')
    if project.get("cleanroom_class"):
        attrs.append("洁净 " + str(project["cleanroom_class"]))
    if project.get("nc_rating"):
        attrs.append("NC " + str(project["nc_rating"]))
    if project.get("esd_level"):
        attrs.append("ESD " + str(project["esd_level"]))
    if project.get("power_kva") not in (None, ""):
        attrs.append(str(project["power_kva"]) + " kVA")
    title = project.get("project_name", "本项目")
    lines = [f'【简要结论】{title}（{stamp_line()}）' + (f'｜{" · ".join(attrs)}' if attrs else '')]
    deficit = schedule.get("schedule_deficit")
    if deficit:
        lines.append(str(deficit.get("banner") or f'[SCHEDULE DEFICIT / 负时差警告: -{deficit.get("days")} Days]'))
    result = [f'启动 {start}']
    if key.get('permit'):
        result.append(f'取得施工许可 {key["permit"]}')
    if key.get('move_in'):
        result.append(f'搬迁开始 {key["move_in"]}')
    if finish:
        result.append(f'全部完成 {finish}（共 {days} 个日历天）')
    lines.append('结果：' + '；'.join(result) + '。')
    physical = next((t.get('finish') for t in tasks if '物理竣工' in t.get('name', '')), None)
    if start and finish and key.get('permit') and physical:
        span = lambda a, b: (datetime.date.fromisoformat(b) - datetime.date.fromisoformat(a)).days
        lines.append(f'时间花在：启动到拿证 {span(start, key["permit"]) + 1} 天（设计、招采与报审），'
                     f'施工 {span(key["permit"], physical)} 天，收尾 {span(physical, finish)} 天（检测、验收、家具与搬迁）。')

    decision = schedule.get('target_decision') or {}
    target = project.get('target_date')
    if target and finish:
        # A PM's 搬迁 / 搬家 / move-in target is the move itself: compare with the move-in date.
        moved = key.get('move_in') or finish
        gap = (datetime.date.fromisoformat(moved) - datetime.date.fromisoformat(target)).days
        line = f'目标：{target} 前搬迁 → 按正常工序 {moved} 开始搬迁，' + (f'晚 {gap} 天。' if gap > 0 else f'余 {-gap} 天。')
        if gap > 0:
            line += '没有为了达标缩短工期。'
        lines.append(line)

    driver = critical_line(tasks)
    if driver:
        lines.append(driver)

    draft = bool(project.get('authority_draft'))
    asks = [q for w in payload.get('warnings', []) if w.get('field') == 'authority_draft' for q in w.get('questions', [])
            if q.get('field') != 'city']
    # Outside the verified Shanghai route the plan uses the national approval time limits: a normal plan,
    # not a draft, and nothing for the PM to go and check first.
    lines.append('报审路径：诊断草案；项目分类及属地要求尚未核实，参考时限用于推演。' if draft else '报审路径：按本次提供的上海项目分类及适用规则排。')
    from tools.condition_sheet import summary
    lines.append(summary(payload))

    # Defaults (full strip-out, 7-day calendar, furniture, fire release) are not questions for the PM.
    supplied = supply_notes(project)
    if supplied:
        lines.append(f'需要你核对：{len(supplied)} 项由他方提供或沿用现有的专项，到货和可用日期待确认。详见交付说明最后一节。')
    if changes:
        lines.append(change_line(changes))
    available=[]
    if route.get('mpp_status')=='DELIVERED' and output.is_file():available.append('编辑用 '+output.name)
    for suffix,purpose in [('_交付说明.docx','说明'),('_交付说明.txt','说明'),('.pdf','审阅'),
                           ('_Milestones.pptx','PM阶段视图'),('_BD_KeyMilestones.pptx','BD关键节点')]:
        path=output.with_name(output.stem+suffix)
        if path.is_file():available.append(purpose+' '+path.name)
    lines.append(f'文件（{output.parent}）：'+'；'.join(available)+'。')
    lines.append('日期经 PM 签认后，再作为对客户的承诺。')
    lines.extend(choices(project, route, decision))
    return lines
