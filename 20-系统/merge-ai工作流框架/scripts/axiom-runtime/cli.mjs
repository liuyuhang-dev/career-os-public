import { spawn } from 'node:child_process';
import { createServer } from 'node:net';
import { mkdirSync, openSync, closeSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { alive, readJson, runtimeDir, staleSupervisor, parsePort, parseOpencodePort } from './state.mjs';
import { healthyResponse, httpProbe, portProbe, runtimeReport, tail } from './diagnose.mjs';
import { transformSource } from './transform.mjs';
const port=parsePort(process.env.AXIOM_UI_PORT || undefined);
parseOpencodePort(process.env.AXIOM_OPENCODE_PORT);
const dir=process.env.AXIOM_RUNTIME_DIR || runtimeDir(process.cwd(),port);
const action=process.argv[2]||'start';
const url=`http://<IP>:${port}`;
const uiLog=join(dir,'ui.log'),aiLog=join(dir,'ai.log');
const state=()=>readJson(join(dir,'ui.json'));
const aiState=()=>readJson(join(dir,'ai.json'));
const health=()=>httpProbe(`${url}/api/opencode`);
const report=(lines=20)=>runtimeReport({dir,port,ui:state(),ai:aiState(),lines});
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const stopSupervisor=async pid=>{
 if(!alive(pid))return;
 process.kill(pid,'SIGTERM');
 for(let attempt=0;attempt<24&&alive(pid);attempt++)await sleep(250);
 if(alive(pid))process.kill(pid,'SIGKILL');
};
const waitPortFree=async(ms=5000)=>{
 const until=Date.now()+ms;
 for(;;) {
  try {
   await new Promise((resolve,reject)=>{const probe=createServer();probe.once('error',reject);probe.listen(port,'<IP>',()=>probe.close(resolve));});
   return;
  } catch {
   if(Date.now()>until)throw new Error(`端口 ${port} 已被占用；未接管或停止现有服务。请先查看 ui:status。`);
   await sleep(250);
  }
 }
};
const lineCount=(fallback=40)=>Number(process.argv.find(arg=>/^--lines=\d+$/.test(arg))?.split('=')[1]||fallback);
const summary=()=>{
 const ui=state(),ai=aiState();
 return [
  `运行目录：${dir}`,
  `supervisor pid=${ui?.pid??'-'}  UI pid=${ui?.childPid??'-'}  AI pid=${ai?.pid??'-'}  AI 端口=${ai?.port??'-'}`,
  `UI 日志：${uiLog}`,
  `AI 日志：${aiLog}`,
  `详情：npm run ui:logs（日志尾部） / npm run ui:status（端口探测）`,
 ].join('\n');
};
if(action==='status') {
 if(process.argv.includes('--json')) {
  const ui=state(),ai=aiState();
  console.log(JSON.stringify({url,supervisor:alive(ui?.pid),uiPid:ui?.childPid??null,ai:alive(ai?.pid),aiPort:ai?.port??null,directory:dir,uiLog,aiLog},null,2));
 } else console.log(await report());
} else if(action==='logs') {
 const count=lineCount();
 console.log(`运行目录：${dir}\n\n--- ui.log 最后 ${count} 行 ---\n${tail(uiLog,count)}\n\n--- ai.log 最后 ${count} 行 ---\n${tail(aiLog,count)}`);
} else if(action==='restart') {
 const ui=state();
 // 正常托管时只让 supervisor 换子进程，独立 AI 不受影响。
 if(alive(ui?.pid)&&alive(ui?.childPid)&&!staleSupervisor(ui)) {
  try {process.kill(ui.childPid,'SIGTERM');} catch {}
  let probe='';
  const until=Date.now()+45000;
  while(Date.now()<until) {probe=await health();if(healthyResponse(probe))break;await sleep(250);}
  if(!healthyResponse(probe)) {console.error(`UI 重启后不可用：${probe}\n\n${await report()}`);process.exit(1);}
  console.log(`UI 已重启：${url}（HTTP /api/opencode ${probe}）\n${summary()}`);
  process.exit(0);
 }
 // 未托管、崩溃循环或 supervisor 是旧代码：连同 supervisor 一起换掉。
 const args=Array.isArray(ui?.args)&&ui.args.length?ui.args:process.argv.slice(3);
 if(!alive(ui?.pid)&&!args.length)throw new Error('UI 未托管，请先 npm run ui');
 if(alive(ui?.pid)) {await stopSupervisor(ui.pid);console.log(`supervisor（pid ${ui.pid}）已停止，正在用当前代码重新启动……`);}
 await startFresh(args);
} else if(action==='stop' || action==='stop-all') {
 const ui=state(); if(alive(ui?.pid))await stopSupervisor(ui.pid);
 if(action==='stop-all') {const ai=aiState();if(alive(ai?.pid))process.kill(ai.pid,'SIGTERM');}
 console.log(action==='stop'?'UI 已停止；AI 保持运行。':'已停止 UI 和 AI。');
} else if(action==='start') {
 const ui=state();
 // 已有 supervisor 不等于 UI 可用：它可能正在重启或崩溃循环，必须实测端口再报成功。
 if(alive(ui?.pid)) {
  let probe=await health();
  // supervisor 可能正在按退避重启 UI，先给一个短暂宽限期再判定不可用。
  for(let attempt=0;attempt<6&&!healthyResponse(probe);attempt++) {
   await sleep(500);
   probe=await health();
  }
  if(healthyResponse(probe)) {console.log(`UI 已托管：${url}\n本地探测：<IP>:${port} ${await portProbe('<IP>',port)}，HTTP /api/opencode ${probe}\n${summary()}\n关闭终端不会停止任务。`);process.exit(0);}
  // 代码比运行中的 supervisor 新：它只会拿旧参数反复崩，直接换进程，不必让人记得 stop-all。
  if(staleSupervisor(ui)) {
   console.error(`supervisor（pid ${ui.pid}）启动于本次代码更新之前，且 UI 端口不可用：${probe}\n正在停止旧进程并用当前代码重新启动……`);
   await stopSupervisor(ui.pid);
  } else {
   console.error(`supervisor 在运行（pid ${ui.pid}），但 UI 端口不可用：${probe}\n可能是 UI 正在重启或崩溃循环，未重复启动；可先 npm run ui:restart，仍失败则 npm run ui:stop-all 后重新 npm run ui。\n\n${await report()}`);
   process.exit(1);
  }
 }
 await startFresh();
} else throw new Error('用法：start | status | logs | restart | stop | stop-all');

async function startFresh(extra) {
 // Validate upstream contracts before creating any detached process.
 for(const name of ['ui.js','opencode-chat.js']) {
  const path=new URL(`../../node_modules/@axiom/cli/lib/${name}`,import.meta.url);
  transformSource(name,readFileSync(path,'utf8'),new URL('./server.mjs',import.meta.url).href);
 }
 // 刚停掉的 supervisor 可能还在收尾，给端口一点释放时间再判定占用。
 await waitPortFree();
 mkdirSync(dir,{recursive:true,mode:0o700});
 const fd=openSync(uiLog,'a',0o600);
 let supervisorPid=null;
 try {
  const child=spawn(process.execPath,[fileURLToPath(new URL('./supervisor.mjs',import.meta.url)),...(Array.isArray(extra)?extra:process.argv.slice(3))],{
   cwd:process.cwd(),env:{...process.env,AXIOM_UI_PORT:String(port),AXIOM_UI_URL:`http://<IP>:${port}`,AXIOM_RUNTIME_DIR:dir},detached:true,stdio:['ignore',fd,fd],windowsHide:true,
  });child.unref();
  supervisorPid=child.pid;
 } finally {closeSync(fd);}
 // 先确认 supervisor 真的接管了：它可能因启动锁被占用（旧进程强杀后残留）而静默退出，
 // 此时等 45 秒只会得到「未就绪」，必须立刻给出原因。
 const bootUntil=Date.now()+6000;
 while(Date.now()<bootUntil&&state()?.pid!==supervisorPid)await sleep(200);
 if(state()?.pid!==supervisorPid) {
  console.error(`supervisor（pid ${supervisorPid??'-'}）未接管运行目录，进程应已退出；\n常见原因是启动锁被旧进程残留占用（Windows 强杀不会清理 ui.lock）。\n\n${await report()}`);
  process.exit(1);
 }
 const until=Date.now()+45000;
 let probe='';
 while(Date.now()<until) {
  probe=await health();
  if(healthyResponse(probe))break;
  await new Promise(resolve=>setTimeout(resolve,250));
 }
 if(!healthyResponse(probe)) {
  console.error(`UI 未就绪（45 秒超时）：${probe}；supervisor 仍在后台重试。\n\n${await report()}`);
  process.exit(1);
 }
 // 3 秒稳定性观察：连续两次探测失败就视为「起来就崩」，再给 5 秒恢复窗口，避免只报成功或误报。
 let failures=0;
 for(let attempt=0;attempt<6;attempt++) {
  await new Promise(resolve=>setTimeout(resolve,500));
  probe=await health();
  if(healthyResponse(probe)) {failures=0;continue;}
  if(++failures>=2)break;
 }
 if(failures>=2) {
  for(let attempt=0;attempt<10&&!healthyResponse(probe);attempt++) {
   await new Promise(resolve=>setTimeout(resolve,500));
   probe=await health();
  }
  if(!healthyResponse(probe)) {
   console.error(`UI 启动后立即不可用：${probe}\n\n${await report()}`);
   process.exit(1);
  }
 }
 console.log(`UI 已托管：${url}\n本地探测：<IP>:${port} ${await portProbe('<IP>',port)}，HTTP /api/opencode ${probe}\n${summary()}\n浏览器打不开时优先用 ${url}（避开 localhost 的 IPv6 解析与系统代理）。\n关闭终端不会停止任务。`);
}
