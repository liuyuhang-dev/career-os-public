import { spawn } from 'node:child_process';
import { mkdirSync, openSync, closeSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { alive, atomicJson, moduleURL, readJson, runtimeDir, parsePort } from './state.mjs';

export async function healthy(state) {
  if(!state?.url || !state?.password || !alive(state.pid))return false;
  try {
    const response=await fetch(`${state.url}/api/info`,{
      headers:{Authorization:'Basic '+Buffer.from(`opencode:${state.password}`).toString('base64')},signal:AbortSignal.timeout(1500),
    });
    return response.ok;
  } catch {return false;}
}
export async function connectDaemon({cwd=process.cwd(),env={}}={}) {
  const port=parsePort(env.AXIOM_UI_PORT ?? process.env.AXIOM_UI_PORT);
  const dir=process.env.AXIOM_RUNTIME_DIR || runtimeDir(cwd,port);
  mkdirSync(dir,{recursive:true,mode:0o700});
  const file=join(dir,'ai.json');
  let state=readJson(file);
  if(await healthy(state))return state;
  // A live daemon owns its OpenCode process even while the health request is transiently failing.
  if(!alive(state?.pid)) {
    const fd=openSync(join(dir,'ai.log'),'a',0o600);
    try {
      // 经项目 preload 启动：daemon 里的 opencode-server 需要被适配层改写（AXIOM_OPENCODE_PORT）。
      // --import 按 URL 解析 specifier；Windows 绝对路径（<WINDOWS_PATH>，必须传 file:// URL。
      const child=spawn(process.execPath,['--import',moduleURL(new URL('./register.mjs',import.meta.url)),fileURLToPath(new URL('./daemon.mjs',import.meta.url))],{
        cwd,env:{...process.env,...env,AXIOM_RUNTIME_DIR:dir,AXIOM_UI_PORT:String(port)},
        detached:true,stdio:['ignore',fd,fd],windowsHide:true,
      });
      child.unref();
    } finally {closeSync(fd);}
  }
  const until=Date.now()+45000;
  while(Date.now()<until) {
    state=readJson(file);
    if(await healthy(state))return state;
    await new Promise(resolve=>setTimeout(resolve,250));
  }
  throw new Error(`独立 AI 服务未就绪，请检查 ${join(dir,'ai.log')}`);
}
export async function startOpencodeServer({cwd=process.cwd(),env={},onExit,log=()=>{}}={}) {
  try {
    const state=await connectDaemon({cwd,env});
    log('[opencode] 已连接独立 AI 服务（UI 退出时继续执行）');
    let failures=0,checking=false;
    const timer=setInterval(async()=>{
      if(checking)return;
      checking=true;
      try {
        failures=await healthy(state)?0:failures+1;
        if(failures>=3) {clearInterval(timer); onExit?.(new Error('独立 AI 服务暂不可用，正在重新连接'));}
      } finally {checking=false;}
    },2000);
    timer.unref();
    return {...state,close(){clearInterval(timer);}};
  } catch(error) {return {url:null,password:null,error:error.message,close(){}};}
}
