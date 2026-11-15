import {datasets,privacy} from '../api/entities.js';
import {workspace} from '../stores/workspace.js';
import {session} from '../stores/session.js';
import {heading} from '../components/common/shell.js';
import {table} from '../components/common/table.js';
import {esc,fmt,date,notify} from '../utils/dom.js';
import {submit} from '../hooks/forms.js';
import {ReleaseStatus} from '../constants/enums.js';

export async function privacyPage(){
 const [dsList,budgets,releaseList,ledgerRows]=await Promise.all([
   datasets.list(),privacy.budgets(),privacy.releases(),privacy.ledger(),
 ]);
 workspace.datasets=dsList;workspace.budgets=budgets;
 const budgetMap=Object.fromEntries(budgets.map(b=>[b.dataset_id,b]));
 const isAdmin=session.user.role==='admin';
 const canPublish=session.user.role==='admin'||session.user.role==='analyst';
 return heading('06 / PRIVACY PUBLISHING','差分隐私发布与预算核对','按数据批次共享隐私预算。同批次同隐私参数只取一次噪声并复用；阈值筛选与全部指标均从同一份加噪统计生成，发布载荷不含任何真实计数。','<button class="button" id="refresh-privacy">刷新核对 ↻</button>')
 +'<div class="privacy-grid">'
 // 左列：预算设定 + 发布表单
 +'<section class="panel"><div class="section-title"><h2>批次隐私预算</h2><span>负责人设定总额</span></div>'
 +'<form id="budget-form"><label>数据批次<select name="dataset_id" required>'+dsList.map(d=>'<option value="'+d.id+'">'+esc(d.name)+' · '+d.rows+' 条</option>').join('')+'</select></label><div class="parameter-grid"><label>总 ε（顺序组合累计）<input name="total_epsilon" type="number" step="0.1" min="0.1" max="10" value="4" required></label><label>总 δ（0=纯DP）<input name="total_delta" type="number" step="0.00001" min="0" max="0.01" value="0.0001" required></label></div><button type="submit" class="primary" '+(isAdmin?'':'disabled')+'>设定 / 调整总额</button><p class="hint">仅业务负责人可设定。新总额不能低于已消耗额度。ε 越大误差越小、隐私保护越弱。</p></form>'
 +'<div id="budget-cards" class="budget-list">'+budgets.map(b=>budgetCard(b,dsList)).join('')+'</div></section>'
 +'<section class="panel"><div class="section-title"><h2>发起差分隐私发布</h2><span>发布即绑定预算扣减</span></div>'
 +'<form id="release-form"><label>发布名称<input name="name" required minlength="2" value="合作方共败统计 · 加噪版"></label><label>数据批次<select name="dataset_id" required>'+dsList.map(d=>'<option value="'+d.id+'">'+esc(d.name)+'</option>').join('')+'</select></label><div class="parameter-grid"><label>单次 ε（0.05~2）<input name="epsilon" type="number" step="0.05" min="0.05" max="2" value="0.5" required></label><label>δ（0=Laplace 纯DP）<input name="delta" type="number" step="0.00001" min="0" max="0.001" value="0" required></label><label>最小支持度<input name="min_support" type="number" step="0.01" min="0.05" max="0.5" value="0.12" required></label><label>最小置信度<input name="min_confidence" type="number" step="0.01" min="0.2" max="0.95" value="0.65" required></label></div><button type="submit" class="primary" '+(canPublish?'':'disabled')+'>发布加噪统计 ↗</button><p class="hint">每次请求带幂等键：网络重试不会重复扣费或重新取噪。同批次同 (ε,δ) 的发布复用已存噪声，零额外扣费。'+(canPublish?'':'观察员仅可查看，不能发布。')+'</p></form>'
 +'<div class="method-sheet compact"><p class="eyebrow">PRIVACY GUARANTEE</p><h3>隐私保证与误差口径</h3><ul class="questions"><li><b>Laplace（δ=0）</b>：纯 ε-差分隐私。单计数噪声尺度 b=1/ε，95% 误差幅度≈'+esc(fmt(Math.LN20*2))+'b（约 3b）。</li><li><b>Gaussian（δ&gt;0）</b>：(ε,δ)-近似差分隐私，除概率 ≤δ 的事件外满足保证；噪声尺度 ≈1.64/ε，95% 幅度≈1.96 倍尺度。</li><li>同批次多次发布按顺序组合：总 ε、总 δ 为各次之和；复用噪声不追加预算。</li><li>加噪计数可能为负时归零展示；小计数项相对误差更大，仅供合作方总体判断。</li></ul></div></section>'
 // 右列：发布记录 + 流水
 +'<section><div class="section-title"><h2>发布记录</h2><span>'+releaseList.length+' 次</span></div><div class="schedule-list">'+(releaseList.map(r=>releaseCard(r,budgetMap,canPublish)).join('')||'<p class="empty">尚无发布。设定预算后可发起。</p>')+'</div></section>'
 +'<section class="panel"><div class="section-title"><h2>预算消耗流水</h2><span>'+ledgerRows.length+' 条</span></div>'+table(
   [{key:'id',label:'流水'},{key:'kind',label:'类型'},{key:'epsilon_spend',label:'ε 消耗'},{key:'delta_spend',label:'δ 消耗'},{key:'actor',label:'操作账号'},{key:'note',label:'说明'},{key:'created_at',label:'时间'}],
   ledgerRows.map(x=>({...x,kind:x.kind==='charge'?'扣费':'复用(0)',created_at:date(x.created_at)}))
 )+'</section></div>';
}

function budgetCard(b,dsList){
 const ds=dsList.find(d=>d.id===b.dataset_id);
 const pct=b.total_epsilon>0?Math.min(100,Math.round(b.spent_epsilon/b.total_epsilon*100)):0;
 return '<article class="budget-card"><div><strong>'+esc(ds?ds.name:('批次#'+b.dataset_id))+'</strong><p>ε 总额 '+esc(fmt(b.total_epsilon))+' · 已耗 '+esc(fmt(b.spent_epsilon))+' · 剩余 <b>'+esc(fmt(b.remaining_epsilon))+'</b></p><p>δ 总额 '+esc(fmt(b.total_delta))+' · 已耗 '+esc(fmt(b.spent_delta))+' · 剩余 '+esc(fmt(b.remaining_delta))+'</p></div><div class="budget-bar"><span style="width:'+pct+'%"></span></div><small>更新于 '+date(b.updated_at)+'</small></article>';
}

function releaseCard(r,budgetMap,canPublish){
 const p=r.payload||{};const priv=p.privacy||{};
 const statusBadge='<span class="status '+(r.status==='published'?'completed':r.status)+'">'+esc(ReleaseStatus[r.status]||r.status)+'</span>';
 return '<article class="release-card"><div><strong>'+esc(r.name)+'</strong><p>ε='+esc(fmt(r.epsilon))+' · δ='+esc(fmt(r.delta))+' · '+(r.mechanism==='laplace'?'Laplace 纯DP':'Gaussian 近似DP')+' · 缓存#'+r.cache_id+'</p><small>支持度≥'+esc(r.min_support)+' 置信度≥'+esc(r.min_confidence)+' · '+date(r.created_at)+' · '+statusBadge+'</small>'+(priv.guarantee?'<p class="hint">'+esc(priv.guarantee)+' 单计数95%误差幅度 ±'+esc(fmt(priv.count_error_95))+'。</p>':'')+'</div><div class="release-actions">'
 +(r.status==='reserved'?'<button class="button small" data-resume="'+r.id+'" '+(canPublish?'':'disabled')+'>恢复原次发布</button>':'')
 +(r.status==='published'?'<button class="text-button" data-dl="'+r.id+'" data-fmt="csv">CSV ↓</button><button class="text-button" data-dl="'+r.id+'" data-fmt="json">JSON ↓</button><button class="text-button" data-inspect="'+r.id+'">核对加噪明细</button>':'')
 +'</div></article>';
}

export function bindPrivacy(refresh){
 document.querySelector('#refresh-privacy').onclick=refresh;
 const budgetForm=document.querySelector('#budget-form');
 if(budgetForm){
  const fillBudget=dsId=>{
   const b=(workspace.budgets||[]).find(x=>x.dataset_id===Number(dsId));
   if(b){budgetForm.querySelector('[name=total_epsilon]').value=b.total_epsilon;budgetForm.querySelector('[name=total_delta]').value=b.total_delta;}
  };
  fillBudget(budgetForm.querySelector('[name=dataset_id]').value);
  budgetForm.querySelector('[name=dataset_id]').addEventListener('change',e=>fillBudget(e.target.value));
  submit(budgetForm,async f=>{
   await privacy.setBudget({dataset_id:Number(f.get('dataset_id')),total_epsilon:Number(f.get('total_epsilon')),total_delta:Number(f.get('total_delta'))});
   notify('批次隐私预算已保存');await refresh();
  });
 }
 submit(document.querySelector('#release-form'),async f=>{
  const body={name:f.get('name'),dataset_id:Number(f.get('dataset_id')),epsilon:Number(f.get('epsilon')),delta:Number(f.get('delta')),min_support:Number(f.get('min_support')),min_confidence:Number(f.get('min_confidence'))};
  const r=await privacy.publish(body);
  notify(r.status==='published'?'发布成功：预算已扣减（或命中复用零扣费）':'发布已预留，可在记录中恢复');
  await refresh();
  if(r.id)inspect(r.id);
 });
 document.querySelectorAll('[data-resume]').forEach(b=>b.onclick=async()=>{try{await privacy.resume(b.dataset.resume);notify('已恢复原次发布，未重复扣费');await refresh();}catch(e){notify(e.message,true);}});
 document.querySelectorAll('[data-dl]').forEach(b=>b.onclick=()=>privacy.download(b.dataset.dl,b.dataset.fmt).catch(e=>notify(e.message,true)));
 document.querySelectorAll('[data-inspect]').forEach(b=>b.onclick=()=>inspect(Number(b.dataset.inspect)));
}

async function inspect(id){
 try{
  const r=await privacy.getRelease(id);const p=r.payload||{};
  const rows=(p.itemsets||[]).filter(x=>x.kept_frequent);
  const html='<div class="inspect-mask" id="inspect-mask"><section class="panel inspect-panel"><div class="section-title"><h2>'+esc(r.name)+' · 加噪明细核对</h2><button class="text-button" id="inspect-close">关闭 ✕</button></div>'
   +'<p class="hint">'+esc(p.summary||'')+'</p>'
   +'<div class="metrics">'+(p.metrics||[]).map(m=>'<div><span>'+esc(m.label)+'</span><strong>'+esc(fmt(m.value))+'</strong></div>').join('')+'</div>'
   +'<h3>阈值筛出的频繁项集（全部来自同份加噪统计）</h3>'
   +table([{key:'itemset',label:'项集'},{key:'size',label:'项数'},{key:'noisy_count',label:'加噪计数'},{key:'support_pct',label:'加噪支持度(%)'}],rows)
   +'<h3>加噪规则</h3>'+table((p.table||{}).columns||[],(p.table||{}).rows||[])
   +'<p class="warning">'+esc((p.limitations||[]).join(' '))+'</p></section></div>';
  document.body.insertAdjacentHTML('beforeend',html);
  document.querySelector('#inspect-close').onclick=()=>document.querySelector('#inspect-mask').remove();
 }catch(e){notify(e.message,true);}
}
