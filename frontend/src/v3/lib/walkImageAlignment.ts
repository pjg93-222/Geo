// Image pixels are fitted to Web Mercator metres. Only surveyed/matched control points
// define the transform; OCR label centres are never treated as exact ground coordinates.
export type ImageControlPoint={x:number;y:number;lon:number;lat:number;label?:string};
export type ImageAlignment={x:[number,number,number];y:[number,number,number];rmsMeters:number;maxErrorMeters:number;coverage:number;verified:boolean;reason:string};
const R=6378137;
export function projectImageLocation(lon:number,lat:number):[number,number]{return [R*lon*Math.PI/180,R*Math.log(Math.tan(Math.PI/4+lat*Math.PI/360))];}
export function unprojectImageLocation(x:number,y:number):[number,number]{return [x/R*180/Math.PI,(2*Math.atan(Math.exp(y/R))-Math.PI/2)*180/Math.PI];}
function solve(matrix:number[][],rhs:number[]):[number,number,number]{
  const a=matrix.map((row,i)=>[...row,rhs[i]]);
  for(let col=0;col<3;col++){
    let pivot=col;for(let row=col+1;row<3;row++)if(Math.abs(a[row][col])>Math.abs(a[pivot][col]))pivot=row;
    if(Math.abs(a[pivot][col])<1e-9)throw new Error('Choose points spread across the image, away from a single straight line.');
    [a[col],a[pivot]]=[a[pivot],a[col]];const divisor=a[col][col];for(let j=col;j<4;j++)a[col][j]/=divisor;
    for(let row=0;row<3;row++)if(row!==col){const scale=a[row][col];for(let j=col;j<4;j++)a[row][j]-=scale*a[col][j];}
  }
  return a.map(row=>row[3]) as [number,number,number];
}
export function fitImageAlignment(points:ImageControlPoint[],width:number,height:number):ImageAlignment{
  if(points.length<3)throw new Error('Mark at least three matching places on the image and geographic map.');
  if(points.length>20||!Number.isFinite(width)||!Number.isFinite(height)||width<=0||height<=0)throw new Error('Invalid image alignment dimensions or point count.');
  if(points.some(p=>![p.x,p.y,p.lon,p.lat].every(Number.isFinite)||p.x<0||p.x>width||p.y<0||p.y>height||Math.abs(p.lon)>180||Math.abs(p.lat)>=85))throw new Error('Control points must be inside the image and have valid longitude and latitude.');
  const rows=points.map(p=>[p.x/width,p.y/height,1]),geo=points.map(p=>projectImageLocation(p.lon,p.lat)),origin=geo[0];
  const matrix=[0,1,2].map(i=>[0,1,2].map(j=>rows.reduce((sum,r)=>sum+r[i]*r[j],0)));
  const cx=solve(matrix,[0,1,2].map(i=>rows.reduce((sum,r,k)=>sum+r[i]*(geo[k][0]-origin[0]),0)));
  const cy=solve(matrix,[0,1,2].map(i=>rows.reduce((sum,r,k)=>sum+r[i]*(geo[k][1]-origin[1]),0)));
  const x:[number,number,number]=[cx[0]/width,cx[1]/height,cx[2]+origin[0]],y:[number,number,number]=[cy[0]/width,cy[1]/height,cy[2]+origin[1]];
  const det=x[0]*y[1]-x[1]*y[0];
  if(det>=-1e-8)throw new Error('The points reverse or flatten the image. Check that each point marks the same place on both maps.');
  const cos=Math.cos(points.reduce((sum,p)=>sum+p.lat,0)/points.length*Math.PI/180);
  const errors=points.map((p,i)=>Math.hypot(x[0]*p.x+x[1]*p.y+x[2]-geo[i][0],y[0]*p.x+y[1]*p.y+y[2]-geo[i][1])*cos);
  let area=0;for(let i=0;i<rows.length;i++)for(let j=i+1;j<rows.length;j++)for(let k=j+1;k<rows.length;k++)area=Math.max(area,Math.abs((rows[j][0]-rows[i][0])*(rows[k][1]-rows[i][1])-(rows[k][0]-rows[i][0])*(rows[j][1]-rows[i][1]))/2);
  const rmsMeters=Math.sqrt(errors.reduce((sum,v)=>sum+v*v,0)/errors.length),maxErrorMeters=Math.max(...errors);
  const distinctPixels=new Set(points.map(p=>`${p.x.toFixed(2)},${p.y.toFixed(2)}`)).size,distinctLocations=new Set(points.map(p=>`${p.lon.toFixed(7)},${p.lat.toFixed(7)}`)).size;
  const extent=Math.max(Math.hypot(cx[0],cy[0]),Math.hypot(cx[1],cy[1]))*cos;
  const independent=distinctPixels>=4&&distinctLocations>=4;
  const reason=!independent?'Add a fourth distinct point to check the fit; three places alone cannot measure alignment error.':area<.02?'Spread points over more of the image before using the alignment.':extent>50000?'This image covers too large an area for a local street overlay.':rmsMeters>40||maxErrorMeters>75?'The points disagree. Correct their locations before using the alignment.':'Fit checked against four or more points. Review road overlap before confirming.';
  return {x,y,rmsMeters,maxErrorMeters,coverage:area,verified:independent&&area>=.02&&extent<=50000&&rmsMeters<=40&&maxErrorMeters<=75,reason};
}
export function imagePixelToLocation(fit:ImageAlignment,x:number,y:number):[number,number]{return unprojectImageLocation(fit.x[0]*x+fit.x[1]*y+fit.x[2],fit.y[0]*x+fit.y[1]*y+fit.y[2]);}
export function locationToImagePixel(fit:ImageAlignment,lon:number,lat:number):[number,number]{
  const [mx,my]=projectImageLocation(lon,lat),dx=mx-fit.x[2],dy=my-fit.y[2],det=fit.x[0]*fit.y[1]-fit.x[1]*fit.y[0];
  return [(dx*fit.y[1]-dy*fit.x[1])/det,(dy*fit.x[0]-dx*fit.y[0])/det];
}
