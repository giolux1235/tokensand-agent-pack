"""Black-box a published or candidate checker under realistic editor saves."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import signal
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time

parser=argparse.ArgumentParser()
parser.add_argument("archive",type=Path)

parser.add_argument("--node",default=shutil.which("node"))
args=parser.parse_args()
archive=args.archive.resolve()
node=Path(args.node).resolve()
assert node.is_file()

correct="export const enqueue = ids => [...new Set(ids)];\n"
duplicate="export const enqueue = ids => [...ids];\n"
syntax="export const enqueue = ids => ;\n"
bad_import="export const enqueueItems = ids => [...new Set(ids)];\n"
adapter="""import {enqueue} from './src/queue.mjs';
export default {cases:[{id:'first-delivery',input:['a']},{id:'retry-delivery',input:['a','a']}],checks:[{id:'once',label:'Process each delivery once'}],maxCallsPerRun:{candidate:0},candidate:enqueue,evaluate:({input,output})=>({once:JSON.stringify(output)===JSON.stringify([...new Set(input)])})};"""
def exercise(scenario):
    mode,mutation,code,expected=scenario
    with tempfile.TemporaryDirectory(prefix="tokensand-save-repair-") as tmp:
        root=Path(tmp)
        main=root/"app"
        main.mkdir()
        (main/"src").mkdir()
        (main/"package.json").write_text('{"private":true}')
        (main/"observe.workflow.mjs").write_text(adapter)
        source=main/"src/queue.mjs"
        source.write_text(correct)
        with tarfile.open(archive) as tar:
            tar.extractall(root,filter="data")
        home=root/"home"
        home.mkdir()
        env={"PATH":str(node.parent)+os.pathsep+os.environ["PATH"],"HOME":str(home),
             "npm_config_userconfig":"/dev/null","npm_config_offline":"true","npm_config_update_notifier":"false","NO_COLOR":"1"}
        env.update({k:os.environ[k] for k in ["SYSTEMROOT","SystemDrive","COMSPEC","PATHEXT"] if k in os.environ})
        env.update(USERPROFILE=str(home),TEMP=str(root),TMP=str(root))
        cli = next(path for path in [node.parent/"node_modules/npm/bin/npm-cli.js",node.parent.parent/"lib/node_modules/npm/bin/npm-cli.js"] if path.is_file())
        command=[str(node),str(root/"package/cli.mjs"),"--install-test"]
        subprocess.run(command,cwd=main,env=env,capture_output=True,timeout=30,check=True)
        proc=subprocess.Popen([str(node),str(cli),"run","test:tokensand:watch"],cwd=main,env=env,
              stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,start_new_session=os.name != "nt",bufsize=1,encoding="utf-8",errors="replace")
        q=queue.Queue()
        def collect():
            for line in proc.stdout:
                q.put((time.monotonic(),line))
        threading.Thread(target=collect,daemon=True).start()
        def receive(start,pattern,timeout):
            end=time.monotonic()+timeout
            sample=[]
            while time.monotonic()<end:
                try: at,line=q.get(timeout=.10)
                except queue.Empty: continue
                if at<start: continue
                sample.append(line.strip())
                if re.search(pattern,line,re.I):
                    return round((at-start)*1000),sample
            return None,sample
        def write(content):
            if mode=="atomic":
                stage=source.with_name(".queue-edit.tmp")
                stage.write_text(content)
                stage.replace(source)
            else: source.write_text(content)
        try:
            ready=receive(time.monotonic(),r"(?:✔|ok)\s+(?:\d+ - )?retry-delivery",7)
            assert ready[0] is not None,ready
            time.sleep(.3)
            start=time.monotonic()
            write(code)
            first=receive(start,expected,7)
            assert first[0] is not None,(scenario,first)
            time.sleep(.3)
            start=time.monotonic()
            write(correct)
            second=receive(start,r"(?:✔|ok)\s+(?:\d+ - )?retry-delivery",6)
            return {"saveStyle":mode,"initialFault":mutation,"faultDetectedMs":first[0],"repairDetectedMs":second[0],
                "timeoutSeconds":6,"watchExited":proc.poll() is not None,"trailingRepairOutput":second[1][-5:]}
        finally:
            if proc.poll() is None:
                if os.name=="nt":
                    subprocess.run(["taskkill","/T","/F","/PID",str(proc.pid)],capture_output=True)
                else: os.killpg(proc.pid,signal.SIGTERM)
            proc.wait(timeout=5)
scenarios=[(mode,label,code,pattern) for mode in ["in-place","atomic"] for label,code,pattern in [
      ("score regression",duplicate,r"(?:✖|not ok|FAIL)\s+(?:\d+ - )?retry-delivery"),
      ("syntax error",syntax,r"Unexpected token|SyntaxError"),
      ("renamed export",bad_import,r"does not provide an export")]]
with ThreadPoolExecutor(max_workers=2) as pool:
    trials=list(pool.map(exercise,scenarios))
result={"archive":archive.name,"sha256":hashlib.sha256(archive.read_bytes()).hexdigest(),
        "node":subprocess.check_output([str(node),"--version"],text=True).strip(),"environment":"Synthetic workflow with zero provider budget and an isolated home",
        "repairWatchDetected":sum(t["repairDetectedMs"] is not None for t in trials),"trials":trials}
print(json.dumps(result,indent=2))
if os.environ.get("GITHUB_STEP_SUMMARY"):
    with open(os.environ["GITHUB_STEP_SUMMARY"],"a",encoding="utf-8") as summary:
        print(f"Verified {result['archive']} on {result['node']}. Detected {result['repairWatchDetected']} of {len(trials)} repairs.",file=summary)
        for trial in trials:
            detail = f"rechecked in {trial['repairDetectedMs']} ms" if trial["repairDetectedMs"] is not None else "missed repair"
            print(f"- {trial['saveStyle']}, {trial['initialFault']}: {detail}",file=summary)
raise SystemExit(0 if result["repairWatchDetected"]==len(trials) else 1)
