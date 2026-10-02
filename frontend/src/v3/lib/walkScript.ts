// The door script as data: a few short steps, each with the answers a voter may give.
// `mark` records a result code, `next` names the step that follows. Placeholders in square brackets are filled in by the walker.
import type {WalkCode} from '../api/walkTypes';

export interface ScriptChoice{say:string;next?:string;mark?:WalkCode}
export interface ScriptStep{id:string;title:string;line:string;choices:ScriptChoice[]}
export type ScriptField='your name'|'campaign'|'voter name';

export const DOOR_SCRIPT:readonly ScriptStep[]=[
  {id:'intro',title:'Introduction',line:"Hi, I'm [your name] with [campaign]. Is this [voter name]?",choices:[
    {say:'Yes',next:'support'},
    {say:'No one answers',mark:'NH'},
    {say:'They have moved',mark:'MV'},
  ]},
  {id:'support',title:'Support',line:'We are talking with neighbors about the coming election. Are you planning to support [campaign]?',choices:[
    {say:'Yes',mark:'S',next:'thanks'},
    {say:'Not sure yet',mark:'U',next:'issue'},
    {say:'No',mark:'O',next:'thanks'},
  ]},
  {id:'issue',title:'One question',line:'What local issue matters most to you right now, for example local schools?',choices:[
    {say:'Any answer',next:'thanks'},
  ]},
  {id:'thanks',title:'Thank you',line:'Thank you for your time. Have a good day.',choices:[]},
];

export const stepById=(id:string):ScriptStep|undefined=>DOOR_SCRIPT.find(s=>s.id===id);
/** 1-based position of a step in the script, 0 when the id is unknown. */
export const stepNumber=(id:string):number=>DOOR_SCRIPT.findIndex(s=>s.id===id)+1;

/** Replaces the bracketed placeholders that have a value and leaves the others as they are. */
export function fillScript(text:string,values:Partial<Record<ScriptField,string>>):string{
  return text.replace(/\[(your name|campaign|voter name)\]/g,(whole,key:ScriptField)=>values[key]?.trim()||whole);
}

/** What an answer leads to, in words: "Not sure yet: mark U, go to 3". */
export function choiceText(choice:ScriptChoice):string{
  const actions=[choice.mark?`mark ${choice.mark}`:'',choice.next?`go to ${stepNumber(choice.next)}`:''].filter(Boolean);
  return actions.length?`${choice.say}: ${actions.join(', ')}`:choice.say;
}
