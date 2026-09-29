#!/usr/bin/env python3
"""Compare nettop's own type classification against validated endpoints/routes.
Uses one controlled process per route and a combined external+loopback process.
Extra nettop observers exist only in this explicit experiment.
"""
import json, socket, subprocess, sys, threading, time
from m2_common import Nettop, OUT, save, active_route
CLIENT=r'''
import json,socket,sys,time,urllib.request
mode=sys.argv[1];results=[]
if mode in ('wifi','mixed','lan'):
    url='http://'+sys.argv[4]+'/' if mode=='lan' else 'https://speed.cloudflare.com/__down?bytes=1048576'
    try:
        with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'curl/8.7.1'}),timeout=12) as r:results.append({'url':url,'payload':len(r.read()),'status':r.status})
    except Exception as e:results.append({'url':url,'error':str(e)})
if mode in ('loopback','mixed','self-lan'):
    s=socket.create_connection((sys.argv[3],int(sys.argv[2])))
    s.sendall(b'x'*1048576);s.shutdown(socket.SHUT_WR)
    n=0
    while True:
        b=s.recv(65536)
        if not b:break
        n+=len(b)
    results.append({'local_upload':1048576,'local_download':n});s.close()
print(json.dumps(results),flush=True)
time.sleep(18)
'''
route=active_route()
listener=socket.socket();listener.bind(('0.0.0.0',0));listener.listen()
def serve():
    while True:
        try:c,_=listener.accept()
        except OSError:return
        with c:
            while c.recv(65536):pass
            c.sendall(b'y'*2097152)
threading.Thread(target=serve,daemon=True).start()
streams={scope:Nettop('interfaces-'+scope,extra=['-J','bytes_in,bytes_out']+([] if scope=='all' else ['-t',scope])) for scope in ('all','wifi','wired','loopback','undefined','external')}
flows=Nettop('interfaces-flows',summary=False)
children=[];records=[]
try:
    time.sleep(3)
    for mode in ('wifi','loopback','lan','mixed','self-lan'):
        p=subprocess.Popen([sys.executable,'-c',CLIENT,mode,str(listener.getsockname()[1]),route['address'] if mode=='self-lan' else '127.0.0.1',route['gateway']],stdout=subprocess.PIPE,text=True)
        children.append(p);records.append({'mode':mode,'pid':p.pid})
    for p,r in zip(children,records):r['outcome']=p.communicate(timeout=40)[0]
    time.sleep(3)
    for r in records:
        r['counters']={scope:{d:max((f['rows'].get(r['pid'],{}).get(d,0) for f in stream.frames),default=0) for d in ('download','upload')} for scope,stream in streams.items()}
    save('interfaces-results',{'route':route,'records':records,'notes':'Private flow CSV includes endpoint addresses; do not publish raw.'})
finally:
    for p in children:
        if p.poll() is None:p.terminate();p.wait()
    for s in list(streams.values())+[flows]:s.close()
    listener.close()
