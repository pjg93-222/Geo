import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {getMeta,getPeople,getProfile,getStreet,getHousehold} from '../../src/v3/api/client.ts';

const query={reg:'all',grade:'ABCD',lean:'all',level:'',area:'',balanced:false,after:'',dir:'next'} as const;
test('application requests local records and never supplies a demo flag',async()=>{
  const original=globalThis.fetch;const requests:{url:string;init?:RequestInit}[]=[];
  globalThis.fetch=async(input,init)=>{requests.push({url:String(input),init});return new Response(JSON.stringify({rows:[]}),{status:200});};
  try{
    await getPeople(query);await getProfile('p_source');await getStreet([-104.81,38.8,-104.80,38.81],16.4);await getHousehold('h_source');
    assert.equal(requests.length,4);
    assert.equal(new URL(requests[3].url,'http://local').searchParams.get('basis'),'registration');
    assert.ok(requests.every(r=>r.url.startsWith('/api/v3/')&&!r.url.includes('demo=')));
    assert.equal(new URL(requests[0].url,'http://local').searchParams.get('balanced'),'0');
    assert.equal(new URL(requests[2].url,'http://local').searchParams.get('zoom'),'16');
    assert.ok(requests.every(r=>r.init?.credentials==='same-origin'&&r.init?.cache==='no-store'));
  }finally{globalThis.fetch=original;}
});
test('missing local data produces an error instead of generated fallback',async()=>{
  const original=globalThis.fetch;let calls=0;
  globalThis.fetch=async()=>{calls++;return new Response(JSON.stringify({error:'Source unavailable'}),{status:503});};
  try{await assert.rejects(()=>getPeople(query),/Source unavailable/);assert.equal(calls,1);}finally{globalThis.fetch=original;}
});
test('startup refuses a demonstration backend or mismatched local store',async()=>{
  const original=globalThis.fetch;
  try{
    globalThis.fetch=async()=>new Response(JSON.stringify({demo:true,store:{status:'ok'}}));
    await assert.rejects(()=>getMeta(),/demonstration service/);
    globalThis.fetch=async()=>new Response(JSON.stringify({demo:false,store:{status:'mismatch',note:'Snapshot mismatch'}}));
    await assert.rejects(()=>getMeta(),/Snapshot mismatch/);
    globalThis.fetch=async()=>new Response(JSON.stringify({demo:false,people:491308,store:{status:'ok'}}));
    assert.equal((await getMeta()).source,'live');
  }finally{globalThis.fetch=original;}
});
test('active entrypoints cannot import fixture generators',()=>{
  for(const file of ['main.tsx','api/client.ts','api/contacts.ts','components/ContactsScreen.tsx']){
    const source=readFileSync(new URL('../../src/v3/'+file,import.meta.url),'utf8');
    assert.doesNotMatch(source,/from ['"][^'"]*fixtures['"]/);
  }
});
