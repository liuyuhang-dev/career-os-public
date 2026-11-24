import { connect } from 'node:net';
import { readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { alive, lockOwners } from './state.mjs';

// 启动失败时最有价值的信息是日志尾部和端口探测结果，避免只说「未就绪」。
export function tail(file,count=20) {
  let text;
  try {text=readFileSync(file,'utf8');} catch(err) {return `（读不到日志：${err.code||err.message}）`;}
  if(!text.trim())return '（暂无内容）';
  return text.trimEnd().split('\n').slice(-count).join('\n');
}
export function logInfo(file) {
  try {const info=statSync(file);return `${file}（${(info.size/1024).toFixed(1)} KB，更新于 ${new Date(info.mtimeMs).toLocaleString()}）`;}
  catch {return `${file}（尚未创建）`;}
}
export function portProbe(host,port,timeout=800) {
  return new Promise(resolve=>{
    const socket=connect({host,port});
    const done=value=>{socket.destroy();resolve(value);};
    socket.setTimeout(timeout);
    socket.once('connect',()=>done('可连接'));
    socket.once('timeout',()=>done('超时'));
    socket.once('error',err=>done(err.code||err.message));
  });
}
export async function httpProbe(url,timeout=2000) {
  try {const response=await fetch(url,{signal:AbortSignal.timeout(timeout)});return `HTTP ${response.status}`;}
  catch(err) {return err.cause?.code||err.name||err.message;}
}
export const healthyResponse = text => /^HTTP 2\d\d$/.test(String(text));
// 中文按两列宽计算，保持诊断表的列对齐。
const columns=text=>[...String(text)].reduce((sum,char)=>/[\u2e80-\u9fff\uf900-\ufaff\uff00-\uff60\uffe0-\uffe6]/.test(char)?sum+2:sum+1,0);
const pad=(text,width)=>text+' '.repeat(Math.max(0,width-columns(text)));

export async function runtimeReport({dir,port,ui,ai,lines=20}) {
  const pid=value=>value?`pid ${value}（${alive(value)?'存活':'已退出'}）`:'未记录';
  const rows=[
    ['UI 端口',`${port}（http://<IP>:${port} / http://localhost:${port}）`],
    ['supervisor',pid(ui?.pid)],
    ['UI 子进程',pid(ui?.childPid)],
    // 残留的启动锁会让新 supervisor 直接退出，是「UI 未就绪但日志没线索」的常见原因。
    ['启动锁',lockOwners(join(dir,'ui.lock')).map(owner=>`pid ${owner.pid??'-'}（${owner.alive?'存活':'已退出'}）`).join('、')||'无'],
    ['AI daemon',pid(ai?.pid)],
    ['AI 端口',ai?.port?String(ai.port):'未就绪'],
    ['探测 <IP>',await portProbe('<IP>',port)],
    ['探测 ::1',await portProbe('::1',port)],
    ['HTTP /api/opencode',await httpProbe(`http://<IP>:${port}/api/opencode`)],
    ['运行目录',dir],
    ['UI 日志',logInfo(join(dir,'ui.log'))],
    ['AI 日志',logInfo(join(dir,'ai.log'))],
  ];
  const width=Math.max(...rows.map(([name])=>columns(name)));
  return [
    ...rows.map(([name,value])=>`${pad(name,width)}  ${value}`),
    '',
    `--- ui.log 最后 ${lines} 行 ---`,
    tail(join(dir,'ui.log'),lines),
    '',
    `--- ai.log 最后 ${lines} 行 ---`,
    tail(join(dir,'ai.log'),lines),
  ].join('\n');
}
