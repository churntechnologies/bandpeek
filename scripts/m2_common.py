"""Private evidence helpers for opt-in macOS Milestone 2 tests."""
import json, os, pathlib, pty, subprocess, threading, time
ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / '.validation' / 'milestone2'
OUT.mkdir(parents=True, exist_ok=True)
PROBE = ROOT / 'src-tauri/target/release/bandpeek-probe'
class Nettop:
    def __init__(self, name, interval=1, extra=(), summary=True):
        self.lines=[]; self.frames=[]; self.frame=None
        self.file=(OUT/f'{name}.csv').open('w')
        self.master,slave=pty.openpty()
        args=['/usr/bin/nettop','-L','0','-s',str(interval),'-n','-x']
        if summary: args+=['-P']
        args+=list(extra)
        self.proc=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=slave,stderr=subprocess.PIPE)
        os.close(slave)
        self.thread=threading.Thread(target=self.read,daemon=True);self.thread.start()
    def read(self):
        buf=b''
        while True:
            try: chunk=os.read(self.master,65536)
            except OSError: break
            if not chunk: break
            buf+=chunk
            while b'\n' in buf:
                line,buf=buf.split(b'\n',1);line=line.decode(errors='replace').rstrip('\r')
                at=time.monotonic();self.lines.append((at,line));self.file.write(line+'\n');self.file.flush()
                if line==',bytes_in,bytes_out,':
                    if self.frame is not None:self.frames.append(self.frame)
                    self.frame={'at':at,'rows':{}}
                elif self.frame is not None:
                    try:
                        label,down,up=line.rstrip(',').rsplit(',',2);name,pid=label.rsplit('.',1)
                        self.frame['rows'][int(pid)]={'download':int(down),'upload':int(up)}
                    except ValueError:pass
    def close(self):
        self.proc.terminate();self.proc.wait(timeout=10);self.thread.join(timeout=2)
        os.close(self.master);self.file.close()

def snapshots(path):
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]

def save(name, result):
    (OUT/f'{name}.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2),flush=True)


def active_route():
    """Read actual route evidence; callers must separately verify hardware type."""
    text=subprocess.check_output(['route','-n','get','default'],text=True)
    fields=dict(line.strip().split(':',1) for line in text.splitlines() if ':' in line)
    device=fields['interface'].strip();gateway=fields['gateway'].strip()
    address=subprocess.check_output(['ipconfig','getifaddr',device],text=True).strip()
    return {'device':device,'gateway':gateway,'address':address}
