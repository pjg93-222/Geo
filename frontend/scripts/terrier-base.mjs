// Shared by the browser checks: the base URL comes from TERRIER_URL (default: a synthetic server on 8768) and port 8767,
// the live Terrier with real voter data, is always refused. Call this before any browser, network or file activity.
export function terrierBase(script,fallback='http://127.0.0.1:8768'){
  const base=(process.env.TERRIER_URL||fallback).replace(/\/+$/,'');
  if(new URL(base).port==='8767'){
    console.error(`Refusing to run ${script} against port 8767: that is the live app with real data. Point TERRIER_URL at a synthetic server (8768 by default).`);
    process.exit(2);
  }
  return base;
}
