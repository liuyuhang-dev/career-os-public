export function transformSource(name, source, adapterURL) {
  let text=String(source);
  const replace=(before,after)=>{
    if(!text.includes(before))throw new Error(`Axiom runtime 不兼容当前 ${name}，请更新适配层`);
    text=text.replace(before,after);
  };
  if(name==='ui.js') {
    const original = 'import { startOpencodeServer, basicAuth } from "./opencode-server.js";';
    if (text.includes(original)) replace(original, `import { basicAuth } from "./opencode-server.js";\nimport { startOpencodeServer } from ${JSON.stringify(adapterURL)};`);
    else replace('import { startOpencodeServer } from "./opencode-server.js";', `import { startOpencodeServer } from ${JSON.stringify(adapterURL)};`);
    // 新版 ui.js 已内置 AXIOM_UI_PORT（resolvePort），旧版需要适配；两者都不匹配则说明适配层过期。
    if (text.includes('const port = options.port ?? DEFAULT_PORT;')) {
      replace('const port = options.port ?? DEFAULT_PORT;', 'const port = options.port ?? Number(process.env.AXIOM_UI_PORT || DEFAULT_PORT);');
    } else if (!text.includes('process.env.AXIOM_UI_PORT')) {
      throw new Error(`Axiom runtime 不兼容当前 ${name}，请更新适配层`);
    }
  }
  if(name==='opencode-server.js') {
    // 固定 AI 端口（AXIOM_OPENCODE_PORT）；未设置时保持上游的随机空闲端口。
    replace(`function freePort() {
  return new Promise((resolve, reject) => {
    const srv = createServer();
    srv.unref();
    srv.once("error", reject);
    srv.listen(0, "<IP>", () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}`, `function freePort(preferred) {
  return new Promise((resolve, reject) => {
    const text = preferred === undefined || preferred === null ? "" : String(preferred);
    if (text && (!/^\\d+$/.test(text) || Number(text) < 1 || Number(text) > 65535)) {
      reject(new Error("AXIOM_OPENCODE_PORT 必须是 1–65535 的整数"));
      return;
    }
    const srv = createServer();
    srv.unref();
    srv.once("error", (err) => reject(text ? new Error("AXIOM_OPENCODE_PORT=" + text + " 不可用（" + (err.code || err.message) + "）") : err));
    srv.listen(text ? Number(text) : 0, "<IP>", () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}`);
    replace('port = await freePort();', 'port = await freePort(process.env.AXIOM_OPENCODE_PORT);');
  }
  if(name==='opencode-chat.js') {
    replace('if (!newID) throw new Error("fork 会话失败");','if (!newID) throw new Error("fork 会话失败");\n    await options.onCreated?.(newID);');
    // Never turn an unavailable status API into an idle session (would trigger duplicate recovery prompts).
    replace('const activeMap = await request("GET", "/api/session/active");',
      'const activeResponse = await request("GET", "/api/session/active");\n      const activeMap = activeResponse?.data ?? activeResponse;\n      if (!activeMap || typeof activeMap !== "object" || Array.isArray(activeMap)) throw new Error("AI 状态响应无效");');
    replace('running = false;\n    }\n    let queued', 'throw new Error("AI 状态接口暂不可用");\n    }\n    let queued');
    if (text.includes('queued = 0;\n    }\n    let value')) {
      replace('queued = Array.isArray(inbox) ? inbox.length : 0;', 'const pending = inbox?.data ?? inbox;\n      if (!Array.isArray(pending)) throw new Error("AI 收件箱响应无效");\n      queued = pending.length;');
      replace('queued = 0;\n    }\n    let value', 'throw new Error("AI 收件箱暂不可用");\n    }\n    let value');
    }
  }
  return text;
}
