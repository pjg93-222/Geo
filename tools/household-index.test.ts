import {test} from 'node:test';
import assert from 'node:assert/strict';
import {buildHouseholdIndex} from '../frontend/src/householdIndex.ts';

const row=(id:string,longitude:number,latitude:number,count=1)=>({id,longitude,latitude,count,address:'Synthetic address'});
test('inclusive grid boundaries, negative coordinates and coincident households',()=>{
  const rows=[row('a',-104.82,38.80),row('b',-104.81,38.81),row('c',-104.81,38.81,4),row('d',-104.80999,38.82),row('e',-104.83,38.79)];
  const index=buildHouseholdIndex(rows);
  assert.deepEqual(index.query([-104.82,38.8,-104.81,38.81]).map(item=>item.id).sort(),['a','b','c']);
  assert.equal(index.total,5);assert.equal(index.query([-180,-90,180,90]).length,5);
  assert.equal(index.query([0,0,1,1]).length,0);assert.equal(index.query([NaN,0,1,1]).length,0);
});
test('the complete dense viewport is returned beyond the old 300-household cap',()=>{
  const rows=Array.from({length:2000},(_,i)=>row(String(i),-104.80+i*.000001,38.81,2));
  const index=buildHouseholdIndex(rows);const found=index.query([-104.81,38.8,-104.79,38.82]);
  assert.equal(found.length,2000);assert.equal(found.reduce((sum,item)=>sum+item.count,0),4000);
  assert.equal(new Set(found.map(item=>item.id)).size,2000);
});
test('indexed viewport lookup agrees with brute force across a larger synthetic map',()=>{
  const rows=Array.from({length:100000},(_,i)=>row(String(i),-105+(i%500)*.001,38+Math.floor(i/500)*.001));
  const index=buildHouseholdIndex(rows);
  for(const bounds of [[-104.91,38.05,-104.89,38.07],[-105,38,-104.995,38.01],[-104.504,38.195,-104.499,38.2]] as [number,number,number,number][]){
    const [w,s,e,n]=bounds;
    const expected=rows.filter(row=>row.longitude>=w&&row.longitude<=e&&row.latitude>=s&&row.latitude<=n).map(row=>row.id).sort();
    assert.deepEqual(index.query(bounds).map(row=>row.id).sort(),expected);
  }
});
