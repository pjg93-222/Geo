import {useEffect,useState,type FormEvent,type ReactNode} from 'react';
import {AUTHENTICATION_REQUIRED,getSession,postJson,type Session} from '../api/client';
import '../styles/auth.css';
import {useStartup,useStartupReady} from './StartupFrame';

/** The server remains the authority for passwords, invitations, roles and sessions. */
export function AuthGate({children}:{children:ReactNode}){
  const [session,setSession]=useState<Session|null>(null);
  const [loading,setLoading]=useState(true);
  const [loadError,setLoadError]=useState('');
  const [attempt,setAttempt]=useState(0);
  const [invitation,setInvitation]=useState(()=>new URLSearchParams(location.hash.slice(1)).get('invite')??new URLSearchParams(location.search).get('invite'));
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  const startup=useStartup();
  useEffect(()=>{if(loading)startup.restart('Opening your workspace…');},[loading,startup]);
  useStartupReady(!loading&&(!!loadError||session?.authenticated!==true||invitation!==null));
  useEffect(()=>{
    if(session?.authenticated!==true)return;
    const expired=()=>{setSession(null);setLoading(true);setError('');setAttempt(value=>value+1);};
    window.addEventListener(AUTHENTICATION_REQUIRED,expired);
    return ()=>window.removeEventListener(AUTHENTICATION_REQUIRED,expired);
  },[session]);
  useEffect(()=>{
    const controller=new AbortController();setLoading(true);setLoadError('');
    getSession(controller.signal).then(value=>{
      if(typeof value.authenticated!=='boolean')throw new Error('The sign-in service returned an invalid session.');
      if(!controller.signal.aborted)setSession(value);
    }).catch((failure:unknown)=>{if(!controller.signal.aborted)setLoadError(failure instanceof Error?failure.message:'The sign-in service could not be reached.');})
      .finally(()=>{if(!controller.signal.aborted)setLoading(false);});
    return ()=>controller.abort();
  },[attempt]);
  function forgetInvitation(){
    const url=new URL(location.href);url.searchParams.delete('invite');
    const fragment=new URLSearchParams(url.hash.slice(1));
    if(fragment.has('invite')){fragment.delete('invite');url.hash=fragment.toString();}
    history.replaceState(history.state,'',url.pathname+url.search+url.hash);setInvitation(null);
  }
  async function submit(event:FormEvent<HTMLFormElement>){
    event.preventDefault();if(busy)return;setBusy(true);setError('');
    const form=event.currentTarget,values=new FormData(form);
    try{
      const value=await postJson<Session>(invitation!==null?'/api/auth/accept':'/api/auth/login',invitation!==null
        ?{token:invitation,password:values.get('password')}
        :{username:values.get('username'),password:values.get('password')});
      if(value.authenticated!==true)throw new Error('Sign-in was not completed. Please try again.');
      forgetInvitation();form.reset();setSession(value);
    }catch(failure){setError(failure instanceof Error?failure.message:'Sign-in could not be completed.');}
    finally{setBusy(false);}
  }
  if(loading)return null;
  if(loadError)return <main className="terrier-auth"><section><h1>Sign-in service unavailable</h1><p role="alert">{loadError}</p><button type="button" onClick={()=>setAttempt(value=>value+1)}>Retry</button></section></main>;
  if(session?.authenticated===true&&invitation===null)return <>{children}</>;
  const setup=invitation!==null;
  const validInvitation=!setup||/^[A-Za-z0-9_-]{40,100}$/.test(invitation);
  return <main className="terrier-auth"><section aria-labelledby="terrier-auth-title">
    <img src="/brand/terrier-icon-hq.png" alt="" width="72" height="72"/>
    <h1 id="terrier-auth-title">{setup?'Create your password':'Sign in to Terrier'}</h1>
    <p>{setup?'Choose a password for your approved account. Existing accounts keep their username and access.':'Use the account for this district’s workspace.'}</p>
    {session?.account_identity&&<p className="terrier-auth-note">Account: {session.account_identity}</p>}
    {validInvitation?<form onSubmit={submit}>
      {!setup&&<label>Username<input name="username" autoComplete="username" maxLength={150} required autoFocus disabled={busy} defaultValue={session?.account_identity??''} readOnly={Boolean(session?.account_identity)}/></label>}
      <label>Password<input type="password" name="password" autoComplete={setup?'new-password':'current-password'} minLength={setup?12:undefined} required disabled={busy}/></label>
      {setup&&<p className="terrier-auth-note">Use at least 12 characters. Avoid common passwords.</p>}
      {error&&<p className="terrier-auth-error" role="alert">{error}</p>}
      <button type="submit" disabled={busy}>{busy?'Signing in…':setup?'Activate account':'Sign in'}</button>
    </form>:<p className="terrier-auth-error" role="alert">This invitation link is invalid. Ask your administrator for a new invitation, or sign in with an existing account.</p>}
    {setup&&<button className="terrier-auth-secondary" type="button" disabled={busy} onClick={()=>{forgetInvitation();setError('');}}>Sign in with an existing account</button>}
    <p className="terrier-auth-note">Accounts and password resets require an administrator’s invitation. Existing accounts and passwords are preserved.</p>
  </section></main>;
}
