import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
import {terrierBase} from './terrier-base.mjs';
const base=terrierBase('verify-terrier-dart.mjs');
const browser=await chromium.launch({channel:'chrome',headless:true});
try {
  const page=await browser.newPage();
  await page.goto(base+'/brand/terrier-loading-hq.mp4');
  await page.waitForFunction(()=>document.querySelector('video')?.readyState>=2);
  const frames=await page.locator('video').evaluate(async video=>{
    video.pause();
    const canvas=document.createElement('canvas');
    canvas.width=video.videoWidth;canvas.height=video.videoHeight;
    const context=canvas.getContext('2d',{willReadFrequently:true}),results=[];
    for(let frame=1;frame<143;frame++) {
      const time=frame/24;
      await new Promise(resolve=>{video.addEventListener('seeked',resolve,{once:true});video.currentTime=time;});
      await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
      context.drawImage(video,0,0);
      const data=context.getImageData(0,0,canvas.width,canvas.height).data;
      let count=0,x0=canvas.width,y0=canvas.height,x1=0,y1=0;
      for(let i=0;i<data.length;i+=4) {
        const r=data[i],g=data[i+1],b=data[i+2];
        if(r>160&&g>85&&g<215&&b<95&&r>g*1.15) {
          count++;const x=i/4%canvas.width,y=Math.floor(i/4/canvas.width);
          x0=Math.min(x0,x);x1=Math.max(x1,x);y0=Math.min(y0,y);y1=Math.max(y1,y);
        }
      }
      if(count>4)results.push({time,count,x0,y0,x1,y1});
    }
    return results;
  });
  assert.ok(frames.length>=3&&frames.length<=6,'A brief, visible dart');
  assert.ok(frames.every(f=>f.time>=2.667&&f.time<=2.917&&f.y0>=143*2.5&&f.y1<=161*2.5),'Only the neck-height band');
  assert.ok(frames[0].x0>frames.at(-1).x0,'Right-to-left motion');
  const report={frames,checks:['one right-to-left gold dart','visible for at most 0.25 seconds','gold confined to the standing neck-height band']};
  await writeFile(new URL('../../_build/terrier-video-verification/gold-frame-scan.json',import.meta.url),JSON.stringify(report,null,2));
  console.log(JSON.stringify(report));
} finally {await browser.close();}
