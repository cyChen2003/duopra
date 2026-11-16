/** Local organizer. Manual actions never become evidence for the model. */
export class PersonalOrganizer {
  constructor({ root, authorize, permitted }) {
    this.root=root;this.authorize=authorize;this.permitted=permitted;
    this.kind="calendar";this.date=this.today();this.busy=false;this.available=false;
    this.items=[];this.loadedScope=null;this.editing=null;this.undo=null;this.retry=null;
    this.build();
    this.ready=this.loadProfile();
    window.addEventListener("duplex-language",()=>{this.build();void this.refresh()});
    window.addEventListener("duplex-personal-open",event=>{this.kind=event.detail?.kind||"calendar";this.build();void this.refresh()});
    window.addEventListener("duplex-personal-permission",()=>this.syncAuthorization());
  }
  zh(){return document.documentElement.lang==="zh"}
  text(zh,en){return this.zh()?zh:en}
  node(tag,cls,text){const el=document.createElement(tag);if(cls)el.className=cls;if(text!==undefined)el.textContent=text;return el}
  button(label,fn,cls=""){const b=this.node("button",cls,label);b.type="button";b.onclick=fn;return b}
  today(){return new Intl.DateTimeFormat("en-CA",{timeZone:"Asia/Shanghai",year:"numeric",month:"2-digit",day:"2-digit"}).format(new Date())}
  async request(tool,args,key){
    const response=await fetch("/api/personal/execute",{method:"POST",credentials:"same-origin",
      headers:{"Content-Type":"application/json","X-Duplex-Personal-Request":"1",...(key?{"X-Idempotency-Key":key}:{})},
      body:JSON.stringify({tool,arguments:args})});
    const value=await response.json();if(!response.ok)throw Error(value.message||value.detail||this.text("操作失败","Request failed"));
    return value.payload.result;
  }
  async loadProfile(){
    try{const r=await fetch("/api/personal/profile",{credentials:"same-origin",cache:"no-store"}),v=await r.json();
      if(!r.ok||!v.available)throw Error(v.message||"Organizer unavailable");this.available=true;await this.refresh();
    }catch(error){this.status.textContent=error.message;this.status.setAttribute("role","alert")}
  }
  build(){
    if(this.loadedScope!==`${this.kind}:${this.date}`)this.items=[];
    const p=this.root;p.replaceChildren();p.className="personal-organizer";
    const heading=this.node("div","organizer-heading");
    heading.append(this.node("span","organizer-eyebrow",this.text("你的本地工作区","YOUR LOCAL SPACE")),
      this.node("h3","",this.text("把接下来的事，安排好。","Make room for what’s next.")),
      this.node("p","organizer-caption",this.text("保存在当前浏览器对应的私有工作区 · 北京时间","Saved in this browser’s private workspace · Asia/Shanghai")));
    const auth=this.node("label","organizer-access"),check=document.createElement("input");check.type="checkbox";check.id="personal-model-access";check.checked=this.permitted();
    check.onchange=()=>this.authorize(check.checked);
    const authText=this.node("span");authText.append(this.node("b","",this.text("允许本次对话管理日程与待办","Allow this conversation to manage events and tasks")),
      this.node("small","",this.text("关闭后仍可手动管理；不会同步外部账号或发送通知。","Manual controls stay available. No external sync or notifications.")));
    auth.append(check,authText);
    const segments=this.node("div","organizer-segments");segments.setAttribute("role","tablist");
    for(const [kind,zh,en]of [["calendar","日程","Calendar"],["task","待办","Tasks"]]){
      const b=this.button(this.text(zh,en),()=>{if(this.busy)return;this.kind=kind;this.editing=null;this.build();void this.refresh()});
      b.setAttribute("role","tab");b.setAttribute("aria-selected",String(this.kind===kind));segments.append(b);
    }
    const toolbar=this.node("div","organizer-toolbar");
    if(this.kind==="calendar"){
      const date=document.createElement("input");date.type="date";date.value=this.date;date.id="personal-date";date.setAttribute("aria-label",this.text("查看日期","Date to view"));
      date.onchange=()=>{if(!date.value)return;this.date=date.value;this.items=[];this.renderItems();void this.refresh()};toolbar.append(date);
    }else toolbar.append(this.node("p","organizer-count",this.text("轻一点，逐件完成。","One thing at a time.")));
    toolbar.append(this.button(this.text("刷新","Refresh"),()=>void this.refresh(),"organizer-secondary"),
      this.button(this.text(this.kind==="calendar"?"＋ 添加日程":"＋ 添加待办",this.kind==="calendar"?"＋ New event":"＋ New task"),()=>this.editor(),"organizer-primary"));
    this.status=this.node("p","organizer-status",this.available?"":this.text("正在连接本地工作区…","Connecting to your local workspace…"));this.status.setAttribute("role","status");this.status.setAttribute("aria-live","polite");
    this.editorRoot=this.node("div","organizer-editor");this.editorRoot.hidden=true;
    this.list=this.node("div","organizer-list");this.list.id="personal-items";
    this.undoRoot=this.node("div","organizer-undo");
    p.append(heading,auth,segments,toolbar,this.status,this.editorRoot,this.undoRoot,this.list);
    this.renderItems();this.renderUndo();
  }
  syncAuthorization(){const c=this.root.querySelector("#personal-model-access");if(c)c.checked=this.permitted()}
  async refresh(){
    if(!this.available)return;
    const kind=this.kind,date=this.date;
    try{const r=await this.request(kind==="calendar"?"calendar.agenda":"task.list",kind==="calendar"?{calendar_id:"personal",date}:{status:"all"});
      if(kind!==this.kind||date!==this.date)return;
      this.items=kind==="calendar"?r.events:r.items;this.loadedScope=`${kind}:${date}`;this.renderItems();
      this.status.textContent=this.text(`${this.items.length} 项 · 已保存到本地工作区`,`${this.items.length} items · saved in your local workspace`);
      this.status.setAttribute("role","status");
    }catch(error){this.status.textContent=error.message;this.status.setAttribute("role","alert")}
  }
  time(value){if(!value)return this.text("无截止时间","No due date");const d=new Date(value);if(!Number.isFinite(d.getTime()))return this.text("时间格式不可用","Time unavailable");return new Intl.DateTimeFormat(this.zh()?"zh-CN":"en-GB",{timeZone:"Asia/Shanghai",month:"short",day:"numeric",hour:"2-digit",minute:"2-digit",hour12:false}).format(d)}
  renderItems(){
    if(!this.list)return;this.list.replaceChildren();
    if(!this.items.length){const empty=this.node("div","organizer-empty");empty.append(this.node("span","organizer-empty-symbol",this.kind==="calendar"?"◷":"✓"),
      this.node("h4","",this.text(this.kind==="calendar"?"这一天，留白也很好。":"暂时没有待办。",this.kind==="calendar"?"A little room in your day.":"Nothing on your list yet.")),
      this.node("p","",this.text("添加一项，或在授权后让模型帮你安排。","Add something here, or let the model help after enabling access.")));this.list.append(empty);return}
    for(const item of this.items){
      const card=this.node("article","organizer-item"+(item.completed?" is-done":""));card.dataset.itemId=item.event_id||item.task_id;
      if(this.kind==="task"){
        const done=document.createElement("input");done.type="checkbox";done.checked=item.completed;done.disabled=this.busy;done.setAttribute("aria-label",this.text("完成：","Complete: ")+item.title);
        done.onchange=()=>{done.checked=item.completed;void this.mutate("task.update",{task_id:item.task_id,completed:!item.completed,expected_revision:item.revision}).catch(()=>{})};card.append(done);
      }else{const stamp=this.node("div","organizer-time");const d=new Date(item.start);
        stamp.append(this.node("b","",new Intl.DateTimeFormat("en-GB",{timeZone:"Asia/Shanghai",hour:"2-digit",minute:"2-digit",hour12:false}).format(d)),this.node("span","",this.text("北京时间","Shanghai")));card.append(stamp)}
      const content=this.node("div","organizer-item-content");content.append(this.node("h4","",item.title),this.node("p","",this.kind==="calendar"?`${this.time(item.start)} — ${this.time(item.end)}`:this.time(item.due)));
      if(item.location)content.append(this.node("p","organizer-location",item.location));if(item.notes)content.append(this.node("p","organizer-note",item.notes));
      const actions=this.node("div","organizer-item-actions");
      actions.append(this.button(this.text("编辑","Edit"),()=>this.editor(item)),this.button(this.text("删除","Delete"),()=>void this.remove(item),"organizer-delete"));
      card.append(content,actions);this.list.append(card);
    }
  }
  localTime(value){if(!value)return "";const d=new Date(value);const parts=new Intl.DateTimeFormat("sv-SE",{timeZone:"Asia/Shanghai",year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false}).format(d);return parts.replace(" ","T")}
  editor(item=null){
    if(this.busy)return;this.editing=item;const root=this.editorRoot;root.hidden=false;root.replaceChildren();
    const form=this.node("form","organizer-form");form.id="personal-form";
    const heading=this.node("h4","",this.text(item?"修改安排":"新的安排",item?"Edit item":"A new item"));form.append(heading);
    const field=(name,label,type="text",value="",required=false)=>{const wrap=this.node("label");wrap.append(this.node("span","",label));const input=document.createElement(type==="textarea"?"textarea":"input");if(type!=="textarea")input.type=type;input.name=name;input.value=value;input.required=required;input.maxLength=name==="title"?160:500;wrap.append(input);form.append(wrap);return input};
    const title=field("title",this.text("标题","Title"),"text",item?.title||"",true);
    if(this.kind==="calendar"){
      field("start",this.text("开始 · 北京时间","Starts · Asia/Shanghai"),"datetime-local",this.localTime(item?.start)||this.date+"T09:00",true);
      field("end",this.text("结束 · 北京时间","Ends · Asia/Shanghai"),"datetime-local",this.localTime(item?.end)||this.date+"T09:30",true);
      field("location",this.text("地点（可选）","Location (optional)"),"text",item?.location||"");
    }else field("due",this.text("截止时间 · 可选 · 北京时间","Due · optional · Asia/Shanghai"),"datetime-local",this.localTime(item?.due));
    field("notes",this.text("备注（可选）","Notes (optional)"),"textarea",item?.notes||"");
    const error=this.node("p","organizer-error");error.setAttribute("role","alert");form.append(error);
    const actions=this.node("div","organizer-form-actions");const cancel=this.button(this.text("取消","Cancel"),()=>{root.hidden=true;this.editing=null});
    const save=this.node("button","organizer-primary",this.text(item?"保存修改":"添加",item?"Save changes":"Add"));save.type="submit";actions.append(cancel,save);form.append(actions);
    form.onkeydown=event=>{if(event.key==="Escape"){event.stopPropagation();cancel.click()}};
    form.onsubmit=async event=>{event.preventDefault();if(this.busy||!form.reportValidity())return;
      const args=Object.fromEntries(new FormData(form));args.title=args.title.trim();
      for(const key of ["start","end","due"])if(args[key])args[key]+="+08:00";
      if(this.kind==="calendar"&&Date.parse(args.end)<=Date.parse(args.start)){error.textContent=this.text("结束时间应晚于开始时间。","The end must be after the start.");return}
      if(this.kind==="calendar")args.calendar_id="personal";
      if(item){args[this.kind==="calendar"?"event_id":"task_id"]=item.event_id||item.task_id;args.expected_revision=item.revision}
      save.disabled=true;cancel.disabled=true;
      try{await this.mutate(this.kind+"."+(item?"update":"create"),args);root.hidden=true;this.editing=null}
      catch(e){error.textContent=e.message}
      finally{save.disabled=false;cancel.disabled=false}
    };
    root.append(form);title.focus({preventScroll:true});root.scrollIntoView({block:"nearest",behavior:"instant"});
  }
  async mutate(tool,args){
    if(this.busy)throw Error(this.text("正在保存，请稍候。","A save is in progress."));
    this.busy=true;const signature=JSON.stringify([tool,args]);
    const key=this.retry?.signature===signature?this.retry.key:crypto.randomUUID();this.retry={signature,key};
    this.status.textContent=this.text("正在保存…","Saving…");
    try{const value=await this.request(tool,args,key);this.retry=null;await this.refresh();return value}
    catch(error){this.status.textContent=error.message;this.status.setAttribute("role","alert");throw error}
    finally{this.busy=false;this.renderItems()}
  }
  async remove(item){
    try{const kind=this.kind,key=kind==="calendar"?"event_id":"task_id";await this.mutate(kind+".delete",{[key]:item[key],expected_revision:item.revision});this.undo={kind,key,id:item[key],title:item.title};this.renderUndo()}
    catch{/* the inline error remains visible */}
  }
  renderUndo(){
    this.undoRoot.replaceChildren();if(!this.undo)return;
    const undo=this.undo;this.undoRoot.append(this.node("span","",this.text("已删除：","Deleted: ")+undo.title),
      this.button(this.text("撤销","Undo"),async()=>{try{await this.mutate(undo.kind+".restore",{[undo.key]:undo.id});this.undo=null;this.renderUndo()}catch{} }));
  }
}
