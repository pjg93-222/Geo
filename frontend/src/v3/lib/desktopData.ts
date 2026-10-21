import type {DesktopDataDistrict,DesktopDataPhase,DesktopDistrictData} from '../api/desktopData';

export const DATA_DISTRICT_LABEL:Record<DesktopDataDistrict,string>={'co-05':'Colorado 05','or-05':'Oregon 05','ct-01':'Connecticut 01'};
const PHASES:DesktopDataPhase[]=['checking','downloading','verifying','ready','offline','update-ready','error'];
const count=(value:unknown):value is number=>typeof value==='number'&&Number.isSafeInteger(value)&&value>=0;
function timestamp(value:unknown):string|undefined{
  if(typeof value!=='string')return;
  const parts=/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$/.exec(value);
  if(!parts)return;
  const [year,month,day,hour,minute,second]=parts.slice(1,7).map(Number),days=[31,year%4===0&&(year%100!==0||year%400===0)?29:28,31,30,31,30,31,31,30,31,30,31];
  if(month<1||month>12||day<1||day>days[month-1]||hour>23||minute>59||second>59)return;
  const date=new Date(value);return Number.isFinite(date.getTime())?date.toISOString():undefined;
}
export function dataDistrict(value:unknown):value is DesktopDataDistrict{return typeof value==='string'&&Object.hasOwn(DATA_DISTRICT_LABEL,value);}
/** Copy only named public provenance/progress fields. Native message/settings/path/grant fields are deliberately never relayed. */
export function desktopDistrictData(value:unknown):DesktopDistrictData|null{
  if(!value||typeof value!=='object'||Array.isArray(value))return null;
  try{
    const raw=value as Record<string,unknown>;
    if(raw.provider!=='github'||raw.repository!=='pjg93-222/Geo'||!dataDistrict(raw.district)||!PHASES.includes(raw.phase as DesktopDataPhase)
      ||typeof raw.cached!=='boolean'||typeof raw.canRefresh!=='boolean')return null;
    const result:DesktopDistrictData={provider:'github',repository:'pjg93-222/Geo',district:raw.district,phase:raw.phase as DesktopDataPhase,cached:raw.cached,canRefresh:raw.canRefresh};
    if(typeof raw.releaseTag==='string'&&/^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$/.test(raw.releaseTag)&&!raw.releaseTag.includes('..'))result.releaseTag=raw.releaseTag;
    if(count(raw.catalogueSequence)&&raw.catalogueSequence>0)result.catalogueSequence=raw.catalogueSequence;
    const checked=timestamp(raw.checkedAt),verified=timestamp(raw.verifiedAt);
    if(checked)result.checkedAt=checked;if(verified)result.verifiedAt=verified;
    if(count(raw.receivedBytes))result.receivedBytes=raw.receivedBytes;
    if(count(raw.totalBytes)&&raw.totalBytes>0)result.totalBytes=raw.totalBytes;
    if(result.totalBytes!==undefined&&result.receivedBytes!==undefined&&result.receivedBytes>result.totalBytes)delete result.receivedBytes;
    if(typeof raw.updateAvailable==='boolean')result.updateAvailable=raw.updateAvailable;
    return result;
  }catch{return null;}
}
export const districtDataBusy=(value:DesktopDistrictData):boolean=>['checking','downloading','verifying'].includes(value.phase);
export function districtDataSummary(value:DesktopDistrictData):string{
  const label=DATA_DISTRICT_LABEL[value.district];
  switch(value.phase){
    case 'checking':return `Checking GitHub for ${label} data.${value.cached?' Your verified local cache remains in use.':''}`;
    case 'downloading':return `Downloading ${label} data from GitHub.${value.cached?' Your current verified data remains in use.':''}`;
    case 'verifying':return `Verifying ${label} data.${value.cached?' Your current verified data remains in use.':''}`;
    case 'ready':return value.cached?'Using a verified local cache of the GitHub dataset.':'GitHub data downloaded and verified on this computer.';
    case 'offline':return value.cached?'GitHub check unavailable. Using the verified local cache.':'GitHub is unavailable and no verified local cache is ready.';
    case 'update-ready':return 'A newer GitHub dataset is downloaded and verified. Save your work, then reopen this district or Terrier to apply it.';
    case 'error':return value.cached?'GitHub data could not be updated. Using the verified local cache.':'GitHub district data is not ready.';
  }
}
export const dataBytes=(value:number):string=>`${(value/1048576).toFixed(value>=10485760?0:1)} MB`;

/** An event supersedes an outstanding read/refresh. Closing a district invalidates every late reply. */
export function districtDataReceiver(district:DesktopDataDistrict){
  let revision=0,closed=false;
  const matching=(value:unknown):DesktopDistrictData|null|undefined=>{
    if(value===null)return null;
    const data=desktopDistrictData(value);return data?.district===district?data:undefined;
  };
  return {
    event(value:unknown){if(closed)return;const data=matching(value);if(data!==undefined)revision++;return data;},
    read(){const ticket=++revision;return (value:unknown)=>closed||ticket!==revision?undefined:matching(value);},
    close(){closed=true;revision++;},
  };
}
