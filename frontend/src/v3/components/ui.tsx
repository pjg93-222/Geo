// Shared primitives (FIDELITY_SPEC section 3.5): segmented control, select, switch, grade square, range bar,
// lean bars, value bar, skeleton. Presentation only; no data fetching.
import type {CSSProperties} from 'react';
import {ChevronDown} from 'lucide-react';
import {LEAN_BANDS,leanCellPos,leanLinearPos,type LeanKey} from '../lib/format';
import type {Grade} from '../types';

export const cx=(...c:(string|false|null|undefined)[]):string=>c.filter(Boolean).join(' ');

export interface SegItem<T extends string>{id:T;label:string}
export function Segmented<T extends string>(p:{items:SegItem<T>[];value:T;onChange:(v:T)=>void;variant:'index'|'level';label:string}){
  return <div className={cx('seg',p.variant)} role="group" aria-label={p.label}>
    {p.items.map(it=><button key={it.id} type="button" className={cx('seg-item',it.id===p.value&&'on')}
      aria-pressed={it.id===p.value} onClick={()=>p.onChange(it.id)}>{it.label}</button>)}
  </div>;
}

export function Select(p:{label:string;value:string;options:{id:string;label:string}[];onChange:(v:string)=>void;shown:string;width?:number}){
  return <label className="select" style={p.width?{minWidth:`calc(${p.width} * var(--u))`}:undefined}>
    <span className="select-text">{p.label}: {p.shown}</span>
    <ChevronDown className="select-chev" strokeWidth={1.75} aria-hidden="true"/>
    <select aria-label={p.label} value={p.value} onChange={e=>p.onChange(e.target.value)}>
      {p.options.map(o=><option key={o.id} value={o.id}>{o.label}</option>)}
    </select>
  </label>;
}

export function Switch(p:{checked:boolean;onChange:(v:boolean)=>void;label:string;className?:string;labelClass?:string}){
  return <button type="button" role="switch" aria-checked={p.checked} className={cx('switch-row',p.className)} onClick={()=>p.onChange(!p.checked)}>
    <span className={cx('switch',p.checked&&'on')}><i/></span>
    <span className={cx('switch-label',p.labelClass)}>{p.label}</span>
  </button>;
}

export function GradeBox(p:{grade:Grade|string;size:'lg'|'md'|'row'|'sm'}){
  return <span className={cx('grade',p.size)} aria-label={`Grade ${p.grade}`}>{p.grade}</span>;
}

export type RangeVariant='railA'|'profile'|'table'|'railB';
/** Likely range: track 0..100, segment lo..hi, tick at the value. Never a fill from the left edge. */
export function RangeBar(p:{value:number|null;lo?:number|null;hi?:number|null;variant:RangeVariant;className?:string}){
  const has=p.value!==null&&p.value!==undefined;
  const lo=has&&p.lo!=null?Math.max(0,Math.min(100,p.lo)):0;
  const hi=has&&p.hi!=null?Math.max(lo,Math.min(100,p.hi)):0;
  return <div className={cx('rbar','v-'+p.variant,p.className)} role="img"
    aria-label={has?`Standing ${Math.round(p.value!)}, likely ${Math.round(lo)} to ${Math.round(hi)}`:'Not available'}>
    {has&&hi>lo&&<i className="rbar-seg" style={{left:`${lo}%`,width:`${hi-lo}%`}}/>}
    {has&&<i className="rbar-tick" style={{left:`${Math.max(0,Math.min(100,p.value!))}%`}}/>}
  </div>;
}

/** Single standing as a fill (household rows, popover): a fill, because it shows one value, not a range. */
export function ValueBar(p:{value:number;variant:'row'|'pop';title?:string}){
  return <div className={cx('vbar',p.variant)} role="img" aria-label={`Standing ${Math.round(p.value)}`} title={p.title}>
    <i style={{width:`${Math.max(1.5,Math.min(100,p.value))}%`}}/>
  </div>;
}

/** Linear lean bar of the map rails: five segments 1:2:2:2:1, tick at the value. */
export function LeanBar(p:{value:number|null;variant:'a'|'b'}){
  return <div className={cx('lbar',p.variant)} role="img" aria-label={p.value===null?'Not available':`Partisan lean ${Math.round(p.value)}`}>
    <i className="ls strong-r"/><i className="ls lean-r"/><i className="ls centre"/><i className="ls lean-d"/><i className="ls strong-d"/>
    {p.value!==null&&<i className="lbar-tick" style={{left:`${leanLinearPos(p.value)*100}%`}}/>}
  </div>;
}

/** Cell variant of the profile lean panel: five equal cells, labels beneath, marker placed inside its band. */
export function LeanCells(p:{value:number|null}){
  return <div className="lcells">
    <div className="lcells-bar">
      {LEAN_BANDS.map(b=><i key={b.key} className={cx('cell',b.key)}/>)}
      {p.value!==null&&<i className="lcells-dot" style={{left:`${leanCellPos(p.value)*100}%`}}/>}
    </div>
    <div className="lcells-labels">{LEAN_BANDS.map(b=><span key={b.key}>{b.label}</span>)}</div>
  </div>;
}

export function Swatch(p:{leanKey?:LeanKey|'none';size?:number}){
  return <i className={cx('lswatch',p.leanKey==='none'&&'none')} style={p.leanKey&&p.leanKey!=='none'?{background:`var(--${p.leanKey})`}:undefined}/>;
}

export function Sk(p:{w:number;h?:number;className?:string;style?:CSSProperties}){
  return <span className={cx('sk',p.className)} style={{width:`calc(${p.w} * var(--u))`,height:`calc(${p.h??14} * var(--u))`,...p.style}} aria-hidden="true"/>;
}
