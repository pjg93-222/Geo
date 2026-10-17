// Reproducible offline OCR assets. No user images or machine state belong here.
import {copyFileSync,mkdirSync,readFileSync,writeFileSync} from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
const root=path.resolve(import.meta.dirname,'..');
const output=path.join(root,'public','ocr','tesseract-6.0.1');
const modules=path.join(root,'node_modules');
for(const [name,expected] of [['tesseract.js','6.0.1'],['tesseract.js-core','6.1.2'],['@tesseract.js-data/eng','1.0.0']]){
  const actual=JSON.parse(readFileSync(path.join(modules,name,'package.json'),'utf8')).version;
  if(actual!==expected)throw new Error(`OCR asset version mismatch: ${name} ${actual}, expected ${expected}`);
}
mkdirSync(output,{recursive:true});
const files=[
  ['tesseract.js/dist/worker.min.js','worker.min.js'],
  ['tesseract.js/LICENSE.md','LICENSE-tesseract.js.txt'],
  ['tesseract.js-core/LICENSE','LICENSE-tesseract-core.txt'],
  ['scripts/ocr-LICENSE-tessdata.txt','LICENSE-tessdata.txt'],
  ['@tesseract.js-data/eng/4.0.0_best_int/eng.traineddata.gz','eng.traineddata.gz'],
  ...['tesseract-core','tesseract-core-simd','tesseract-core-lstm','tesseract-core-simd-lstm'].map(name=>[`tesseract.js-core/${name}.wasm.js`,`${name}.wasm.js`]),
];
const manifest=[];
for(const [input,name] of files){
  const destination=path.join(output,name);
  copyFileSync(path.join(input.startsWith('scripts/')?root:modules,input),destination);
  if(name==='worker.min.js'){
    const original=readFileSync(destination,'utf8');
    const fallback='r.g=function(){if("object"==typeof globalThis)return globalThis;try{return this||new Function("return this")()}catch(t){if("object"==typeof window)return window}}()';
    if(original.split(fallback).length!==2)throw new Error('Pinned OCR worker global-detection code changed; review the no-eval adaptation.');
    writeFileSync(destination,original.replace(fallback,'r.g=globalThis'));
  }
  const bytes=readFileSync(destination);
  manifest.push({file:name,bytes:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex')});
}
writeFileSync(path.join(output,'NOTICE.txt'),'Local OCR: Tesseract.js 6.0.1 and tesseract.js-core 6.1.2 (Apache-2.0). English 4.0.0_best_int model from naptha/tessdata (Apache-2.0), packaged as @tesseract.js-data/eng 1.0.0 (package metadata declares MIT).\nSources: https://github.com/naptha/tesseract.js ; https://github.com/naptha/tesseract.js-core ; https://github.com/naptha/tessdata\nTerrier adapts only the worker webpack global-detection fallback to globalThis, supported by its target browsers, so the distributed worker contains no dynamic JavaScript evaluation.\n');
writeFileSync(path.join(output,'asset-manifest.json'),JSON.stringify(manifest,null,2)+'\n');
console.log(`Prepared ${manifest.length} local OCR assets (${manifest.reduce((n,item)=>n+item.bytes,0)} bytes).`);
