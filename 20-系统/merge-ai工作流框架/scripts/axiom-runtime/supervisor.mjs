import { spawn } from 'node:child_process';
import { mkdirSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { acquireLock, atomicJson, moduleURL, readJson, runtimeStamp } from './state.mjs';
const dir=process.env.AXIOM_RUNTIME_DIR;
mkdirSync(dir,{recursive:true,mode:0o700});
let release;
// 锁被真正的持有者占用时静默退出是对的；但其它原因（如 Windows 上目录改名报 EPERM）不能静默，
// 否则上层只能看到「UI 未就绪」，日志里没有任何线索。
try {release=acquireLock(join(dir,'ui.lock'));}
catch(err) {console.log(`[runtime] supervisor 未启动：${err.message}`);process.exit(0);}
const file=join(dir,'ui.json');
// 指纹固定为进程启动时的脚本状态，后续重启子进程不再刷新，否则旧进程会被误判为「新代码」。
const stamp=runtimeStamp(),bootedAt=Date.now();
console.log(`[runtime] supervisor pid=${process.pid} node=${process.version} cwd=${process.cwd()}`);
let child=null,stopping=false,delay=500;
const save=()=>atomicJson(file,{pid:process.pid,childPid:child?.pid??null,port:Number(process.env.AXIOM_UI_PORT),stamp,bootedAt,args:process.argv.slice(2)});
process.once('exit',()=>{if(readJson(file)?.pid===process.pid)rmSync(file,{force:true}); release();});
const stop=()=>{
 stopping=true;
 if(child) {child.kill('SIGTERM'); setTimeout(()=>{child?.kill('SIGKILL');process.exit(0);},5000).unref();}
 else process.exit(0);
};
for(const signal of ['SIGTERM','SIGINT'])process.once(signal,stop);
process.on('SIGHUP',()=>{});
while(!stopping) {
 const began=Date.now();
 const cli=process.env.AXIOM_RUNTIME_UI_ENTRY || fileURLToPath(new URL('../../node_modules/@axiom/cli/bin/axiom.js',import.meta.url));
 // --import 按 URL 解析 specifier；Windows 绝对路径（<WINDOWS_PATH>，必须传 file:// URL。
 const args=['--import',moduleURL(new URL('./register.mjs',import.meta.url)),cli,'ui','--port',process.env.AXIOM_UI_PORT,'--no-open',...process.argv.slice(2)];
 child=spawn(process.execPath,args,{cwd:process.cwd(),env:process.env,stdio:['ignore','inherit','inherit'],windowsHide:true});
 save();
 console.log(`[runtime] UI started pid=${child.pid} import=${args[1]}`);
 await new Promise(resolve=>{child.once('exit',resolve);child.once('error',err=>{console.error(err.message);resolve();});});
 child=null; save();
 if(stopping)break;
 delay=Date.now()-began>30000?500:Math.min(delay*2,15000);
 console.log(`[runtime] UI disconnected; restart in ${delay}ms (AI continues)`);
 await new Promise(resolve=>setTimeout(resolve,delay));
}
