import {useMemo,useRef,useState} from 'react';
import {Upload} from 'lucide-react';
import {parseAddressText,readAddressFile} from '../lib/walkAddressImport';
import type {AddressImportResult,ImportedWalkAddress,ImportedWalkStreet,ImportSource} from '../lib/walkAddressImport';
import {addressReviewEntries,applyEvidenceDecisions,evidenceReviewRows} from '../lib/walkAddressSheets';
import type {ImportedWalkSelection} from '../lib/walkAddressSheets';
import type {EvidenceChoice} from '../lib/walkEvidence';
import {importSections,prepareImportedGroups} from '../lib/walkImportGroups';
import type {ImportedLocalGroup} from '../lib/walkImportGroups';
import type {WalkListPayload} from '../api/walkTypes';
import {buildWalkSheetsCsv,doorCap,splitWalkSheets} from '../lib/walkSheets';
import {countText,localToday,walkFileName,watermarkText} from '../lib/walk';
import {downloadFile} from '../lib/download';
import {ApiError,getSession,postJson} from '../api/client';
import {WalkImageReview} from './WalkImageReview';
import type {WalkImageEvidence} from './WalkImageReview';
import {readWalkImageFile,assertWalkImages} from '../lib/walkImageImport';
import {WalkOptIns} from './WalkOptIns';
import {hasRecordedOptIn} from '../lib/walkOptIns';

type Candidate={id:number;label:string;city:string;state:string;zip:string;zip_filter:string;doors:number};
type Proximity={distance_m:number|null;runner_up_distance_m:number|null;runner_up_beyond_m?:number;anchor_count:number;confidence:'clear'|'review';reason:string};
type Match={row:number;input:string;status:'matched'|'pick'|'none';candidates:Candidate[];truncated:boolean;reason?:string;method?:'exact'|'proximity'|'review';selected_id?:number;proximity?:Proximity};
type MatchResponse={district:string;snapshot:string;rows:Match[]};
type ReviewStreet={source:ImportedWalkStreet;query:ImportedWalkStreet;match?:Match;choice:string;choiceOrigin?:'manual'|'exact'|'proximity'|'image-layout'};
type AddressListGroup={key:string;payload:WalkListPayload;selections:{id:number;zip:string;person_ids:string[]}[];reason:string};
type MapPlacementCorrection={registration_recovery?:unknown;[key:string]:unknown};
type AddressListResponse={district:string;snapshot:string;groups:AddressListGroup[];warnings:string[];map_placement_omissions:unknown[];map_placement_corrections?:MapPlacementCorrection[]};
const candidateKey=(candidate:Pick<Candidate,'id'|'zip_filter'>)=>`${candidate.id}:${candidate.zip_filter||''}`;
const sourceLabel=(source:ImportSource)=>[source.sourceSheet,source.sourceCell||`row ${source.sourceRow}`].filter(Boolean).join(' · ');
const excluded='__exclude';
const distanceText=(metres:number)=>metres<1000?`${Math.round(metres)} m`:`${(metres/1000).toFixed(1)} km`;
const imageNameKey=(value:string)=>value.toLowerCase().replace(/\b(?:street|road|drive|avenue|terrace|circle|court|lane|boulevard|place|parkway)\b/g,word=>({street:'st',road:'rd',drive:'dr',avenue:'ave',terrace:'ter',circle:'cir',court:'ct',lane:'ln',boulevard:'blvd',place:'pl',parkway:'pkwy'}[word]!)).replace(/[^a-z0-9]/g,'');

export function WalkAddressSheets({capText,splitSides,hosted,showOptIns=false}:{capText:string;splitSides:boolean;hosted:boolean;showOptIns?:boolean}){
  const [loaded,setLoaded]=useState<{name:string;result:AddressImportResult}|null>(null);
  const [pastedText,setPastedText]=useState('');
  const [streets,setStreets]=useState<ReviewStreet[]>([]);
  const [matchedDistrict,setMatchedDistrict]=useState<{district:string;snapshot:string}|null>(null);
  const [expanded,setExpanded]=useState<ImportedLocalGroup[]|null>(null),[expandedSplit,setExpandedSplit]=useState(splitSides);
  const [mapOmissions,setMapOmissions]=useState<unknown[]>([]);
  const [mapCorrections,setMapCorrections]=useState<MapPlacementCorrection[]>([]);
  const [warnings,setWarnings]=useState<string[]>([]),[warningsAccepted,setWarningsAccepted]=useState(false);
  const [decisions,setDecisions]=useState<Record<string,{decision:EvidenceChoice;at:string}>>({});
  const [excludedAddresses,setExcludedAddresses]=useState<Set<number>>(new Set()),[excludedIssues,setExcludedIssues]=useState<Set<number>>(new Set());
  const [bulkTown,setBulkTown]=useState(''),[bulkState,setBulkState]=useState(''),[bulkZip,setBulkZip]=useState('');
  const [error,setError]=useState('');const [busy,setBusy]=useState(false);const [part,setPart]=useState(0);
  const [imageEvidence,setImageEvidence]=useState<Record<string,WalkImageEvidence>>({});
  const imageEvidenceRef=useRef<Record<string,WalkImageEvidence>>({});
  const [imageContextVersion,setImageContextVersion]=useState(0),imageContextRef=useRef(0);
  const [workingImage,setWorkingImage]=useState<string|null>(null),workingImageRef=useRef<string|null>(null);
  const blocked=busy||workingImage!==null;
  const input=useRef<HTMLInputElement>(null),imageInput=useRef<HTMLInputElement>(null);const request=useRef(0);
  let cap:number|null=null,capError='';try{cap=doorCap(capText);}catch(e){capError=(e as Error).message;}
  const pendingStreets=streets.filter(row=>row.choice!==excluded&&!row.match?.candidates.some(candidate=>candidateKey(candidate)===row.choice)).length;
  const streetCount=streets.filter(row=>row.choice!==excluded).length;
  const unread=loaded?(loaded.result.issues.length-excludedIssues.size):0;
  const sections=useMemo(()=>loaded?importSections(streets.filter(row=>row.choice!==excluded).map(row=>row.source),loaded.result.addresses.filter((_,i)=>!excludedAddresses.has(i)),loaded.result.images):[],[loaded,streets,excludedAddresses]);
  const structural=!!loaded&&!unread&&!pendingStreets&&(!streetCount||expanded!==null&&expandedSplit===splitSides);
  const ready=structural&&(!warnings.length||warningsAccepted);
  // Built before the warning acknowledgement so the per-address review rows are visible; one acknowledgement never sets a per-address decision.
  const built=useMemo(()=>structural&&loaded?prepareImportedGroups(sections,streetCount?expanded??[]:[],{sourceName:loaded.name,splitSides,date:localToday()}):null,[structural,loaded,sections,expanded,streetCount,splitSides]);
  const reviewRows=useMemo(()=>built?evidenceReviewRows(built.payloads):[],[built]);
  const undecided=reviewRows.filter(row=>!decisions[row.key]).length;
  const prepared=useMemo(()=>ready&&built?{...built,payloads:applyEvidenceDecisions(built.payloads,Object.fromEntries(Object.entries(decisions).map(([key,value])=>[key,value.decision])))}:null,[ready,built,decisions]);
  const addresses=prepared?.addresses??[];
  const sheets=useMemo(()=>prepared&&!capError?prepared.payloads.flatMap(payload=>splitWalkSheets(payload,cap)):[],[prepared,cap,capError]);
  const index=Math.min(part,Math.max(0,sheets.length-1)),sheet=sheets[index];
  const leftOut=excludedAddresses.size+excludedIssues.size+streets.filter(row=>row.choice===excluded).length;
  const recoveredNumbers=mapCorrections.filter(correction=>correction.registration_recovery!==undefined).length;
  function resetOutput(){setExpanded(null);setDecisions({});setMapOmissions([]);setMapCorrections([]);setWarnings([]);setWarningsAccepted(false);setPart(0);}
  function beginImageWork(id:string){if(workingImageRef.current&&workingImageRef.current!==id)return false;workingImageRef.current=id;setWorkingImage(id);return true;}
  function endImageWork(id:string){if(workingImageRef.current===id){workingImageRef.current=null;setWorkingImage(null);}}
  function invalidateImageContext(){imageContextRef.current++;setImageContextVersion(imageContextRef.current);imageEvidenceRef.current={};setImageEvidence({});setStreets(rows=>rows.map(row=>row.choiceOrigin==='image-layout'?{...row,choice:'',choiceOrigin:undefined}:row));resetOutput();}
  function toggleExcluded(set:(value:Set<number>)=>void,current:Set<number>,index:number){const next=new Set(current);if(next.has(index))next.delete(index);else next.add(index);set(next);setPart(0);}
  async function matchStreets(rows:ReviewStreet[],id:number,anchors:ImportedWalkAddress[],evidence=imageEvidenceRef.current){
    const active=rows.map((row,index)=>({row,index})).filter(({row})=>row.choice!==excluded);
    if(!active.length){setStreets(rows);return;}
    let district:Pick<MatchResponse,'district'|'snapshot'>|null=null;
    async function pass(proximity:boolean,selected:{id:number;zip:string}[]=[]):Promise<Map<number,Match>>{
      const result=new Map<number,Match>();
      for(let offset=0;offset<active.length;offset+=500){
        const batch=active.slice(offset,offset+500);
        const response=await postJson<MatchResponse>('/api/v3/walk/address-match',{
          rows:batch.map(({row:{query}})=>({street:query.street,city:query.city,state:query.state,zip:query.zip})),proximity,
          ...(proximity?{anchors:anchors.map(({house,street,unit,city,state,zip})=>({house,street,unit,city,state,zip})),selected_streets:selected,snapshot:district?.snapshot}:{})
        });
        if(id!==request.current)return result;
        if(district&&(district.snapshot!==response.snapshot||district.district!==response.district))throw new Error('The district changed while reading streets. Check matches again.');
        district={district:response.district,snapshot:response.snapshot};
        if(response.rows.length!==batch.length||response.rows.some((row,i)=>row.row!==i+1))throw new Error('The district service did not return every street in order. Check matches again.');
        response.rows.forEach((match,i)=>result.set(batch[i].index,match));
      }
      return result;
    }
    // Establish all uploaded locations before measuring any ambiguity. Inferred choices never
    // become evidence for another inferred choice, including across batches of a large upload.
    let result=await pass(false);if(id!==request.current)return;
    const selected=new Map<string,{id:number;zip:string}>();
    for(const {row,index} of active){
      const match=result.get(index)!;
      const known=row.choiceOrigin==='manual'?match.candidates.find(candidate=>candidateKey(candidate)===row.choice):
        match.status==='matched'&&match.method!=='proximity'&&match.candidates.length===1?match.candidates[0]:undefined;
      if(known)selected.set(candidateKey(known),{id:known.id,zip:known.zip_filter||''});
    }
    for(const image of Object.values(evidence)){
      const current=district as Pick<MatchResponse,'district'|'snapshot'>|null;
      if(!current||image.snapshot!==current.snapshot||image.district!==current.district)throw new Error('Image locations belong to an earlier district snapshot. Match the images again.');
      // Image evidence is applied only to its source rows below. Do not turn one
      // map's reviewed choices into global anchors for another map in the file.
    }
    if(anchors.length>500||selected.size>5000)setError('This file exceeds the proximity context limit (500 exact addresses or 5,000 established streets). Every entry is retained; choose the remaining street matches manually.');
    else result=await pass(true,[...selected.values()]);
    if(id!==request.current)return;
    setMatchedDistrict(district);
    setStreets(rows.map((row,index)=>{
      if(row.choice===excluded)return row;
      const match=result.get(index)!,previous=match.candidates.find(candidate=>candidateKey(candidate)===row.choice);
      if(row.choiceOrigin==='manual')return {...row,match,choice:previous?row.choice:''};
      if(match.status==='matched'&&match.method==='exact'&&match.candidates.length===1)return {...row,match,choice:candidateKey(match.candidates[0]),choiceOrigin:'exact' as const};
      const imageChoices=Object.values(evidence).filter(image=>!image.scope||row.source.sourceImageId===image.imageId||row.source.sourceFile===image.scope.sourceFile&&row.source.sourceSheet===image.scope.sourceSheet&&row.source.sourceRow>=image.scope.fromRow&&row.source.sourceRow<=image.scope.toRow).flatMap(image=>image.streets.filter(street=>imageNameKey(street.input??street.label)===imageNameKey(row.query.street)).map(street=>match.candidates.find(candidate=>candidate.id===street.id&&(!row.query.zip||candidate.zip_filter===row.query.zip)))).filter((candidate):candidate is Candidate=>!!candidate);
      const uniqueImageChoices=[...new Map(imageChoices.map(candidate=>[candidateKey(candidate),candidate])).values()];
      if(uniqueImageChoices.length===1)return {...row,match,choice:candidateKey(uniqueImageChoices[0]),choiceOrigin:'image-layout' as const};
      if(uniqueImageChoices.length>1)return {...row,match,choice:'',choiceOrigin:undefined};
      const inferred=match.status==='matched'&&match.method==='proximity'&&match.proximity?.confidence==='clear'?match.candidates.find(candidate=>candidate.id===match.selected_id):undefined;
      const exact=match.status==='matched'&&match.method!=='proximity'&&match.candidates.length===1?match.candidates[0]:undefined;
      const chosen=inferred??exact;
      return {...row,match,choice:chosen?candidateKey(chosen):'',choiceOrigin:inferred?'proximity':exact?'exact':undefined};
    }));
  }
  async function importAddresses(name:string,read:()=>Promise<AddressImportResult>){
    invalidateImageContext();
    const id=++request.current;setBusy(true);setError('');setLoaded(null);setStreets([]);imageEvidenceRef.current={};setImageEvidence({});setMatchedDistrict(null);resetOutput();setExcludedAddresses(new Set());setExcludedIssues(new Set());setBulkTown('');setBulkState('');setBulkZip('');
    try{
      const result=await read();if(id!==request.current)return;
      setLoaded({name,result});
      const rows=(result.streets??[]).map(source=>({source,query:{...source},choice:''}));setStreets(rows);
      if(!result.addresses.length&&!rows.length&&!result.issues.length&&!result.images?.length)setError('No addresses or street names found.');
      if(rows.length)await matchStreets(rows,id,result.addresses,{});
    }catch(e){if(id===request.current)setError(e instanceof Error?e.message:'The addresses could not be read.');}
    finally{if(id===request.current)setBusy(false);}
  }
  const upload=(file:File)=>importAddresses(file.name,()=>readAddressFile(file));
  const reviewPaste=()=>importAddresses('pasted-addresses.txt',async()=>parseAddressText(pastedText,'pasted-addresses.txt'));
  async function checkMatches(rows=streets,anchors=loaded?.result.addresses.filter((_,i)=>!excludedAddresses.has(i))??[],evidence=imageEvidenceRef.current){
    const id=++request.current;setBusy(true);setError('');resetOutput();
    // A failed recheck must not leave stale automatic choices available for sheet generation.
    setStreets(rows.map(row=>row.choice===excluded||row.choiceOrigin==='manual'?row:{...row,choice:'',match:undefined,choiceOrigin:undefined}));
    try{await matchStreets(rows,id,anchors,evidence);}catch(e){if(id===request.current)setError(e instanceof Error?e.message:'Street matches could not be checked.');}finally{if(id===request.current)setBusy(false);}
  }
  async function addImage(file:File){
    setBusy(true);setError('');try{const image=await readWalkImageFile(file);const images=[...loaded?.result.images??[],image];assertWalkImages(images);setLoaded(current=>current?{...current,result:{...current.result,images}}:{name:file.name,result:{addresses:[],streets:[],issues:[],sourceRows:0,images}});}catch(e){setError((e as Error).message);}finally{setBusy(false);}
  }
  function useImageEvidence(imageId:string,evidence:WalkImageEvidence|null,contextVersion:number){
    if(contextVersion!==imageContextRef.current)return;
    const previous=imageEvidenceRef.current,next={...previous};if(evidence)next[imageId]=evidence;else delete next[imageId];imageEvidenceRef.current=next;setImageEvidence(next);
    if(evidence||previous[imageId])void checkMatches(streets,loaded?.result.addresses.filter((_,i)=>!excludedAddresses.has(i))??[],next);
  }
  function appendImageText(result:AddressImportResult){
    if(!loaded)return;
    const rows=[...streets,...(result.streets??[]).map(source=>({source,query:{...source},choice:''}))];
    const next={...loaded.result,addresses:[...loaded.result.addresses,...result.addresses],streets:[...loaded.result.streets??[],...result.streets??[]],issues:[...loaded.result.issues,...result.issues],notes:[...loaded.result.notes??[],...result.notes??[]],sourceRows:loaded.result.sourceRows+result.sourceRows};
    setLoaded({...loaded,result:next});setStreets(rows);resetOutput();if(rows.length)void checkMatches(rows,next.addresses.filter((_,i)=>!excludedAddresses.has(i)));
  }
  function toggleAddress(index:number){
    if(!loaded)return;
    invalidateImageContext();
    const next=new Set(excludedAddresses);if(next.has(index))next.delete(index);else next.add(index);setExcludedAddresses(next);setPart(0);
    if(streets.length)void checkMatches(streets,loaded.result.addresses.filter((_,i)=>!next.has(i)),{});
  }
  function changeStreet(index:number,field:'street'|'city'|'state'|'zip',value:string){
    invalidateImageContext();
    setStreets(rows=>rows.map((row,i)=>i===index?{...row,query:{...row.query,[field]:value},match:undefined,choice:'',choiceOrigin:undefined}:row));resetOutput();
  }
  function applyLocation(city=bulkTown,state=bulkState,zip=bulkZip){
    invalidateImageContext();
    const rows=streets.map(row=>row.choiceOrigin==='manual'?row:{...row,query:{...row.query,city:row.query.city||city.trim(),state:row.query.state||state.trim(),zip:row.query.zip||zip.trim()},match:undefined,choice:row.choice===excluded?excluded:'',choiceOrigin:undefined});
    setStreets(rows);void checkMatches(rows,loaded?.result.addresses.filter((_,i)=>!excludedAddresses.has(i))??[],{});
  }
  function chooseStreet(index:number,choice:string){invalidateImageContext();const rows=streets.map((row,i)=>i===index?{...row,choice,choiceOrigin:choice?'manual' as const:undefined}:row);setStreets(rows);void checkMatches(rows,loaded?.result.addresses.filter((_,i)=>!excludedAddresses.has(i))??[],{});}
  async function makeSheets(){
    if(!matchedDistrict||pendingStreets)return;
    const id=++request.current;setBusy(true);setError('');resetOutput();
    try{
      const output:ImportedLocalGroup[]=[],serviceWarnings:string[]=[],omissions:unknown[]=[],corrections:MapPlacementCorrection[]=[];
      for(const section of sections){
        const chosen=streets.flatMap(row=>{const candidate=section.sources.includes(row.source)?row.match?.candidates.find(candidate=>candidateKey(candidate)===row.choice):undefined;return candidate?[{row,candidate}]:[];});
        const unique=[...new Map(chosen.map(({candidate})=>[candidateKey(candidate),candidate])).values()];
        const batches:Candidate[][]=[];let batch:Candidate[]=[],batchDoors=0;
        for(const candidate of unique){
          if(candidate.doors>10000)throw new Error(`${candidate.label} has more than 10,000 recorded doors. Add a ZIP filter or provide exact addresses to keep each request within the service limit.`);
          if(batch.length&&(batch.length===40||batchDoors+candidate.doors>10000)){batches.push(batch);batch=[];batchDoors=0;}
          batch.push(candidate);batchDoors+=candidate.doors;
        }
        if(batch.length)batches.push(batch);
        let groupNumber=0;
        async function readBatch(batch:Candidate[]):Promise<void>{
          let response:AddressListResponse;
          try{response=await postJson<AddressListResponse>('/api/v3/walk/address-list',{snapshot:matchedDistrict!.snapshot,streets:batch.map(candidate=>({id:candidate.id,zip:candidate.zip_filter||''})),split_sides:splitSides,local_groups:true});}
          catch(error){
            if(error instanceof ApiError&&error.status===400&&error.answered&&/exceed 10,000 voters/i.test(error.message)){
              if(batch.length===1)throw new Error(`${batch[0].label} exceeds 10,000 recorded people. Add a ZIP filter or provide exact addresses. No partial sheets were created.`);
              const middle=Math.ceil(batch.length/2);await readBatch(batch.slice(0,middle));if(id===request.current)await readBatch(batch.slice(middle));return;
            }
            throw error;
          }
          if(id!==request.current)return;
          if(response.snapshot!==matchedDistrict!.snapshot||response.district!==matchedDistrict!.district)throw new Error('The district changed. Check the street matches before creating sheets.');
          if(!Array.isArray(response.groups)||!response.groups.length)throw new Error('The district service did not return local street groups. No partial sheets were created.');
          const expected=new Set(batch.map(candidateKey)),seenSelections=new Map<string,Set<string>>(),allPeople=new Set<string>();
          for(const group of response.groups){
            const selectedPeople=new Set<string>(),byKey=new Map<string,string[]>();
            if(!Array.isArray(group.selections)||!group.selections.length)throw new Error('A local group has no recorded street membership. No partial sheets were created.');
            for(const selection of group.selections){
              const key=candidateKey({id:selection.id,zip_filter:selection.zip});
              if(!expected.has(key)||byKey.has(key)||!Array.isArray(selection.person_ids)||!selection.person_ids.length)throw new Error('The district service returned an incomplete street selection. No partial sheets were created.');
              const seen=seenSelections.get(key)??new Set<string>();
              for(const person of selection.person_ids){if(seen.has(person))throw new Error('A selected person appeared in more than one local group. No partial sheets were created.');seen.add(person);selectedPeople.add(person);}
              seenSelections.set(key,seen);byKey.set(key,selection.person_ids);
            }
            const people=group.payload.runs.flatMap(run=>run.doors.flatMap(door=>door.voters.map(voter=>voter.person_id)));
            if(people.length!==group.payload.totals.voters||new Set(people).size!==people.length||selectedPeople.size!==people.length||people.some(person=>!selectedPeople.has(person)||allPeople.has(person)))throw new Error('The district service returned an incomplete or repeated person list. No partial sheets were created.');
            people.forEach(person=>allPeople.add(person));
            const selected:ImportedWalkSelection[]=chosen.flatMap(({row,candidate})=>{const personIds=byKey.get(candidateKey(candidate));return personIds?[{source:row.source,payload:group.payload,personIds,streetId:candidate.id,city:candidate.city,state:candidate.state,zip:candidate.zip_filter||''}]:[];});
            const towns=[...new Set(selected.map(selection=>selection.city).filter(Boolean))];groupNumber++;
            output.push({key:`${section.key}:local-${groupNumber}`,sectionKey:section.key,label:`${section.label} · ${towns.join(', ')||'Local area'} ${groupNumber}`,reason:group.reason,selections:selected});
          }
          if([...expected].some(key=>!seenSelections.has(key)))throw new Error('A selected street is missing from the local groups. No partial sheets were created.');
          serviceWarnings.push(...response.warnings??[]);omissions.push(...response.map_placement_omissions??[]);corrections.push(...response.map_placement_corrections??[]);
        }
        for(const batch of batches){await readBatch(batch);if(id!==request.current)return;}
      }
      const checked=prepareImportedGroups(sections,output,{sourceName:loaded?.name??'Imported addresses',splitSides,date:localToday()});
      setExpanded(output);setExpandedSplit(splitSides);setMapOmissions(omissions);setMapCorrections(corrections);setWarnings([...new Set([...serviceWarnings,...checked.warnings])]);
    }catch(e){if(id===request.current)setError(e instanceof Error?e.message:'The street doors could not be read.');}finally{if(id===request.current)setBusy(false);}
  }
  async function exportSheets(format:'pdf'|'zip'|'csv'){
    if(!loaded||!sheets.length)return;setBusy(true);setError('');
    try{
      const session=await getSession();
      if(session.hosted&&!session.email)throw new Error('Sign in again before exporting this sheet.');
      if(format==='csv'){
        if(session.hosted)throw new Error('CSV export is unavailable on this hosted service.');
        downloadFile(walkFileName(loaded.name,localToday(),'csv'),'text/csv;charset=utf-8',buildWalkSheetsCsv(sheets));return;
      }
      const [{buildWalkSheetsPdf},{exportWalkZip}]=await Promise.all([import('../lib/walkPdf'),import('../lib/walkRouteExport')]);
      const excludedSources=[
        ...[...excludedAddresses].map(i=>loaded.result.addresses[i]),
        ...streets.filter(row=>row.choice===excluded).map(row=>row.source),
        ...[...excludedIssues].map(i=>{const issue=loaded.result.issues[i];return {sourceSheet:issue.sourceSheet,sourceCell:issue.sourceCell,sourceRow:issue.row,sourceText:issue.source??''};}),
      ].map(source=>({sourceSheet:source.sourceSheet,sourceCell:source.sourceCell,sourceRow:source.sourceRow,sourceText:source.sourceText??''}));
      const streetMatches=streets.flatMap(row=>{
        const candidate=row.match?.candidates.find(candidate=>candidateKey(candidate)===row.choice);if(!candidate)return [];
        return [{sourceSheet:row.source.sourceSheet,sourceCell:row.source.sourceCell,sourceRow:row.source.sourceRow,input:row.source.sourceText??row.source.street,
          streetId:candidate.id,street:candidate.label,city:candidate.city,state:candidate.state,zip:candidate.zip_filter||candidate.zip,method:row.choiceOrigin??'exact',
          ...(row.choiceOrigin==='proximity'&&row.match?.proximity?{distanceMeters:row.match.proximity.distance_m??undefined,anchorCount:row.match.proximity.anchor_count}:{})}];
      });
      const provenance={source:loaded.name,district:matchedDistrict?.district,snapshot:matchedDistrict?.snapshot,sourceRows:addresses,excludedSources,streetMatches,imageEvidence:Object.values(imageEvidence),mapPlacementOmissions:mapOmissions,mapPlacementCorrections:mapCorrections,addressReviews:addressReviewEntries(sheets,Object.fromEntries(Object.entries(decisions).map(([key,value])=>[key,value.at]))),localGroups:expanded?.map(group=>({key:group.key,sectionKey:group.sectionKey,label:group.label,reason:group.reason,sourceRows:group.selections.map(selection=>selection.source)}))};
      const notes=[...(loaded.result.notes??[]).map(note=>`${sourceLabel(note)}: ${note.text}`),...warnings];
      if(loaded.result.addresses.some((_,i)=>!excludedAddresses.has(i)))notes.push('Supplied exact addresses are retained with blank voter fields and no assumed coordinates.');
      const watermark=session.hosted&&session.email?watermarkText(session.email):'';
      if(format==='zip')await exportWalkZip(sheets,{area:loaded.name,includeCsv:!session.hosted,watermark,results:{},history:{},unsaved:new Set(),provenance,notes,originalLayout:true});
      else{const {doc}=await buildWalkSheetsPdf(sheets,{area:loaded.name,watermark});doc.save(walkFileName(loaded.name,localToday(),'pdf'));}
    }catch(e){setError(e instanceof Error?e.message:'The sheets could not be exported.');}finally{setBusy(false);}
  }
  const suggested=loaded?.result.addresses.find(address=>!!address.city);
  return <div className="walk-address-sheets">
    <h2>Sheets from your addresses</h2>
    <p className="walk-hint">Paste addresses or street names, or upload CSV, TSV, TXT, JSON, XLSX or an image. Terrier reads address fields, text lists, scattered workbook cells, and printed text in pictures.</p>
    <p className="walk-hint">Exact addresses stay as supplied. Street names use recorded doors from the selected district after review. Recorded voter details are included for selected streets. Supplied exact addresses retain blank voter fields. Repeated source entries are retained. Workbook sections and distant locations receive separate local maps; the door cap applies to each local list.</p>
    <div className="walk-upload">
      <input ref={input} type="file" accept=".csv,.tsv,.txt,.json,.xlsx,.png,.jpg,.jpeg,.webp,.gif,.bmp" hidden aria-label="Upload an address file" onChange={e=>{const f=e.target.files?.[0];e.target.value='';if(f)void upload(f);}}/>
      <input ref={imageInput} type="file" accept=".png,.jpg,.jpeg,.webp,.gif,.bmp" hidden aria-label="Add a map or address image" onChange={e=>{const f=e.target.files?.[0];e.target.value='';if(f)void addImage(f);}}/>
      <button className="walk-button walk-primary" type="button" disabled={blocked} onClick={()=>input.current?.click()}><Upload size={15}/>{busy?'Preparing…':'Upload address file'}</button>
      <button className="walk-button" type="button" disabled={blocked} onClick={()=>imageInput.current?.click()}>Add map or address image</button>
      {loaded&&<span className="walk-file">{loaded.name}</span>}
    </div>
    <details className="walk-paste">
      <summary>Paste addresses or street names</summary>
      <label htmlFor="walk-pasted-addresses">Addresses or street names</label>
      <p className="walk-hint" id="walk-paste-hint">Use one address or street per line, or paste columns copied from a spreadsheet. Include a town or ZIP when street names repeat. Reviewing this text replaces the current import.</p>
      <textarea id="walk-pasted-addresses" aria-describedby="walk-paste-hint" value={pastedText} disabled={blocked} rows={6} placeholder={'123 Main St, Hartford, CT 06103\nOak Rd, Hartford, CT 06103'} onChange={e=>setPastedText(e.target.value)}/>
      <button className="walk-button" type="button" disabled={blocked||!pastedText.trim()} onClick={()=>void reviewPaste()}>Review pasted list</button>
    </details>
    {(error||capError)&&<p className="walk-note" role="alert">{error||capError}</p>}
    {loaded&&<>
      <p className="walk-summary">Found {loaded.result.addresses.length} exact {loaded.result.addresses.length===1?'address':'addresses'} and {countText(streets.length,'street request')}{loaded.result.issues.length?` · ${countText(loaded.result.issues.length,'entry')} ${loaded.result.issues.length===1?'needs':'need'} review`:''}.</p>
      {!!loaded.result.images?.length&&<WalkImageReview images={loaded.result.images} contextVersion={imageContextVersion} beginWork={beginImageWork} endWork={endImageWork} requests={streets.filter(row=>row.choice!==excluded).map(row=>row.query)} disabled={blocked} anchors={loaded.result.addresses.filter((_,i)=>!excludedAddresses.has(i))} known={streets.flatMap(row=>{const c=row.choiceOrigin==='exact'||row.choiceOrigin==='manual'?row.match?.candidates.find(c=>candidateKey(c)===row.choice):undefined;return c?[{id:c.id,zip:c.zip_filter||''}]:[];})} onEvidence={(id,evidence)=>useImageEvidence(id,evidence,imageContextVersion)} onAppend={appendImageText}/>}
      {!!loaded.result.notes?.length&&<details><summary>{countText(loaded.result.notes.length,'heading or context entry')} retained for review</summary><ul>{loaded.result.notes.map((note,i)=><li key={i}>{sourceLabel(note)}: {note.text}</li>)}</ul></details>}
      {!!loaded.result.addresses.length&&<details open={streets.length>0||loaded.result.addresses.some(address=>!!address.note)}>
        <summary>Review exact addresses ({loaded.result.addresses.length})</summary>
        <p className="walk-hint">Keep requested doors checked. An address beside a business name or meeting label may be a starting location; uncheck it to leave it out explicitly.</p>
        <div className="walk-address-table-wrap"><table className="walk-address-table"><thead><tr><th>Include</th><th>Source</th><th>Exact address</th><th>Context</th></tr></thead><tbody>
          {loaded.result.addresses.map((address,i)=><tr key={i}><td><input type="checkbox" aria-label={`Include ${address.address}`} checked={!excludedAddresses.has(i)} disabled={blocked} onChange={()=>toggleAddress(i)}/></td><td>{sourceLabel(address)}</td><td>{address.address}</td><td>{address.note||'—'}</td></tr>)}
        </tbody></table></div>
      </details>}
      {!!loaded.result.issues.length&&<div className="walk-note" role="alert">
        <p>{countText(unread,'unresolved entry')}. Fix the file and upload again, or explicitly leave out non-address text. Export waits until every entry is handled.</p>
        <ul>{loaded.result.issues.map((issue,i)=><li key={i}>{issue.sourceSheet?`${issue.sourceSheet} · `:''}{issue.sourceCell||`row ${issue.row}`}: {issue.source&&<strong>{issue.source} — </strong>}{issue.message} <label><input type="checkbox" checked={excludedIssues.has(i)} disabled={blocked} onChange={()=>toggleExcluded(setExcludedIssues,excludedIssues,i)}/> Leave out this entry</label></li>)}</ul>
      </div>}
      {!!streets.length&&<section aria-label="Review street matches">
        <h3>Review street matches{matchedDistrict?` · ${matchedDistrict.district}`:''}</h3>
        <p className="walk-hint">Unique name matches and clearly nearer matches are selected automatically using established locations from this file. Inferences show their distance and evidence; unclear choices stay open. Your manual selections stay unchanged. A heading does not set the town for every street.</p>
        <div className="walk-actions"><label>Town for missing locations <input aria-label="Town for streets without a town" value={bulkTown} disabled={blocked} onChange={e=>setBulkTown(e.target.value)}/></label><label>State <input aria-label="State for streets without a state" value={bulkState} disabled={blocked} style={{width:60}} onChange={e=>setBulkState(e.target.value)}/></label><label>ZIP <input aria-label="ZIP for streets without a ZIP" value={bulkZip} disabled={blocked} style={{width:80}} onChange={e=>setBulkZip(e.target.value)}/></label><button type="button" className="walk-button" disabled={blocked||!bulkTown.trim()&&!bulkState.trim()&&!bulkZip.trim()} onClick={()=>applyLocation()}>Apply location and recheck</button>
        {suggested&&<button type="button" className="walk-button" disabled={blocked} onClick={()=>applyLocation(suggested.city,suggested.state,'')}>Use {suggested.city} for streets without a town</button>}</div>
        <div className="walk-address-table-wrap"><table className="walk-address-table"><thead><tr><th>Source</th><th>Street / location from file</th><th>Recorded street in this district</th></tr></thead><tbody>
          {streets.map((row,i)=><tr key={i}><td>{sourceLabel(row.source)}</td><td>
            <input aria-label={`Street name ${i+1}`} value={row.query.street} disabled={blocked} onChange={e=>changeStreet(i,'street',e.target.value)}/>
            <div style={{display:'flex',gap:4,marginTop:5}}><input aria-label={`Town ${i+1}`} placeholder="Town (optional)" value={row.query.city} disabled={blocked} style={{minWidth:100,width:130}} onChange={e=>changeStreet(i,'city',e.target.value)}/><input aria-label={`State ${i+1}`} placeholder="State" value={row.query.state} disabled={blocked} style={{width:60}} onChange={e=>changeStreet(i,'state',e.target.value)}/><input aria-label={`ZIP ${i+1}`} placeholder="ZIP" value={row.query.zip} disabled={blocked} style={{width:80}} onChange={e=>changeStreet(i,'zip',e.target.value)}/></div>
          </td><td><select aria-label={`Match for street ${i+1}`} value={row.choice} disabled={blocked} onChange={e=>chooseStreet(i,e.target.value)}>
            <option value="">{row.match?.status==='none'?'No match — correct or leave out':row.match?'Choose a street…':'Check matches…'}</option>
            {row.match?.candidates.map(candidate=><option key={candidateKey(candidate)} value={candidateKey(candidate)}>{candidate.label} · {[candidate.city,candidate.state,candidate.zip].filter(Boolean).join(' ')} · {countText(candidate.doors,'recorded door')}</option>)}
            <option value={excluded}>Leave out this street request</option>
          </select>
          {row.choiceOrigin==='manual'&&row.choice?<div className="walk-hint">{row.choice===excluded?'Left out by you.':'Selected by you.'}</div>:
            row.choiceOrigin==='image-layout'?<div className="walk-hint"><strong>Matched using image surroundings</strong><div>Known streets and the nearby road layout narrowed this location. You can change the selection.</div></div>:
            row.choiceOrigin==='proximity'&&row.match?.proximity?<div className="walk-hint"><strong>Inferred by proximity</strong>{row.match.proximity.distance_m!==null?` · ${distanceText(row.match.proximity.distance_m)}`:''}{` · ${countText(row.match.proximity.anchor_count,'known location')} used`}<div>{row.match.proximity.reason}</div></div>:
            (row.match?.proximity?.reason||row.match?.reason)&&<div className="walk-hint">{row.match?.proximity?.reason||row.match?.reason}</div>}
          {row.match?.truncated&&<div className="walk-hint">More matches exist. Add the town or a more complete street name.</div>}</td></tr>)}
        </tbody></table></div>
        <div className="walk-actions"><button className="walk-button" type="button" disabled={blocked} onClick={()=>void checkMatches()}>Recheck street matches</button><button className="walk-button walk-primary" type="button" disabled={blocked||!!pendingStreets||!streetCount||!!unread||!!undecided} onClick={()=>void makeSheets()}>Create sheets from selected streets</button><span>{pendingStreets?`${countText(pendingStreets,'street')} ${pendingStreets===1?'needs':'need'} a choice`:`${countText(streetCount,'street')} selected`}</span></div>
      </section>}
      {expanded!==null&&expandedSplit!==splitSides&&<p className="walk-note">The even/odd setting changed. Create sheets again to calculate the new routes.</p>}
      {expanded!==null&&expandedSplit===splitSides&&!!mapCorrections.length&&<p className="walk-note" role="status">{countText(mapCorrections.length,'map location')} {mapCorrections.length===1?'uses':'use'} municipal parcel evidence. These locations identify properties; entrances are not confirmed. {mapOmissions.length?`${countText(mapOmissions.length,'record')} ${mapOmissions.length===1?'remains':'remain'} without confirmed map locations. `:''}Original locations and supporting evidence are retained in the ZIP manifest.</p>}
      {expanded!==null&&expandedSplit===splitSides&&!!recoveredNumbers&&<p className="walk-note" role="status">{countText(recoveredNumbers,'missing house number')} {recoveredNumbers===1?'was':'were'} recovered from matching source address fields and municipal parcel evidence. Original blank values are retained in the ZIP audit.</p>}
      {expanded!==null&&expandedSplit===splitSides&&!!reviewRows.length&&<details open>
        <summary>Review address evidence ({reviewRows.length})</summary>
        <p className="walk-hint">Each address below could not be fully confirmed. Your choice changes only the map pin and this review record; the printed street name, house number and the people at the door stay as recorded. Removing a pin from a row with a recovered house number keeps the recovered number.</p>
        <p className="walk-note" role="status">{undecided?`${undecided} ${undecided===1?'address needs':'addresses need'} a review decision`:'Every address has a review decision.'}</p>
        <div className="walk-address-table-wrap"><table className="walk-address-table"><thead><tr><th>Original address</th><th>Sheet address</th><th>Address status</th><th>Location</th><th>Reasons</th><th>Decision</th></tr></thead><tbody>
          {reviewRows.map(row=><tr key={row.key}><td>{row.original}</td><td>{row.sheet}</td><td>{row.status}</td><td>{row.location}</td>
            <td>{row.reasons.length?(row.evidence.address_status==='conflicting'?<strong>{row.reasons.join('; ')}</strong>:row.reasons.join('; ')):'—'}</td>
            <td><select aria-label={`Decision for ${row.sheet}`} value={decisions[row.key]?.decision??''} disabled={blocked} onChange={e=>{const value=e.target.value as ''|EvidenceChoice;setDecisions(current=>{const next={...current};if(value)next[row.key]={decision:value,at:new Date().toISOString()};else delete next[row.key];return next;});}}>
              <option value="">Needs review</option>
              {row.options.map(option=><option key={option.decision} value={option.decision}>{option.label}</option>)}
            </select></td></tr>)}
        </tbody></table></div>
      </details>}
      {!!warnings.length&&<div className="walk-note" role="alert"><p>The district records have {countText(warnings.length,'address warning')}. These entries stay on the sheets with the information recorded.</p><ul>{warnings.map((warning,i)=><li key={i}>{warning}</li>)}</ul><label><input type="checkbox" checked={warningsAccepted} disabled={blocked} onChange={e=>setWarningsAccepted(e.target.checked)}/> I reviewed these warnings and want to keep these recorded entries.</label></div>}
      {!!leftOut&&<details className="walk-note"><summary>{countText(leftOut,'source entry')} explicitly left out</summary><ul>
        {[...excludedAddresses].map(i=><li key={`a${i}`}>{sourceLabel(loaded.result.addresses[i])}: {loaded.result.addresses[i].address}</li>)}
        {streets.filter(row=>row.choice===excluded).map((row,i)=><li key={`s${i}`}>{sourceLabel(row.source)}: {row.source.street}</li>)}
        {[...excludedIssues].map(i=><li key={`i${i}`}>{loaded.result.issues[i].sourceCell||`Row ${loaded.result.issues[i].row}`}: {loaded.result.issues[i].source}</li>)}
      </ul></details>}
    </>}
    {sheets.length>0&&<>
      <p className="walk-summary">{countText(addresses.length,'door row')} and {countText(prepared?.voters??0,'recorded person','recorded people')} covered across {countText(sheets.length,'list')}{cap?` · Maximum ${cap} doors per list`:''}{leftOut?` · ${leftOut} source entries left out by your choice`:''}.</p>
      {!!undecided&&<p className="walk-note" role="status">{undecided} {undecided===1?'address needs':'addresses need'} a review decision before export.</p>}
      <div className="walk-actions">
        <button className="walk-button walk-primary" type="button" disabled={blocked||!!undecided} onClick={()=>void exportSheets('zip')}>Download all lists ZIP</button>
        <button className="walk-button" type="button" disabled={blocked||!!undecided} onClick={()=>void exportSheets('pdf')}>Export all lists PDF</button>
        {!hosted&&<button className="walk-button" type="button" disabled={blocked||!!undecided} onClick={()=>void exportSheets('csv')}>Download all lists CSV</button>}
        <label>Preview list <select aria-label="Address list preview" value={index} onChange={e=>setPart(Number(e.target.value))}>{sheets.map((s,i)=><option key={i} value={i}>List {i+1} of {sheets.length} · {s.query.import_group_label} · {s.totals.doors} doors</option>)}</select></label>
      </div>
      <div className="walk-address-table-wrap"><table className="walk-address-table">
        <thead><tr><th>Door</th><th>Precinct</th><th>Street</th><th>Address</th><th>Unit</th><th>Recorded people</th><th>Side</th><th>Notes</th></tr></thead>
        <tbody>{sheet?.runs.flatMap(run=>run.doors.map(door=><tr key={door.seq}><td>{door.seq}</td><td>{run.precinct||'—'}</td><td>{run.street}</td><td>{door.house}</td><td>{door.unit}</td><td>{door.voters.length?(showOptIns?door.voters.map(voter=><div key={voter.person_id} className={hasRecordedOptIn(voter.opt_ins)?'has-recorded-opt-in':undefined}>{voter.name}<WalkOptIns personId={voter.person_id} name={voter.name} value={voter.opt_ins} displayOnly/></div>):door.voters.map(voter=>voter.name).join('; ')):'—'}</td><td>{run.side==='both'?'Both':run.side}</td><td>{addresses[door.seq-1]?.note}</td></tr>))}</tbody>
      </table></div>
    </>}
  </div>;
}
