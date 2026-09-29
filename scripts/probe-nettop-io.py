#!/usr/bin/env python3
"""Diagnose nettop I/O wakeups; intentionally separate from the app."""
import os,pty,subprocess,threading,time,json
children=[];files=[];readers=[]
def drain(file):
 try:
  while os.read(file,65536):pass
 except OSError:pass
def stat(pid):
 text=subprocess.check_output(['ps','-p',str(pid),'-o','time=,rss='],text=True).split()
 m,s=text[0].split(':');return float(m)*60+float(s),int(text[1])
for stdin_mode,gentle in [('null',False),('pipe',False),('null',True),('pipe',True)]:
 master,slave=pty.openpty();files.append(master)
 args=['/usr/bin/nettop','-P','-L','0','-s','3','-n','-x','-J','bytes_in,bytes_out']+(['-c'] if gentle else [])
 child=subprocess.Popen(args,stdin=subprocess.DEVNULL if stdin_mode=='null' else subprocess.PIPE,stdout=slave,stderr=subprocess.PIPE)
 os.close(slave);children.append((stdin_mode,gentle,child))
 reader=threading.Thread(target=drain,args=(master,),daemon=True);reader.start();readers.append(reader)
time.sleep(3);start=time.monotonic();before={p.pid:stat(p.pid) for _,_,p in children}
time.sleep(20);elapsed=time.monotonic()-start
result=[]
for mode,gentle,p in children:
 end=stat(p.pid);result.append({'stdin':mode,'gentle':gentle,'cpu_percent':100*(end[0]-before[p.pid][0])/elapsed,'rss_mib':end[1]/1024})
for _,_,p in children:p.terminate();p.wait()
for fd in files:os.close(fd)
print(json.dumps(result,indent=2))
open('.validation/nettop-io-experiment.json','w').write(json.dumps(result,indent=2))
