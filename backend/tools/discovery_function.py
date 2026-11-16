"""Always-available discovery meta tool; shares the existing lexical retriever."""
import copy

NAME='tools.discover'
VERSION='2'
SCHEMA={
    'name':NAME,'version':VERSION,'read_only':True,'runtime_role':'meta',
    'description':('Discover relevant business tool schemas only when the currently visible tools cannot perform the request. '
        'query describes the required capability, not just a date. Read the returned schema before calling a business tool; '
        'this function returns schemas, never the requested business facts. '
        '当前可见业务工具无法完成请求时发现相关工具。query填写所需查询能力；返回Schema后再按Schema调用，不直接返回业务查询结果。'),
    'excluded':'Does not execute a business tool or grant personal-data authorization.',
    'parameters':{'type':'object','properties':{
        'query':{'type':'string','description':'Concise capability needed for the current user request; include the relevant task words.'},
        'top_k':{'type':'integer','minimum':1,'maximum':8,'description':'Maximum business schemas to return; defaults to the session top-K.'}},
        'required':['query'],'additionalProperties':False},
    'returns':{'type':'object','properties':{'tools':{'type':'array','items':{'type':'object'}},'error':{'type':'object'}},'required':['tools']}
}


def schema():return copy.deepcopy(SCHEMA)


def observed(value,enabled):
    # Keep canonical catalog/visible business names and the old return envelope.
    # The fixed meta schema comes last and is not included in business top-K.
    if not enabled or not isinstance(value,dict) or not isinstance(value.get('tools'),list):return value
    result=copy.deepcopy(value)
    result['tools']=[s for s in result['tools']if s.get('name')!=NAME]+[schema()]
    return result
