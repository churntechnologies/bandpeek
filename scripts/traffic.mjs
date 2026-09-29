// Synthetic loopback upload/download plus one public HTTPS download. No private data.
import net from 'node:net';
import https from 'node:https';
import { once } from 'node:events';
const sleep = ms => new Promise(r => setTimeout(r, ms));
if (process.argv[2] === 'server') {
  const server = net.createServer(socket => {
    let received = 0;
    socket.on('data', data => { received += data.length; if (received === 2 * 1024 * 1024) socket.end(Buffer.alloc(4 * 1024 * 1024, 98)); });
  });
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  console.log(JSON.stringify({pid:process.pid, port:server.address().port}));
  await sleep(45000); server.close();
} else {
  const socket = net.connect(Number(process.argv[2]), '127.0.0.1');
  await once(socket, 'connect'); let received = 0;
  socket.on('data', data => received += data.length);
  const done = once(socket, 'end');
  for (let i = 0; i < 32; i++) { socket.write(Buffer.alloc(65536, 97)); await sleep(100); }
  await done;
  console.log(JSON.stringify({pid:process.pid, loopback_upload:2097152, loopback_download:received}));
  try {
    const downloaded = await new Promise((resolve, reject) => {
      const request = https.get('https://speed.cloudflare.com/__down?bytes=1048576', response => {
        if (response.statusCode !== 200) { response.resume(); reject(new Error(`HTTP ${response.statusCode}`)); return; }
        let size = 0; response.on('data', b => size += b.length); response.on('end', () => resolve(size)); response.on('error', reject);
      });
      request.setTimeout(20000, () => request.destroy(new Error('timeout'))); request.on('error', reject);
    });
    console.log(JSON.stringify({https_download:downloaded}));
  } catch (error) { console.log(JSON.stringify({https_error:String(error)})); }
  // Keep the process alive to measure terminal counters independently of PID exit.
  await sleep(18000);
}
