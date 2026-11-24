import { register, syncBuiltinESMExports } from 'node:module';
import childProcess from 'node:child_process';

// Only this background host's helpers are affected; preserve Node's argument
// and callback overloads, including an explicitly omitted options argument.
if (process.platform === 'win32') {
  const hidden = Symbol.for('axiom.windowsHiddenChildProcess');
  for (const name of ['spawn', 'spawnSync', 'exec', 'execSync', 'execFile', 'execFileSync']) {
    const original = childProcess[name];
    if (original[hidden]) continue;
    const wrapped = function (command, ...args) {
      const hasArgsSlot = Array.isArray(args[0]) ||
        (args[0] == null && args.length > 1 && typeof args[1] !== 'function');
      const index = name.startsWith('exec') && !name.startsWith('execFile')
        ? 0 : hasArgsSlot ? 1 : 0;
      if (typeof args[index] === 'function') args.splice(index, 0, { windowsHide: true });
      else args[index] = { ...args[index], windowsHide: true };
      return original.call(this, command, ...args);
    };
    wrapped[hidden] = true;
    childProcess[name] = wrapped;
  }
  syncBuiltinESMExports();
}
register('./loader.mjs',import.meta.url);
