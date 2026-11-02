import sys,shutil,time,json,subprocess,statistics,argparse
from pathlib import Path
ap=argparse.ArgumentParser();ap.add_argument('--output',required=True);ap.add_argument('--repeats',type=int,default=5);ap.add_argument('--sector',default='finance');a=ap.parse_args()
if a.repeats<5:ap.error('at least five repeats required')
source=Path(__file__).resolve().parents[1];sys.path.insert(0,str(source));from tools.build_pack import files
rows=[];base=Path(a.output).resolve();base.mkdir(exist_ok=True)
for n in range(a.repeats):
 root=base/str(n)/'office-programme';root.mkdir(parents=True)
 for rel in files('full'):
  dst=root/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source/rel,dst)
 out=base/str(n)/'out';out.mkdir()
 cmd=[sys.executable,str(root/'tools/run_contract.py'),'--city','上海','--area','6000','--sector',a.sector,'--delivery','DB','--bidding','invite','--start_date','2027-03-01','--output',str(out/'p.mpp'),'--no_mpp']
 for i in range(1,23):cmd += [f'--addon_a{i:02}','required']
 started=time.perf_counter();r=subprocess.run(cmd,capture_output=True,text=True);seconds=time.perf_counter()-started
 (out/'run.log').write_text(r.stdout+r.stderr)
 row=dict(run=n,sector=a.sector,seconds=seconds,returncode=r.returncode,files=len([p for p in out.rglob('*') if p.is_file() and p.name!='run.log']))
 rows.append(row);print(row,flush=True);(base/'cold_benchmark.json').write_text(json.dumps(rows,indent=2))
