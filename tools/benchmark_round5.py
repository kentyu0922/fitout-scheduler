import subprocess,sys,json,time,statistics
from pathlib import Path
import argparse
ap=argparse.ArgumentParser()
ap.add_argument('--baseline',required=True,help='Extracted f1571ea source tree')
ap.add_argument('--round4',required=True,help='Frozen Round4 candidate tree')
ap.add_argument('--candidate',default=str(Path(__file__).resolve().parents[1]))
ap.add_argument('--output',required=True)
ap.add_argument('--repeats',type=int,default=5)
a=ap.parse_args()
if a.repeats<5:ap.error('at least five repeats required')
baseout=Path(a.output).resolve();baseout.mkdir(parents=True,exist_ok=True)
rows=[]
for version,root in [('f1571ea',Path(a.baseline).resolve()),('round4',Path(a.round4).resolve()),('round5',Path(a.candidate).resolve())]:
 for case in ('base','all22'):
  for mode in ('default_run_contract','lean_main_mpp_only'):
   times=[];counts=[]
   for n in range(a.repeats):
    out=baseout/version/case/mode/str(n);out.mkdir(parents=True,exist_ok=True)
    entry='tools/run_contract.py' if mode.startswith('default') else 'main.py'
    cmd=[sys.executable,str(root/entry),'--city','上海','--area','6000','--sector','finance','--delivery','DB','--bidding','invite','--start_date','2027-03-01','--output',str(out/'p.mpp'),'--no_mpp']
    if mode.startswith('lean'):cmd+=['--mpp_only']
    if case=='all22':
     for i in range(1,23):cmd += [f'--addon_a{i:02}','required']
    t=time.perf_counter();r=subprocess.run(cmd,capture_output=True,text=True);times.append(time.perf_counter()-t)
    (out/'run.log').write_text(r.stdout+r.stderr)
    if r.returncode:print('FAIL',version,case,mode,r.returncode,(r.stdout+r.stderr)[-1000:],flush=True);break
    counts.append(len([p for p in out.rglob('*') if p.is_file() and p.name!='run.log']))
   row=dict(version=version,case=case,mode=mode,seconds=times,median=statistics.median(times),returncode=r.returncode,files=counts);rows.append(row);print(row,flush=True)
   (baseout/'benchmark.json').write_text(json.dumps(rows,indent=2))
