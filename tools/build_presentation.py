import argparse,json,re
from pathlib import Path
from datetime import date
from tools.build_showcase import build_data
ROOT=Path(__file__).resolve().parents[1]
def main():
 p=argparse.ArgumentParser();p.add_argument('--cases',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();d=build_data();cases=[]
 for sector,title,scope,names in [('biopharma','生物医药办公室','3,600㎡ · 设计施工分开 · 邀请｜机房＋安防',['机房基础及机柜','天花隐蔽验收','门禁与安防安装','机房综合测试','安防与消防联动测试']),('semiconductor','半导体办公室','5,200㎡ · 设计施工一体 · 邀请｜机房＋声学',['声学围护','声学隐蔽验收','天花隐蔽验收','声学性能测试','机房综合测试'])]:
  data=json.loads((a.cases/sector/'programme_schedule.json').read_text(encoding='utf-8'));facts=json.loads((a.cases/sector/'input.json').read_text(encoding='utf-8'));scope=f"{facts['area_sqm']:,}㎡ · {'设计施工分开' if facts['delivery']=='DBB' else '设计施工一体'} · {'邀请' if facts['bidding']=='invite' else '公开'}｜{sum(facts.get('addon_'+k.lower())=='required' for k in d['addons'])}项专项";tasks=[t for t in data['tasks'] if t.get('client_visible')];chosen=[]
  for name in names:
   t=next((t for t in tasks if t.get('client_task_name',t['name'])==name),None)
   if t:chosen.append(t)
  if len(chosen)<5:
   extra=[t for t in tasks if not t.get('summary') and t.get('master_phase')=='实体施工' and any(x in t.get('client_task_name',t['name']) for x in ('隐蔽检查','性能','验证','接入','就位')) and t not in chosen]
   chosen=(chosen+extra)[:5]
  start=min(date.fromisoformat(t['start']) for t in chosen);end=max(date.fromisoformat(t['finish']) for t in chosen);span=(end-start).days+1
  cases.append(dict(title=title.replace('办公室','办公及专项'),scope=scope,rows=len(tasks),construction=sum(t.get('master_phase')=='实体施工' and not t.get('summary') for t in tasks),file=title+'_精简版.mpp',tasks=[dict(name=t.get('client_task_name',t['name']),left=round((date.fromisoformat(t['start'])-start).days/span*100,2),width=round(((date.fromisoformat(t['finish'])-date.fromisoformat(t['start'])).days+1)/span*100,2),special=t.get('module_id') in d['addons'] or any(x in t['name'] for x in ['声学','机房','门禁','安防'])) for t in chosen]))
 data=dict(cases=cases,categories={k:[d['addons'][x]['label'] for x in xs] for k,xs in d['categories'].items()});a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text((ROOT/'assets/presentation.html').read_text(encoding='utf-8-sig').replace('__PRESENTATION_DATA__',json.dumps(data,ensure_ascii=False).replace('</','<\\/')),encoding='utf-8');print(a.output)
if __name__=='__main__':main()
