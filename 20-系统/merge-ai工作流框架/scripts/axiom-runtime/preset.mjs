// 多环境预设：与 npm run ui 同一套托管逻辑，只换 UI / opencode 端口。
// 端口发布后不要改：UI 端口决定运行时目录与 SurrealDB 隔离，opencode 端口供调试直连。
const PRESETS = {
  ll: { ui: 17622, opencode: 19622 },
  'll-test': { ui: 17623, opencode: 19623 },
  pl: { ui: 17624, opencode: 19624 },
  'pl-test': { ui: 17625, opencode: 19625 },
};
const GOSSIP = ['--gossip', '--bootstrap', '062cc06c6bebe4c41ef58dad03ac200757577ad037727e710175ba16bab46663,f2009352849a2348d4f3e23271d7f2f080e325a2f9b92192a3ab6a1e411b2b6a'];
const ACTIONS = new Set(['start', 'status', 'logs', 'restart', 'stop', 'stop-all']);
const args = process.argv.slice(2);
const name = args.shift();
const preset = PRESETS[name];
if (!preset) throw new Error(`未知预设 "${name ?? ''}"；可用：${Object.keys(PRESETS).join(' / ')}`);
const action = ACTIONS.has(args[0]) ? args.shift() : 'start';
process.env.AXIOM_UI_PORT = String(preset.ui);
process.env.AXIOM_OPENCODE_PORT = String(preset.opencode);
process.argv = [process.argv[0], process.argv[1], action, ...(action === 'start' || action === 'restart' ? GOSSIP : []), ...args];
await import('./cli.mjs');
