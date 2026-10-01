import {defineConfig,type Plugin} from 'vite';
import react from '@vitejs/plugin-react';

// The v3 stylesheets are written in design units: "30u" means 30 * var(--u), where --u is one pixel of the 2688-wide
// approved render (FIDELITY_SPEC section 2). This build-time transform rewrites "<number>u" into calc(<number> * var(--u)).
// No dependency is involved; hairlines stay in plain px.
function designUnits():Plugin{
  const unit=/(?<![\w#.\-])(-?\d*\.?\d+)u(?![\w-])/g;
  return {name:'design-units',enforce:'pre',transform(code,id){
    const path=id.split('?')[0];
    if(!path.endsWith('.css')||!path.includes('/v3/'))return null;
    return {code:code.replace(unit,(_m,n)=>`calc(${n} * var(--u))`),map:null};
  }};
}
export default defineConfig({
  plugins:[designUnits(),react()],base:'/',server:{host:'127.0.0.1'},preview:{host:'127.0.0.1',port:8767,strictPort:true},
  // MapLibre and React get their own long-lived chunks, so a screen change never re-downloads them
  build:{chunkSizeWarningLimit:1100,rollupOptions:{output:{manualChunks(id){
    if(id.includes('node_modules/maplibre-gl'))return 'maplibre';
    if(id.includes('node_modules/react')||id.includes('node_modules/scheduler'))return 'react';
  }}}},
});
