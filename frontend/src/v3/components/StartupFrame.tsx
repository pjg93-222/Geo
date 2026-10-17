import {createContext,useCallback,useContext,useEffect,useMemo,useRef,useState,type ReactNode} from 'react';
import {LoadingTerrier} from './TerrierMark';

const StartupContext=createContext({ready:()=>{},phase:(_label:string)=>{},restart:(_label:string)=>{}});

/** One branded animation spans session checks and the first view's actual data load. */
export function StartupFrame({children}:{children:ReactNode}){
  const [waiting,setWaiting]=useState(true),[label,setLabel]=useState('Opening your workspace…');
  const done=useRef(false),paint=useRef(0);
  const ready=useCallback(()=>{
    if(done.current)return;done.current=true;
    performance.mark('terrier:view-data-ready');
    paint.current=requestAnimationFrame(()=>setWaiting(false));
  },[]);
  const restart=useCallback((text:string)=>{cancelAnimationFrame(paint.current);done.current=false;setWaiting(true);setLabel(text);},[]);
  const phase=useCallback((text:string)=>{if(!done.current)setLabel(text);},[]);
  useEffect(()=>()=>cancelAnimationFrame(paint.current),[]);
  useEffect(()=>{if(!waiting){performance.mark('terrier:workspace-visible');void window.desktop?.rendererReady?.().catch(()=>{});}},[waiting]);
  const context=useMemo(()=>({ready,phase,restart}),[ready,phase,restart]);
  return <StartupContext.Provider value={context}>
    <div className="app-stage" inert={waiting} aria-hidden={waiting||undefined}>{children}</div>
    {waiting&&<LoadingTerrier startup label={label}/>}
  </StartupContext.Provider>;
}

/** Data errors reveal the view's retry UI rather than trapping it behind the loading screen. */
export function useStartupReady(ready:boolean){
  const startup=useContext(StartupContext);
  useEffect(()=>{if(ready)startup.ready();},[ready,startup]);
}
export const useStartup=()=>useContext(StartupContext);
