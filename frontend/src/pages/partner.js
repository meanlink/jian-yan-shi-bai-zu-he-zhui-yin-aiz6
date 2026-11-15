import {privacy} from '../api/entities.js';
import {heading} from '../components/common/shell.js';
import {table} from '../components/common/table.js';
import {esc,fmt,date,notify} from '../utils/dom.js';

export async function partnerPage(){
 const all=await privacy.releases();
 const releaseList=all.filter(r=>r.status==='published');
 return heading('PARTNER / PRIVACY-SAFE STATISTICS','共败统计 · 差分隐私对外版','本页面仅展示已发布的加噪统计。每条发布都标注隐私参数、机制与误差幅度；无法从此数据推断任何单一批次的真实标签。','<button class="button" id="refresh-partner">刷新 ↻</button>')
 +'<section class="panel method-sheet"><p class="eyebrow">PRIVACY NOTICE · 隐私说明</p><h2>这些数字是怎么来的</h2><ol class="questions">'
 +'<li>后端对每个项集的失败批次计数加入校准随机噪声（Laplace 或 Gaussian），再由加噪计数推导支持度、置信度、提升度与阈值筛选结果。</li>'
 +'<li><b>Laplace（δ=0）</b>满足纯 ε-差分隐私：任一批次记录是否存在，输出分布几乎不受影响；<b>Gaussian（δ&gt;0）</b>满足 (ε,δ)-近似差分隐私。</li>'
 +'<li>同一数据批次的多次发布按顺序组合累计预算，ε 越小噪声越大；每次发布的 95% 单计数误差幅度随附在卡片上。</li>'
 +'<li>本页面不提供、也无法访问原始检验记录、内部精确分析或旧版归档；相关请求会被服务端直接拒绝。</li>'
 +'</ol></section>'
 +'<div class="partner-list">'+(releaseList.map(r=>releasePanel(r)).join('')||'<section class="panel empty">暂无可查看的已发布统计。请联系内部负责人发起差分隐私发布。</section>')+'</div>';
}

function releasePanel(r){
 const p=r.payload||{};const priv=p.privacy||{};
 return '<section class="panel release-panel"><div class="section-title"><h2>'+esc(r.name)+'</h2><span>'+date(r.published_at||r.created_at)+'</span></div>'
 +'<div class="privacy-tags"><span>机制：'+(r.mechanism==='laplace'?'Laplace 纯 ε-DP':'Gaussian (ε,δ)-DP')+'</span><span>ε='+esc(fmt(r.epsilon))+'</span><span>δ='+esc(fmt(r.delta))+'</span><span>95% 单计数误差 ±'+esc(fmt(priv.count_error_95))+'</span><span>噪声尺度 '+esc(fmt(priv.noise_scale))+'</span></div>'
 +'<p>'+esc(p.summary||'')+'</p>'
 +'<div class="metrics">'+(p.metrics||[]).map(m=>'<div><span>'+esc(m.label)+'</span><strong>'+esc(fmt(m.value))+'</strong></div>').join('')+'</div>'
 +'<p class="hint">口径：支持度≥'+esc(r.min_support)+'，置信度≥'+esc(r.min_confidence)+'。'+esc(priv.guarantee||'')+'</p>'
 +'<div class="section-title"><h3>加噪共败规则</h3><div><button class="text-button" data-dl="'+r.id+'" data-fmt="csv">CSV 下载 ↓</button><button class="text-button" data-dl="'+r.id+'" data-fmt="json">JSON 下载 ↓</button></div></div>'
 +table((p.table||{}).columns||[],(p.table||{}).rows||[])
 +(((p.table||{}).rows||[]).length===0?'<p class="empty">当前阈值下没有规则通过加噪筛选，可等待其他参数的发布。</p>':'')
 +'<details class="partner-method"><summary>查看隐私保证、误差与局限说明</summary><p class="hint">'+esc(p.method||'')+'</p><ul class="questions">'+(p.limitations||[]).map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul><p class="hint">'+esc(priv.composition_note||'')+'</p></details></section>';
}

export function bindPartner(refresh){
 document.querySelector('#refresh-partner').onclick=refresh;
 document.querySelectorAll('[data-dl]').forEach(b=>b.onclick=()=>privacy.download(b.dataset.dl,b.dataset.fmt).catch(e=>notify(e.message,true)));
}
