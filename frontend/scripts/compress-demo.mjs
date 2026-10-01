import {readdir,readFile,writeFile} from 'node:fs/promises';
import {gzipSync} from 'node:zlib';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
let files=0,raw=0,gzip=0;
async function walk(dir){for(const item of await readdir(dir,{withFileTypes:true})){
  const path=join(dir,item.name);if(item.isDirectory()){await walk(path);continue;}
  if(!/\.(js|css|json|html|svg)$/.test(path))continue;
  const bytes=await readFile(path);const compressed=gzipSync(bytes,{level:9});
  if(compressed.length<bytes.length){await writeFile(path+'.gz',compressed);files++;raw+=bytes.length;gzip+=compressed.length;}
}}
await walk(fileURLToPath(new URL('../dist',import.meta.url)));
console.log(JSON.stringify({files,raw,gzip}));
