import { mkdirSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { acquireLock, atomicJson, moduleURL, readJson } from './state.mjs';
const dir=process.env.AXIOM_RUNTIME_DIR;
if(!dir)throw new Error('缺少 AXIOM_RUNTIME_DIR');
mkdirSync(dir,{recursive:true,mode:0o700});
let release;
try {release=acquireLock(join(dir,'ai.lock'));} catch {process.exit(0);}
const file=join(dir,'ai.json');
// Write ownership before booting: concurrent clients wait for this owner instead of spawning a second daemon.
atomicJson(file,{pid:process.pid});
process.once('exit',()=>{if(readJson(file)?.pid===process.pid)rmSync(file,{force:true}); release();});
// 覆盖入口既允许 file:// URL，也允许 Windows 绝对路径；后者直接 import 会被当成协议 d:。
const entryURL=process.env.AXIOM_RUNTIME_SERVER_MODULE
  ? moduleURL(process.env.AXIOM_RUNTIME_SERVER_MODULE)
  : new URL('../../node_modules/@axiom/cli/lib/opencode-server.js',import.meta.url).href;
const {startOpencodeServer}=await import(entryURL);
let server=null,stopping=false,delay=500;
for(const signal of ['SIGTERM','SIGINT'])process.once(signal,()=>{stopping=true;server?.close();process.exit(0);});
while(!stopping) {
 let ended;
 const exit=new Promise(resolve=>{ended=resolve;});
 server=await startOpencodeServer({cwd:process.cwd(),env:{AXIOM_UI_PORT:process.env.AXIOM_UI_PORT,AXIOM_UI_URL:process.env.AXIOM_UI_URL,AXIOM_RUNTIME_DIR:dir},log:console.log,onExit:ended});
 if(server.url) {
  atomicJson(file,{pid:process.pid,url:server.url,password:server.password,port:server.port,startedAt:Date.now()});
  const began=Date.now();
  await exit;
  server.close();
  delay=Date.now()-began>30000?500:Math.min(delay*2,15000);
 } else {console.error(server.error || 'AI 启动失败');delay=Math.min(delay*2,15000);}
 atomicJson(file,{pid:process.pid});
 if(!stopping)await new Promise(resolve=>setTimeout(resolve,delay));
}
