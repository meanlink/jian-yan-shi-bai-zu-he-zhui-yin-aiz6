import {api} from '../api/client.js';
import {session} from '../stores/session.js';
import {workspace} from '../stores/workspace.js';
import {esc} from '../utils/dom.js';
import {submit} from '../hooks/forms.js';
export function login(){
 const m=workspace.meta;
 document.querySelector('#app').innerHTML='<div class="login-scene"><section class="login-story"><div class="edition">FIELD NOTES / ANALYTICS</div><p class="eyebrow">'+esc(m.client)+'</p><h1>'+esc(m.name)+'</h1><p class="login-tag">'+esc(m.tagline)+'</p><div class="flow-lines">'+m.entities.map((v,i)=>'<div><span>0'+(i+1)+'</span><strong>'+esc(v)+'</strong></div>').join('')+'</div><p class="login-foot">从原始记录走到可复核的业务判断。</p></section><section class="login-form"><p class="eyebrow">WORKSPACE ACCESS</p><h2>进入业务现场</h2><p>选择角色后登录，继续当天的分析与复核。</p><form id="login"><label>演示账号<select name="email"><option value="admin@demo.local">林知 · 业务负责人（内部）</option><option value="analyst@demo.local">陈舟 · 分析员（内部）</option><option value="viewer@demo.local">周禾 · 观察员（内部）</option><option value="partner@demo.local">何杉 · 合作方代表（仅加噪统计）</option></select></label><label>密码<input name="password" type="password" value="Demo123!" required autocomplete="current-password"></label><button type="submit" class="primary">进入工作室 ↗</button></form><p class="hint">演示密码 Demo123! · 内部账号可接入、计算、审批与发布；合作方账号仅能读取和下载已发布的差分隐私统计，原始数据、内部分析与旧归档一律无权访问。</p></section></div>';
 submit(document.querySelector('#login'),async f=>{session.save(await api('/auth/login',{method:'POST',body:JSON.stringify(Object.fromEntries(f))}));location.hash='#/overview';});
}
