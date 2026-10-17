import assert from 'node:assert/strict';

/** Complete synthetic address-list response, including the people required by the shared export renderer. */
export function importListResponse(streets,lookup,{district='co-05',snapshot='a'.repeat(64)}={}){
 const runs=streets.map((selection,index)=>{
  const street=lookup(selection.id);assert.ok(street,`No synthetic street ${selection.id}`);
  const lon=-104.8+index*.0002,lat=38.8;
  return {stop:index+1,street_id:street.id,street:street.label??street.street,side:'even',precinct:'P1',from_house:'2',to_house:'2',walk_m:25,lr:1,path:[[lon,lat],[lon+.0001,lat]],approach:[],doors:[{seq:index+1,house:'2',unit:'',city:street.city||'Example',state:street.state||'CO',zip:selection.zip||'80000',address:`2 ${street.label??street.street}, ${street.city||'Example'}, CO ${selection.zip||'80000'}`,warnings:[],lon,lat,placed_by:'geocode',voters:[{person_id:`synthetic-${street.id}`,name:`Resident ${street.id}`,phone:'5550101000',pri:{voted:1,eligible:2},gen:{voted:2,eligible:3},abs:{voted:1,eligible:3},party:'',age:30,gender:'',reg_date:'2020-01-01',last:null}]}]};
 });
 const result={district,snapshot,payload:{schema:'walk-list-1',generated_on:'2026-10-16',list:{key:null,title:'Synthetic import',kind:'standard',created_at:null},query:{split_sides:'1'},window:{primaries:['2024'],generals:['2024'],abs_rule:'Synthetic history'},bbox:[-104.81,38.79,-104.78,38.81],totals:{streets:runs.length,stops:runs.length,doors:runs.length,voters:runs.length,walk_m:runs.length*25},runs},selections:streets.map(street=>({id:street.id,zip:street.zip||'',person_ids:[`synthetic-${street.id}`]}))};
 return {...result,groups:[{key:'local-1',payload:result.payload,selections:result.selections,reason:'One local synthetic neighborhood.'}],warnings:[],map_placement_omissions:[]};
}
