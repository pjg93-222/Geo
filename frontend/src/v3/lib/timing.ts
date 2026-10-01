// The one quiet timing line (screen 06): "Map ready 0.4 s" = from the user action (performance.now() at input) to the
// MapLibre `idle` event after the new data is set; "List ready 0.1 s" = from the page request to rows painted.
// The times are measured, never typed. There are no spinners anywhere.
import {useSyncExternalStore} from 'react';

interface State{map:number|null;list:number|null}
let state:State={map:null,list:null};
const subs=new Set<()=>void>();
const starts:{map:number|null;list:number|null}={map:0,list:null}; // the page load itself is the first map action

function emit(){state={...state};subs.forEach(f=>f());}
const marks:Record<string,number>={};
export function begin(kind:'map'|'list'):void{
  starts[kind]=performance.now();
  if(kind==='map')for(const k of Object.keys(marks))delete marks[k];
}
/** Record a labelled milestone (ms since the open map action), so a slow "Map ready" can be split into script and paint. */
export function mark(label:string):void{
  const s=starts.map;if(s===null)return;
  marks[label]=Math.round(performance.now()-s);
  (window as unknown as {__cuMarks?:Record<string,number>}).__cuMarks=marks;
}
export function end(kind:'map'|'list'):void{
  const s=starts[kind];if(s===null)return;
  starts[kind]=null;state[kind]=performance.now()-s;emit();
  // exposed for tests and for the screenshot harness
  (window as unknown as {__cuTiming?:State}).__cuTiming=state;
}
export function useTiming():State{
  return useSyncExternalStore(f=>{subs.add(f);return ()=>subs.delete(f);},()=>state);
}
/** Run `fn` after the next paint, then call end(kind): "rows painted" for the list. */
export function endAfterPaint(kind:'map'|'list'):void{
  requestAnimationFrame(()=>requestAnimationFrame(()=>end(kind)));
}
