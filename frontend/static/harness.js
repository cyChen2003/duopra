import { getLanguage } from './i18n.js?v=20261027-duopra-r9-personal';
const tr=(zh,en)=>getLanguage()==='zh'?zh:en;
const button=document.createElement('button');button.className='harness-launch';button.id='harness-launch';
button.dataset.version='duopra-harness';
button.innerHTML='<span class="h-dot"></span><span>后台助手 · DeepSeek Harness</span>';
button.setAttribute('aria-haspopup','dialog');document.body.append(button);
const modal=document.createElement('dialog');modal.className='harness-dialog';modal.id='harness-dialog';modal.setAttribute('aria-labelledby','harness-heading');
modal.innerHTML=`<div class="h-top"><div><div class="h-eyebrow">Duopra · DEEPSEEK HARNESS</div><h2 id="harness-heading">后台助手 / Background assistant</h2></div><button class="h-close" aria-label="关闭 Close">×</button></div>
<div class="h-tabs" role="tablist"><button data-tab="configuration" role="tab" aria-selected="true">配置</button><button data-tab="activity" role="tab" aria-selected="false">执行轨迹</button><button data-tab="protocol" role="tab" aria-selected="false">协议与工具</button></div>
<section class="h-page" data-page="configuration"><p class="h-note">前台 Duopra · 后台联网搜索、网页读取与业务工具。配置作用于本浏览器，Key 不进入前台模型或录音调试包。</p><form id="harness-form">
<label class="h-hero">API Key<input name="api_key" type="password" autocomplete="new-password" placeholder="仅写入，不回显 / Write only"></label>
<p class="h-endpoint" id="harness-endpoint" aria-live="polite"></p>
<div class="h-presets"><button type="button" class="h-preset" id="harness-preset">恢复 DeepSeek 官方默认</button><button type="button" class="h-disclose" id="harness-disclose" aria-expanded="false" aria-controls="harness-advanced">高级设置</button></div>
<div class="h-advanced" id="harness-advanced" data-open="false"><div class="h-fields">
<label class="h-wide">API Base URL<input name="base_url" type="url" placeholder="https://api.deepseek.com/anthropic" required autocomplete="off"></label>
<label>API 协议<select name="protocol"><option value="deepseek">DeepSeek 原生 API</option><option value="openai-compatible">OpenAI-compatible API</option></select></label>
<label>Model name<input name="model" placeholder="deepseek-flash" required autocomplete="off"></label>
<label>后台总预算（秒）<input name="deadline_seconds" type="number" min="30" max="600" value="180" required></label>
<label>前台可选委派<select name="enabled"><option value="true">开启 assistant.query</option><option value="false">关闭后台委派</option></select></label></div></div>
<div class="h-actions"><button class="h-button" type="submit">保存配置</button><span class="h-message" id="harness-message" role="status"></span></div></form>
<p class="h-note">凭证仅保存在服务器内存，12小时过期；切换API地址不会自动携带旧Key。原有直调与工具发现保留。</p></section>
<section class="h-page" data-page="activity" hidden><p class="h-note">“模型委派”来自前台真实私有动作；手动测试不进入前台上下文，也不算模型成功。</p><textarea id="harness-test-task" class="h-test-input" aria-label="手动后台测试任务" placeholder="请用计算工具计算 138 × 0.8。"></textarea><div class="h-actions"><button id="harness-test" class="h-button secondary">仅测试后台</button><span class="h-message" id="harness-test-message" role="status"></span></div><div id="harness-activity" style="margin-top:20px"><p class="h-empty">尚无后台任务。配置API后，可语音提问或手动检查后台链路。</p></div></section>
<section class="h-page" data-page="protocol" hidden><p class="h-note">前台只提交完整 task；后台工具名和参数Schema不会进入前台朗读条件。官方Harness负责多步工具执行。</p><pre class="h-protocol">前台：assistant.query
arguments：只有 task，一个完整自然语言任务

前台回执（完整执行证据见轨迹）：
{ "status": "done | need_info | error",
  "answer": "面向用户的简短自然语言回答",
  "missing_information": [] }

done：所有适用步骤实际完成
need_info：列明缺失的用户信息
error：如实告知失败和未完成的部分</pre><div id="harness-handoff-rules" class="h-handoff-rules"></div><div id="harness-tool-list" class="h-tools"></div><p class="h-note">官方联网搜索、网页读取，以及前台同一份68项业务工具目录：7项真实数据或本地计算、51项明确标注的演示夹具、10项需个人授权。日程与待办由前台本地数据库执行；后台助手不获得个人数据授权。参考资料仅包含演示目录，没有企业规章。</p></section>`;
document.body.append(modal);
const form=modal.querySelector('form'),message=modal.querySelector('#harness-message');
let configuration=null,lastSequence=0;const tasks=new Map();
let replyContract=null;
// Server-side defaults (harness_backend.public_config). Keep these in sync with it;
// the POST field allowlist is enforced server-side, so no new named input may be added.
const DEFAULT_BASE_URL='https://api.deepseek.com/anthropic',DEFAULT_PROTOCOL='deepseek';
let DEFAULT_MODEL='deepseek-v4-flash';
const DEEPSEEK_HOST='api.deepseek.com';
const endpointLine=modal.querySelector('#harness-endpoint');
const advanced=modal.querySelector('#harness-advanced'),disclose=modal.querySelector('#harness-disclose');
const PROTOCOL_LABEL={deepseek:'DeepSeek 原生 API','openai-compatible':'OpenAI-compatible API'};
function hostOf(value){try{return new URL(value).hostname;}catch{return '';}}
// Endpoint paths can suggest a protocol; explicit selection controls plugin registration.
function impliedProtocol(value){
  let url;try{url=new URL(value);}catch{return null;}
  const path=url.pathname.replace(/\/+$/,'');
  if(path.endsWith('/anthropic'))return 'deepseek';
  if(/\/(v1\/)?(chat\/)?completions$/.test(path)||path.endsWith('/v1'))return 'openai-compatible';
  return null;
}
function setAdvanced(open){advanced.dataset.open=String(open);disclose.setAttribute('aria-expanded',String(open));}
function renderEndpoint(){
  if(!endpointLine)return;
  const base=form.elements.base_url.value.trim(),protocol=form.elements.protocol.value,model=form.elements.model.value.trim();
  const host=hostOf(base)||base||'未设置';
  endpointLine.replaceChildren();
  const summary=document.createElement('span');
  summary.className='h-endpoint-summary';
  summary.textContent=(PROTOCOL_LABEL[protocol]||protocol)+' · '+host+(model?' · '+model:'');
  endpointLine.append(summary);
  // State the consequence plainly rather than only naming the protocol.
  if(protocol!=='deepseek'){
    const warn=document.createElement('span');warn.className='h-endpoint-hint';
    warn.textContent='此协议下不注册联网搜索/网页读取,后台只有业务工具';
    endpointLine.append(warn);
  }
  const implied=impliedProtocol(base);
  if(implied&&implied!==protocol){
    const hint=document.createElement('span');hint.className='h-endpoint-hint';
    hint.textContent='该地址的路径形态通常对应 '+(PROTOCOL_LABEL[implied]||implied);
    const fix=document.createElement('button');fix.type='button';fix.className='h-inline-fix';
    fix.textContent='改用它';
    fix.onclick=()=>{form.elements.protocol.value=implied;renderEndpoint();};
    endpointLine.append(hint,fix);
  }
}
async function loadCapabilities(){
  try{
    replyContract=await fetch('/static/harness-contract.json',{cache:'no-store'}).then(r=>r.json());
    const host=modal.querySelector('#harness-tool-list');host.replaceChildren();
    for(const name of configuration?.web_tools||[]){const chip=document.createElement('span');chip.className='h-chip live';chip.textContent=name+' · 后台联网工具（需要可用 API）';host.append(chip);}
    for(const tool of configuration?.business_tools||[]){const chip=document.createElement('span');chip.className='h-chip';
      chip.textContent=tool.name+' · '+(tool.name==='timezone.offset'?'本地时区计算':({live:'真实数据/计算',fixture:'演示数据：固定结果',local:'本地日程/待办（前台授权使用）',local_authorization_required:'需个人授权',custom:'自定义工具：你的服务'}[tool.runtime_mode]||tool.runtime_mode));host.append(chip);}
    const rules=modal.querySelector('#harness-handoff-rules');
    if(rules){rules.replaceChildren();for(const rule of replyContract.status_rules){
      const item=document.createElement('p');item.textContent=rule.label+' → '+rule.foreground_action;rules.append(item);}
      const note=document.createElement('p');note.className='h-note';note.textContent=replyContract.acceptance_note;rules.append(note);}
  }catch{}
}
// Four kinds of feedback: ongoing status, completion, warning, error.
function status(text,kind='info'){if(kind===true)kind='error';message.textContent=text;for(const name of ['done','warn','error'])message.classList.toggle(name,kind===name);}
function switchTab(name){for(const tab of modal.querySelectorAll('[data-tab]'))tab.setAttribute('aria-selected',String(tab.dataset.tab===name));for(const page of modal.querySelectorAll('[data-page]'))page.hidden=page.dataset.page!==name;}
function closeModal(){modal.classList.remove('h-in');modal.close();}
modal.querySelector('.h-close').onclick=closeModal;
button.onclick=()=>{modal.showModal();requestAnimationFrame(()=>modal.classList.add('h-in'));void loadConfig();};
for(const tab of modal.querySelectorAll('[data-tab]'))tab.onclick=()=>switchTab(tab.dataset.tab);
disclose.onclick=()=>setAdvanced(advanced.dataset.open!=='true');
modal.querySelector('#harness-preset').onclick=()=>{
  form.elements.base_url.value=DEFAULT_BASE_URL;
  form.elements.protocol.value=DEFAULT_PROTOCOL;
  form.elements.model.value=DEFAULT_MODEL;
  renderEndpoint();
  status('已填入 DeepSeek 官方默认，仍需粘贴 API Key 后保存。','info');
};
for(const name of ['base_url','protocol','model'])form.elements[name].addEventListener('input',renderEndpoint);
form.elements.protocol.addEventListener('change',renderEndpoint);
async function loadConfig(){try{
  const response=await fetch('/api/harness/config',{cache:'no-store'});
  if(!response.ok)throw new Error('读取后台配置失败');
  configuration=await response.json();
  DEFAULT_MODEL=configuration.default_model||DEFAULT_MODEL;
  for(const name of ['base_url','model','protocol','deadline_seconds'])if(configuration[name]!=null)form.elements[name].value=configuration[name];
  // Unconfigured browsers start on the documented DeepSeek default so that
  // pasting a key is the only required step.
  if(!configuration.configured){
    if(!form.elements.base_url.value)form.elements.base_url.value=DEFAULT_BASE_URL;
    if(!form.elements.protocol.value)form.elements.protocol.value=DEFAULT_PROTOCOL;
    form.elements.model.value=DEFAULT_MODEL;
  }
  form.elements.enabled.value=String(configuration.enabled||false);
  form.elements.api_key.value='';
  form.elements.api_key.placeholder=configuration.has_key?'已保存；留空沿用当前地址凭证':'粘贴 API Key 即可，不回显';
  button.querySelector('.h-dot').classList.toggle('ready',configuration.configured&&configuration.enabled&&configuration.runtime?.ready);
  // Keep advanced settings folded on the common path; open it when the saved
  // configuration is not the documented default, so nothing is hidden.
  const customised=configuration.configured&&(form.elements.base_url.value!==DEFAULT_BASE_URL||form.elements.protocol.value!==DEFAULT_PROTOCOL);
  setAdvanced(customised);
  renderEndpoint();
  const host=modal.querySelector('#harness-tool-list');host.replaceChildren();
  for(const name of (capabilities?[]:configuration.tools)||[]){const chip=document.createElement('span');chip.className='h-chip';chip.textContent=name;host.append(chip);}
  void loadCapabilities();
  const ready=configuration.runtime?.ready;
  status(!ready ? (configuration.runtime?.message||'后台运行环境不可用') : configuration.configured ? '运行环境就绪 · 已保存 '+configuration.model+'；请测试 API 连通性' : '运行环境就绪；填写 API Key 和可用模型名后保存并测试',!ready?'error':configuration.configured?'done':'info');
}catch(error){status(error.message,'error');}}
// harness_backend.validated_config enforces this exact allowlist; sending anything
// else is rejected outright. Build the body explicitly so added UI controls can
// never leak into the request.
const ALLOWED_FIELDS=['enabled','protocol','base_url','api_key','model','deadline_seconds','clear_key'];
form.onsubmit=async event=>{event.preventDefault();const submit=form.querySelector('button[type="submit"]');submit.disabled=true;status('正在保存…','info');try{const entries=Object.fromEntries(new FormData(form));const data={};for(const name of ALLOWED_FIELDS)if(entries[name]!==undefined)data[name]=entries[name];data.enabled=data.enabled==='true';data.deadline_seconds=Number(data.deadline_seconds);const response=await fetch('/api/harness/config',{method:'POST',headers:{'Content-Type':'application/json','X-Duplex-Harness-Request':'1'},body:JSON.stringify(data)});const result=await response.json();if(!response.ok)throw new Error(result.detail||'保存失败');form.elements.api_key.value='';await loadConfig();status('已保存，请重新开始语音会话以使用此后台。','done');}catch(error){status(error.message,'error');}finally{submit.disabled=false;}};
function element(tag,css,text){const node=document.createElement(tag);if(css)node.className=css;if(text!=null)node.textContent=text;return node;}
function render(){
  const host=modal.querySelector('#harness-activity');
  const expanded=new Set([...host.querySelectorAll('details[open]')].map(x=>x.dataset.key));
  host.replaceChildren();
  for(const [id,task]of [...tasks.entries()].reverse()){
    const card=element('article','h-task');const top=element('div','h-task-head');
    top.append(element('span','h-source',task.origin==='model'?'模型真实委派':'手动后台测试'));
    const state=task.finished?({done:'后台声明已完成',need_info:'需要必要信息',error:'未完整完成'}[task.result?.status]||'未确认'):'后台执行中';
    top.append(element('span','h-status',state));card.append(top,element('p','h-task-request',task.task||'后台任务'));
    const stages=element('div','h-stages');
    stages.append(element('span','active','1 · 已接收任务'),element('span',task.steps.length?'active':'','2 · 工具与证据'),element('span',task.finished?'active':'','3 · 交给前台'));
    card.append(stages);
    for(const [index,step]of task.steps.entries()){
      const detail=element('details','h-tool-detail');detail.dataset.key=id+':'+index;detail.open=expanded.has(detail.dataset.key);
      detail.append(element('summary','',step.name+(step.finished?(step.error?' · 失败':' · 已返回'):' · 调用中')));
      detail.append(element('pre','',typeof step.arguments==='string'?step.arguments:JSON.stringify(step.arguments,null,2)));
      if(step.observation)detail.append(element('pre','h-observation',step.observation.slice(0,18000)));
      card.append(detail);
    }
    if(task.sources?.size){const group=element('div','h-evidence');group.append(element('div','h-section-label','实际工具来源'));
      for(const source of task.sources.values()){
        let url;try{url=new URL(source.url);}catch{continue;}
        if(!['https:','http:'].includes(url.protocol))continue;
        const link=element('a','h-source-link',source.title||url.hostname);link.href=url.href;link.target='_blank';link.rel='noopener noreferrer';
        group.append(link);
      }card.append(group);
    }
    if(task.result){
      card.append(element('div','h-section-label','后台给前台的公开回答'),element('p','h-result',task.result.answer));
      const rule=replyContract?.status_rules.find(r=>r.status===task.result.status);
      if(rule)card.append(element('p','h-next-action','建议前台：'+rule.foreground_action));
      if(task.result.missing_information?.length){const missing=element('div','h-tools');
        for(const value of task.result.missing_information)missing.append(element('span','h-chip',value));card.append(missing);}
      if(task.error_code)card.append(element('p','h-note error','故障分类：'+task.error_code));
      const detail=element('details','h-receipt');detail.dataset.key=id+':receipt';detail.open=expanded.has(detail.dataset.key);
      detail.append(element('summary','','结构化回执 · '+(task.seconds?.toFixed(1)||'—')+'s'),element('pre','',JSON.stringify(task.result,null,2)));
      card.append(detail,element('p','h-note','状态为后台声明；不等于已验证任务全对，也不代表前台已朗读或执行建议动作。'));
    }
    host.append(card);
  }
  if(!tasks.size)host.append(element('p','h-empty','尚无后台任务'));
}
function ingest(event){
  let task=tasks.get(event.task_id);if(!task){task={origin:event.origin,steps:[],sources:new Map()};tasks.set(event.task_id,task);}
  if(event.type==='backend_started'){task.task=event.task;task.model=event.model;}
  if(event.type==='backend_trace'){
    const item=event.event,data=item.data||{};
    if(item.type==='tool/call')task.steps.push({callId:data.callId,name:data.name,arguments:data.arguments});
    if(item.type==='tool/result'){
      const step=task.steps.find(x=>x.callId===(data.message?.toolCallId||data.message?.source?.callId));
      if(step){step.finished=true;step.error=data.message?.isError;step.observation=(data.message?.content||[]).filter(x=>x.type==='text').map(x=>x.text).join('\n');
        try{const value=JSON.parse(step.observation);for(const url of [...(value.provenance?.urls||[]),value.provenance?.url].filter(Boolean))task.sources.set(url,{url});}catch{}}
      for(const source of data.meta?.sources||[])if(source.url)task.sources.set(source.url,source);
      if(data.meta?.url)task.sources.set(data.meta.url,{url:data.meta.url,title:data.meta.title});
    }
  }
  if(event.type==='backend_finished'){task.finished=true;task.result=event.result;task.seconds=event.seconds;task.error_code=event.error_code;}
}
async function poll(){try{
  const response=await fetch('/api/harness/events?after='+lastSequence,{cache:'no-store'});
  if(response.ok){const data=await response.json();for(const event of data.events){lastSequence=Math.max(lastSequence,event.sequence);ingest(event);}
    while(tasks.size>32)tasks.delete(tasks.keys().next().value);if(data.events.length)render();}
}catch{}finally{setTimeout(poll,1500);}}
modal.querySelector('#harness-test').onclick=async()=>{const task=modal.querySelector('#harness-test-task').value.trim(),note=modal.querySelector('#harness-test-message'),control=modal.querySelector('#harness-test');if(!task){note.textContent='请输入测试任务';return;}control.disabled=true;note.textContent='手动测试进行中，不进入前台上下文…';try{const response=await fetch('/api/harness/test',{method:'POST',headers:{'Content-Type':'application/json','X-Duplex-Harness-Request':'1'},body:JSON.stringify({task})});const value=await response.json();if(!response.ok)throw new Error(value.detail||'测试失败');note.textContent='后台返回：'+value.payload.result.status;}catch(error){note.textContent=error.message;}finally{control.disabled=false;}};
void loadConfig();void loadCapabilities();void poll();
