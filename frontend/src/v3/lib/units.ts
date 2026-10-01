// The design unit in CSS pixels: 1u = 100vw / 2688, clamped to the 1440..1920 px range (FIDELITY_SPEC section 2).
export const U_MIN=1440/2688;
export const U_MAX=1920/2688;
export const uPx=():number=>window.innerWidth<1280?0.65:Math.min(U_MAX,Math.max(U_MIN,window.innerWidth/2688));
/** CSS length for n design units, for inline styles. */
export const U=(n:number):string=>`calc(${n} * var(--u))`;
