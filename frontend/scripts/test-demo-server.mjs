// Loopback-only static demo server. No proxy, database, or live API routes.
import {createServer} from 'node:http';
import {readFile,stat} from 'node:fs/promises';
import {resolve,extname,sep} from 'node:path';
import {fileURLToPath} from 'node:url';
const root=fileURLToPath(new URL('../dist/',import.meta.url));
const types={'.html':'text/html','.js':'text/javascript','.css':'text/css','.json':'application/json','.woff2':'font/woff2','.png':'image/png','.svg':'image/svg+xml','.mp4':'video/mp4'};
const server=createServer(async(req,res)=>{
  try{
    if(!['GET','HEAD'].includes(req.method)){res.writeHead(405);return res.end();}
    const pathname=decodeURIComponent(new URL(req.url,'http://127.0.0.1').pathname);
    if(pathname==='/_terrier/health'){
      await stat(resolve(root,'index.html'));
      const body=JSON.stringify({app:'Terrier',mode:'synthetic',version:1});
      res.writeHead(200,{'Content-Type':'application/json','Content-Length':Buffer.byteLength(body),'Cache-Control':'no-store'});
      return res.end(req.method==='HEAD'?undefined:body);
    }
    if(pathname==='/favicon.ico'){res.writeHead(204);return res.end();}
    if(pathname.startsWith('/api/')||pathname.includes('\\')){res.writeHead(404);return res.end();}
    const route=['/','/people','/method','/contacts','/criteria','/sources','/workspace','/walk'].includes(pathname.replace(/\/+$/,'')||'/')||/^\/people\/[^/]+$/.test(pathname);
    const file=resolve(root,'.'+(route?'/index.html':pathname));
    if(!file.startsWith(resolve(root)+sep)){res.writeHead(403);return res.end();}
    let actual=file;let zipped=false;
    if(/\bgzip\b/.test(req.headers['accept-encoding']||'')){
      try{await stat(file+'.gz');actual=file+'.gz';zipped=true;}catch{}
    }
    const data=await readFile(actual);
    // MP4 seeking/replay requires byte ranges; without 206 Chrome resets seeks to frame zero.
    if(extname(file)==='.mp4'&&req.headers.range){
      const match=/^bytes=(\d*)-(\d*)$/.exec(req.headers.range);
      const start=match?(match[1]?Number(match[1]):Math.max(0,data.length-Number(match[2]))):NaN;
      const finish=match?(match[1]&&match[2]?Math.min(Number(match[2]),data.length-1):data.length-1):NaN;
      if(!match||(!match[1]&&!match[2])||!Number.isSafeInteger(start)||!Number.isSafeInteger(finish)||start<0||start>=data.length||finish<start){
        res.writeHead(416,{'Content-Range':`bytes */${data.length}`});return res.end();
      }
      const part=data.subarray(start,finish+1);
      res.writeHead(206,{'Content-Type':'video/mp4','Content-Length':part.length,'Content-Range':`bytes ${start}-${finish}/${data.length}`,'Accept-Ranges':'bytes','Cache-Control':'no-cache','X-Content-Type-Options':'nosniff'});
      return res.end(req.method==='HEAD'?undefined:part);
    }
    res.writeHead(200,{'Content-Type':(types[extname(file)]||'application/octet-stream')+'; charset=utf-8',
      'Content-Length':data.length,'Cache-Control':'no-cache','X-Content-Type-Options':'nosniff',
      'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; worker-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
      ...(extname(file)==='.mp4'?{'Accept-Ranges':'bytes'}:{}),...(zipped?{'Content-Encoding':'gzip','Vary':'Accept-Encoding'}:{})});
    res.end(req.method==='HEAD'?undefined:data);
  }catch{res.writeHead(404);res.end('Not found');}
});
// Port defaults to 8767 (the standalone preview); TERRIER_PORT overrides it so a check can run beside the live app.
const port=Number(process.env.TERRIER_PORT||8767);
if(!Number.isInteger(port)||port<1||port>65535){console.error('TERRIER_PORT must be a port number');process.exit(2);}
server.listen(port,'127.0.0.1',()=>console.log('Test-dummy preview: http://127.0.0.1:'+port));
server.on('error',e=>{console.error(e.message);process.exitCode=1;});
