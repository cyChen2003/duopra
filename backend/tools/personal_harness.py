"""Explicit personal-tool representation aliases, never infer write intent/IDs."""
import json
from personal_client import PERSONAL_TOOLS

VERSION='personal-exact-operation-alias-v2'
CALL_FIELDS={'op','call_id','tool','arguments','schema_version'}


def normalize(action,catalog=None):
    name=action.get('tool')
    op=action.get('op')
    if (not isinstance(name,str) or name not in PERSONAL_TOOLS or not isinstance(op,str) or
        op not in ('call',name.split('.',1)[1]) or not isinstance(action.get('arguments'),dict)):
        return action,None
    extras=set(action)-CALL_FIELDS
    allowed=set(catalog[name]['parameters']['properties']) if catalog is not None else set()
    def same(a,b):return json.dumps(a,sort_keys=True,allow_nan=False)==json.dumps(b,sort_keys=True,allow_nan=False)
    if any(k not in allowed or k not in action['arguments'] or
           not same(action[k],action['arguments'][k]) for k in extras):return action,None
    if op=='call'and not extras:return action,None
    normalized={k:v for k,v in action.items()if k not in extras};normalized['op']='call'
    return normalized,dict(adapter='personal_operation_to_call' if op!='call' else 'personal_redundant_argument_field',version=VERSION,
        original_op=op,normalized_op='call',tool=name,
        removed_redundant_outer_fields=sorted(extras),
        business_arguments_changed=False,tool_name_changed=False,
        guards='normal schema visibility, arguments, authorization, ownership and revision checks still required',
        execution_not_implied=True)


def documentation(schema):
    name=schema['name']
    if name not in PERSONAL_TOOLS:return schema
    op=name.split('.',1)[1]
    if name == 'calendar.agenda':
        schema['description'] += ' 仅用于查看日程。用户要求创建日程时使用 calendar.create；若其 Schema 不可见，先发现 calendar.create。title 和 starts 是返回字段。'
    schema['description']+=' 传输协议统一使用外层op=call；业务动作由完整tool名称表示。call_id仅关联本次调用，不是事件或待办ID。'
    schema['description']+=' Use transport op=call and this exact tool name. Copy the real object ID from a completed create/list result; never invent it or use call_id. Business fields belong only inside arguments, never duplicated outside it.'
    fields=schema['parameters']['properties']
    if name.startswith('calendar.'):
        id_key='event_id';source='calendar.create返回的payload.result.item.event_id，或calendar.agenda返回的payload.result.events中的event_id'
    else:
        id_key='task_id';source='task.create返回的payload.result.item.task_id，或task.list返回的payload.result.items中的task_id'
    if id_key in fields:
        fields[id_key]['description']='逐字复制'+source+'。不要使用call_id、日历分类、标题或自己编造ID。没有真实ID时先发现并调用对应查询工具；多个候选先让用户选择，不要求用户记住内部ID。'
    if 'expected_revision'in fields:
        fields['expected_revision']['description']='可选并发保护，填写同一次工具结果中的revision整数；不是Schema版本。不知道时不要猜，冲突后先重新查询。'
    if op=='update':
        editable=sorted(set(fields)-{id_key,'expected_revision'})
        schema['description']+=' arguments必须包含'+id_key+'和至少一个实际修改字段（'+','.join(editable)+'）；仅传要改的字段，直接放在arguments中，不要嵌套在changes或patch下。收到state=updated后才能声称修改成功。'
    elif op in {'delete','restore'}:
        schema['description']+=' arguments只传真实'+id_key+'及可选expected_revision。删除/恢复必须来自用户明确要求；不得对猜测的目标执行。'
    elif op=='create':
        schema['description']+=' 成功返回item中的真实ID和revision，后续修改/删除复用该返回值；不要自行生成ID。'
    if 'calendar_id'in fields:
        fields['calendar_id']['description']='本地日历分类，通常为personal；不是event_id。使用用户已指定或已知日历，不要把日历分类当事件ID。'
    if 'start'in fields:
        fields['start']['description']='明确含时区的ISO日期时间，格式YYYY-MM-DDTHH:MM+08:00；修改开始时间时须确保最终end仍晚于start。'
    if 'end'in fields:
        fields['end']['description']='含时区的ISO结束时间，必须晚于最终start；缺少必要时间范围时先查原日程或追问，不得编造。'
    if 'completed'in fields:
        fields['completed']['description']='JSON布尔值true表示完成，false表示重新打开；不要写成带引号的字符串。'
    return schema


def patch_error(name,args):
    if name not in {'calendar.update','task.update'}:return None
    identity='event_id'if name.startswith('calendar.')else'task_id'
    if not(set(args)-{identity,'expected_revision'}):
        return ('empty_update','Update requires the real '+identity+' and at least one changed field directly in arguments; expected_revision is not a change.')
    return None


def error_contract(name,schema):
    identity='event_id'if name.startswith('calendar.')else'task_id'
    return dict(transport_op='call',tool=name,
        required_outer_fields=['op','call_id','tool','arguments'],optional_outer_fields=['schema_version'],
        business_fields_only_inside_arguments=True,
        required_arguments=list(schema['parameters'].get('required',[])),
        allowed_argument_keys=sorted(schema['parameters']['properties']),
        id_field=identity if identity in schema['parameters']['properties']else None,
        id_source='previous create/query result, never call_id or an invented value',
        updates_are_flat=True,mutation_success_requires_completed_tool_result=True)
