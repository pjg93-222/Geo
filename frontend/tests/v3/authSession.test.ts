import {test} from 'node:test';
import assert from 'node:assert/strict';
import {AUTHENTICATION_REQUIRED,getMeta,postJson} from '../../src/v3/api/client.ts';

test('protected session expiry requests authentication; generic failures do not',async()=>{
  const originalFetch=globalThis.fetch,originalWindow=globalThis.window;
  const events=new EventTarget();let expired=0;events.addEventListener(AUTHENTICATION_REQUIRED,()=>expired++);
  Object.assign(globalThis,{window:events});
  try{
    globalThis.fetch=async()=>new Response(JSON.stringify({error:'Sign in to continue'}),{status:401});
    await assert.rejects(()=>getMeta(),/Sign in to continue/);assert.equal(expired,1);
    globalThis.fetch=async()=>new Response(JSON.stringify({error:'Store unavailable'}),{status:503});
    await assert.rejects(()=>getMeta(),/Store unavailable/);assert.equal(expired,1);
  }finally{globalThis.fetch=originalFetch;Object.assign(globalThis,{window:originalWindow});}
});

test('wrong login retains form while unauthorized protected writes rebootstrap authentication',async()=>{
  const originalFetch=globalThis.fetch,originalWindow=globalThis.window;
  const events=new EventTarget();let expired=0;events.addEventListener(AUTHENTICATION_REQUIRED,()=>expired++);
  Object.assign(globalThis,{window:events});
  globalThis.fetch=async(url,options)=>{
    if(String(url)==='/api/auth/session')return new Response(JSON.stringify({authenticated:false,csrf:'isolated-test-token'}));
    assert.equal(options?.credentials,'same-origin');assert.equal((options?.headers as Record<string,string>)['X-CSRFToken'],'isolated-test-token');
    return new Response(JSON.stringify({error:'The username or password is incorrect.'}),{status:401});
  };
  try{
    await assert.rejects(()=>postJson('/api/auth/login',{username:'fixture',password:'fixture'}),/incorrect/);assert.equal(expired,0);
    await assert.rejects(()=>postJson('/api/v3/fixture',{}),/incorrect/);assert.equal(expired,1);
  }finally{globalThis.fetch=originalFetch;Object.assign(globalThis,{window:originalWindow});}
});
