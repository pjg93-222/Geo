export type DesktopDataDistrict='co-05'|'or-05'|'ct-01';
export type DesktopDataPhase='checking'|'downloading'|'verifying'|'ready'|'offline'|'update-ready'|'error';
export interface DesktopDistrictData {
  provider:'github';repository:'pjg93-222/Geo';district:DesktopDataDistrict;phase:DesktopDataPhase;
  cached:boolean;canRefresh:boolean;releaseTag?:string;catalogueSequence?:number;checkedAt?:string;verifiedAt?:string;
  receivedBytes?:number;totalBytes?:number;updateAvailable?:boolean;
}
/** Native responses are untrusted until the frontend whitelist validates them. No settings or credentials cross this interface. */
export interface DesktopDataBridge {
  districtData?:()=>Promise<unknown>;
  refreshDistrictData?:()=>Promise<unknown>;
  onDistrictData?:(callback:(value:unknown)=>void)=>unknown;
}
