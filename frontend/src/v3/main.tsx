import {Component,Suspense,lazy,useCallback,useEffect,useState,type ReactNode} from 'react';
import {createRoot} from 'react-dom/client';
import './styles/tokens.css';
import './styles/base.css';
import './styles/chrome.css';
import './styles/map.css';
import './styles/pages.css';
import {parseRoute,useRoute} from './lib/router';
import {getMeta,sources} from './api/client';
import type {Meta} from './types';
import {PageHeader} from './components/Header';
import {LoadingTerrier} from './components/TerrierMark';
const loadMap=()=>import('./components/MapScreen');
const MapScreen=lazy(()=>loadMap().then(m=>({default:m.MapScreen})));
if(parseRoute().name==='map')void loadMap().catch(()=>{}); // React.lazy/AppBoundary handles a failed chunk.
// People, Profile and Method are code-split: opening the list never downloads MapLibre (IMPLEMENTATION_PLAN 6.3 item 8)
const PeopleScreen=lazy(()=>import('./components/PeopleScreen').then(m=>({default:m.PeopleScreen})));
const ProfileScreen=lazy(()=>import('./components/ProfileScreen').then(m=>({default:m.ProfileScreen})));
const MethodScreen=lazy(()=>import('./components/MethodScreen').then(m=>({default:m.MethodScreen})));
const ContactsScreen=lazy(()=>import('./components/ContactsScreen').then(m=>({default:m.ContactsScreen})));
const WorkspaceScreen=lazy(()=>import('./components/WorkspaceScreen').then(m=>({default:m.WorkspaceScreen})));
const WalkScreen=lazy(()=>import('./components/WalkScreen').then(m=>({default:m.WalkScreen})));

function V3App(){
  const route=useRoute();
  const [meta,setMeta]=useState<Meta|null>(null);
  const [error,setError]=useState('');const [attempt,setAttempt]=useState(0);
  const [introDone,setIntroDone]=useState(()=>matchMedia('(prefers-reduced-motion:reduce)').matches);
  const finishIntro=useCallback(()=>setIntroDone(true),[]);
  useEffect(()=>{let current=true;setError('');getMeta().then(m=>{if(current)setMeta(m);}).catch(e=>{if(current)setError(e instanceof Error?e.message:'The local data could not be loaded.');});return ()=>{current=false;};},[attempt]);
  useEffect(()=>{
    // Data provenance is reported by the connected local service.
    const demo=!!meta?.demo;
    const root=document.documentElement;
    root.dataset.demo=demo?'1':'0';
    root.dataset.source=meta?.source??'';
    document.title='Terrier';
    (window as unknown as {__cuV3?:unknown}).__cuV3={meta,sources};
  },[meta,route]);
  const shell=()=>introDone?<LoadingTerrier/>:null;
  if(error)return <div className="screen"><PageHeader meta={null} active="map"/><main className="app-error" role="alert"><h1>{error}</h1><p>Check that the local Terrier data service is running, then retry.</p><button type="button" onClick={()=>setAttempt(n=>n+1)}>Retry</button></main></div>;
  let screen:ReactNode=null;
  if(meta)switch(route.name){
    case 'people':screen=<PeopleScreen meta={meta} route={route}/>;break;
    case 'profile':screen=<ProfileScreen meta={meta} route={route}/>;break;
    case 'method':screen=<MethodScreen meta={meta}/>;break;
    case 'contacts':screen=<ContactsScreen meta={meta} route={route}/>;break;
    case 'criteria':case 'sources':case 'workspace':screen=<WorkspaceScreen meta={meta} route={route}/>;break;
    case 'walk':screen=<WalkScreen meta={meta} route={route}/>;break;
    default:screen=<MapScreen meta={meta} route={route}/>;
  }
  const booting=!introDone||!meta;
  return <>
    <div className="app-stage" inert={booting} aria-hidden={booting||undefined}><Suspense fallback={shell()}>{screen}</Suspense></div>
    {booting&&<LoadingTerrier startup onComplete={finishIntro}/>}
  </>;
}

class AppBoundary extends Component<{children:ReactNode},{failed:boolean}>{
  state={failed:false};
  static getDerivedStateFromError(){return {failed:true};}
  render(){return this.state.failed?<main className="app-error" role="alert"><h1>This view could not open.</h1><p>Reload Terrier to try again.</p><button type="button" onClick={()=>location.reload()}>Reload</button></main>:this.props.children;}
}
createRoot(document.getElementById('root')!).render(<AppBoundary><V3App/></AppBoundary>);

import './styles/completion.css';
import './styles/workspace-shell.css';
import './styles/ui-refinements.css';
