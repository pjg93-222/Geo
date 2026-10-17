import {Component,Suspense,lazy,useEffect,useState,type ReactNode} from 'react';
import {createRoot} from 'react-dom/client';
import './styles/tokens.css';
import './styles/base.css';
import './styles/chrome.css';
import './styles/map.css';
import './styles/pages.css';
import {parseRoute,useRoute} from './lib/router';
import {getMeta,sources} from './api/client';
import type {Meta} from './types';
import {PageHeader,preloadFeatures} from './components/Header';
import {LoadingTerrier} from './components/TerrierMark';
import {AuthGate} from './components/AuthGate';
import {StartupFrame,useStartup,useStartupReady} from './components/StartupFrame';
const loadMap=()=>import('./components/MapScreen');
const MapScreen=lazy(()=>loadMap().then(m=>({default:m.MapScreen})));
// People, Profile and Method are code-split: opening the list never downloads MapLibre (IMPLEMENTATION_PLAN 6.3 item 8)
const loadPeople=()=>import('./components/PeopleScreen'),loadProfile=()=>import('./components/ProfileScreen'),loadMethod=()=>import('./components/MethodScreen');
const loadContacts=()=>import('./components/ContactsScreen'),loadWorkspace=()=>import('./components/WorkspaceScreen'),loadWalk=()=>import('./components/WalkScreen');
const PeopleScreen=lazy(()=>loadPeople().then(m=>({default:m.PeopleScreen})));
const ProfileScreen=lazy(()=>loadProfile().then(m=>({default:m.ProfileScreen})));
const MethodScreen=lazy(()=>loadMethod().then(m=>({default:m.MethodScreen})));
const ContactsScreen=lazy(()=>loadContacts().then(m=>({default:m.ContactsScreen})));
const WorkspaceScreen=lazy(()=>loadWorkspace().then(m=>({default:m.WorkspaceScreen})));
const WalkScreen=lazy(()=>loadWalk().then(m=>({default:m.WalkScreen})));
const firstRoute=parseRoute().name;
const preload=firstRoute==='map'?loadMap:firstRoute==='people'?loadPeople:firstRoute==='profile'?loadProfile:firstRoute==='method'?loadMethod:firstRoute==='contacts'?loadContacts:firstRoute==='walk'?loadWalk:loadWorkspace;
void preload().catch(()=>{}); // Fetch only the requested view's code while the session and metadata load.

function V3App(){
  const route=useRoute();
  const [meta,setMeta]=useState<Meta|null>(null);
  const [error,setError]=useState('');const [attempt,setAttempt]=useState(0);
  const startup=useStartup();
  useEffect(()=>{void preloadFeatures();},[]);
  useStartupReady(!!error);
  useEffect(()=>{if(meta)startup.phase(`Opening ${meta.district_label}…`);},[meta,startup]);
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
  if(error)return <div className="screen"><PageHeader meta={null} active="map"/><main className="app-error" role="alert"><h1>{error}</h1><p>Check that the local Terrier data service is running, then retry.</p><button type="button" onClick={()=>{startup.restart('Retrying your workspace…');setAttempt(n=>n+1);}}>Retry</button></main></div>;
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
  return <Suspense fallback={<LoadingTerrier/>}>{screen}</Suspense>;
}

function ViewFailure(){useStartupReady(true);return <main className="app-error" role="alert"><h1>This view could not open.</h1><p>Reload Terrier to try again.</p><button type="button" onClick={()=>location.reload()}>Reload</button></main>;}
function SignedIn(){useStartupReady(true);return <main className="terrier-auth"><section><h1>Signed in</h1><p role="status">Returning to your open workspace…</p></section></main>;}

class AppBoundary extends Component<{children:ReactNode},{failed:boolean}>{
  state={failed:false};
  static getDerivedStateFromError(){return {failed:true};}
  render(){return this.state.failed?<ViewFailure/>:this.props.children;}
}
const signInOnly=location.pathname==='/desktop-signin';
createRoot(document.getElementById('root')!).render(<StartupFrame><AppBoundary><AuthGate>{signInOnly?<SignedIn/>:<V3App/>}</AuthGate></AppBoundary></StartupFrame>);

import './styles/completion.css';
import './styles/workspace-shell.css';
import './styles/ui-refinements.css';
