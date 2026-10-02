/** Save text content as a file download from the browser. */
export function downloadFile(name:string,type:string,content:string){
  const url=URL.createObjectURL(new Blob([content],{type}));const link=document.createElement('a');
  link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
