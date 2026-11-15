// 前端渲染冒烟测试：用最小 DOM 桩验证 privacy/partner 页面模板拼接不抛错。
globalThis.localStorage={getItem:()=>null,setItem(){},removeItem(){}};
const [{privacyPage},{partnerPage}]=await Promise.all([
 import('./frontend/src/pages/privacy.js'),
 import('./frontend/src/pages/partner.js'),
]);

const datasets=[{id:1,name:'恒谱240批五项检验复核标签',source:'a.csv',rows:240,checksum:'x',created_at:1}];
const budgets=[{dataset_id:1,total_epsilon:4,total_delta:0.0001,spent_epsilon:0.5,spent_delta:0,remaining_epsilon:3.5,remaining_delta:0.0001,updated_at:1700000000}];
const payload={
 summary:'加噪口径下共 241 批复检事务，按支持度≥0.12、置信度≥0.65 筛出 2 条规则。',
 privacy:{epsilon:0.5,delta:0,mechanism:'laplace',cache_id:1,guarantee:'纯ε-DP保证。',composition_note:'顺序组合。',count_error_95:5.99,noise_scale:2},
 thresholds:{min_support:0.12,min_confidence:0.65},
 metrics:[{label:'加噪批次总数',value:241},{label:'筛选后规则',value:2}],
 table:{columns:[{key:'rule_id',label:'规则编号'},{key:'antecedent',label:'前项A'},{key:'lift',label:'提升度'}],rows:[{rule_id:'DP-R01',antecedent:'热稳定失败',lift:2.3}]},
 itemsets:[{itemset:'热稳定失败',size:1,noisy_count:80,support_pct:33.2,kept_frequent:true},{itemset:'罕见项',size:1,noisy_count:2,support_pct:0.8,kept_frequent:false}],
 method:'口径说明。',limitations:['局限1','局限2'],
};
const releases=[
 {id:1,name:'合作方月度共败概览',dataset_id:1,cache_id:1,epsilon:0.5,delta:0,mechanism:'laplace',min_support:0.12,min_confidence:0.65,status:'published',created_at:1700000000,published_at:1700000100,payload},
 {id:2,name:'中断待恢复',dataset_id:1,cache_id:1,epsilon:0.5,delta:0,mechanism:'laplace',min_support:0.12,min_confidence:0.65,status:'reserved',created_at:1700000200,payload:{}},
];
const ledger=[{id:1,kind:'charge',epsilon_spend:0.5,delta_spend:0,actor:'admin@demo.local',note:'扣费',created_at:1700000000},{id:2,kind:'reuse',epsilon_spend:0,delta_spend:0,actor:'analyst@demo.local',note:'复用',created_at:1700000200}];

// 桩：拦截页面模块对 API/store 的依赖。
const apiState={datasets,budgets,releases,ledger};
const {workspace}=await import('./frontend/src/stores/workspace.js');
const {session}=await import('./frontend/src/stores/session.js');
session.user={name:'林知',role:'admin',email:'admin@demo.local'};
workspace.meta={short:'assay-rules',name:'检验失败组合追因',workbench_title:'工作台',client:'恒谱',tagline:'x',entities:[],accent:'',style:'laboratory'};
const entities=await import('./frontend/src/api/entities.js');
entities.privacy.budgets=async()=>apiState.budgets;
entities.privacy.releases=async()=>apiState.releases;
entities.privacy.ledger=async()=>apiState.ledger;
entities.datasets.list=async()=>apiState.datasets;

let pass=0,fail=0;
const ok=(n,c)=>{if(c){pass++;console.log('  PASS',n);}else{fail++;console.log('  FAIL',n);}};

const html1=await privacyPage();
ok('内部隐私页渲染成功',html1.includes('差分隐私发布与预算核对'));
ok('含预算卡片',html1.includes('预算')&&html1.includes('3.5'));
ok('含发布记录与恢复按钮',html1.includes('恢复原次发布')&&html1.includes('合作方月度共败概览'));
ok('含流水表 charge/reuse',html1.includes('扣费')&&html1.includes('复用(0)'));
ok('含隐私保证说明',html1.includes('Laplace')&&html1.includes('误差'));

session.user={name:'何杉',role:'partner',email:'partner@demo.local'};
const html2=await partnerPage();
ok('合作方页渲染成功',html2.includes('差分隐私对外版'));
ok('合作方页只列已发布（无 reserved 状态徽章）',!html2.includes('待恢复'));
ok('合作方页不展示内部恢复按钮',!html2.includes('恢复原次发布'));
ok('含下载按钮',html2.includes('CSV 下载')&&html2.includes('JSON 下载'));
ok('含误差标签',html2.includes('95% 单计数误差'));
ok('含隐私说明',html2.includes('隐私说明')&&html2.includes('旧版归档'));

// 空数据兜底
apiState.releases=[];
const html3=await partnerPage();
ok('无发布时显示空态',html3.includes('暂无可查看的已发布统计'));

console.log(`\n结果：${pass} 通过 / ${fail} 失败`);
process.exit(fail?1:0);
