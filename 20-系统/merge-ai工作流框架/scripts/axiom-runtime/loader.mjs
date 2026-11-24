import { transformSource } from './transform.mjs';
const adapter=new URL('./server.mjs',import.meta.url).href;
export async function load(url, context, nextLoad) {
  const loaded=await nextLoad(url,context);
  if(url.includes('/@axiom/cli/lib/') && /\/(ui|opencode-chat|opencode-server)\.js$/.test(url)) {
    return {...loaded,source:transformSource(url.split('/').at(-1),loaded.source,adapter)};
  }
  return loaded;
}
