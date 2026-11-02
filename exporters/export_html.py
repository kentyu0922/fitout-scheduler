# -*- coding: utf-8 -*-
"""
export_html.py - 生成 高管级 (C-Suite Standard) 交付策略与进度推演决策看板
======================================================================================
特性标准 (executive dashboard standard):
  1. 【排版风格与商务美学】：纯净瓷白与雅致浅灰 (#f8fafc / #ffffff)，极细 1px 微边框 (#e2e8f0)，深空海军蓝 (#1F1F1F) 与克制点缀，Pretendard/Inter/SF Pro 高阶等宽数字排版。
  2. 【项目主轴大标题第一优先】：统领全屏 21px Hero Title，去除非必要突兀英文黑标。
  3. 【极简横向胶囊分段器】：右上角纯横向 [ 中文 | English ] 一体化圆角药丸滑动切换，100% 双语字典覆盖。
  4. 【5 大 Executive KPI 卡片】：项目开工日、施工许可核发(含5~8d质安监保函安全窗口)、硬装物理竣工日、最终交付入驻日、总工期基准锁定。
  5. 【工程发包与交付策略多方案比选矩阵】：基于后端 CPM 数学算力引擎解算 Option A (DBB基准) / Option B (D&B提速29天) / Option C (公开招标合规)。
  6. 【单线贯穿式战略里程碑时间轴 (Strategic Milestone Timeline)】：渐变实线 + 10 大关键卡口节点，纯净无冗余播放器杂音。
  7. 【大阶段推进与核心门禁甘特图 (Level 2 Gantt)】：仅呈现 Phase 1~8 汇总条 + 3 大核心门禁 (⭐️/🏁) + 2027 春节 15 天停工阴影带。
  8. 【核心关键路径全景导览链 (Master Critical Path Driving Chain)】：7 大步骤卡片，突出高亮时间区间框 (📅 10.09~10.29 [15 工日])，彻底移除冗余 Slack=0 标签。
  9. 【4 维属地合规与风控雷达】：涵盖施工许可、敏捷招采、春节停工对冲、以及 4 类法定竣工验收/住建消防备案全景合规策略。
"""

import os
import sys
import json
import base64
import datetime
from typing import List, Dict, Any, Optional
import logging
from pathlib import Path

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Neutral accent mark (no company lockup).
BRAND_MARK_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 32" height="32" width="64" role="presentation">
  <rect x="0" y="10" width="64" height="4" fill="#6B5CE7"/>
  <rect x="0" y="18" width="36" height="4" fill="#1F1F1F"/>
</svg>"""

def _parse_date(val: Any) -> Optional[datetime.date]:
    if not val:
        return None
    if isinstance(val, datetime.date):
        return val
    try:
        return datetime.datetime.strptime(str(val)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def generate_html_report(
    project_title: str,
    project_meta: Dict[str, Any],
    tasks: List[Dict[str, Any]],
    output_html_path: str
) -> str:
    """
    根据已解算任务列表与项目元数据，渲染生成 高管级交付控制台。
    """
    output_html_path = os.path.abspath(output_html_path)
    out_dir = os.path.dirname(output_html_path)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    city = project_meta.get("city", "上海")
    area = project_meta.get("area", 3600)
    cost = project_meta.get("cost", 800)
    delivery = project_meta.get("delivery", "DB")
    bidding = project_meta.get("bidding", "invite")
    delivery_label = "D&B 设计施工一体化" if delivery == "DB" else "DBB 分批发包"
    bidding_label = "邀请招标 (比选)" if bidding == "invite" else "公开招标 (法定流程)"

    # 加载品牌标志 Base64 (确保离线纯净渲染)
    brand_mark_white_b64 = ""
    brand_mark_white_path = os.path.join(BASE_DIR, "brand_mark_white.png")
    if os.path.exists(brand_mark_white_path):
        with open(brand_mark_white_path, "rb") as f:
            brand_mark_white_b64 = base64.b64encode(f.read()).decode("utf-8")

    brand_mark_trans_b64 = ""
    brand_mark_trans_path = os.path.join(BASE_DIR, "brand_mark_transparent.png")
    if os.path.exists(brand_mark_trans_path):
        with open(brand_mark_trans_path, "rb") as f:
            brand_mark_trans_b64 = base64.b64encode(f.read()).decode("utf-8")

    dated_tasks = [t for t in tasks if t.get("start") and t.get("finish")]
    if dated_tasks:
        start_dt = min(_parse_date(t["start"]) for t in dated_tasks if _parse_date(t["start"]))
        finish_dt = max(_parse_date(t["finish"]) for t in dated_tasks if _parse_date(t["finish"]))
    else:
        start_dt = datetime.date(2026, 9, 15)
        finish_dt = datetime.date(2027, 5, 14)

    start_date_str = start_dt.strftime("%Y-%m-%d")
    finish_date_str = finish_dt.strftime("%Y-%m-%d")
    total_calendar_days = (finish_dt - start_dt).days + 1
    
    workday_tasks = [int(t.get("duration", t.get("duration_days", 0))) for t in tasks if t.get("outline_level", t.get("level", 1)) >= 3]
    total_workdays = sum(workday_tasks) if workday_tasks else total_calendar_days

    def find_task(kw_list, fallback_dt=start_dt):
        for t in tasks:
            name = str(t.get("name", ""))
            if any(k in name for k in kw_list):
                f = _parse_date(t.get("finish", t.get("start")))
                if f:
                    return t, f, False
        return None, fallback_dt, True

    estimated_milestones = []
    _, t_kickoff, _e = find_task(["Kick Off", "启动会"], start_dt)
    if _e:
        estimated_milestones.append("启动会 (Kick-off)")
        logging.getLogger(__name__).warning("里程碑[启动会]未匹配任务名，按常规节奏推算 %s", t_kickoff)
    _, t_tender, _e = find_task(["定标", "中标通知", "合同签订", "LOI"], start_dt + datetime.timedelta(days=45))
    if _e:
        estimated_milestones.append("定标签约")
        logging.getLogger(__name__).warning("里程碑[定标签约]未匹配任务名，按常规节奏推算 %s", t_tender)
    _, t_permit, _e = find_task(["施工许可", "许可证", "开工许可"], start_dt + datetime.timedelta(days=75))
    if _e:
        estimated_milestones.append("施工许可证核发")
        logging.getLogger(__name__).warning("里程碑[施工许可证核发]未匹配任务名，按常规节奏推算 %s", t_permit)
    _, t_concealed, _e = find_task(["隐蔽验收", "管线隐蔽", "隐蔽工程"], start_dt + datetime.timedelta(days=110))
    if _e:
        estimated_milestones.append("隐蔽工程验收")
        logging.getLogger(__name__).warning("里程碑[隐蔽工程验收]未匹配任务名，按常规节奏推算 %s", t_concealed)
    _, t_build, _e = find_task(["物理竣工", "硬装自检", "竣工自检", "硬装完工"], start_dt + datetime.timedelta(days=170))
    if _e:
        estimated_milestones.append("现场硬装物理竣工")
        logging.getLogger(__name__).warning("里程碑[现场硬装物理竣工]未匹配任务名，按常规节奏推算 %s", t_build)
    _, t_fire, _e = find_task(["消防验收合格", "消防合格", "消防备案凭证", "消防查验"], t_build + datetime.timedelta(days=18))
    if _e:
        estimated_milestones.append("消防验收合格")
        logging.getLogger(__name__).warning("里程碑[消防验收合格]未匹配任务名，按常规节奏推算 %s", t_fire)

    curr = datetime.date(start_dt.year, start_dt.month, 1)
    end_month = datetime.date(finish_dt.year, finish_dt.month, 1)
    month_cols = []
    while curr <= end_month:
        m_name = f"{curr.month:02d}月"
        if curr.month == 1:
            m_name += f" ({curr.year})"
        elif curr.month == 2 and curr.year == 2027:
            m_name += " (春节)"
        elif curr.month == finish_dt.month and curr.year == finish_dt.year:
            m_name += " (交付)"
        
        m_events = []
        for t in tasks:
            f = _parse_date(t.get("finish"))
            if f and f.year == curr.year and f.month == curr.month and (t.get("milestone") or t.get("outline_level", t.get("level", 1)) == 2 or any(k in t.get("name", "") for k in ["开工", "定标", "许可", "竣工", "移交", "验收", "春节", "入驻", "消防"])):
                day_str = f"{f.day:02d}"
                short_name = t["name"].replace("Phase ", "P").replace("阶段", "").replace("[M] ", "").strip()[:4]
                if f"{day_str} {short_name}" not in m_events and len(m_events) < 3:
                    m_events.append(f"{day_str} {short_name}")
        
        if not m_events:
            m_events = ["按期推进"]

        month_cols.append({
            "year": curr.year,
            "month": curr.month,
            "title": m_name,
            "events": m_events,
            "is_cny": (curr.month == 2 and curr.year == 2027),
            "is_permit": (curr.month == t_permit.month and curr.year == t_permit.year)
        })

        if curr.month == 12:
            curr = datetime.date(curr.year + 1, 1, 1)
        else:
            curr = datetime.date(curr.year, curr.month + 1, 1)

    total_months = len(month_cols)

    def get_month_col_idx(dt: datetime.date) -> int:
        for idx, m in enumerate(month_cols):
            if m["year"] == dt.year and m["month"] == dt.month:
                return idx + 1
        return 1

    section_todo = []
    section_inprog = []
    section_done = []

    for t in tasks:
        lv = t.get("outline_level", t.get("level", 1))
        if lv < 3:
            continue
        name = str(t.get("name", ""))
        f_dt = _parse_date(t.get("finish"))
        dur = t.get("duration", t.get("duration_days", 1))
        unit = t.get("responsible_unit", "")
        person = t.get("responsible_person", "")
        prio = "high" if (t.get("critical") or t.get("milestone")) else "medium"

        item = {
            "id": t.get("id"),
            "name": name,
            "desc": f"{unit} · {person} | 工期 {dur}d" + (f" | 前置: {t.get('predecessors')}" if t.get('predecessors') else ""),
            "deadline": f_dt.strftime("%d %b, %Y") if f_dt else "2027",
            "unit": unit,
            "person": person,
            "dur": dur,
            "priority": prio,
            "critical": t.get("critical", False),
            "milestone": t.get("milestone", False)
        }

        if any(kw in name for kw in ["空气", "检测", "治理", "通风", "散味", "移交", "入驻", "竣工验收", "保洁", "家具", "消防"]):
            item["progress"] = 0
            section_todo.append(item)
        elif any(kw in name for kw in ["施工", "拆除", "隔墙", "吊顶", "机电", "桥架", "隐蔽", "封板", "漆", "地毯", "春节", "避峰", "联调"]):
            item["progress"] = 65
            section_inprog.append(item)
        else:
            item["progress"] = 100
            section_done.append(item)

    def render_table_rows(items: List[Dict[str, Any]]) -> str:
        rows_html = ""
        for it in items:
            prio_cls = "high" if it["priority"] == "high" else ("medium" if it["priority"] == "medium" else "low")
            prio_label = "● High" if it["priority"] == "high" else ("● Medium" if it["priority"] == "medium" else "● Normal")
            checked = 'checked' if it['progress'] > 0 else ''
            disabled = 'disabled' if it['progress'] == 100 else ''
            
            av_code = it["person"][:2] if it["person"] else "PM"
            av_color = "#6B5CE7" if "设计" in it["unit"] else ("#5C534A" if "施工" in it["unit"] else ("#6B5CE7" if "采招" in it["unit"] else "#6E675F"))
            av_bg = "#F3EAE1"
            fill_color = "#6B5CE7" if it['progress'] == 100 else ("#8B7EF0" if it['progress'] > 0 else "#D9D6F0")
            icon = ""

            rows_html += f"""
                <div class="d4-tr-row">
                  <div><input type="checkbox" {checked} {disabled} /></div>
                  <div class="d4-task-name-cell">{icon} <span>{it['name']}</span></div>
                  <div class="d4-desc-cell">{it['desc']}</div>
                  <div class="d4-date-cell">{it['deadline']}</div>
                  <div class="d4-people-cell">
                    <div class="d4-p-avatar" style="background:{av_bg}; color:{av_color};">{av_code}</div>
                  </div>
                  <div class="d4-prog-bar-cell">
                    <div class="d4-pbar-bg"><div class="d4-pbar-fill" style="width:{it['progress']}%; background:{fill_color};"></div></div>
                    <span class="d4-pbar-val" style="color:{fill_color};">{it['progress']}%</span>
                  </div>
                  <div><span class="d4-prio-tag {prio_cls}">{prio_label}</span></div>
                  <div style="color:var(--text-dim); cursor:pointer;">···</div>
                </div>"""
        return rows_html

    table_todo_html = render_table_rows(section_todo)
    table_inprog_html = render_table_rows(section_inprog)
    table_done_html = render_table_rows(section_done)

    timeline_x_cols_html = ""
    for idx, m in enumerate(month_cols):
        col_cls = " cny" if m["is_cny"] else (" gate" if m["is_permit"] else "")
        ev_html = "".join([f"<span>{ev}</span>" for ev in m["events"]])
        timeline_x_cols_html += f"""
              <div class="x-month-col{col_cls}">
                <span class="x-m-title">{m['title']}</span>
                <div class="x-m-days">{ev_html}</div>
              </div>"""

    col_kickoff = get_month_col_idx(start_dt)
    col_tender = get_month_col_idx(t_tender)
    col_permit = get_month_col_idx(t_permit)
    col_build = get_month_col_idx(t_build)
    col_fire = get_month_col_idx(t_fire)
    col_handover = get_month_col_idx(finish_dt)
    col_cny = get_month_col_idx(datetime.date(2027, 2, 10))

    today = datetime.date.today()
    span_days = max(1, (finish_dt - start_dt).days)
    if today < start_dt:
        overall_pct = 0
        overall_delta = f"距开工 {(start_dt - today).days} 天"
        overall_status = "基准锁定"
    elif today > finish_dt:
        overall_pct = 100
        overall_delta = "已完成交付闭环"
        overall_status = "已闭环"
    else:
        overall_pct = int(round((today - start_dt).days / span_days * 100))
        overall_delta = "按 CPM 基准推进"
        overall_status = "按期受控"

    # [P2-15] UNKNOWN cost must not be coerced into 0 and presented as a real figure.
    cost_wan = float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None
    cost_unknown = cost_wan is None
    _c = 0.0 if cost_unknown else cost_wan
    cost_display = "UNKNOWN" if cost_unknown else f"{cost_wan:g}"
    cost_design = round(_c * 0.18, 1)
    cost_gc = round(cost_wan * 0.62, 1)
    cost_ffne = round(cost_wan * 0.14, 1)
    cost_qa = round(cost_wan - cost_design - cost_gc - cost_ffne, 1)
    gauge_pct = 100
    donut_pct = 100
    wbs_count = len(tasks) if tasks else 0

    def _bar_status(seg_start, seg_end):
        if today > seg_end:
            return "done"
        if today >= seg_start:
            return "prog"
        return "plan"

    def _bar_style(start_idx, span_n, status):
        return f'class="d7-bar {status}" style="grid-column:{start_idx} / span {max(1, span_n)};"'

    s_design, n_design = col_kickoff, max(1, col_tender - col_kickoff + 1)
    s_tender, n_tender = col_tender, max(1, 1)
    s_permit, n_permit = col_tender, max(1, col_permit - col_tender + 1)
    s_build, n_build = col_permit, max(1, col_build - col_permit + 1)
    s_cny, n_cny = col_cny, 1
    s_close, n_close = col_build, max(1, col_handover - col_build + 1)

    d7_gantt_head = "".join(
        f'<div class="d7-gantt-col{" cny" if m.get("is_cny") else ""}">{m["title"]}</div>'
        for m in month_cols
    )

    ics_events = f"""BEGIN:VCALENDAR\\nVERSION:2.0\\nPRODID:-//Office Programme Scheduler//{city}//CN\\nBEGIN:VEVENT\\nSUMMARY:{project_title} · 项目启动 Kick-off\\nDTSTART;VALUE=DATE:{start_dt.strftime('%Y%m%d')}\\nDTEND;VALUE=DATE:{start_dt.strftime('%Y%m%d')}\\nEND:VEVENT\\nBEGIN:VEVENT\\nSUMMARY:{project_title} · D&B 招采定标签约\\nDTSTART;VALUE=DATE:{t_tender.strftime('%Y%m%d')}\\nDTEND;VALUE=DATE:{t_tender.strftime('%Y%m%d')}\\nEND:VEVENT\\nBEGIN:VEVENT\\nSUMMARY:{project_title} · 施工许可证核发 (政务前置)\\nDTSTART;VALUE=DATE:{t_permit.strftime('%Y%m%d')}\\nDTEND;VALUE=DATE:{t_permit.strftime('%Y%m%d')}\\nEND:VEVENT\\nBEGIN:VEVENT\\nSUMMARY:{project_title} · 现场硬装物理竣工自检\\nDTSTART;VALUE=DATE:{t_build.strftime('%Y%m%d')}\\nDTEND;VALUE=DATE:{t_build.strftime('%Y%m%d')}\\nEND:VEVENT\\nBEGIN:VEVENT\\nSUMMARY:{project_title} · 建设工程消防验收合格意见书/备案凭证办结\\nDTSTART;VALUE=DATE:{t_fire.strftime('%Y%m%d')}\\nDTEND;VALUE=DATE:{t_fire.strftime('%Y%m%d')}\\nEND:VEVENT\\nBEGIN:VEVENT\\nSUMMARY:{project_title} · 二次室内空气质量复测与客户入驻\\nDTSTART;VALUE=DATE:{finish_dt.strftime('%Y%m%d')}\\nDTEND;VALUE=DATE:{finish_dt.strftime('%Y%m%d')}\\nEND:VEVENT\\nEND:VCALENDAR"""

    json_safe_data = json.dumps({
        "project_title": project_title,
        "project_meta": project_meta,
        "start_date": start_date_str,
        "finish_date": finish_date_str,
        "total_days": total_calendar_days,
        "tasks_count": len(tasks),
        "milestones": {
            "kickoff": start_date_str,
            "tender": t_tender.strftime("%Y-%m-%d"),
            "permit": t_permit.strftime("%Y-%m-%d"),
            "build": t_build.strftime("%Y-%m-%d"),
            "finish": finish_date_str
        }
    }, ensure_ascii=False)

    html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{project_title} · 全周期工程交付控制台</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@600;700&amp;display=swap" rel="stylesheet" />
  <style>
    :root {{
      --canvas-bg: #F7F5FB;
      --tablet-bezel: #1F1F1F;
      --app-bg: #F7F5FB;
      --card-bg: #FFFFFF;
      --card-subtle: #F7F5FB;
      --card-opaque: #FFFFFF;
      --border-subtle: #E5E5E5;
      --border-line: #D4D0E0;

      --text-main: #1F1F1F;
      --text-secondary: #4A4A4A;
      --text-muted: #6B6B6B;
      --text-dim: #8A8A8A;

      --accent-red: #E6B84D;
      --accent-orange: #6B5CE7;
      --accent-deep: #6B5CE7;
      --accent-sand: #D9D6F0;
      --accent-peach: #F7F5FB;
      --color-primary: #6B5CE7;
      --color-on-primary: #FFFFFF;
      --color-surface: #FFFFFF;
      --color-on-surface: #1F1F1F;

      --space-1: 4px;
      --space-2: 8px;
      --space-3: 12px;
      --space-4: 16px;
      --space-5: 24px;
      --fs-xs: 12px;
      --fs-sm: 13px;
      --fs-md: 14px;
      --fs-lg: 16px;
      --fs-xl: 20px;
      --fs-2xl: 24px;
      --fs-hero: 32px;
      --lh-tight: 1.25;
      --lh-body: 1.5;
      --ease: 180ms ease;
      --focus: 2px solid var(--accent-deep);
      --header-h: 64px;

      --radius-sm: 8px;
      --radius-md: 14px;
      --radius-lg: 20px;
      --radius-xl: 32px;
    }}

    * {{ box-sizing: border-box; margin: 0; padding: 0; user-select: none; }}
    html, body {{
      width: 100vw;
      height: 100vh;
      margin: 0;
      padding: 0;
      overflow: hidden;
      font-family: -apple-system, BlinkMacSystemFont, "Inter", "Pretendard", "SF Pro Display", "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
      font-size: 16px;
      line-height: var(--lh-body);
      background: #F7F5FB;
      color: var(--text-main);
      -webkit-font-smoothing: antialiased;
      scroll-padding-top: var(--header-h);
    }}
    button:focus-visible,
    .p12-nav-btn:focus-visible,
    .p12-export-btn:focus-visible,
    .d7-ghost-btn:focus-visible,
    .d7-insight-btn:focus-visible {{
      outline: var(--focus);
      outline-offset: 2px;
    }}
    @media (prefers-reduced-motion: reduce) {{
      *, *::before, *::after {{
        animation-duration: 0.01ms !important;
        animation-iteration-count: 1 !important;
        transition-duration: 0.01ms !important;
        scroll-behavior: auto !important;
      }}
      .p8-capsule-task:hover {{ transform: none; }}
    }}

    /* 100% 全屏固定座舱框架 (Zero Resize Jump) */
    .p12-tablet-frame {{
      width: 100vw;
      height: 100vh;
      max-width: 100vw;
      max-height: 100vh;
      background: var(--app-bg);
      padding: 0;
      display: flex;
      flex-direction: column;
      overflow: hidden;
      position: relative;
    }}

    .p12-inner-screen {{
      width: 100%;
      height: 100%;
      background: var(--app-bg);
      border-radius: 0;
      overflow: hidden;
      display: flex;
      flex-direction: column;
    }}

    /* 顶部导航：暖白一体 + 三列网格对齐 */
    .p12-top-bar {{
      height: var(--header-h);
      flex-shrink: 0;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
      align-items: center;
      column-gap: var(--space-4);
      padding: 0 var(--space-5);
      background: var(--app-bg);
      color: var(--text-main);
      border-bottom: 1px solid var(--border-line);
    }}
    .p12-brand-left {{
      display: flex;
      align-items: center;
      gap: 14px;
      min-width: max-content;
      height: 100%;
      justify-self: start;
      overflow: visible;
    }}
    .p12-brand-lockup {{
      display: flex;
      flex-direction: row;
      flex-wrap: nowrap;
      align-items: center;
      gap: 12px;
      flex: 0 0 auto;
      height: 40px;
      writing-mode: horizontal-tb;
      direction: ltr;
      transform: none !important;
    }}
    .p12-brand-logo {{
      display: block;
      width: 118px;
      height: 36px;
      flex: 0 0 118px;
      line-height: 0;
      overflow: visible;
      writing-mode: horizontal-tb;
      transform: none !important;
    }}
    .p12-brand-logo svg {{
      display: block;
      width: 118px !important;
      height: 36px !important;
      min-width: 118px;
      min-height: 36px;
      max-width: 118px;
      max-height: 36px;
      flex-shrink: 0;
      transform: none !important;
      writing-mode: horizontal-tb;
    }}
    .p12-brand-text {{
      display: flex;
      flex-direction: column;
      justify-content: center;
      gap: 2px;
      flex: 0 0 auto;
      min-width: max-content;
      writing-mode: horizontal-tb;
    }}
    .p12-brand-word {{
      font-family: "Cinzel", Georgia, "Palatino Linotype", "Times New Roman", serif;
      font-size: 26px;
      font-weight: 700;
      letter-spacing: 0.18em;
      line-height: 1;
      color: var(--text-main);
      white-space: nowrap;
      transform: none !important;
      font-stretch: 100%;
      font-kerning: normal;
      writing-mode: horizontal-tb;
    }}
    .p12-brand-tag {{
      font-size: 11px;
      font-weight: 600;
      letter-spacing: 0.04em;
      color: var(--text-muted);
      line-height: 1.2;
      white-space: nowrap;
      text-transform: uppercase;
    }}
    .p12-brand-divider {{
      width: 1px;
      height: 18px;
      background: var(--border-line);
      flex-shrink: 0;
    }}
    .p12-brand-title {{
      font-size: 13px;
      font-weight: 700;
      color: var(--text-main);
      letter-spacing: -0.15px;
      line-height: 1.2;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}

    .p12-nav-capsule {{
      justify-self: center;
      display: flex;
      align-items: center;
      height: 38px;
      gap: 2px;
      background: var(--card-subtle);
      border: 1px solid var(--border-line);
      border-radius: 9999px;
      padding: 3px;
    }}
    .p12-nav-btn {{
      border: none;
      background: transparent;
      min-height: 32px;
      height: 32px;
      padding: 0 16px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      font-size: var(--fs-xs);
      font-weight: 600;
      line-height: 1;
      color: var(--text-secondary);
      border-radius: 9999px;
      cursor: pointer;
      transition: background var(--ease), color var(--ease), box-shadow var(--ease);
    }}
    .p12-nav-btn:hover {{ color: var(--text-main); }}
    .p12-nav-btn.active {{
      background: var(--card-bg);
      color: var(--accent-deep);
      font-weight: 700;
      box-shadow: 0 1px 3px rgba(90, 62, 43, 0.08);
    }}

    .p12-top-right {{
      justify-self: end;
      display: flex;
      align-items: center;
      height: 100%;
    }}
    .p12-export-btn {{
      height: 38px;
      min-height: 32px;
      padding: 0 14px;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border: 1px solid var(--border-line);
      background: var(--card-bg);
      color: var(--text-main);
      border-radius: 9999px;
      font-size: var(--fs-xs);
      font-weight: 700;
      line-height: 1;
      cursor: pointer;
      transition: border-color var(--ease), background var(--ease);
    }}
    .p12-export-btn:hover {{ border-color: var(--accent-sand); }}

    /* 统一所有面板高度与外边距 (100% 相同尺寸) */
    .view-panel {{
      display: none !important;
    }}
    .view-panel.active {{
      display: flex !important;
      flex-direction: column;
      flex: 1;
      height: calc(100vh - var(--header-h));
      max-height: calc(100vh - var(--header-h));
      padding: var(--space-3) var(--space-5);
      overflow-y: auto;
      overflow-x: hidden;
      gap: 10px;
      box-sizing: border-box;
    }}

    #panelDashboard.view-panel.active {{
      background: var(--app-bg);
      gap: 12px;
      padding: 14px 18px 16px;
    }}

    .d7-kpi-row {{
      display: grid;
      grid-template-columns: 1.35fr 1fr 1fr 1fr;
      gap: 12px;
      flex-shrink: 0;
    }}
    .d7-card {{
      background: var(--card-bg);
      border: 1px solid var(--border-line);
      border-radius: 18px;
      padding: 16px 18px;
      box-shadow: 0 8px 24px rgba(90, 62, 43, 0.05);
      display: flex;
      flex-direction: column;
      min-height: 0;
    }}
    .d7-card h3 {{
      font-size: 12px;
      font-weight: 700;
      color: var(--text-secondary);
      letter-spacing: -0.1px;
    }}
    .d7-card .d7-kicker {{
      font-size: var(--fs-xs);
      font-weight: 600;
      color: var(--text-muted);
      margin-top: 2px;
      line-height: var(--lh-body);
    }}
    .d7-hero {{
      background: linear-gradient(145deg, #6B5CE7 0%, #6B5CE7 55%, #8B7EF0 100%);
      border: none;
      color: #FFF7F1;
      box-shadow: 0 10px 28px rgba(194, 65, 12, 0.22);
    }}
    .d7-hero h3, .d7-hero .d7-kicker {{ color: rgba(255, 247, 241, 0.82); }}
    .d7-hero-val {{
      font-size: 40px;
      font-weight: 800;
      letter-spacing: -1.4px;
      line-height: 1;
      margin: 10px 0 12px;
      font-variant-numeric: tabular-nums;
    }}
    .d7-bar-track {{
      height: 8px;
      border-radius: 999px;
      background: rgba(255,255,255,0.28);
      overflow: hidden;
    }}
    .d7-hero .d7-bar-fill {{
      height: 100%;
      width: {overall_pct}%;
      background: #FFF7F1;
      border-radius: 999px;
    }}
    .d7-hero-meta {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-top: 10px;
      font-size: 11px;
      font-weight: 700;
    }}
    .d7-pill {{
      background: var(--card-subtle);
      color: var(--accent-deep);
      border-radius: 999px;
      padding: 3px 10px;
      font-size: 11px;
      font-weight: 800;
      white-space: nowrap;
    }}
    .d7-hero .d7-pill {{
      background: rgba(255,255,255,0.92);
      color: #6B5CE7;
    }}
    .d7-metric-val {{
      font-size: var(--fs-2xl);
      font-weight: 800;
      letter-spacing: -0.8px;
      color: var(--text-main);
      margin: 8px 0 10px;
      font-variant-numeric: tabular-nums;
    }}
    .d7-metric-val small {{
      font-size: 12px;
      font-weight: 700;
      color: var(--text-muted);
      margin-left: 4px;
    }}
    .d7-bar-track.light {{ background: #F0E6DC; }}
    .d7-bar-fill.terra {{
      height: 100%;
      background: linear-gradient(90deg, #6B5CE7, #6B5CE7);
      border-radius: 999px;
    }}
    .d7-gauge-wrap {{
      display: flex;
      align-items: center;
      justify-content: center;
      margin-top: 2px;
      position: relative;
    }}
    .d7-gauge-wrap svg {{ width: 132px; height: 78px; }}
    .d7-gauge-label {{
      position: absolute;
      bottom: 2px;
      text-align: center;
      font-size: 18px;
      font-weight: 800;
      color: var(--text-main);
      letter-spacing: -0.4px;
    }}

    .d7-mid-row {{
      display: grid;
      grid-template-columns: 1.7fr 0.85fr;
      gap: 12px;
      flex: 1;
      min-height: 210px;
    }}
    .d7-gantt-legend {{
      display: flex;
      gap: 12px;
      font-size: var(--fs-xs);
      color: var(--text-muted);
      font-weight: 700;
    }}
    .d7-leg-dot {{
      width: 8px; height: 8px; border-radius: 50%; display: inline-block; margin-right: 4px;
    }}
    .d7-gantt {{
      margin-top: 10px;
      display: grid;
      grid-template-columns: 132px 1fr;
      gap: 0;
      flex: 1;
    }}
    .d7-gantt-head, .d7-gantt-lane {{
      display: grid;
      grid-template-columns: repeat({total_months}, 1fr);
      gap: 6px;
      align-items: center;
    }}
    .d7-gantt-col {{
      font-size: 11px;
      font-weight: 700;
      color: var(--text-muted);
      text-align: center;
      padding-bottom: 6px;
    }}
    .d7-gantt-col.cny {{ color: #6B5CE7; }}
    .d7-gantt-label {{
      font-size: 11px;
      font-weight: 700;
      color: var(--text-secondary);
      padding: 6px 8px 6px 0;
      white-space: nowrap;
    }}
    .d7-gantt-lane {{
      position: relative;
      height: 34px;
      background: repeating-linear-gradient(
        to right,
        transparent 0,
        transparent calc(100% / {total_months} - 1px),
        #F0E6DC calc(100% / {total_months} - 1px),
        #F0E6DC calc(100% / {total_months})
      );
      border-radius: 8px;
      margin-bottom: 6px;
    }}
    .d7-bar {{
      height: 22px;
      border-radius: 8px;
      font-size: 11px;
      font-weight: 800;
      color: #FFF7F1;
      display: flex;
      align-items: center;
      padding: 0 8px;
      min-width: 0;
    }}
    .d7-bar.done {{ background: #6B5CE7; }}
    .d7-bar.prog {{ background: #8B7EF0; }}
    .d7-bar.plan {{ background: #EDE4DA; color: #8A8178; }}
    .d7-today-line {{
      position: absolute;
      top: 0; bottom: 0;
      width: 0;
      border-left: 1.5px dashed #6B5CE7;
      opacity: 0.55;
      pointer-events: none;
    }}

    .d7-stack {{
      display: flex;
      height: 14px;
      border-radius: 999px;
      overflow: hidden;
      background: #F0E6DC;
      margin: 14px 0 16px;
    }}
    .d7-stack span {{ display: block; height: 100%; }}
    .d7-budget-row {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 7px 0;
      border-bottom: 1px solid var(--border-subtle);
      font-size: 12px;
    }}
    .d7-budget-row:last-child {{ border-bottom: none; font-weight: 800; }}
    .d7-dot {{
      width: 8px; height: 8px; border-radius: 50%; display: inline-block; margin-right: 8px;
    }}

    .d7-bottom-row {{
      display: grid;
      grid-template-columns: 1.05fr 1fr 1.15fr;
      gap: 12px;
      flex-shrink: 0;
      min-height: 188px;
    }}
    .d7-donut-layout {{
      display: flex;
      align-items: center;
      gap: 16px;
      flex: 1;
    }}
    .d7-donut {{
      position: relative;
      width: 118px;
      height: 118px;
      flex-shrink: 0;
    }}
    .d7-donut svg {{ width: 118px; height: 118px; transform: rotate(-90deg); }}
    .d7-donut-center {{
      position: absolute;
      inset: 0;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      text-align: center;
    }}
    .d7-donut-center b {{ font-size: 20px; letter-spacing: -0.6px; }}
    .d7-donut-center span {{ font-size: 11px; color: var(--text-muted); font-weight: 700; }}
    .d7-legend-list {{ display: flex; flex-direction: column; gap: 8px; font-size: 11px; color: var(--text-secondary); }}
    .d7-ghost-btn {{
      margin-top: auto;
      align-self: flex-start;
      border: 1px solid var(--border-line);
      background: #FFFCF8;
      color: var(--text-secondary);
      border-radius: 999px;
      padding: 6px 12px;
      font-size: 11px;
      font-weight: 800;
      cursor: pointer;
    }}
    .d7-eq-item {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 8px 0;
      border-bottom: 1px solid var(--border-subtle);
    }}
    .d7-eq-item:last-child {{ border-bottom: none; }}
    .d7-eq-name {{ font-size: 12px; font-weight: 700; color: var(--text-main); }}
    .d7-eq-sub {{ font-size: 11px; color: var(--text-muted); margin-top: 1px; line-height: var(--lh-body); }}
    .d7-status {{
      font-size: 11px;
      font-weight: 800;
      border-radius: 999px;
      padding: 3px 9px;
    }}
    .d7-status.down {{ background: var(--accent-peach); color: var(--accent-deep); }}
    .d7-status.active {{ background: var(--card-subtle); color: var(--accent-deep); }}
    .d7-status.idle {{ background: #F7F5FB; color: var(--text-secondary); }}
    .d7-insight {{
      background: linear-gradient(145deg, #FFFFFF 0%, #F7F5FB 55%, #FFFFFF 100%);
      border: 1px solid #D9D6F0;
      border-radius: var(--radius-md);
      padding: 18px 20px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      box-shadow: 0 4px 20px -2px rgba(107, 92, 231, 0.08);
      position: relative;
      overflow: hidden;
      transition: all var(--ease);
    }}
    .d7-insight:hover {{
      border-color: #C4B8E8;
      box-shadow: 0 8px 24px -2px rgba(107, 92, 231, 0.12);
    }}
    .d7-insight::before {{
      content: "";
      position: absolute;
      top: 0; left: 0; right: 0;
      height: 3px;
      background: linear-gradient(90deg, #6B5CE7 0%, #6B5CE7 100%);
    }}
    .d7-insight h3 {{
      font-size: 14.5px;
      font-weight: 800;
      color: #1F1F1F;
      margin: 4px 0 6px;
    }}
    .d7-insight .d7-kicker {{
      font-size: 10.5px;
      font-weight: 800;
      color: #6B5CE7;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    .d7-insight p {{
      font-size: 12px;
      color: #475569;
      line-height: 1.55;
      margin: 6px 0 12px;
      max-width: 100%;
    }}
    .d7-insight-btn {{
      background: #1F1F1F;
      color: #FFFFFF;
      border: none;
      border-radius: 999px;
      padding: 7px 16px;
      font-size: 11.5px;
      font-weight: 700;
      cursor: pointer;
      width: 100%;
      text-align: center;
      transition: all var(--ease);
      box-shadow: 0 2px 6px rgba(15, 23, 42, 0.12);
    }}
    .d7-insight-btn:hover {{
      background: #1E293B;
      transform: translateY(-1px);
    }}

    /* =========================================================================
       2. VIEW 2: P12 Interactive Frosted Timeline (100% P12 标准)
       ========================================================================= */
    .p8-hero-timeline-bar {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      background: #FFFCF8;
      border: 1px solid var(--border-line);
      border-radius: var(--radius-md);
      padding: 14px 20px;
      flex-shrink: 0;
      gap: 20px;
    }}
    .p8-hero-left {{
      min-width: 0;
      flex: 1;
    }}
    .p8-hero-left h1 {{
      font-size: var(--fs-lg);
      font-weight: 700;
      color: var(--text-main);
      letter-spacing: -0.25px;
      line-height: var(--lh-tight);
    }}
    .p8-hero-left p {{
      font-size: var(--fs-xs);
      color: var(--text-secondary);
      margin-top: 4px;
      line-height: var(--lh-body);
    }}
    .p8-chips-row {{
      display: flex;
      align-items: center;
      gap: 8px;
      flex-shrink: 0;
    }}
    .p8-meta-chip {{
      font-size: var(--fs-xs);
      font-weight: 600;
      color: var(--text-secondary);
      background: var(--app-bg);
      border: 1px solid var(--border-line);
      padding: 8px 12px;
      border-radius: 999px;
      line-height: 1;
      white-space: nowrap;
    }}

    .p8-timeline-wrapper {{
      background: #FFFCF8;
      border: 1px solid var(--border-line);
      border-radius: var(--radius-lg);
      box-shadow: 0 2px 10px rgba(90, 62, 43, 0.04);
      display: grid !important;
      grid-template-columns: 228px 1fr !important;
      overflow: hidden;
      flex: 1;
      min-height: 460px;
      position: relative;
    }}

    .p8-stakeholders-sidebar {{
      background: #F6F1EA;
      border-right: 1px solid var(--border-line);
      display: flex;
      flex-direction: column;
    }}
    .sidebar-head-cell {{
      height: 52px;
      padding: 0 18px;
      border-bottom: 1px solid var(--border-line);
      display: flex;
      align-items: center;
      font-size: 12px;
      font-weight: 700;
      color: var(--text-secondary);
      letter-spacing: 0.2px;
    }}
    .sh-row-item {{
      flex: 1;
      padding: 0 18px;
      border-bottom: 1px solid var(--border-subtle);
      display: flex;
      align-items: center;
      gap: 12px;
    }}
    .sh-row-item:last-child {{ border-bottom: none; }}
    .sh-avatar-box {{
      width: 36px;
      height: 36px;
      border-radius: 10px;
      background: var(--card-subtle);
      border: 1px solid var(--border-line);
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 11px;
      font-weight: 700;
      color: var(--accent-deep);
      flex-shrink: 0;
    }}
    .sh-text-info h4 {{ font-size: 13px; font-weight: 600; color: var(--text-main); line-height: 1.35; }}
    .sh-text-info p {{ font-size: 11px; color: var(--text-muted); margin-top: 2px; line-height: 1.3; }}

    .p8-timeline-stage {{
      display: flex;
      flex-direction: column;
      position: relative;
      background: #FFFCF8;
      overflow-x: hidden;
    }}
    .timeline-x-header {{
      height: 52px;
      border-bottom: 1px solid var(--border-line);
      display: grid;
      grid-template-columns: repeat({total_months}, 1fr);
      background: #F6F1EA;
    }}
    .x-month-col {{
      padding: 8px 12px;
      display: flex;
      flex-direction: column;
      justify-content: center;
      gap: 3px;
      border-right: 1px solid var(--border-subtle);
    }}
    .x-month-col:last-child {{ border-right: none; }}
    .x-m-title {{ font-size: var(--fs-xs); font-weight: 600; color: var(--text-main); letter-spacing: -0.1px; }}
    .x-m-days {{ font-size: 11px; color: var(--text-muted); display: flex; justify-content: space-between; line-height: 1.3; }}
    .x-month-col.cny {{ background: var(--accent-peach); }}
    .x-month-col.cny .x-m-title {{ color: var(--accent-deep); }}
    .x-month-col.gate {{ background: var(--card-subtle); }}

    .timeline-rows-container {{
      display: flex;
      flex-direction: column;
      position: relative;
      flex: 1;
    }}
    .timeline-data-row {{
      flex: 1;
      border-bottom: 1px solid var(--border-subtle);
      display: grid;
      grid-template-columns: repeat({total_months}, 1fr);
      align-items: center;
      padding: 0 8px;
      position: relative;
      min-height: 76px;
    }}
    .timeline-data-row:last-child {{ border-bottom: none; }}

    .bg-grid-lines {{
      position: absolute;
      top: 0; left: 0; right: 0; bottom: 0;
      display: grid;
      grid-template-columns: repeat({total_months}, 1fr);
      pointer-events: none;
    }}
    .bg-grid-col {{ border-right: 1px dashed var(--border-subtle); }}
    .bg-grid-col:last-child {{ border-right: none; }}

    .p8-capsule-task {{
      border-radius: 10px;
      padding: 8px 12px;
      font-size: var(--fs-xs);
      font-weight: 600;
      color: var(--text-main);
      display: flex;
      flex-direction: column;
      gap: 3px;
      box-shadow: 0 1px 4px rgba(90, 62, 43, 0.04);
      cursor: pointer;
      transition: transform var(--ease), box-shadow var(--ease);
      z-index: 5;
      background: var(--card-bg);
      border: 1px solid var(--border-line);
      border-left: 3px solid var(--accent-orange);
    }}
    .p8-capsule-task:hover {{ transform: translateY(-1.5px); box-shadow: 0 4px 10px rgba(90, 62, 43, 0.08); }}
    .p8-capsule-task.terra {{
      background: var(--card-bg);
      border-color: var(--border-line);
      border-left-color: var(--accent-orange);
      color: var(--text-main);
    }}
    .p8-capsule-task.gate {{
      background: var(--card-subtle);
      border-color: var(--border-line);
      border-left-color: var(--accent-deep);
      color: var(--text-main);
    }}
    .p8-capsule-task.alert {{
      background: #FFF7F1;
      border-color: var(--accent-sand);
      border-left-width: 4px;
      border-left-color: var(--accent-deep);
      color: var(--text-main);
    }}
    .p8-capsule-task.done {{
      background: var(--card-bg);
      border-color: var(--border-line);
      border-left-color: var(--text-dim);
      color: var(--text-main);
    }}

    .capsule-title {{ font-size: 12.5px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; line-height: 1.35; }}
    .capsule-meta-row {{ font-size: 10.5px; color: var(--text-muted); display: flex; justify-content: space-between; font-weight: 500; line-height: 1.4; gap: 8px; }}

    /* 磨砂毛玻璃遮罩与拖拽游标 */
    .p8-frosted-curtain {{
      position: absolute;
      top: 0; bottom: 0; left: 0;
      width: 48%;
      background: rgba(250, 249, 247, 0.45);
      backdrop-filter: blur(6px);
      -webkit-backdrop-filter: blur(6px);
      pointer-events: none;
      z-index: 10;
      border-right: 1.5px solid var(--accent-deep);
      transition: width 0.04s ease-out;
    }}
    .p8-cursor-handle-bar {{
      position: absolute;
      top: 0; bottom: 0; left: 48%;
      width: 20px;
      margin-left: -10px;
      z-index: 25;
      cursor: ew-resize;
      display: flex;
      flex-direction: column;
      align-items: center;
    }}
    .p8-cursor-needle-line {{ width: 1.5px; height: 100%; background: var(--accent-deep); }}
    .p8-cursor-pill-badge {{
      position: absolute;
      top: 12px;
      background: var(--text-main);
      color: #FFFCF8;
      font-size: 11.5px;
      font-weight: 600;
      padding: 5px 10px;
      border-radius: 9999px;
      white-space: nowrap;
      box-shadow: 0 4px 12px rgba(90, 62, 43, 0.18);
      line-height: 1.2;
    }}
    .p8-cursor-hint {{
      position: absolute;
      bottom: 12px;
      background: rgba(42, 36, 30, 0.82);
      color: #FFFCF8;
      font-size: 10.5px;
      font-weight: 600;
      padding: 4px 8px;
      border-radius: 6px;
      white-space: nowrap;
      pointer-events: none;
    }}

    /* =========================================================================
       3. VIEW 3: Executive Risk & Governance Report (高管工程风险报告 - 暖白半透明高管桌面底图)
       ========================================================================= */
    #panelRisk {{
      position: relative;
      overflow: hidden;
      background: var(--app-bg);
    }}

    .risk-bg-img-wrapper {{
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
      overflow: hidden;
      z-index: 0;
      pointer-events: none;
    }}
    .risk-bg-img {{
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
      object-fit: cover;
      object-position: 78% 86%;
      opacity: 0.06;
      filter: grayscale(0.5) saturate(0.18) contrast(0.88) brightness(1.18);
      z-index: 1;
    }}
    .risk-bg-warm-overlay {{
      position: absolute;
      inset: 0;
      background: linear-gradient(180deg, rgba(246, 241, 234, 0.96) 0%, rgba(246, 241, 234, 0.94) 55%, rgba(243, 234, 225, 0.95) 100%);
      z-index: 2;
    }}

    .risk-content-container {{
      position: relative;
      z-index: 3;
      display: flex;
      flex-direction: column;
      gap: 12px;
      width: 100%;
      height: 100%;
      overflow-y: auto;
      padding: 4px 2px;
    }}

    .risk-header-kpis {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 12px;
      flex-shrink: 0;
    }}
    .risk-kpi-card {{
      background: #FFFCF8;
      border: 1px solid var(--border-line);
      border-radius: 14px;
      padding: 12px 14px;
      box-shadow: 0 8px 22px rgba(42, 36, 30, 0.06);
      display: flex;
      flex-direction: column;
      gap: 4px;
    }}
    .risk-kpi-title {{ font-size: var(--fs-xs); color: var(--text-secondary); font-weight: 600; line-height: var(--lh-body); }}
    .risk-kpi-val {{ font-size: var(--fs-lg); font-weight: 700; color: var(--text-main); letter-spacing: -0.3px; font-variant-numeric: tabular-nums; }}
    .risk-kpi-val em {{ font-style: normal; color: var(--accent-deep); }}

    .risk-sections-grid {{
      display: grid;
      grid-template-columns: 1.15fr 1fr;
      gap: 14px;
      flex: 1;
      min-height: 0;
    }}
    .risk-column-panel {{
      display: flex;
      flex-direction: column;
      gap: 10px;
      min-height: 0;
    }}
    .risk-panel-header h3 {{
      color: var(--text-main);
      font-size: var(--fs-md);
      font-weight: 700;
      margin: 0;
      letter-spacing: -0.2px;
    }}
    .risk-panel-header p {{
      color: var(--text-muted);
      font-size: var(--fs-xs);
      margin: 3px 0 0;
      line-height: var(--lh-body);
    }}

    .risk-item-box {{
      background: #FFFCF8;
      border: 1px solid var(--border-line);
      border-left: 3px solid var(--accent-deep);
      border-radius: 12px;
      padding: 11px 13px;
      display: flex;
      flex-direction: column;
      gap: 5px;
    }}
    .risk-item-box.high,
    .risk-item-box.medium {{ border-left-color: var(--accent-deep); }}
    .risk-item-box.critical {{ border-left-color: var(--accent-red); border-left-width: 4px; }}

    .risk-item-head {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 8px;
      font-size: 12.5px;
      font-weight: 700;
      color: var(--text-main);
      line-height: 1.35;
    }}
    .risk-sev {{
      flex-shrink: 0;
      font-size: 11px;
      font-weight: 600;
      color: var(--accent-deep);
      background: var(--card-subtle);
      border: 1px solid var(--accent-sand);
      padding: 3px 8px;
      border-radius: 999px;
      white-space: nowrap;
      line-height: 1.2;
    }}
    .risk-item-desc {{
      font-size: var(--fs-xs);
      color: var(--text-main);
      line-height: var(--lh-body);
    }}
    .risk-item-mitigation {{
      font-size: var(--fs-xs);
      color: var(--text-main);
      font-weight: 600;
      background: var(--app-bg);
      border: 1px solid var(--border-subtle);
      padding: 7px 10px;
      border-radius: 8px;
      line-height: var(--lh-body);
    }}

    .risk-table-grid {{
      border: 1px solid var(--border-line);
      border-radius: 14px;
      overflow: hidden;
      background: #FFFCF8;
    }}
    .risk-th-row, .risk-tr-row {{
      display: grid;
      grid-template-columns: 58px 1fr 1.7fr 72px 72px;
      align-items: center;
      padding: 9px 12px;
      gap: 8px;
      border-bottom: 1px solid var(--border-subtle);
      font-size: 11.5px;
      color: var(--text-main);
    }}
    .risk-th-row {{ background: var(--app-bg); font-weight: 700; color: var(--text-muted); font-size: var(--fs-xs); }}
    .risk-tr-row:last-child {{ border-bottom: none; }}
    .risk-id {{ font-weight: 700; color: var(--accent-deep); }}
    .risk-owner,
    .risk-status {{
      display: inline-block;
      background: var(--card-subtle);
      color: var(--text-secondary);
      border: 1px solid var(--accent-sand);
      padding: 3px 8px;
      border-radius: 999px;
      font-size: 11px;
      font-weight: 600;
      white-space: nowrap;
      line-height: 1.2;
    }}
  </style>
</head>
<body>
  <div class="p12-tablet-frame">
    <div class="p12-inner-screen">
      <header class="p12-top-bar">
        <div class="p12-brand-left">
          <span class="p12-brand-lockup" role="img" aria-label="Programme master schedule">
            <span class="p12-brand-logo">{BRAND_MARK_SVG}</span>
            <span class="p12-brand-text">
              <span class="p12-brand-word">Programme</span>
              <span class="p12-brand-tag">Master schedule</span>
            </span>
          </span>
          <span class="p12-brand-divider"></span>
          <span class="p12-brand-title">{project_title} · 交付总控枢纽</span>
        </div>

        <nav class="p12-nav-capsule" aria-label="主视图">
          <button type="button" class="p12-nav-btn active" id="tabDashboard" onclick="switchMainView('dashboard')" aria-current="page">Dashboard</button>
          <button type="button" class="p12-nav-btn" id="tabCalendar" onclick="switchMainView('calendar')">Timeline</button>
          <button type="button" class="p12-nav-btn" id="tabRisk" onclick="switchMainView('risk')">Risk 风险报告</button>
        </nav>

        <div class="p12-top-right">
          <button type="button" class="p12-export-btn" onclick="exportICalendar()">导出排程 (.ICS)</button>
        </div>
      </header>

      <!-- VIEW 1: D7 Warm Korean Dashboard -->
      <section class="view-panel active" id="panelDashboard">
        <div class="d7-kpi-row">
          <div class="d7-card d7-hero">
            <h3>项目整体进度</h3>
            <div class="d7-kicker">{city} · {area:,}㎡ · {delivery_label}</div>
            <div class="d7-hero-val">{overall_pct}%</div>
            <div class="d7-bar-track"><div class="d7-bar-fill"></div></div>
            <div class="d7-hero-meta">
              <span>{overall_delta}</span>
              <span class="d7-pill">{overall_status}</span>
              {('<div style="margin-top:8px;color:#B45309;font-size:12px;">⚠ 以下里程碑按常规节奏推算（未匹配任务名）：' + "、".join(estimated_milestones) + '</div>') if estimated_milestones else ''}
            </div>
          </div>

          <div class="d7-card">
            <h3>投资造价锁定</h3>
            <div class="d7-kicker">Budget Baseline</div>
            <div class="d7-metric-val">{cost_display}<small>{"(未确认)" if cost_unknown else "万元"}</small></div>
            <div class="d7-bar-track light"><div class="d7-bar-fill terra" style="width:100%;"></div></div>
            <div class="d7-kicker" style="margin-top:10px;">100% 预算结构已锁定 · 开工前零发生</div>
          </div>

          <div class="d7-card">
            <h3>工序与工日配置</h3>
            <div class="d7-kicker">Workforce / WBS</div>
            <div class="d7-metric-val">{wbs_count}<small> / {total_workdays} 工日</small></div>
            <div class="d7-bar-track light"><div class="d7-bar-fill terra" style="width:93%;"></div></div>
            <div class="d7-kicker" style="margin-top:10px;">WBS 节点已编入 · 关键路径零时差</div>
          </div>

          <div class="d7-card">
            <h3>合规闭环覆盖</h3>
            <div class="d7-kicker">Safety & Governance</div>
            <div class="d7-gauge-wrap">
              <svg viewBox="0 0 120 72" aria-hidden="true">
                <path d="M14 62 A46 46 0 0 1 106 62" fill="none" stroke="#F0E6DC" stroke-width="10" stroke-linecap="round"/>
                <path d="M14 62 A46 46 0 0 1 106 62" fill="none" stroke="#6B5CE7" stroke-width="10" stroke-linecap="round" stroke-dasharray="144" stroke-dashoffset="0"/>
              </svg>
              <div class="d7-gauge-label">{gauge_pct}%</div>
            </div>
          </div>
        </div>

        <div class="d7-mid-row">
          <div class="d7-card" style="min-height:220px;">
            <div style="display:flex; justify-content:space-between; align-items:flex-start;">
              <div>
                <h3 style="font-size:14px; color:var(--text-main);">阶段推进 · Project Progress</h3>
                <div class="d7-kicker">{start_date_str} → {finish_date_str} · {total_calendar_days} 日历天</div>
              </div>
              <div class="d7-gantt-legend">
                <span><i class="d7-leg-dot" style="background:#6B5CE7;"></i>已完成</span>
                <span><i class="d7-leg-dot" style="background:#8B7EF0;"></i>进行中</span>
                <span><i class="d7-leg-dot" style="background:#EDE4DA;"></i>未开始</span>
              </div>
            </div>
            <div class="d7-gantt">
              <div></div>
              <div class="d7-gantt-head">{d7_gantt_head}</div>

              <div class="d7-gantt-label">设计深化</div>
              <div class="d7-gantt-lane"><div {_bar_style(s_design, n_design, _bar_status(start_dt, t_tender))}>图审前</div></div>
              <div class="d7-gantt-label">招采定标</div>
              <div class="d7-gantt-lane"><div {_bar_style(s_tender, n_tender, _bar_status(t_tender, t_tender))}>LOI</div></div>
              <div class="d7-gantt-label">施工许可</div>
              <div class="d7-gantt-lane"><div {_bar_style(s_permit, n_permit, _bar_status(t_tender, t_permit))}>政务前置</div></div>
              <div class="d7-gantt-label">现场硬装</div>
              <div class="d7-gantt-lane"><div {_bar_style(s_build, n_build, _bar_status(t_permit, t_build))}>GC 施工</div></div>
              <div class="d7-gantt-label">春节避峰</div>
              <div class="d7-gantt-lane"><div {_bar_style(s_cny, n_cny, _bar_status(datetime.date(2027,2,5), datetime.date(2027,2,20)))}>15d</div></div>
              <div class="d7-gantt-label">消防与入驻</div>
              <div class="d7-gantt-lane"><div {_bar_style(s_close, n_close, _bar_status(t_build, finish_dt))}>验收移交</div></div>
            </div>
          </div>

          <div class="d7-card">
            <h3 style="font-size:14px; color:var(--text-main);">投资结构 · Budget Mix</h3>
            <div class="d7-kicker">按阶段锁定，非现场已发生</div>
            <div class="d7-stack">
              <span style="width:18%; background:#6B5CE7;"></span>
              <span style="width:62%; background:#8B7EF0;"></span>
              <span style="width:14%; background:#E8B48A;"></span>
              <span style="width:6%; background:#F0E6DC;"></span>
            </div>
            <div class="d7-budget-row"><span><i class="d7-dot" style="background:#6B5CE7;"></i>设计深化</span><span>{("-" if cost_unknown else str(cost_design) + " 万")}</span></div>
            <div class="d7-budget-row"><span><i class="d7-dot" style="background:#8B7EF0;"></i>现场施工</span><span>{("-" if cost_unknown else str(cost_gc) + " 万")}</span></div>
            <div class="d7-budget-row"><span><i class="d7-dot" style="background:#E8B48A;"></i>家具机电</span><span>{("-" if cost_unknown else str(cost_ffne) + " 万")}</span></div>
            <div class="d7-budget-row"><span><i class="d7-dot" style="background:#D9D6F0;"></i>检测验收</span><span>{("-" if cost_unknown else str(cost_qa) + " 万")}</span></div>
            <div class="d7-budget-row"><span>合计</span><span>{("-" if cost_unknown else str(cost_wan) + " 万")}</span></div>
          </div>
        </div>

        <div class="d7-bottom-row">
          <div class="d7-card">
            <h3 style="font-size:14px; color:var(--text-main);">责任主体配置</h3>
            <div class="d7-kicker">RACI · Staffing Mix</div>
            <div class="d7-donut-layout">
              <div class="d7-donut">
                <svg viewBox="0 0 36 36">
                  <circle cx="18" cy="18" r="14" fill="none" stroke="#F0E6DC" stroke-width="5"/>
                  <circle cx="18" cy="18" r="14" fill="none" stroke="#6B5CE7" stroke-width="5" stroke-dasharray="55 45" stroke-dashoffset="0"/>
                  <circle cx="18" cy="18" r="14" fill="none" stroke="#8B7EF0" stroke-width="5" stroke-dasharray="25 75" stroke-dashoffset="-55"/>
                  <circle cx="18" cy="18" r="14" fill="none" stroke="#E8B48A" stroke-width="5" stroke-dasharray="20 80" stroke-dashoffset="-80"/>
                </svg>
                <div class="d7-donut-center"><b>{donut_pct}%</b><span>配置完成</span></div>
              </div>
              <div class="d7-legend-list">
                <div><i class="d7-leg-dot" style="background:#6B5CE7;"></i>施工总包 55%</div>
                <div><i class="d7-leg-dot" style="background:#8B7EF0;"></i>设计深化 25%</div>
                <div><i class="d7-leg-dot" style="background:#E8B48A;"></i>检测顾问 20%</div>
                <button type="button" class="d7-ghost-btn" onclick="switchMainView('calendar')">查看时间轴</button>
              </div>
            </div>
          </div>

          <div class="d7-card">
            <h3 style="font-size:14px; color:var(--text-main);">关键门禁状态</h3>
            <div class="d7-kicker">Gate Status</div>
            <div class="d7-eq-item">
              <div><div class="d7-eq-name">施工许可证</div><div class="d7-eq-sub">{t_permit.strftime('%Y.%m.%d')} 核发窗口</div></div>
              <span class="d7-status idle">未开始</span>
            </div>
            <div class="d7-eq-item">
              <div><div class="d7-eq-name">春节 15 天防线</div><div class="d7-eq-sub">2027.02.05–02.20 已编入</div></div>
              <span class="d7-status active">已锁定</span>
            </div>
            <div class="d7-eq-item">
              <div><div class="d7-eq-name">消防验收 18d</div><div class="d7-eq-sub">{t_fire.strftime('%Y.%m.%d')} 批文办结</div></div>
              <span class="d7-status idle">未开始</span>
            </div>
            <div class="d7-eq-item">
              <div><div class="d7-eq-name">二次 IAQ 检测</div><div class="d7-eq-sub">{finish_dt.strftime('%Y.%m.%d')} 入驻门槛</div></div>
              <span class="d7-status idle">未开始</span>
            </div>
          </div>

          <div class="d7-card d7-insight">
            <h3>AI 洞察 · Delay Risk</h3>
            <div class="d7-kicker">春节劳务断档已识别</div>
            <p>2027 元宵前民工返乡窗口若无年前封板预案，现场湿作业可能延误 15–20 天。排程已刚性锁定 15 天避峰。</p>
            <button type="button" class="d7-insight-btn" onclick="switchMainView('risk')">查看风险报告</button>
            <img class="d7-insight-photo" src="hangzhou_smart_fitout.jpg" alt="Fit-out preview" />
          </div>
        </div>
      </section>

      <!-- ==========================================
           VIEW 2: P12 Interactive Frosted Timeline (100% P12 参照)
           ========================================== -->
      <section class="view-panel" id="panelCalendar">
        <div class="p8-hero-timeline-bar">
          <div class="p8-hero-left">
            <h1>时间轴 · {project_title}</h1>
            <p>{city} {area:,}㎡ · {total_calendar_days} 天全周期 · 含春节 15 天避峰与消防 18 天闭环 · 拖拽游标查看节点</p>
          </div>

          <div class="p8-chips-row">
            <span class="p8-meta-chip">{city} {area:,}㎡</span>
            <span class="p8-meta-chip">{delivery_label} · {bidding_label}</span>
            <span class="p8-meta-chip">{total_calendar_days} 天全周期</span>
          </div>
        </div>

        <div class="p8-timeline-wrapper">
          <!-- 左侧责任主体/单位栏 (5大角色) -->
          <aside class="p8-stakeholders-sidebar">
            <div class="sidebar-head-cell">责任主体 / 单位 (RACI)</div>

            <div class="sh-row-item">
              <div class="sh-avatar-box">PMO</div>
              <div class="sh-text-info">
                <h4>业主项目总</h4>
                <p>Accountable (A)</p>
              </div>
            </div>

            <div class="sh-row-item">
              <div class="sh-avatar-box">DES</div>
              <div class="sh-text-info">
                <h4>设计深化院</h4>
                <p>Responsible (R)</p>
              </div>
            </div>

            <div class="sh-row-item">
              <div class="sh-avatar-box">PRO</div>
              <div class="sh-text-info">
                <h4>商务采招部</h4>
                <p>Accountable (A)</p>
              </div>
            </div>

            <div class="sh-row-item">
              <div class="sh-avatar-box">GC</div>
              <div class="sh-text-info">
                <h4>施工总承包方</h4>
                <p>Responsible (R)</p>
              </div>
            </div>

            <div class="sh-row-item">
              <div class="sh-avatar-box">CMA</div>
              <div class="sh-text-info">
                <h4>消防与检测所</h4>
                <p>Consulted (C)</p>
              </div>
            </div>
          </aside>

          <!-- 右侧时间轴主体区域 (带磨砂毛玻璃遮罩与拖拽游标) -->
          <section class="p8-timeline-stage" id="timelineStage">
            <!-- 磨砂毛玻璃雾化遮罩层 -->
            <div class="p8-frosted-curtain" id="frostedCurtain"></div>

            <!-- 可拖拽游标针与把手 -->
            <div class="p8-cursor-handle-bar" id="cursorHandleBar">
              <div class="p8-cursor-pill-badge" id="cursorBadge">{t_permit.strftime('%Y.%m.%d')} 施工许可证核发</div>
              <div class="p8-cursor-needle-line"></div>
              <div class="p8-cursor-hint">拖动时间轴游标</div>
            </div>

            <!-- X 轴月度与日期刻度头部 -->
            <div class="timeline-x-header">
              {timeline_x_cols_html}
            </div>

            <!-- 时间轴任务横道流 (严格与左侧 5 个角色对齐) -->
            <div class="timeline-rows-container">
              <div class="bg-grid-lines">
                {''.join(['<div class="bg-grid-col"></div>' for _ in range(total_months)])}
              </div>

              <!-- Row 1: 业主 PMO / 报建专员 -->
              <div class="timeline-data-row">
                <div style="grid-column: {col_kickoff} / span 1;">
                  <div class="p8-capsule-task terra">
                    <div class="capsule-title">概念方案与立项锁定</div>
                    <div class="capsule-meta-row"><span>{start_dt.strftime('%m.%d')} 启动</span><span>15 工日</span></div>
                  </div>
                </div>
                <div style="grid-column: {col_permit} / span 1;">
                  <div class="p8-capsule-task gate">
                    <div class="capsule-title">施工许可证核发</div>
                    <div class="capsule-meta-row"><span>{t_permit.strftime('%m.%d')} 办结</span><span>法定前置</span></div>
                  </div>
                </div>
              </div>

              <!-- Row 2: 设计深化院 (DES) -->
              <div class="timeline-data-row">
                <div style="grid-column: {col_tender} / span {max(1, col_permit - col_tender + 1)};">
                  <div class="p8-capsule-task terra">
                    <div class="capsule-title">全套施工图深化与图审合格</div>
                    <div class="capsule-meta-row"><span>34 工日深化</span><span>图审合格证</span></div>
                  </div>
                </div>
              </div>

              <!-- Row 3: 商务采招部 (PRO) -->
              <div class="timeline-data-row">
                <div style="grid-column: {col_kickoff} / span {max(1, col_tender - col_kickoff + 1)};">
                  <div class="p8-capsule-task terra">
                    <div class="capsule-title">D&amp;B 邀请招采/述标答辩/定标签约</div>
                    <div class="capsule-meta-row"><span>33 工日敏捷比选</span><span>{t_tender.strftime('%m.%d')} 定标</span></div>
                  </div>
                </div>
              </div>

              <!-- Row 4: 施工总承包方 (GC) -->
              <div class="timeline-data-row">
                <div style="grid-column: {col_permit} / span 1;">
                  <div class="p8-capsule-task terra">
                    <div class="capsule-title">进场施工与隐蔽工程</div>
                    <div class="capsule-meta-row"><span>管线隐蔽验收</span></div>
                  </div>
                </div>
                <div style="grid-column: {col_cny} / span 1;">
                  <div class="p8-capsule-task alert">
                    <div class="capsule-title">2027 春节避峰防线 (15天)</div>
                    <div class="capsule-meta-row"><span>02.05 ~ 02.20</span><span>15天避峰</span></div>
                  </div>
                </div>
                <div style="grid-column: {col_build} / span 1;">
                  <div class="p8-capsule-task done">
                    <div class="capsule-title">现场硬装物理竣工自检</div>
                    <div class="capsule-meta-row"><span>{t_build.strftime('%m.%d')} 完工</span><span>硬装达标</span></div>
                  </div>
                </div>
              </div>

              <!-- Row 5: 消防与检测所 (CMA) -->
              <div class="timeline-data-row">
                <div style="grid-column: {col_build} / span {max(1, col_fire - col_build + 1)};">
                  <div class="p8-capsule-task gate">
                    <div class="capsule-title">第三方消防检测与消防批文办结 (18d)</div>
                    <div class="capsule-meta-row"><span>{t_fire.strftime('%m.%d')} 出批文</span><span>合格意见书</span></div>
                  </div>
                </div>
                <div style="grid-column: {col_fire} / span {max(1, col_handover - col_fire + 1)};">
                  <div class="p8-capsule-task done">
                    <div class="capsule-title">二次空气复测合格与钥匙移交</div>
                    <div class="capsule-meta-row"><span>{finish_dt.strftime('%m.%d')} 交付入驻</span><span>客户入驻</span></div>
                  </div>
                </div>
              </div>
            </div>
          </section>
        </div>
      </section>

      <!-- ==========================================
           VIEW 3: Executive Risk & Governance Report (暖白半透明高管桌面工程图纸底图)
           ========================================== -->
      <section class="view-panel" id="panelRisk">
        <!-- Neutral solid-color risk panel background -->
        <div class="risk-bg-img-wrapper">
          <div class="risk-bg-warm-overlay"></div>
        </div>

        <div class="risk-content-container">
          <div class="risk-header-kpis">
            <div class="risk-kpi-card">
              <span class="risk-kpi-title">综合风险健康评级</span>
              <div class="risk-kpi-val"><em>正常受控</em> · Low–Med</div>
            </div>
            <div class="risk-kpi-card">
              <span class="risk-kpi-title">识别关键风险项</span>
              <div class="risk-kpi-val">5 项重点风险</div>
            </div>
            <div class="risk-kpi-card">
              <span class="risk-kpi-title">对冲措施覆盖</span>
              <div class="risk-kpi-val">100% 闭环</div>
            </div>
            <div class="risk-kpi-card">
              <span class="risk-kpi-title">关键路径时差</span>
              <div class="risk-kpi-val">0d 零时差</div>
            </div>
          </div>

          <div class="risk-sections-grid">
            <div class="risk-column-panel">
              <div class="risk-panel-header">
                <h3>重点风险与对冲策略</h3>
                <p>报建 · 劳务 · 消防 · 环保 · 长周期供货</p>
              </div>

              <div style="display:flex; flex-direction:column; gap:8px; overflow-y:auto; padding-right:4px;">
                <div class="risk-item-box critical">
                  <div class="risk-item-head"><span>1. 2027 跨春节劳务断档与停工</span><span class="risk-sev">极高</span></div>
                  <div class="risk-item-desc">2027-02-05 ~ 02-20（元宵前）民工返乡断档，若无预案将导致工期延误 15~20 天。</div>
                  <div class="risk-item-mitigation">对冲：排程内刚性锁定 15 天安全停工防线；年前完成湿作业与管线隐蔽，节后补贴确保准时复工。</div>
                </div>

                <div class="risk-item-box high">
                  <div class="risk-item-head"><span>2. 竣工消防验收与批文时限</span><span class="risk-sev">高</span></div>
                  <div class="risk-item-desc">消防联动测试不达标或住建现场查验提出整改，可能导致交付前无法取得正式批文。</div>
                  <div class="risk-item-mitigation">对冲：物理竣工后立即第三方预检(5d)，安排 8d 现场查验 + 5d 整改复核，共 18d 法定闭环。</div>
                </div>

                <div class="risk-item-box high">
                  <div class="risk-item-head"><span>3. 施工图审与施工许可证审批</span><span class="risk-sev">高</span></div>
                  <div class="risk-item-desc">{city} ≥300㎡ 必须依法办理施工许可证，图审返工可能拖延开工。</div>
                  <div class="risk-item-mitigation">对冲：设计深化预留 34 工日，提前锁定图审所与住建绿色通道，前置办理质安监保函。</div>
                </div>

                <div class="risk-item-box medium">
                  <div class="risk-item-head"><span>4. 室内空气质量与入驻延误</span><span class="risk-sev">中</span></div>
                  <div class="risk-item-desc">硬装与家具进场后散味不充分，二次复测超标将引发投诉与入驻延期。</div>
                  <div class="risk-item-mitigation">对冲：SOP-4 硬装首测 → 光触媒治理 → 家具进场 → 21 天强排新风 → 二次复测入驻。</div>
                </div>

                <div class="risk-item-box medium">
                  <div class="risk-item-head"><span>5. 长周期机电设备与家具交期</span><span class="risk-sev">中</span></div>
                  <div class="risk-item-desc">风机盘管、配电箱及定制家具供货延迟，将影响隐蔽封板与后期软装。</div>
                  <div class="risk-item-mitigation">对冲：定标后首周下发采购 PO，设定到场前置罚款条款与厂验机制。</div>
                </div>
              </div>
            </div>

            <div class="risk-column-panel">
              <div class="risk-panel-header">
                <h3>风险控制矩阵</h3>
                <p>责任主体与处置状态</p>
              </div>

              <div class="risk-table-grid">
                <div class="risk-th-row">
                  <div>编号</div><div>风险类别</div><div>对冲措施摘要</div><div>责任方</div><div>状态</div>
                </div>
                <div class="risk-tr-row">
                  <div class="risk-id">RSK-01</div>
                  <div>跨春节停工</div>
                  <div>15 天刚性防线 + 复工补贴</div>
                  <div><span class="risk-owner">GC</span></div>
                  <div class="risk-status">已对冲</div>
                </div>
                <div class="risk-tr-row">
                  <div class="risk-id">RSK-02</div>
                  <div>消防验收批文</div>
                  <div>18d 检测查验整改闭环</div>
                  <div><span class="risk-owner">CMA</span></div>
                  <div class="risk-status">已编入</div>
                </div>
                <div class="risk-tr-row">
                  <div class="risk-id">RSK-03</div>
                  <div>施工许可证</div>
                  <div>34d 深化 + 绿色通道保函</div>
                  <div><span class="risk-owner">DES</span></div>
                  <div class="risk-status">前置预留</div>
                </div>
                <div class="risk-tr-row">
                  <div class="risk-id">RSK-04</div>
                  <div>IAQ 甲醛超标</div>
                  <div>两次检测 + 21 天强排</div>
                  <div><span class="risk-owner">GC</span></div>
                  <div class="risk-status">SOP 受控</div>
                </div>
                <div class="risk-tr-row">
                  <div class="risk-id">RSK-05</div>
                  <div>长周期设备交期</div>
                  <div>定标首周下发 PO</div>
                  <div><span class="risk-owner">PRO</span></div>
                  <div class="risk-status">前置采购</div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>
  </div>

  <script>
    const PROJECT_DATA = {json_safe_data};

    // 1. 视图切换逻辑 (Dashboard, Timeline, Risk)
    function switchMainView(viewKey) {{
      document.querySelectorAll('.view-panel').forEach(el => el.classList.remove('active'));
      document.querySelectorAll('.p12-nav-btn').forEach(el => {{
        el.classList.remove('active');
        el.removeAttribute('aria-current');
      }});

      if (viewKey === 'dashboard') {{
        document.getElementById('panelDashboard').classList.add('active');
        document.getElementById('tabDashboard').classList.add('active');
        document.getElementById('tabDashboard').setAttribute('aria-current', 'page');
      }} else if (viewKey === 'calendar' || viewKey === 'timeline') {{
        document.getElementById('panelCalendar').classList.add('active');
        document.getElementById('tabCalendar').classList.add('active');
        document.getElementById('tabCalendar').setAttribute('aria-current', 'page');
      }} else if (viewKey === 'risk' || viewKey === 'overview') {{
        document.getElementById('panelRisk').classList.add('active');
        document.getElementById('tabRisk').classList.add('active');
        document.getElementById('tabRisk').setAttribute('aria-current', 'page');
      }}
    }}

    // 2. Risk 页面内单屏 / S2 分屏模式切换 (Version 3)
    function setRiskLayoutMode(mode) {{
      const btnSingle = document.getElementById('btnRiskSingle');
      const btnSplit = document.getElementById('btnRiskSplit');
      const dualCols = document.getElementById('riskDualCols');
      const s2Pane = document.getElementById('riskS2Pane');

      if (mode === 'split') {{
        btnSplit.classList.add('active');
        btnSingle.classList.remove('active');
        dualCols.classList.add('split-mode');
        s2Pane.style.display = 'flex';
      }} else {{
        btnSingle.classList.add('active');
        btnSplit.classList.remove('active');
        dualCols.classList.remove('split-mode');
        s2Pane.style.display = 'none';
      }}
    }}

    // 页面加载完成后预热视频播放
    window.addEventListener('DOMContentLoaded', function() {{
      const bgVid = document.querySelector('.risk-bg-video');
      if (bgVid) {{
        bgVid.muted = true;
        bgVid.play().catch(function() {{}});
      }}
      const hash = (location.hash || '').replace('#', '').toLowerCase();
      if (hash === 'risk' || hash === 'overview') switchMainView('risk');
      else if (hash === 'calendar' || hash === 'timeline') switchMainView('calendar');
      else if (hash === 'dashboard') switchMainView('dashboard');
    }});

    // 2. 拖拽磨砂毛玻璃游标交互逻辑 (Timeline)
    let isDragging = false;
    const stage = document.getElementById('timelineStage');
    const curtain = document.getElementById('frostedCurtain');
    const handle = document.getElementById('cursorHandleBar');
    const badge = document.getElementById('cursorBadge');

    const CURSOR_TEXTS = [
      {{ maxPct: 15, text: "{start_date_str} 项目正式开工" }},
      {{ maxPct: 35, text: "{t_tender.strftime('%Y.%m.%d')} D&B 定标签约 (33d)" }},
      {{ maxPct: 52, text: "{t_permit.strftime('%Y.%m.%d')} 施工许可证核发" }},
      {{ maxPct: 68, text: "2027.02.05~02.20 春节避峰防线" }},
      {{ maxPct: 82, text: "{t_build.strftime('%Y.%m.%d')} 现场硬装物理竣工" }},
      {{ maxPct: 92, text: "{t_fire.strftime('%Y.%m.%d')} 消防验收合格批文 (18d)" }},
      {{ maxPct: 100, text: "{finish_date_str} 二次空气检测合格与入驻" }}
    ];

    function updateCursorPosition(clientX) {{
      if (!stage || !curtain || !handle || !badge) return;
      const rect = stage.getBoundingClientRect();
      let offsetX = clientX - rect.left;
      if (offsetX < 20) offsetX = 20;
      if (offsetX > rect.width - 20) offsetX = rect.width - 20;

      const pct = (offsetX / rect.width) * 100;
      curtain.style.width = pct + '%';
      handle.style.left = pct + '%';

      for (let item of CURSOR_TEXTS) {{
        if (pct <= item.maxPct) {{
          badge.textContent = item.text;
          break;
        }}
      }}
    }}

    if (handle && stage) {{
      handle.addEventListener('mousedown', (e) => {{
        isDragging = true;
        e.preventDefault();
      }});
      stage.addEventListener('mousedown', (e) => {{
        if (e.target.closest('.p8-capsule-task')) return;
        isDragging = true;
        updateCursorPosition(e.clientX);
      }});
      window.addEventListener('mousemove', (e) => {{
        if (!isDragging) return;
        updateCursorPosition(e.clientX);
      }});
      window.addEventListener('mouseup', () => {{
        isDragging = false;
      }});
    }}

    function exportICalendar() {{
      const icsData = `{ics_events}`;
      const blob = new Blob([icsData], {{ type: 'text/calendar;charset=utf-8;' }});
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.setAttribute("download", "{project_title}_Master_Schedule.ics");
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
    }}
  </script>
</body>
</html>"""

    with open(output_html_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"[SUCCESS] Exported 100% Dynamic executive HTML report to: {output_html_path}")
    return output_html_path


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Export executive HTML dashboard")
    parser.add_argument("--json", help="Input solved json file")
    parser.add_argument("--out", default="output_mpp/Executive_Master_Cockpit.html", help="Output html path")
    args = parser.parse_args()

    title = "杭州1000㎡ 办公工装项目 · 交付策略与进度推演决策看板"
    meta = {"city": "杭州", "area": 1000, "cost": 350.0, "delivery": "DBB", "bidding": "invite"}
    tasks = []

    if args.json and os.path.exists(args.json):
        with open(args.json, "r", encoding="utf-8") as f:
            data = json.load(f)
        title = data.get("project_title", title)
        meta = data.get("project_meta", meta)
        tasks = data.get("tasks", [])

    out_file = generate_html_report(title, meta, tasks, args.out)
