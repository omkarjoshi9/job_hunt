import { JSDOM } from 'jsdom';
import fs from 'fs';
const dom = new JSDOM('<!DOCTYPE html><body></body>');
globalThis.window = dom.window; globalThis.document = dom.window.document;
Object.defineProperty(globalThis, 'navigator', { value: dom.window.navigator, configurable: true });
const mermaid = (await import('mermaid')).default;
mermaid.initialize({ startOnLoad: false });
const blocks = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
let bad = 0;
for (const b of blocks) {
  try { await mermaid.parse(b.code); }
  catch (e) { bad++; console.log(`FAIL ${b.where}: ${String(e.message || e).split('\n').slice(0,3).join(' | ')}`); }
}
console.log(`mermaid checked=${blocks.length} failed=${bad}`);
