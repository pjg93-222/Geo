import test from 'node:test';
import assert from 'node:assert/strict';
import {fitImageAlignment,imagePixelToLocation,locationToImagePixel,unprojectImageLocation} from '../../src/v3/lib/walkImageAlignment.ts';
const point=(x:number,y:number)=>{const [lon,lat]=unprojectImageLocation(-8100000+x*2,5100000-y*2);return {x,y,lon,lat};};
test('four distributed control points recover a rotated/translated coordinate overlay and inverse',()=>{
  const fit=fitImageAlignment([point(100,100),point(900,100),point(100,700),point(900,700)],1000,800);
  assert.equal(fit.verified,true);assert.ok(fit.rmsMeters<1e-6);
  const at=imagePixelToLocation(fit,350,420),px=locationToImagePixel(fit,...at);assert.ok(Math.abs(px[0]-350)<1e-6&&Math.abs(px[1]-420)<1e-6);
});
test('three points create preview only, not an independently checked fit',()=>assert.equal(fitImageAlignment([point(0,0),point(1000,0),point(0,800)],1000,800).verified,false));
test('duplicating a control point does not provide independent alignment evidence',()=>assert.equal(fitImageAlignment([point(0,0),point(1000,0),point(0,800),point(0,0)],1000,800).verified,false));
test('bad point or insufficient spread cannot be confirmed',()=>{
  assert.throws(()=>fitImageAlignment([point(0,0),point(500,400),point(1000,800)],1000,800),/spread/);
  assert.equal(fitImageAlignment([point(10,10),point(20,10),point(10,20),point(20,20)],1000,800).verified,false);
  const points=[point(0,0),point(1000,0),point(0,800),{...point(1000,800),lon:-70}];assert.equal(fitImageAlignment(points,1000,800).verified,false);
  assert.throws(()=>fitImageAlignment([point(-1,0),point(1000,0),point(0,800)],1000,800),/inside/);
});
