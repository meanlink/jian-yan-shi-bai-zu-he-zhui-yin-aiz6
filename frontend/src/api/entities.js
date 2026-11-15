import {api} from './client.js';
export const datasets={list:()=>api('/datasets'),get:id=>api('/datasets/'+id),create:data=>api('/datasets',{method:'POST',body:JSON.stringify(data)})};
export const jobs={list:()=>api('/jobs'),get:id=>api('/jobs/'+id),create:data=>api('/jobs',{method:'POST',body:JSON.stringify(data)}),approve:id=>api('/jobs/'+id+'/approve',{method:'POST'})};
export const reports={list:()=>api('/reports'),create:data=>api('/reports',{method:'POST',body:JSON.stringify(data)}),toggle:id=>api('/reports/'+id+'/toggle',{method:'POST'}),deliveries:()=>api('/deliveries')};
export const privacy={
 budgets:datasetId=>api('/privacy/budgets'+(datasetId?('?dataset_id='+datasetId):'')),
 setBudget:data=>api('/privacy/budgets',{method:'PUT',body:JSON.stringify(data)}),
 releases:datasetId=>api('/privacy/releases'+(datasetId?('?dataset_id='+datasetId):'')),
 getRelease:id=>api('/privacy/releases/'+id),
 publish:data=>api('/privacy/releases',{method:'POST',headers:{'X-Idempotency-Key':newIdempotencyKey()},body:JSON.stringify(data)}),
 resume:id=>api('/privacy/releases/'+id+'/resume',{method:'POST'}),
 ledger:datasetId=>api('/privacy/ledger'+(datasetId?('?dataset_id='+datasetId):'')),
 download:(id,format)=>downloadRelease(id,format),
};
export function newIdempotencyKey(){
 if(globalThis.crypto?.randomUUID)return crypto.randomUUID();
 return 'idem-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2,12);
}
async function downloadRelease(id,format){
 const {session}=await import('../stores/session.js');
 const r=await fetch('/api/privacy/releases/'+id+'/download?format='+format,{headers:{Authorization:'Bearer '+session.token}});
 if(!r.ok)throw new Error('下载失败，请重试');
 const u=URL.createObjectURL(await r.blob()),a=document.createElement('a');a.href=u;a.download='差分隐私发布-'+id+'.'+format;a.click();setTimeout(()=>URL.revokeObjectURL(u),1000);
}
