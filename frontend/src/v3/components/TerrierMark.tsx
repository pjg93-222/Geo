import {useEffect,useState} from 'react';

/** Branded walk/sniff/turn animation; loading screens loop until their data is ready. */
export function TerrierMark({busy=false,startup=false,repeat=false,onComplete}:{busy?:boolean;startup?:boolean;repeat?:boolean;onComplete?:()=>void}){
  // Startup and district transitions animate; small header marks stay still.
  const animate=busy&&startup;
  const [reduced,setReduced]=useState(()=>matchMedia('(prefers-reduced-motion:reduce)').matches);
  const [finished,setFinished]=useState(false);
  const [failed,setFailed]=useState(false);
  const [playing,setPlaying]=useState(false);
  useEffect(()=>{
    const media=matchMedia('(prefers-reduced-motion:reduce)');
    const change=()=>setReduced(media.matches);
    media.addEventListener('change',change);
    return ()=>media.removeEventListener('change',change);
  },[]);
  useEffect(()=>{setFinished(false);setFailed(false);setPlaying(false);},[busy]);
  useEffect(()=>{if(reduced||finished||failed)onComplete?.();},[reduced,finished,failed,onComplete]);
  // A stalled/blocked media request must never trap the app behind its splash.
  useEffect(()=>{if(!animate||reduced||finished||failed||playing)return;const timer=setTimeout(()=>setFailed(true),10000);return ()=>clearTimeout(timer);},[animate,reduced,finished,failed,playing]);
  return <span className="terrier" aria-hidden="true">
    <img className="terrier-still" src="/brand/terrier-icon-hq.png" alt="" width="1024" height="1024" draggable="false"/>
    {animate&&!reduced&&!finished&&!failed&&<video className={playing?'terrier-video is-playing':'terrier-video'}
      src="/brand/terrier-loading-hq.mp4" poster="/brand/terrier-icon-hq.png" muted autoPlay playsInline loop={repeat} preload="auto"
      onCanPlay={e=>{e.currentTarget.play().catch(()=>setFailed(true));}}
      onPlaying={()=>setPlaying(true)} onEnded={()=>{if(!repeat)setFinished(true);}} onError={()=>setFailed(true)}/>}
  </span>;
}

export function LoadingTerrier({label='Loading Terrier…',startup=false,onComplete}:{label?:string;startup?:boolean;onComplete?:()=>void}){
  return <div className={startup?'terrier-loading terrier-startup':'terrier-loading'} role="status" aria-live="polite" aria-label={label}>
    <div className="terrier-loading-icon"><TerrierMark busy startup={startup} repeat={startup} onComplete={onComplete}/></div>
    <div className="terrier-loading-copy"><span className="terrier-loading-brand">Terrier</span><span>{label}</span></div>
  </div>;
}
