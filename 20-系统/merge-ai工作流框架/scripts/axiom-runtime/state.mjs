import { createHash } from 'node:crypto';
import { mkdirSync, readFileSync, writeFileSync, renameSync, openSync, closeSync, fsyncSync, rmSync, statSync, linkSync, unlinkSync, readdirSync, rmdirSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

// import()/--import 只接受 URL：Windows 绝对路径（<WINDOWS_PATH>。
export function moduleURL(value) {
  const text=String(value);
  if (/^[a-z]:[\\/]/i.test(text)) return pathToFileURL(text).href;
  if (/^[a-z][a-z0-9+.-]*:/i.test(text)) return text;
  return pathToFileURL(text).href;
}

// supervisor 只在启动时读一遍脚本：代码更新后旧进程仍会用旧参数拉子进程（例如 Windows 上 --import 传
// 绝对路径会被当成 d: 协议，崩溃循环）。记录脚本指纹，让新代码能识别并替换掉旧进程。
const RUNTIME_FILES=['cli.mjs','supervisor.mjs','server.mjs','daemon.mjs','state.mjs','diagnose.mjs','transform.mjs','register.mjs','loader.mjs'];
export function runtimeStamp() {
  return Math.round(Math.max(0,...RUNTIME_FILES.map(name=>{try {return statSync(new URL(`./${name}`,import.meta.url)).mtimeMs;} catch {return 0;}})));
}
// 没有指纹（旧版本写入）也算过期：旧进程只会重复旧行为。
export function staleSupervisor(state) {
  return Number(state?.stamp??0)!==runtimeStamp();
}

export function parsePort(value) {
  const text=String(value ?? '17621');
  if (!/^\d+$/.test(text) || Number(text)<1 || Number(text)>65535) throw new Error('AXIOM_UI_PORT 必须是 1–65535 的整数');
  return Number(text);
}
// 未设置时返回 null（由上游随机分配空闲端口）。
export function parseOpencodePort(value) {
  if (value === undefined || value === null || value === '') return null;
  const text=String(value);
  if (!/^\d+$/.test(text) || Number(text)<1 || Number(text)>65535) throw new Error('AXIOM_OPENCODE_PORT 必须是 1–65535 的整数');
  return Number(text);
}
export function runtimeDir(cwd=process.cwd(),port=parsePort(process.env.AXIOM_UI_PORT)) {
  const id=createHash('sha256').update(`${resolve(cwd)}\n${port}`).digest('hex').slice(0,20);
  return join(homedir(),'.axiom','runtime',id);
}
export function readJson(file) {
  try {return JSON.parse(readFileSync(file,'utf8'));} catch(err) {if(err.code==='ENOENT')return null; throw err;}
}
function syncDirectory(path) {
  // Windows does not support fsync on directory handles; files remain fsynced.
  if (process.platform === 'win32') return;
  const fd=openSync(path,'r'); try {fsyncSync(fd);} finally {closeSync(fd);}
}
export function atomicJson(file,value) {
  mkdirSync(dirname(file),{recursive:true,mode:0o700});
  const tmp=`${file}.${process.pid}.${Math.random().toString(36).slice(2)}.tmp`;
  const fd=openSync(tmp,'wx',0o600);
  try {writeFileSync(fd,JSON.stringify(value)); fsyncSync(fd);} finally {closeSync(fd);}
  renameSync(tmp,file);
  syncDirectory(dirname(file));
}
export function alive(pid) {try {if(!Number.isInteger(pid)||pid<1)return false; process.kill(pid,0); return true;}catch{return false;}}
const OCCUPIED=['ENOTEMPTY','EEXIST','EPERM','EACCES'];
export function acquireLock(dir) {
  const token=`${process.pid}-${Math.random().toString(36).slice(2)}`;
  const staging=`${dir}.${token}.new`;
  const marker=`owner-${token}.json`;
  mkdirSync(staging,{mode:0o700});
  atomicJson(join(staging,marker),{pid:process.pid});
  try {
    // Windows 上「把目录改名到已存在的目录」返回 EPERM（不是 POSIX 的 ENOTEMPTY），同样算「锁被占用」。
    for(let retry=0;retry<5;retry++) {
      try { renameSync(staging,dir); return ()=>{
        // Only remove this owner's unique marker. Never recursively delete a new owner's lock.
        try {unlinkSync(join(dir,marker));rmdirSync(dir);} catch(err) {if(!['ENOENT',...OCCUPIED].includes(err.code))throw err;}
      }; } catch(err) {if(!OCCUPIED.includes(err.code))throw err;}
      const owners=readdirSync(dir).filter(name=>/^owner-.*\.json$/.test(name));
      for(const name of owners) {
        const owner=readJson(join(dir,name));
        if(alive(owner?.pid))throw new Error('服务已在运行或启动中');
        // A concurrent reclaimer can remove this old marker, but cannot delete a new token's marker.
        try {unlinkSync(join(dir,name));} catch(err) {if(err.code!=='ENOENT')throw err;}
      }
      try {rmdirSync(dir);} catch(err) {if(!['ENOENT',...OCCUPIED].includes(err.code))throw err;}
    }
    throw new Error('服务启动锁正在恢复，请重试');
  } finally {rmSync(staging,{recursive:true,force:true});}
}
// 诊断用：启动锁的持有者，以及它们是否还活着（旧进程被强杀后会残留）。
export function lockOwners(dir) {
  try {
    return readdirSync(dir).filter(name=>/^owner-.*\.json$/.test(name))
      .map(name=>{const owner=readJson(join(dir,name));return {pid:owner?.pid??null,alive:alive(owner?.pid)};});
  } catch {return [];}
}

const key = value => {if(!/^[\w-]{1,160}$/.test(value??''))throw new Error('非法待办或执行 ID'); return value;};
export function createInbox(dir) {
  const file=(todo,attempt)=>join(dir,'results',`${key(todo)}.${key(attempt)}.json`);
  return {
    read(todo,attempt) {return readJson(file(todo,attempt));},
    // Only an explicit manual recovery can retire a system-generated blocked receipt.
    // Keep the old receipt for audit; completed and business-failure receipts stay immutable.
    recoverBlocked(todo,attempt,expectedError) {
      const path=file(todo,attempt), prior=readJson(path);
      if (!prior) return;
      if(prior.status!=='blocked'||prior.error!==expectedError)throw new Error('回报结果不支持恢复原执行，请先核对结果');
      renameSync(path,`${path}.recovered-${Date.now()}-${Math.random().toString(36).slice(2)}`);
      syncDirectory(dirname(path));
    },
    report(value) {
      if(!['done','failed','blocked'].includes(value.status))throw new Error('非法回报状态');
      const path=file(value.todoKey,value.attempt);
      const result={todoKey:value.todoKey,attempt:value.attempt,status:value.status,result:String(value.result??''),error:String(value.error??'')};
      // Hard link publishes a fully fsynced immutable result; concurrent conflicting writers cannot win a rename race.
      mkdirSync(dirname(path),{recursive:true,mode:0o700});
      const tmp=`${path}.${process.pid}.${Math.random().toString(36).slice(2)}.tmp`;
      atomicJson(tmp,result);
      try {linkSync(tmp,path);} catch(err) {
        if(err.code!=='EEXIST')throw err;
        if(JSON.stringify(readJson(path))!==JSON.stringify(result))throw new Error('同一执行已有不同结果，拒绝覆盖');
      } finally {unlinkSync(tmp);}
      syncDirectory(dirname(path));
      return result;
    },
  };
}
