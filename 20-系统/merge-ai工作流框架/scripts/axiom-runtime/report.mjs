// Local durable result handoff: no UI HTTP connection is required.
import { createInbox, runtimeDir, parsePort } from './state.mjs';
let input='';
for await (const chunk of process.stdin) {input+=chunk; if(input.length>1024*1024)throw new Error('回报超过 1 MiB');}
const [todoKey,attempt,directory]=process.argv.slice(2);
const inbox=createInbox(directory || process.env.AXIOM_RUNTIME_DIR || runtimeDir(process.cwd(),parsePort(process.env.AXIOM_UI_PORT)));
inbox.report({...JSON.parse(input),todoKey,attempt});
console.log('结果已持久保存，机器人将自动接收。');
