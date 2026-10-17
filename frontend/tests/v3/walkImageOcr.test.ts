import {test} from 'node:test';
import assert from 'node:assert/strict';
import {walkImageDimensions,walkImageOriginalBox,mergeWalkImageLines,WalkOcrWorker} from '../../src/v3/lib/walkImageOcr.ts';

const png=(width:number,height:number)=>{const b=new Uint8Array(24);b.set([137,80,78,71,13,10,26,10],0);b.set([73,72,68,82],12);const v=new DataView(b.buffer);v.setUint32(16,width);v.setUint32(20,height);return b;};
test('image headers bound allocations before decoding and reject malformed/unsupported input',()=>{
  assert.deepEqual(walkImageDimensions(png(1200,800)),{width:1200,height:800});
  assert.throws(()=>walkImageDimensions(png(10000,10000)),/20 million/);
  assert.throws(()=>walkImageDimensions(png(13000,1)),/12,000/);
  assert.throws(()=>walkImageDimensions(png(0,3)),/could not be read/);
  assert.throws(()=>walkImageDimensions(png(100,100).subarray(0,23)),/could not be read/);
  assert.throws(()=>walkImageDimensions(new TextEncoder().encode('<svg width="100" height="100"/>')),/could not be read/);
});
test('GIF BMP JPEG and all WebP header variants report dimensions',()=>{
  const gif=new Uint8Array(10);gif.set(new TextEncoder().encode('GIF89a'));new DataView(gif.buffer).setUint16(6,500,true);new DataView(gif.buffer).setUint16(8,200,true);
  assert.deepEqual(walkImageDimensions(gif),{width:500,height:200});
  const bmp=new Uint8Array(30),bv=new DataView(bmp.buffer);bmp.set([66,77]);bv.setUint32(14,40,true);bv.setInt32(18,500,true);bv.setInt32(22,-200,true);
  assert.deepEqual(walkImageDimensions(bmp),{width:500,height:200});
  const jpg=new Uint8Array([255,216,255,192,0,8,8,0,200,1,244,3]);assert.deepEqual(walkImageDimensions(jpg),{width:500,height:200});
  for(const type of ['VP8X','VP8 ','VP8L']){
    const b=new Uint8Array(30),v=new DataView(b.buffer);b.set(new TextEncoder().encode('RIFF'),0);b.set(new TextEncoder().encode('WEBP'+type),8);v.setUint32(16,10,true);
    if(type==='VP8X'){b[24]=243;b[25]=1;b[27]=199;}
    else if(type==='VP8 '){b.set([157,1,42],23);v.setUint16(26,500,true);v.setUint16(28,200,true);}
    else{b[20]=47;v.setUint32(21,499|(199<<14),true);}
    assert.deepEqual(walkImageDimensions(b),{width:500,height:200});
  }
});
test('rotated and scaled OCR boxes return to the original image coordinates',()=>{
  const original={width:100,height:50},scaled={width:200,height:100};
  const expected={x:10,y:5,width:20,height:10};
  for(const [angle,box] of [[0,{x0:20,y0:10,x1:60,y1:30}],[90,{x0:70,y0:20,x1:90,y1:60}],[-90,{x0:10,y0:140,x1:30,y1:180}]] as const){
    const actual=walkImageOriginalBox(box,original,scaled,angle);
    for(const key of ['x','y','width','height'] as const)assert.ok(Math.abs(actual[key]-expected[key])<.00001,`${angle} ${key}`);
  }
});
test('overlapping repeat OCR labels keep higher confidence while separate map labels remain',()=>{
  const line={text:'Main St',confidence:80,x:10,y:10,width:50,height:10};
  assert.deepEqual(mergeWalkImageLines([line,{...line,confidence:90},{...line,x:100}]),[{...line,confidence:90},{...line,x:100}]);
});
class FakeWorker{
  onerror:((event:unknown)=>void)|null=null;onmessageerror:((event:unknown)=>void)|null=null;onmessage:((event:{data:unknown})=>void)|null=null;
  sent:unknown[]=[];terminated=0;
  postMessage(message:unknown){this.sent.push(message);}
  terminate(){this.terminated++;}
}
test('an initialization timeout terminates native worker and rejects pending job',async()=>{
  const native=new FakeWorker(),reader=new WalkOcrWorker(native as unknown as Worker,undefined,5);
  await assert.rejects(reader.job('load',{}),/too long/);assert.equal(native.terminated,1);reader.close();assert.equal(native.terminated,1);
});
test('worker errors reject all pending work and termination is idempotent',async()=>{
  const native=new FakeWorker(),reader=new WalkOcrWorker(native as unknown as Worker);
  const load=reader.job('load',{}),second=reader.job('recognize',{});
  native.onerror?.({});await assert.rejects(load,/could not start/);await assert.rejects(second,/could not start/);
  assert.equal(native.terminated,1);await assert.rejects(reader.job('load',{}),/stopped/);
});
test('worker job routing passes successful results and surfaces recognition errors',async()=>{
  const native=new FakeWorker(),reader=new WalkOcrWorker(native as unknown as Worker);
  try{const pending=reader.job('load',{});native.onmessage?.({data:{jobId:'1',status:'resolve',data:{ok:true}}});assert.deepEqual(await pending,{ok:true});
    const rejected=reader.job('recognize',{});native.onmessage?.({data:{jobId:'2',status:'reject',data:'internal'}});await assert.rejects(rejected,/could not process/);
  }finally{reader.close();}
});
test('abort stops active recognition immediately and rejects with AbortError',async()=>{
  const native=new FakeWorker(),controller=new AbortController(),reader=new WalkOcrWorker(native as unknown as Worker,undefined,90000,controller.signal);
  const pending=reader.job('recognize',{});controller.abort();
  await assert.rejects(pending,{name:'AbortError'});assert.equal(native.terminated,1);reader.close();assert.equal(native.terminated,1);
});
