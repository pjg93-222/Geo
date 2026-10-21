import {test} from 'node:test';
import assert from 'node:assert/strict';
import {dataBytes,desktopDistrictData,districtDataBusy,districtDataReceiver,districtDataSummary} from '../../src/v3/lib/desktopData.ts';

const ready={provider:'github',repository:'pjg93-222/Geo',district:'co-05',phase:'ready',cached:true,canRefresh:true,
  releaseTag:'district-testsets-2026-10-21',catalogueSequence:1,checkedAt:'2026-10-21T18:00:00Z',verifiedAt:'2026-10-21T17:59:00Z'};
test('desktop source accepts only the fixed repository/provider/district and explicit booleans',()=>{
  assert.equal(desktopDistrictData(null),null);
  for(const changed of [{provider:'publisher'},{repository:'other/Geo'},{district:'tx-01'},{phase:'arbitrary'},{cached:'true'},{canRefresh:1}])assert.equal(desktopDistrictData({...ready,...changed}),null);
  assert.equal(desktopDistrictData({...ready})?.district,'co-05');
});
test('whitelist never forwards paths, access credentials, arbitrary native text or malformed optional metadata',()=>{
  const parsed=desktopDistrictData({...ready,releaseTag:'../../credentials?token=secret',catalogueSequence:1.5,checkedAt:'today',verifiedAt:'not a date',
    receivedBytes:'1048576',totalBytes:-1,updateAvailable:'yes',message:'C:\\private\\secret-token',settings:{password:'private'},grant:'private',credential:'private'});
  assert.deepEqual(parsed,{provider:'github',repository:'pjg93-222/Geo',district:'co-05',phase:'ready',cached:true,canRefresh:true});
  const invalidGetter={...ready};Object.defineProperty(invalidGetter,'phase',{get(){throw new Error('unreadable');}});assert.equal(desktopDistrictData(invalidGetter),null);
});
test('valid provenance and bounded integer progress survive, while impossible progress is omitted',()=>{
  const parsed=desktopDistrictData({...ready,phase:'downloading',receivedBytes:1048576,totalBytes:2097152,updateAvailable:false})!;
  assert.equal(parsed.checkedAt,'2026-10-21T18:00:00.000Z');assert.equal(parsed.verifiedAt,'2026-10-21T17:59:00.000Z');
  assert.equal(parsed.receivedBytes,1048576);assert.equal(parsed.totalBytes,2097152);assert.equal(parsed.updateAvailable,false);
  for(const value of ['2026-02-30T18:00:00Z','2025-02-29T18:00:00Z','2026-01-01T24:00:00Z'])assert.equal(desktopDistrictData({...ready,checkedAt:value})?.checkedAt,undefined);
  assert.equal(desktopDistrictData({...ready,checkedAt:'2024-02-29T18:00:00Z'})?.checkedAt,'2024-02-29T18:00:00.000Z');
  assert.equal(desktopDistrictData({...ready,receivedBytes:3,totalBytes:2})?.receivedBytes,undefined);
  for(const value of [NaN,Infinity,-1,.5,Number.MAX_SAFE_INTEGER+1])assert.equal(desktopDistrictData({...ready,receivedBytes:value})?.receivedBytes,undefined);
  assert.equal(dataBytes(1048576),'1.0 MB');
});
test('phase copy distinguishes GitHub checks from verified local queries and offline cache; ready updates wait for reopen',()=>{
  const parsed=desktopDistrictData(ready)!;
  assert.match(districtDataSummary(parsed),/verified local cache/);
  assert.match(districtDataSummary({...parsed,phase:'offline'}),/GitHub check unavailable.*verified local cache/);
  assert.match(districtDataSummary({...parsed,phase:'offline',cached:false}),/no verified local cache/);
  assert.match(districtDataSummary({...parsed,phase:'update-ready'}),/Save your work, then reopen/);
  for(const phase of ['checking','downloading','verifying'] as const)assert.equal(districtDataBusy({...parsed,phase}),true);
  for(const phase of ['ready','offline','update-ready','error'] as const)assert.equal(districtDataBusy({...parsed,phase}),false);
});
test('events supersede stale reads/refreshes, wrong districts do not leak, and closed districts ignore all late replies',()=>{
  const receiver=districtDataReceiver('co-05'),initial=receiver.read();
  const newer=receiver.event({...ready,phase:'downloading',receivedBytes:10,totalBytes:20});assert.equal(newer?.phase,'downloading');
  assert.equal(initial(ready),undefined,'an older initial read cannot hide download progress');
  const refresh=receiver.read();assert.equal(receiver.event({...ready,district:'or-05'}),undefined);
  assert.equal(refresh({...ready,phase:'offline'})?.phase,'offline','a foreign district event cannot invalidate a current-district reply');
  const first=receiver.read(),second=receiver.read();assert.equal(first(ready),undefined);assert.equal(second(ready)?.phase,'ready');
  const final=receiver.read();receiver.close();assert.equal(final(ready),undefined);assert.equal(receiver.event(ready),undefined);
});
test('a null native provider removes GitHub presentation without relabeling recipient/hosted sources',()=>{
  const receiver=districtDataReceiver('co-05'),read=receiver.read();assert.equal(receiver.event(null),null);assert.equal(read(ready),undefined);
  assert.equal(receiver.event({provider:'publisher',district:'co-05'}),undefined);
});
