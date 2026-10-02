// After `npm run build`: fails when a built chunk that carries jsPDF code also contains eval( or new Function,
// which a strict content-security policy would block.
// Usage: node scripts/check-no-eval.mjs [--require] [assets-dir]   (--require also fails when no chunk carries jsPDF code)
import {existsSync, readdirSync, readFileSync} from 'node:fs';
import path from 'node:path';

const args = process.argv.slice(2);
const require_ = args.includes('--require');
const dir = path.resolve(args.find(a => !a.startsWith('--')) || path.join(import.meta.dirname, '..', 'dist', 'assets'));
const BANNED = [['eval(', /\beval\s*\(/], ['new Function', /new\s+Function\b/]];
const kb = n => `${(n / 1024).toFixed(1)} kB`;

if (!existsSync(dir)) {
  console.error(`check-no-eval: ${dir} does not exist. Run npm run build first.`);
  process.exit(1);
}
const carriers = [], found = [];
for (const file of readdirSync(dir).filter(f => f.endsWith('.js')).sort()) {
  const text = readFileSync(path.join(dir, file), 'utf8');
  if (!/jsPDF/.test(text) && !/jspdf/i.test(file)) continue;
  carriers.push({file, size: Buffer.byteLength(text)});
  for (const [label, pattern] of BANNED) {
    const m = pattern.exec(text);
    if (m) found.push({file, label, context: text.slice(Math.max(0, m.index - 50), m.index + 70).replace(/\s+/g, ' ')});
  }
}
if (!carriers.length) {
  const note = `check-no-eval: no chunk in ${dir} carries jsPDF code (the app does not import the PDF export yet, or the build was not run)`;
  if (require_) { console.error(`FAIL ${note}`); process.exit(1); }
  console.log(`SKIPPED ${note}; nothing to check`);
  process.exit(0);
}
for (const c of carriers) console.log(`jsPDF code in ${c.file} (${kb(c.size)})`);
if (found.length) {
  for (const f of found) console.error(`FAIL ${f.file} contains ${f.label}: ...${f.context}...`);
  process.exit(1);
}
console.log(`check-no-eval: ${carriers.length} chunk${carriers.length === 1 ? '' : 's'} with jsPDF code, no eval( and no new Function`);
