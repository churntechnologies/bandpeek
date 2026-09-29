#!/usr/bin/env python3
"""Long-lived download fixture: retains process identity through terminal samples."""
import json,sys,time,urllib.request
start=time.monotonic();total=0;error=None
try:
    with urllib.request.urlopen(sys.argv[1],timeout=5) as response:
        while time.monotonic()-start<60:
            chunk=response.read(65536)
            if not chunk:break
            total+=len(chunk)
except Exception as e:error=str(e)
print(json.dumps({'pid':__import__('os').getpid(),'bytes':total,'seconds':time.monotonic()-start,'bytes_per_second':total/(time.monotonic()-start),'error':error}),flush=True)
time.sleep(15)
