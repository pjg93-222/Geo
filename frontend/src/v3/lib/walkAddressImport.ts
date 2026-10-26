// Address and street intake. Exact addresses and street requests remain distinct through review.
import {assertWalkImages,extractWalkWorkbookImages,isWalkImageFile,isWalkImageHeader,readWalkImageCell,readWalkImageFile} from './walkImageImport.ts';
import type {ImportedWalkImage} from './walkImageImport.ts';
export interface ImportSource {
  sourceRow: number;
  sourceImageId?: string;
  sourceFile?: string;
  sourceSheet?: string;
  sourceColumn?: number;
  sourceCell?: string;
  sourceText?: string;
  note?: string;
}
export interface ImportedWalkAddress extends ImportSource {
  address: string;
  house: string;
  street: string;
  unit: string;
  city: string;
  state: string;
  zip: string;
}

export interface ImportedWalkStreet extends ImportSource { street:string; city:string; state:string; zip:string }
export interface AddressImportIssue { row: number; message: string; source?: string; sourceSheet?: string; sourceColumn?:number; sourceCell?:string }
export interface AddressImportNote extends ImportSource { text:string }
export interface AddressImportResult { addresses: ImportedWalkAddress[]; issues: AddressImportIssue[]; sourceRows: number; streets?:ImportedWalkStreet[]; notes?:AddressImportNote[];images?:ImportedWalkImage[] }

type Field = 'address' | 'house' | 'street' | 'unit' | 'city' | 'state' | 'zip' | 'direction' | 'streetType' | 'postDirection';
type Fields = Partial<Record<Field, string>>;
type SourceRow = { cells: unknown[]; row: number };
const MAX_BYTES = 20 * 1024 * 1024;
const normalize = (value: string): string => value.replace(/\s+/g, ' ').trim();
const headerKey = (value: string): string => value.toLowerCase().replace(/[^a-z0-9]/g, '');
const HEADERS: Record<Field, readonly string[]> = {
  address: ['address', 'fulladdress', 'streetaddress', 'address1', 'addressline1', 'residentialaddress', 'residenceaddress', 'registrationaddress','situsaddress','siteaddress','propertyaddress','physicaladdress','mailingaddress','mailingstreetaddress','resaddress'],
  house: ['house', 'housenumber', 'houseno', 'housenum', 'streetnumber', 'streetno', 'addressnumber', 'number'],
  street: ['street', 'streetname', 'road', 'roadname'],
  unit: ['unit', 'unitnumber', 'unitno', 'apartment', 'apt', 'apartmentnumber', 'address2', 'addressline2', 'suite'],
  city: ['city', 'town', 'municipality', 'locality','towncity','citytown','rescity'],
  state: ['state', 'statecode', 'province', 'region'],
  zip: ['zip', 'zipcode', 'zip5', 'postalcode', 'postcode','zipcode5','reszip'],
  direction: ['predir', 'predirection', 'streetpredirection', 'streetprefix', 'prefixdirection'],
  streetType: ['streettype', 'streetsuffix', 'roadtype'],
  postDirection: ['postdir', 'postdirection', 'streetpostdirection', 'suffixdirection'],
};
const fieldFor = (value: string): Field | undefined => (Object.keys(HEADERS) as Field[]).find(field => HEADERS[field].includes(headerKey(value)));
const blank = (value: unknown): boolean => value == null || (typeof value === 'string' && !value.trim());
const emptyResult = (): AddressImportResult => ({ addresses: [], streets:[], notes:[], images:[], issues: [], sourceRows: 0 });
const UNIT = /(?:\s+|,\s*)((?:apt\.?|apartment|unit|suite|ste\.?|lot|room|rm\.?|bldg\.?|building|floor|fl\.?)\s*#?\s*\S.*|#\s*\S.*)$/i;
const HOUSE = /^(\d+[a-z]?(?:[-/]\d+[a-z]?)?(?:\s+\d\/\d)?)\s+(.+)$/i;
const STREET_END=/\b(?:st(?:reet)?|rd|road|dr(?:ive)?|ave(?:nue)?|blvd|boulevard|ct|court|cir(?:cle)?|ln|lane|way|loop|pkwy|parkway|pl(?:ace)?|ter(?:race)?|trl|trail|path|pike|hwy|highway|turnpike|plaza)(?:\.?)(?:\s+(?:n|s|e|w|ne|nw|se|sw|north|south|east|west))?$/i;
const TITLE=/^(?:(?:[a-z]{2}|[a-z]+\s+county)\s+)?(?:door\s*knock(?:ing)?|walk|walking|canvass(?:ing)?|address|street|route|volunteer|contact)(?:\s+(?:list|sheet|addresses|streets|routes|assignment|plan|area|schedule|name|names|data|report|for|\d+))*\s*:?(?:\s*\d{4}[-/]\d{1,2}[-/]\d{1,2})?$/i;
const GROUP_TITLE=/^group\s+\d+[a-z]?(?:\s*\([^()]+\))?$/i;
const PLACE_TITLE=/^[a-z][a-z .'-]*\s+(?:nature|regional|community)\s+park$/i;
const colName=(number:number):string=>{let value=number,out='';while(value>0){value--;out=String.fromCharCode(65+value%26)+out;value=Math.floor(value/26);}return out;};
const sourceOf=(row:number,filename:string,sheet?:string,column?:number,text?:string):ImportSource=>({sourceRow:row,sourceFile:filename,...sheet?{sourceSheet:sheet}:{},...column?{sourceColumn:column,sourceCell:`${colName(column)}${row}`}:{},...text?{sourceText:text}:{}});

/** Infer a missing comma only when a single street suffix gives an unambiguous city boundary. */
function locationCommas(raw:string,city=''):string{
  if(raw.includes(','))return raw;
  const tail=/^(.*?)\s+([a-z]{2})\s+(\d{5}(?:-\d{4})?)$/i.exec(raw);
  if(!tail)return raw;
  const body=tail[1],location=`${tail[2]} ${tail[3]}`;
  if(city&&body.toLowerCase().endsWith(` ${city.toLowerCase()}`))return `${body.slice(0,-city.length).trim()}, ${city}, ${location}`;
  const words=body.split(/\s+/),boundaries:number[]=[];
  for(let i=1;i<words.length-1;i++)if(STREET_END.test(words.slice(0,i+1).join(' '))&&words.slice(i+1).every(word=>/^[a-z][a-z.'-]*$/i.test(word)))boundaries.push(i+1);
  if(boundaries.length===1){const at=boundaries[0];return `${words.slice(0,at).join(' ')}, ${words.slice(at).join(' ')}, ${location}`;}
  if(!boundaries.length&&STREET_END.test(body))return `${body}, ${location}`;
  throw new Error('The street and town are unclear in this full address. Add commas or separate street and city columns.');
}

function streetFromFields(fields:Fields,source:ImportSource):ImportedWalkStreet|null{
  if(fields.house)return null;
  const raw=locationCommas(fields.address||[fields.direction,fields.street,fields.streetType,fields.postDirection].filter(Boolean).join(' '),fields.city);
  if(!raw||HOUSE.test(raw.split(',')[0]))return null;
  const section=/^(.*?)\s*\(([^()]*(?:\bsection\b|\bsegment\b|\bportion\b)[^()]*)\)(?:\s*,.*)?$/i.exec(raw);
  if(section&&STREET_END.test(section[1].trim()))throw new Error(`The "${section[2]}" restriction needs exact addresses or defined street boundaries. This entry cannot be expanded to the whole street.`);
  if(fields.unit||UNIT.test(raw.split(',')[0]))throw new Error('An apartment or unit needs an exact house number. Add the house number; this entry cannot be expanded to a whole street.');
  const parts=raw.split(',').map(normalize).filter(Boolean),street=parts[0];
  if(!/[a-z]/i.test(street)||/^(?:po\s*box|p\.?o\.?\s*box)/i.test(street)||(!fields.street&&!STREET_END.test(street)&&!/^\b(?:us|state|county)?\s*(?:route|highway)\s+\d+[a-z]?$/i.test(street)))return null;
  // Reuse strict location parsing with a temporary number; the number is never returned or exported.
  const parsed=addressFromFields({...fields,address:`1 ${raw}`},source.sourceRow,source.sourceFile??'',source.sourceSheet);
  if(parsed.unit)throw new Error('An apartment or unit needs an exact house number. Add the house number; this entry cannot be expanded to a whole street.');
  return {...source,street:parsed.street,city:parsed.city,state:parsed.state,zip:parsed.zip};
}

function stringCell(value: unknown): string {
  if (value == null) return '';
  if (typeof value === 'string') return normalize(value);
  if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  throw new Error('Address fields must contain text or numbers, not dates, booleans or nested objects.');
}

function shownUnit(unit: string): string {
  return !unit ? '' : /^(?:#|apt\b|apartment\b|unit\b|suite\b|ste\b|lot\b|room\b|rm\b|building\b|bldg\b|floor\b|fl\b)/i.test(unit) ? unit : `#${unit}`;
}

function mergeField(inline: string, separate: string, field: string): string {
  const key = (text: string): string => text.toUpperCase().replace(/[\s.,#]/g, '');
  const unitKey = (text: string): string => key(text.replace(/^(?:apt\.?|apartment|unit|suite|ste\.?)\s*/i, ''));
  const compare = field === 'unit' ? unitKey : key;
  if (inline && separate && compare(inline) !== compare(separate)) throw new Error(`The ${field} in the address conflicts with its separate ${field} column.`);
  return separate || inline;
}

/** Full comma-separated addresses or a street address plus explicit location columns. */
function addressFromFields(fields: Fields, row: number, filename: string, sheet?: string): ImportedWalkAddress {
  let raw = fields.address || '';
  if (!raw) raw = [fields.house, fields.direction, fields.street, fields.streetType, fields.postDirection].filter(Boolean).join(' ');
  else if (fields.house && !HOUSE.test(raw.split(',')[0])) raw = `${fields.house} ${raw}`;
  if (!raw) throw new Error('Missing address. Include a house number and street name.');
  let note='';
  const labelled=/^(?:meeting(?:\s+(?:place|location|point))?|meet\s+at|start(?:ing)?\s+(?:point|address)|address|location|office|anchor)\s*:\s*(\d.*)$/i.exec(raw);
  if(labelled){note=normalize(raw.slice(0,raw.indexOf(':')));raw=labelled[1];}
  // A ZIP closes the location. Keep following organization/context text explicitly as a note.
  const annotated=/^(.*(?:,|\s)\s*[a-z]{2}\s+\d{5}(?:-\d{4})?)\s+([^,].*)$/i.exec(raw);
  if(annotated){raw=annotated[1];note=[note,normalize(annotated[2])].filter(Boolean).join(' · ');}
  raw=locationCommas(raw,fields.city);
  const parts = raw.split(',').map(normalize).filter(Boolean);
  let line = parts.shift() || '', city = '', state = '', zip = '', unit = '';
  if (parts.length && /^(?:#|apt\.?\s|apartment\s|unit\s|suite\s|ste\.?\s|lot\s|room\s|rm\.?\s|bldg\.?\s|building\s|floor\s|fl\.?\s)/i.test(parts[0])) {
    unit = parts.shift()!;
  }
  if (parts.length) {
    const last = parts[parts.length - 1];
    const tail = /^(?:(.*?)\s+)?([a-z]{2})(?:\s+(\d{5}(?:-\d{4})?))?$/i.exec(last);
    if (tail) { city = tail[1] || ''; state = tail[2]; zip = tail[3] || ''; parts.pop(); }
    else if (/^\d{5}(?:-\d{4})?$/.test(last)) {
      zip = last; parts.pop();
      if (parts.length && /^[a-z]{2}$/i.test(parts[parts.length - 1])) state = parts.pop()!;
    }
    if (parts.length === 1 && !city) city = parts.pop()!;
    if (parts.length) throw new Error('The address has extra comma-separated fields. Use columns named address, unit, city, state and zip.');
  }
  const unitMatch = UNIT.exec(line);
  if (unitMatch) { unit = mergeField(unitMatch[1], unit, 'unit'); line = normalize(line.slice(0, unitMatch.index)); }
  const house = HOUSE.exec(line);
  if (!house || !/[a-z]/i.test(house[2])) throw new Error('Include an exact house number and street name, such as 123 Main St.');
  if (fields.house) mergeField(house[1], fields.house, 'house number');
  if(fields.address&&fields.street)mergeField(house[2],[fields.direction,fields.street,fields.streetType,fields.postDirection].filter(Boolean).join(' '),'street');
  if(fields.address&&!fields.street){
    const words=house[2].split(/\s+/),key=(value:string)=>value.toUpperCase().replace(/\./g,'');
    const directions:Record<string,string>={NORTH:'N',SOUTH:'S',EAST:'E',WEST:'W',NORTHEAST:'NE',NORTHWEST:'NW',SOUTHEAST:'SE',SOUTHWEST:'SW'};
    const direction=(value:string)=>directions[key(value)]||key(value);
    if(fields.direction&&direction(words[0])!==direction(fields.direction))throw new Error('The address conflicts with its separate pre direction column.');
    if(fields.postDirection&&direction(words.at(-1)!)!==direction(fields.postDirection))throw new Error('The address conflicts with its separate post direction column.');
    if(fields.streetType){
      const suffixes:Record<string,string>={STREET:'ST',ROAD:'RD',DRIVE:'DR',AVENUE:'AVE',BOULEVARD:'BLVD',COURT:'CT',CIRCLE:'CIR',LANE:'LN',PLACE:'PL',TERRACE:'TER',TRAIL:'TRL',PARKWAY:'PKWY',HIGHWAY:'HWY'};
      const suffix=(value:string)=>suffixes[key(value)]||key(value);
      const last=direction(words.at(-1)!);const actual=words.at(['N','S','E','W','NE','NW','SE','SW'].includes(last)?-2:-1)||'';
      if(suffix(actual)!==suffix(fields.streetType))throw new Error('The address conflicts with its separate street type column.');
    }
  }
  // Ranges and unusual house numbers are retained exactly; never expand them into guessed addresses.
  unit = mergeField(unit, fields.unit || '', 'unit');
  city = mergeField(city, fields.city || '', 'city');
  state = mergeField(state, fields.state || '', 'state');
  zip = mergeField(zip, fields.zip || '', 'ZIP');
  const streetLine = [house[1], house[2], shownUnit(unit)].filter(Boolean).join(' ');
  const location = [state, zip].filter(Boolean).join(' ');
  return { sourceRow: row, sourceFile: filename, ...(sheet ? { sourceSheet: sheet } : {}),
    address: [streetLine, city, location].filter(Boolean).join(', '), house: house[1], street: house[2], unit, city, state, zip,...note?{note}:{} };
}

function addRow(out: AddressImportResult, row: number, source: string, fields: () => Fields, filename: string, sheet?: string,column?:number): void {
  out.sourceRows++;
  const provenance=sourceOf(row,filename,sheet,column,source);
  try { const values=fields(),street=streetFromFields(values,provenance);if(street)out.streets!.push(street);else out.addresses.push({...addressFromFields(values, row, filename, sheet),...provenance}); }
  catch (error) { out.issues.push({ row, source, ...(sheet ? { sourceSheet: sheet } : {}),...column?{sourceColumn:column,sourceCell:`${colName(column)}${row}`}:{}, message: error instanceof Error ? error.message : 'This address could not be read.' }); }
}

const displayCell=(value:unknown):string=>typeof value==='object'&&value!==null?'[object]':String(value??'');
const cleanEntry=(value:string):string=>normalize(value.replace(/^\s*[•●▪*-]\s+/,''));

function looksLikeRecord(value:unknown):boolean {
  if(typeof value!=='string'&&typeof value!=='number')return false;
  const raw=cleanEntry(String(value));
  if(TITLE.test(raw))return false;
  try{return !!streetFromFields({address:raw},{sourceRow:0})||!!addressFromFields({address:raw},0,'');}catch{return false;}
}

/** Headers may follow cover titles. Without headers, each independent address/street cell is retained. */
function parseTable(rows: SourceRow[], filename: string, sheet?: string): AddressImportResult {
  const out=emptyResult();
  const imageColumns=new Set<number>();
  for(const {cells} of rows)cells.forEach((value,index)=>{if(typeof value==='string'&&isWalkImageHeader(value))imageColumns.add(index);});
  const imageOnlyTable=imageColumns.size>0&&!rows.some(({cells})=>cells.some(value=>typeof value==='string'&&['address','street'].includes(fieldFor(value)??'')));
  const prepared=rows.map(({cells,row})=>({row,cells:cells.map((value,index)=>{
    if(typeof value!=='string')return value;
    if(isWalkImageHeader(value))return '';
    const source={sourceFile:filename,sourceSheet:sheet,sourceRow:row,sourceCell:`${colName(index+1)}${row}`};
    const read=readWalkImageCell(value,source,imageColumns.has(index));
    if(read.image){out.images!.push(read.image);assertWalkImages(out.images!);}
    if(read.note)out.notes!.push(read.note);
    if(read.handled)return '';
    if(imageOnlyTable&&value.trim()&&!looksLikeRecord(value)){out.notes!.push({...source,text:value});return '';}
    return value;
  })}));
  const nonempty=prepared.filter(({cells})=>cells.some(cell=>!blank(cell)));
  if(!nonempty.length)return out;
  const headerFields=(cells:unknown[])=>cells.map(value=>typeof value==='string'?fieldFor(value):undefined);
  const headerCandidate=(cells:unknown[])=>{
    const fields=headerFields(cells);
    return fields.some(field=>field==='address'||field==='street')&&!cells.some((value,i)=>!blank(value)&&!fields[i]&&(looksLikeRecord(value)||/^\d/.test(String(value))));
  };
  const firstHeader=nonempty.findIndex(({cells})=>headerCandidate(cells));
  const firstRecord=nonempty.findIndex(({cells})=>cells.some(looksLikeRecord));
  const initialFields=headerFields(nonempty[0].cells);
  if(firstHeader<0&&initialFields.filter(Boolean).length>=2)throw new Error(`Add a column named address, or house number and street${sheet?` in sheet ${sheet}`:''}.`);
  let columns:Map<Field,number>|null=null,headerWidth=0;
  const addNote=(row:number,column:number,source:string)=>out.notes!.push({...sourceOf(row,filename,sheet,column,source),text:source});
  for(const [rowIndex,{cells,row}] of nonempty.entries()){
    const fieldsHere=headerFields(cells);
    const primary=columns?.get('address')??columns?.get('street');
    const isHeader=headerCandidate(cells)&&(primary===undefined||fieldsHere[primary]==='address'||fieldsHere[primary]==='street');
    if(isHeader){
      columns=new Map();headerWidth=cells.length;
      for(const [index,field] of fieldsHere.entries()){
        if(!field)continue;
        if(columns.has(field))throw new Error(`More than one ${field} column was found${sheet?` in sheet ${sheet}`:''}. Keep one column for each address field.`);
        columns.set(field,index);
      }
      continue;
    }
    const occupied=cells.map((value,index)=>({value,index})).filter(({value})=>!blank(value));
    const source=cells.map(displayCell).join(', ');
    if(columns){
      const selected=columns;
      // Repeated section titles do not become missing-house errors or location defaults.
      if(occupied.length===1&&typeof occupied[0].value==='string'&&(TITLE.test(normalize(occupied[0].value))||GROUP_TITLE.test(normalize(occupied[0].value)))){addNote(row,occupied[0].index+1,displayCell(occupied[0].value));continue;}
      addRow(out,row,source,()=>{
        if(cells.slice(headerWidth).some(cell=>!blank(cell)))throw new Error('This row has more values than the header. Quote addresses containing commas.');
        const fields:Fields={};for(const [field,index] of selected)fields[field]=stringCell(cells[index]);return fields;
      },filename,sheet,(selected.get('address')??selected.get('street')??0)+1);
      continue;
    }
    const candidates=occupied.filter(({value})=>looksLikeRecord(value));
    // Headerless delimited addresses may have separate city/state/ZIP cells. Two address-like
    // cells represent two requests; they must never be joined into a fabricated address.
    const contiguous=occupied.every(({index},i)=>i===0||index===occupied[i-1].index+1);
    const separateHouse=occupied.length>=2&&/^\d+[a-z]?(?:[-/]\d+[a-z]?)?$/i.test(String(occupied[0].value))&&looksLikeRecord(occupied[1].value);
    if(occupied.length>1&&contiguous&&(candidates.length===1&&candidates[0]===occupied[0]||separateHouse)){
      addRow(out,row,source,()=>({address:separateHouse?
        [occupied.slice(0,2).map(({value})=>stringCell(value)).join(' '),...occupied.slice(2).map(({value})=>stringCell(value))].join(', '):
        occupied.map(({value})=>stringCell(value)).join(', ')}),filename,sheet,occupied[0].index+1);
      continue;
    }
    for(const {value,index} of occupied){
      const raw=displayCell(value),text=typeof value==='string'?cleanEntry(value):raw;
      if(typeof value==='string'&&(TITLE.test(text)||GROUP_TITLE.test(text)||(firstRecord>=0&&rowIndex<firstRecord&&PLACE_TITLE.test(text))||(firstHeader>=0&&rowIndex<firstHeader&&!looksLikeRecord(text)))){addNote(row,index+1,raw);continue;}
      const lines=typeof value==='string'?value.split(/\r\n|\n|\r/).map(cleanEntry).filter(Boolean):[];
      if(lines.length>1&&lines.every(looksLikeRecord))for(const line of lines)addRow(out,row,line,()=>({address:line}),filename,sheet,index+1);
      else addRow(out,row,raw,()=>({address:typeof value==='string'?text:stringCell(value)}),filename,sheet,index+1);
    }
  }
  return out;
}

/** Strict CSV reader: multiline quoted cells, escaped quotes and physical source line numbers. */
function scanDelimited(text: string, delimiter: string): SourceRow[] {
  const rows: SourceRow[] = [];
  let cells: string[] = [], cell = '', quoted = false, closed = false, line = 1, startLine = 1;
  const finishCell = (): void => { cells.push(cell); cell = ''; closed = false; };
  const finishRow = (): void => { finishCell(); rows.push({ cells, row: startLine }); cells = []; };
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"') {
        if (text[i + 1] === '"') { cell += '"'; i++; } else { quoted = false; closed = true; }
      } else {
        cell += ch;
        if (ch === '\n' || (ch === '\r' && text[i + 1] !== '\n')) line++;
      }
    } else if (ch === delimiter) finishCell();
    else if (ch === '\r' || ch === '\n') {
      finishRow(); if (ch === '\r' && text[i + 1] === '\n') i++;
      line++; startLine = line;
    } else if (closed) {
      if (!/\s/.test(ch)) throw new Error(`Row ${startLine}: unexpected text after a closing quote.`);
    } else if (ch === '"') {
      if (cell.trim()) throw new Error(`Row ${startLine}: a quote appears inside an unquoted field.`);
      cell = ''; quoted = true;
    } else cell += ch;
  }
  if (quoted) throw new Error(`Row ${startLine}: a quoted field is missing its closing quote.`);
  if (cell || cells.length || closed) finishRow();
  return rows;
}

function delimiterFor(text: string): string {
  const counts: Record<string, number> = { ',': 0, '\t': 0, ';': 0 };
  let quoted = false;
  for (const ch of text.slice(0,64000)) { if (ch === '"') quoted = !quoted; else if (!quoted && ch in counts) counts[ch]++; }
  return ['\t', ';'].reduce((best, candidate) => counts[candidate] > counts[best] ? candidate : best, ',');
}

function parseJson(text: string, filename: string): AddressImportResult {
  let value: unknown;
  try { value = JSON.parse(text); } catch { throw new Error('This JSON file is not valid JSON.'); }
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    const object = value as Record<string, unknown>;
    value = Array.isArray(object.addresses)&&Array.isArray(object.streets)?[...object.addresses,...object.streets]:object.addresses ?? object.streets ?? object.rows ?? object.records ?? object.data ?? [value];
  }
  if (!Array.isArray(value)) throw new Error('Use a JSON array of address strings or objects, or an object with an addresses array.');
  const out = emptyResult();
  if(value.length&&value.every(entry=>Array.isArray(entry)))return parseTable(value.map((cells,i)=>({cells:cells as unknown[],row:i+1})),filename);
  for (const [i, entry] of value.entries()) {
    addRow(out, i + 1, typeof entry === 'string' ? entry : JSON.stringify(entry), () => {
      if (typeof entry === 'string') return { address: normalize(entry) };
      if (!entry || Array.isArray(entry) || typeof entry !== 'object') throw new Error('Each JSON row must be an address string or an object with address fields.');
      const fields: Fields = {};
      for (const [key, val] of Object.entries(entry)) {
        const field = fieldFor(key);
        if (!field) continue;
        if (field in fields) throw new Error(`More than one ${field} field was found in this JSON row.`);
        fields[field] = stringCell(val);
      }
      return fields;
    }, filename);
  }
  return out;
}

/** Readable text formats. Every nonempty data row is either retained or reported; duplicates stay visible. */
export function parseAddressText(input: string, filename = 'addresses.txt'): AddressImportResult {
  if (input.length > MAX_BYTES) throw new Error('Address files must be 20 MB or smaller.');
  const text = input.replace(/^\ufeff/, '');
  if (/[\u0000\ufffd]/.test(text)) throw new Error('The file contains binary or unreadable text. Save it as UTF-8 CSV, TXT, JSON or XLSX.');
  const extension = filename.split('.').pop()?.toLowerCase();
  if (extension === 'json' || /^[\s]*[\[{]/.test(text)) return parseJson(text, filename);
  if (extension === 'tsv') return parseTable(scanDelimited(text, '\t'), filename);
  if (extension === 'csv') return parseTable(scanDelimited(text, delimiterFor(text)), filename);
  if (extension && !['txt', 'text', ''].includes(extension)) throw new Error('Use CSV, TSV, TXT, JSON or XLSX. Export other documents to one of these formats first.');
  // Plain text means one complete address per line, including any commas in that address.
  // A recognized header or tab separators also permits a table saved with a .txt extension.
  const delimiter = delimiterFor(text);
  const hasHeader=text.split(/\r\n|\r|\n/).some(line=>line.split(delimiter).some(cell=>['address','street'].includes(fieldFor(cell.replace(/^"|"$/g,''))??'')));
  if (delimiter === '\t' || delimiter===';' || hasHeader) return parseTable(scanDelimited(text, delimiter), filename);
  return parseTable(text.split(/\r\n|\r|\n/).map((line, i) => ({ cells: [line], row: i + 1 })), filename);
}

/** Excel workbooks read all worksheets. A bad worksheet prevents a partial import from masquerading as complete. */
export async function readAddressFile(file: Pick<File, 'name' | 'text' | 'arrayBuffer'> & { size?: number;type?:string }): Promise<AddressImportResult> {
  if(isWalkImageFile(file.name,file.type))return {...emptyResult(),images:[await readWalkImageFile(file)]};
  if ((file.size ?? 0) > MAX_BYTES) throw new Error('Address files must be 20 MB or smaller.');
  if (!/\.xlsx$/i.test(file.name)) return parseAddressText(await file.text(), file.name);
  const {default:readXlsxFile}=await import('read-excel-file/universal');
  const buffer=await file.arrayBuffer();
  if(buffer.byteLength>MAX_BYTES)throw new Error('Address files must be 20 MB or smaller.');
  const media=extractWalkWorkbookImages(new Uint8Array(buffer),file.name);
  let sheets;
  try { sheets = await readXlsxFile(buffer, { trim: false, parseNumber: value => value }); }
  catch { throw new Error('This Excel workbook could not be read. Use an unencrypted .xlsx file, or export it as CSV.'); }
  const out = emptyResult();
  out.images!.push(...media.images);out.notes!.push(...media.notes);
  for (const sheet of sheets) {
    const parsed = parseTable(sheet.data.map((cells, i) => ({ cells, row: i + 1 })), file.name, sheet.sheet);
    out.addresses.push(...parsed.addresses);out.streets!.push(...parsed.streets??[]);out.notes!.push(...parsed.notes??[]);out.images!.push(...parsed.images??[]);assertWalkImages(out.images!); out.issues.push(...parsed.issues); out.sourceRows += parsed.sourceRows;
  }
  return out;
}
