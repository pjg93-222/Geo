import {test} from 'node:test';
import assert from 'node:assert/strict';
import {lastView,readSavedViews,rememberView,removeView,safeViewPath,saveView} from '../../src/v3/lib/savedViews.ts';
const storage=()=>{const data=new Map<string,string>();return {getItem:(key:string)=>data.get(key)??null,setItem:(key:string,value:string)=>{data.set(key,value);}};};

test('bookmarks accept internal views and reject external or unsupported routes',()=>{
  assert.equal(safeViewPath('/?index=lean&level=zip&sel=80903&f=123'),'/?index=lean&level=zip&sel=80903');
  assert.equal(safeViewPath('/people/P-test?demo=1'),'/people/P-test?demo=1');
  for(const path of ['https://example.com','//example.com','/\\example.com','/workspace','/api/people','/missing','javascript:alert(1)','/\npeople'])assert.equal(safeViewPath(path),null);
});

test('saved views persist filters, survive reload, and can be removed individually',()=>{
  const s=storage();const path='/people?reg=D&lean=1&balanced=0';
  const first=saveView('People filters',path,s);saveView('Registration map','/?index=lean&level=zip',s);
  assert.equal(readSavedViews(s).length,2);assert.equal(readSavedViews(s)[1].path,path);
  const remaining=removeView(first[0].id,s);assert.equal(remaining.length,1);assert.equal(remaining[0].name,'Registration map');
});

test('malformed storage is surfaced and never overwritten by a failed save',()=>{
  const s=storage();s.setItem('terrier:saved-views:v1','broken');
  assert.throws(()=>readSavedViews(s));assert.throws(()=>saveView('New','/',s));
  assert.equal(s.getItem('terrier:saved-views:v1'),'broken');
});

test('last view preserves query state and ignores workspace or unsafe routes',()=>{
  const s=storage();rememberView('/?level=tract&sel=123',s);rememberView('/workspace',s);
  assert.equal(lastView(s),'/?level=tract&sel=123');rememberView('//example.com',s);assert.equal(lastView(s),'/?level=tract&sel=123');
});

test('invalid stored entries do not turn into active links',()=>{
  const s=storage();const good={id:'one',name:'Safe',path:'/contacts',savedAt:'2026-10-01T12:00:00Z'};
  s.setItem('terrier:saved-views:v1',JSON.stringify([good,{...good,id:'two',path:'//example.com'},{...good,id:'three',savedAt:'invalid'},good]));
  assert.deepEqual(readSavedViews(s),[good]);
});
