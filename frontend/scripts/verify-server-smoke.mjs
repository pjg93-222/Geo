import {chromium} from '@playwright/test';
import {terrierBase} from './terrier-base.mjs';
const base=terrierBase('verify-server-smoke.mjs');
const b=await chromium.launch({channel:'chrome',headless:true,args:['--enable-unsafe-swiftshader']});const p=await b.newPage();
p.on('console',m=>{if(m.type()==='error')console.log(JSON.stringify({text:m.text(),location:m.location()}));});
p.on('response',r=>{if(r.status()>=400)console.log(r.status(),r.url());});
await p.goto(base+'/people');await p.locator('.trow:not(.sk-row)').first().waitFor();await p.waitForTimeout(500);await b.close();
