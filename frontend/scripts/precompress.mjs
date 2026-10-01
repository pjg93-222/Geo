// Writes .br (brotli q11) and .gz (gzip 9) siblings next to the compressible files of frontend/dist, using only
// node's built-in zlib (no package is installed). Run after the build:
//
//   cd frontend && npm run build && node scripts/precompress.mjs
//
// The server (server/research/static_files.py) serves a sibling when the client accepts that encoding and the
// sibling is not older than its source, so a rebuild without this step is still correct (it falls back to in-memory
// gzip). Siblings that would not be smaller than the source are not written.
import { readdirSync, statSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { join, extname, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { brotliCompressSync, gzipSync, constants } from 'node:zlib';

const root = join(dirname(fileURLToPath(import.meta.url)), '..', 'dist');
const COMPRESSIBLE = new Set(['.js', '.mjs', '.css', '.html', '.json', '.svg', '.txt', '.xml', '.map', '.webmanifest']);
const MIN_BYTES = 1024;

function* walk(dir) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    const stat = statSync(path);
    if (stat.isDirectory()) yield* walk(path);
    else yield [path, stat];
  }
}

let files = 0, raw = 0, brotli = 0, gzip = 0;
for (const [path, stat] of walk(root)) {
  if (path.endsWith('.br') || path.endsWith('.gz')) continue;
  if (!COMPRESSIBLE.has(extname(path).toLowerCase())) continue;
  const body = readFileSync(path);
  for (const suffix of ['.br', '.gz']) rmSync(path + suffix, { force: true });
  if (body.length < MIN_BYTES) continue;
  const br = brotliCompressSync(body, {
    params: { [constants.BROTLI_PARAM_QUALITY]: 11, [constants.BROTLI_PARAM_SIZE_HINT]: body.length },
  });
  const gz = gzipSync(body, { level: 9 });
  files++; raw += body.length;
  if (br.length < body.length) { writeFileSync(path + '.br', br); brotli += br.length; } else brotli += body.length;
  if (gz.length < body.length) { writeFileSync(path + '.gz', gz); gzip += gz.length; } else gzip += body.length;
}
console.log(JSON.stringify({ files, raw_bytes: raw, brotli_bytes: brotli, gzip_bytes: gzip }));
