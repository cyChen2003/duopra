"""Native duplex streaming service for the trained Duopra tool protocol.

One real model/KV owner, native 1035ms/1000ms input units, asynchronous tools.
Browser capture and the sample-clock audio player share the native audio contract.
"""
from __future__ import annotations

import asyncio
import base64
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import secrets
import time
from urllib.parse import urlsplit

from fastapi import FastAPI,Request,WebSocket,WebSocketDisconnect,HTTPException
from fastapi.responses import JSONResponse
from personal_client import COOKIE_NAME, rpc as personal_rpc, PERSONAL_TOOLS, connection_mode
from personal_store import PersonalError

ROOT=Path(__file__).resolve().parent
RUNTIME_ROOT=ROOT.parent/'runtime'
STATIC_ROOT=ROOT.parents[1]/'frontend/static'
from security import public_proof, public_message, scoped_cookie_name


def deployment_application_proof(config, catalog):
    """Attest actual loaded deployment adapters without changing native math."""
    from temporal_context import SessionClock, SYSTEM_PREFIX
    settings=json.loads(Path(config).read_bytes()) if not isinstance(config,dict) else config
    clock=settings.get('session_clock',{})
    if type(clock)is not dict or set(clock)-{'enabled','timezone'}:
        raise ValueError('Invalid session_clock deployment configuration')
    source={name:hashlib.sha256((ROOT.parent/name).read_bytes()).hexdigest() for name in
            ('web/server.py','web/temporal_context.py','web/harness_backend.py',
             'tools/tool_runtime.py','tools/discovery_function.py','tools/finance_market.py',
             'tools/personal_client.py','tools/personal_store.py','tools/personal_harness.py')}
    expected=settings.get('deployment_application_source_sha256')
    if expected is not None and source!=expected:
        raise ValueError('Tool/date server source differs from pinned deployment configuration')
    if settings.get('system_prompt')!=SYSTEM_PREFIX:
        raise ValueError('Native system prefix must remain unchanged')
    discovery=settings.get('tool_discovery',{'mode':'legacy'})
    if discovery.get('mode')not in ('legacy','function'):raise ValueError('Unknown tool discovery mode')
    session_clock=SessionClock(timezone_name=clock.get('timezone','Asia/Shanghai'),enabled=clock.get('enabled',False))
    return {'release':'duopra-0.1.0','frontend_version':'r9-packaged-0.1.0','personal_harness':'personal-exact-operation-alias-v2','tool_discovery':discovery,'tool_visibility_policy':'pinned_hot_union_latest_discovered','sources_sha256':source,
            'backend_harness':{'engine':'official_deepseek_harness','sdk_version':'0.2.1rc2','foreground_protocol':'assistant.query arguments.task','credentials_in_model_context':False},
            'schema_version_policy':'visible_schema_authoritative','schema_version_required':False,
            'personal_tools':'owner_scoped_local_persistent','private_protocol':'trained_call_complete_object_with_host_correlation_id_recovery',
            'registry_sha256':catalog.sha256,'session_clock':session_clock.proof(),
            'native_system_prefix':SYSTEM_PREFIX,'clock_observation_precedes_tool_observations':True,
            'dates_in_generated_actions_rewritten':False}


def context_length(decoder):
    native=getattr(decoder,'decoder',None)
    if native is not None and callable(getattr(native,'get_cache_length',None)):
        return int(native.get_cache_length()),'native_decoder_kv'
    return len(decoder.total_ids),'generated_token_history_fallback'


class TokenRouter:
    """Parse registered private spans, including JSON split across units."""
    def __init__(self,tokenizer):
        self.t=tokenizer
        self.open=tokenizer.convert_tokens_to_ids('<delegate>')
        self.close=tokenizer.convert_tokens_to_ids('</delegate>')
        self.eos=tokenizer.convert_tokens_to_ids('<|turn_eos|>')
        self.controls=set(tokenizer.all_special_ids)
        self.controls.update(int(k)for k,v in tokenizer.added_tokens_decoder.items()if v.special)
        self.inside=False;self.private=[];self.quarantined=False

    def feed(self,ids):
        public=[];actions=[];errors=[];details=[];ended=False;private_tokens=self.inside or self.quarantined
        def reject(code,message):
            # Structural diagnostics only. Private argument values never enter
            # ordinary logs merely because a model omitted a delimiter.
            import hashlib
            raw=self.t.decode(self.private,skip_special_tokens=True)
            reference={};valid=False
            try:
                from tool_runtime import strict_json
                value=strict_json(raw)
                if isinstance(value,dict):
                    valid=True
                    reference={k:value[k] for k in ('op','call_id','tool')
                               if isinstance(value.get(k),str) and len(value[k])<=96}
            except (ValueError,TypeError):pass
            errors.append(message)
            details.append(dict(code=code,message=message,private_token_count=len(self.private),
                private_body_sha256=hashlib.sha256(raw.encode()).hexdigest(),
                body_json_valid=valid,action_reference=reference,action_executed=False))
        for token in ids:
            if self.quarantined:
                private_tokens=True
                if token==self.eos:
                    self.quarantined=False;self.inside=False;self.private=[];ended=True
                continue
            if token==self.open:
                private_tokens=True
                if self.inside:
                    reject('nested_private_span','nested delegate span quarantined until turn EOS')
                    self.quarantined=True;self.private=[];continue
                self.inside=True;self.private=[]
            elif token==self.close:
                private_tokens=True
                if not self.inside:
                    reject('unmatched_delegate_close','unmatched delegate closing token');continue
                try:
                    from tool_runtime import strict_json
                    action=strict_json(self.t.decode(self.private,skip_special_tokens=True))
                    if not isinstance(action,dict):raise ValueError('private action must be a JSON object')
                    actions.append(action)
                except (ValueError,TypeError)as e:
                    reject('private_json_invalid','Private action JSON is invalid; no action executed. '+str(e)[:120]);self.quarantined=True
                self.inside=False;self.private=[]
            elif self.inside:
                if token==self.eos:
                    from tool_runtime import strict_json
                    try:valid=isinstance(strict_json(self.t.decode(self.private,skip_special_tokens=True)),dict)
                    except (ValueError,TypeError):valid=False
                    reject('delegate_close_missing' if valid else 'private_json_incomplete',
                           'Private JSON object is complete but closing delegate tag is missing; no action executed.' if valid else
                           'Private JSON is incomplete at turn EOS; no action executed.')
                    self.private=[];self.inside=False;ended=True;continue
                if token not in self.controls:self.private.append(token)
                if len(self.private)>4096:
                    reject('private_action_too_large','private action exceeds safety bound');self.private=[];self.quarantined=True
            elif token==self.eos:ended=True
            elif token not in self.controls:public.append(token)
        return {'text':self.t.decode(public,skip_special_tokens=True),'actions':actions,'errors':errors,'error_details':details,
                'ended':ended,'private_only':bool(private_tokens and not public),'public_token_ids':public}


class NativeOwner:
    def __init__(self,config,checkpoint):
        self.config=config;self.checkpoint=checkpoint
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='duopra-native-kv')
        self.engine=None;self.proof=None;self.active=None;self.ready=False
        self.decode_settings={'text_repetition_penalty':1.05}
        self.lock=asyncio.Lock()

    async def run(self,fn,*args):
        return await asyncio.get_running_loop().run_in_executor(self.pool,fn,*args)

    def load(self):
        import sys
        sys.path.insert(0,str(RUNTIME_ROOT))
        from cuda_runtime import load_for_serving
        self.engine,self.model,self.tokenizer,self.ref_audio,self.proof=load_for_serving(self.config,self.checkpoint)
        from tts_condition_audit import TTSConditionAudit
        self.Audit=TTSConditionAudit
        if getattr(self.engine.d,'delegate_speech_repair_version',None):
            from delegate_speech_gate import qualify
            self.proof['delegate_boundary_gate']=self.pool.submit(qualify,self).result()
        self.ready=True

    def prepare(self):
        self.model.reset_session()
        reference,path=self.model._load_default_reference_audio()
        self.engine.d.prepare(prefix_system_prompt='Streaming Omni Conversation.',ref_audio=reference,prompt_wav_path=path)
        self.audit=self.Audit(self.engine.d)
        return {'reference_audio':str(path),'thinker_dtype':str(next(self.model.llm.parameters()).dtype)}

    def step(self,wave,observations):
        import numpy as np
        import torch
        d=self.engine.d
        context_before,context_metric=context_length(d)
        def audio_cache_length():
            cache=getattr(getattr(self,'model',None),'audio_past_key_values',None)
            if cache is None:return 0
            try:
                if hasattr(cache,'get_seq_length'):return int(cache.get_seq_length())
                return int(cache[0][0].shape[-2])
            except (TypeError,IndexError,AttributeError):return None
        audio_cache_before=audio_cache_length()
        with torch.inference_mode():
            torch.cuda.synchronize();began=time.perf_counter()
            pre=d.streaming_prefill(audio_waveform=wave,text_list=observations or None)
            torch.cuda.synchronize();prefill=time.perf_counter()-began
            if not pre.get('success'):raise RuntimeError('Native model did not consume the complete input unit')
            before=len(d.total_ids)
            self.audit.begin(bool(getattr(self,'public_active',False)))
            began_generation=time.perf_counter()
            output=d.streaming_generate(decode_mode='greedy',max_new_speak_tokens_per_chunk=50,**self.decode_settings)
            condition=self.audit.finish()
            condition['delegate_mask']=getattr(d,'delegate_mask_record',None)
            torch.cuda.synchronize();generation=time.perf_counter()-began_generation
            wave_out=output.get('audio_waveform')
            if hasattr(wave_out,'detach'):wave_out=wave_out.detach().float().cpu().numpy()
            wave_out=np.asarray(wave_out,dtype=np.float32).reshape(-1)if wave_out is not None else np.zeros(0,dtype=np.float32)
            if not np.isfinite(wave_out).all():raise RuntimeError('Non-finite generated waveform')
            if condition['private_hidden_conditioned']or condition['private_text_conditioned']or condition['unmapped_hidden_conditioned']:
                private=condition['private_hidden_conditioned']or condition['private_text_conditioned']
                message=('Private delegate reached speech conditioning; session rejected' if private else
                         'TTS conditioning audit could not match hidden rows; session rejected')
                error=RuntimeError(message)
                error.tts_condition=condition
                error.raw_generated_token_ids=list(d.total_ids[before:])
                raise error
            return {'ids':list(d.total_ids[before:]),'wave':wave_out,'prefill_seconds':prefill,
                    'generation_seconds':generation,'compute_seconds':time.perf_counter()-began,'condition':condition,
                    'end_of_turn':bool(output.get('end_of_turn')),'is_listen':bool(output.get('is_listen')),
                    'context_before':context_before,'context_after':context_length(d)[0],
                    'context_metric':context_metric,'generated_token_count':len(d.total_ids),
                    'audio_cache_before':audio_cache_before,'audio_cache_after':audio_cache_length()}

    def discard_speech(self):
        """Drop the interrupted reply's speech state while keeping conversation KV."""
        d=self.engine.d
        d.tts_past_key_values=None
        d.tts_text_start_pos=0
        d.tts_current_turn_start_time=None
        d._delegate_turn_preflushed=False
        d._reset_token2wav_for_new_turn()

    def finish(self):
        if getattr(self,'audit',None):self.audit.close();self.audit=None
        self.model.reset_session()
        # Duplex and Token2wav own caches separate from the base model session.
        self.engine.d._reset_streaming_state()
        self.engine.d.decoder.reset()
        token2wav=self.model.tts.audio_tokenizer
        token2wav.cache=None
        token2wav.stream_cache=None
        token2wav.hift_cache_dict={}
        import torch
        torch.cuda.empty_cache()


def create_app(config,checkpoint,run_dir,preloaded_owner=None):
    from fastapi import FastAPI,Request,WebSocket,WebSocketDisconnect,HTTPException
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles
    from tool_runtime import Catalog,Executor,ToolSession,ToolError
    import numpy as np

    owner=preloaded_owner or NativeOwner(config,checkpoint);run_dir=Path(run_dir);run_dir.mkdir(parents=True,exist_ok=True)
    tool_catalog=Catalog()
    application_proof=deployment_application_proof(config,tool_catalog)
    stats=deque(maxlen=5000)

    @asynccontextmanager
    async def lifespan(app):
        if not owner.ready:
            raise RuntimeError('Use backend/entrypoint.py to preload the native owner on the main thread')
        owner.proof['deployment_application']=application_proof
        (run_dir/'MODEL_READY.json').write_text(json.dumps(public_proof(owner.proof),ensure_ascii=False,indent=2)+'\n')
        try:
            yield
        finally:
            owner.pool.shutdown(wait=True,cancel_futures=True)

    app=FastAPI(title='Duopra',lifespan=lifespan)
    from harness_backend import install_routes, session_catalog, HarnessExecutor, COOKIE as HARNESS_COOKIE
    database = os.environ.get('DUPLEX_PERSONAL_DB')
    personal_cookie = scoped_cookie_name(COOKIE_NAME, database) if database else COOKIE_NAME
    harness_cookie = scoped_cookie_name(HARNESS_COOKIE, run_dir)
    harness_store=install_routes(app, owner, tool_catalog, cookie_name=harness_cookie)
    app.state.owner=owner
    app.mount('/static',StaticFiles(directory=STATIC_ROOT),name='static')

    @app.get('/')
    async def index():return FileResponse(STATIC_ROOT/'index.html',headers={'Cache-Control':'no-store'})

    @app.get('/healthz')
    @app.get('/health')
    async def health():return JSONResponse({'ready':owner.ready,'product':'Duopra','busy':owner.active is not None},status_code=200 if owner.ready else 503)

    @app.get('/api/model-proof')
    async def model_proof():return {'ready':owner.ready,'proof':public_proof(owner.proof),'busy':owner.active is not None,'personal_connection_mode':connection_mode()}

    @app.get('/api/status')
    async def status():
        return {'model':(owner.proof or {}).get('model_variant','Duopra-unverified'),'model_type':'audio','input_modes':['audio','audio_file'],
                'configured':owner.ready,'busy':owner.active is not None,'upload_limit_mb':10}

    @app.get('/api/config')
    async def configuration():
        return {'configured':owner.ready,'active_sessions':int(owner.active is not None),'local_tools':True,
                'system_prompt':'Streaming Omni Conversation.','native_unit_seconds':1.0,
                'runtime_release':'duopra-0.1.0',
                'runtime_backend':(owner.proof or {}).get('backend','unverified'),
                'engine_surface':'native streaming; target GPU and acoustic acceptance required for each release',
                'debug_recording_available':False,
                'default_hot_tools':['alert.bulletin','bike.docks','weather.day','timezone.offset'],
                'tool_configuration':{'before_session':True,'maximum_hot_tools':8,'explicit_empty_supported':True,'schema_injection':'before_first_input_unit','mid_session_effective':'next_input_unit'},
                'tool_policy':{'allow_repeated_queries':True,'allow_reused_call_id':True,
                               'schema_version_policy':'visible_schema_authoritative','schema_version_required':False,
                               'pending_limit':4,'session_action_budget':128},
                'deployment_application':application_proof,
                'network_tools':{'tools':['weather.day','sun.daylight','currency.reference'],
                                 'transport':'dedicated_https_proxy' if os.getenv('DUPLEX_TOOL_HTTPS_PROXY') else 'system_network',
                                 'proxy_configured':bool(os.getenv('DUPLEX_TOOL_HTTPS_PROXY')),
                                 'arbitrary_web_search':False}}

    @app.get('/api/metrics')
    async def metrics():
        def percentiles(key):
            vals=[r[key]for r in stats if isinstance(r.get(key),(int,float))]
            return {'count':len(vals),'p50':float(np.percentile(vals,50))if vals else None,'p95':float(np.percentile(vals,95))if vals else None}
        return {'time':time.time(),'units':len(stats),'milliseconds':{k:percentiles(k)for k in ['prefill_ms','generation_ms','compute_ms','queue_ms']},
                'scope':'Real live native server unit processing; browser receipt telemetry is reported separately.'}

    @app.get('/api/tools')
    async def catalog():
        from discovery_function import schema
        return {'tools':tool_catalog.cards(),'registry_sha256':tool_catalog.sha256,
                'meta_tools':[schema()] if application_proof['tool_discovery'].get('mode') == 'function' else []}

    def personal_origin(request):
        origin=request.headers.get('origin')
        if origin and urlsplit(origin).netloc!=request.headers.get('host'):
            raise HTTPException(403,'Origin rejected')

    @app.get('/api/personal/profile')
    async def personal_profile(request:Request):
        personal_origin(request)
        try:
            # A legacy cookie can migrate only if this database recognizes it.
            token = request.cookies.get(personal_cookie, request.cookies.get(COOKIE_NAME))
            value=await asyncio.to_thread(personal_rpc,'bootstrap',{'workspace_token':token})
            response=JSONResponse({'available':True,'source':'local_personal_store','timezone':value['timezone'],
                                   'external_calendar_connected':False,'model_access_requires_session_authorization':True})
            response.set_cookie(personal_cookie,value['workspace_token'],max_age=365*86400,httponly=True,samesite='strict',
                                secure=urlsplit(request.headers.get('origin','')).scheme=='https')
            response.headers['Cache-Control']='no-store'
            return response
        except PersonalError as error:
            return JSONResponse({'available':False,'code':error.code,'message':str(error)},status_code=503)

    @app.post('/api/personal/execute')
    async def personal_manual(request:Request):
        personal_origin(request)
        if request.headers.get('x-duplex-personal-request')!='1':raise HTTPException(403,'Explicit organizer request required')
        try:
            body=await request.json();name=body.get('tool');arguments=body.get('arguments')
            if name not in PERSONAL_TOOLS:raise PersonalError('unknown_tool','This endpoint is for local calendar/tasks only.')
            from tool_runtime import validate
            validate(arguments,tool_catalog.tools[name]['parameters'])
            result=await asyncio.to_thread(personal_rpc,'execute',{'workspace_token':request.cookies.get(personal_cookie),
                'tool':name,'arguments':arguments,'authorized':True,'request_key':request.headers.get('x-idempotency-key')})
            validate(result,tool_catalog.tools[name]['returns'],'result')
            return {'origin':'manual','tool':name,'injected_into_model':False,
                    'payload':{'source':'local_personal_store','is_fixture':False,'result':result,
                               'provenance':{'sync':'local_only','persistent':True},'elapsed_ms':0}}
        except (ValueError,TypeError,AttributeError) as error:
            return JSONResponse({'code':getattr(error,'code','invalid_request'),'message':str(error)},status_code=400)

    @app.post('/api/tools/discover')
    async def preview_discovery(request:Request):
        origin=request.headers.get('origin')
        if origin and urlsplit(origin).netloc!=request.headers.get('host'):raise HTTPException(403,'Origin rejected')
        try:
            body=await request.json()
            hits=tool_catalog.discover(body.get('query',''),body.get('top_k',3))
            return {'type':'tools_candidates','candidates':hits,'origin':'manual','injected_into_model':False}
        except (ValueError,TypeError,AttributeError)as exc:raise HTTPException(400,str(exc))

    @app.post('/api/tools/execute')
    async def manual_tool(request:Request):
        # Toolbar diagnostics are visibly manual and never enter model context.
        origin=request.headers.get('origin')
        if origin and urlsplit(origin).netloc!=request.headers.get('host'):raise HTTPException(403,'Origin rejected')
        body=await request.json();name=body.get('tool');arguments=body.get('arguments',{})
        try:
            result=await asyncio.to_thread(Executor(tool_catalog).execute,name,arguments)
            return {'origin':'manual','tool':name,'payload':result,'injected_into_model':False}
        except Exception as exc:raise HTTPException(400,str(exc))

    class Session:
        def __init__(self,ws,initial_tools,discovery_mode):
            self.ws=ws;self.id=secrets.token_hex(12);self.token=secrets.token_urlsafe(24)
            self.audio=bytearray();self.pending_playback={};self.wake=asyncio.Event();self.closed=False
            self.send_lock=asyncio.Lock();self.pending=[];self.tasks=set();self.tick=0;self.turn=0;self.chunk=0
            self.active_public=False;self.interrupted_utterances=set()
            self.silent_samples=0;self.voiced_samples=0;self.speech_onset_armed=False
            self.router=TokenRouter(owner.tokenizer)
            self.harness_cookie=ws.cookies.get(harness_cookie)
            session_tools,fixed_meta=session_catalog(tool_catalog,harness_store,self.harness_cookie)
            original_executor=Executor(session_tools,mode='fixture' if ws.query_params.get('fixture_tools')=='1' else 'auto',
                                       personal_scope=ws.cookies.get(personal_cookie))
            self.harness_executor=HarnessExecutor(original_executor,harness_store,self.harness_cookie,
                                                  generation=lambda:self.tools.generation)
            self.tools=ToolSession(self.id,session_tools,initial_tools=initial_tools,
                executor=self.harness_executor,discovery_mode=discovery_mode,fixed_meta_schemas=fixed_meta)
            from temporal_context import SessionClock
            clock_config=application_proof['session_clock']
            self.clock=SessionClock(timezone_name=clock_config['timezone'],enabled=clock_config['enabled'])
            self.event_cursor=0;self.unit_size=16560;self.incoming_voice_ticks=deque(maxlen=2)
            self.started=time.perf_counter();self.first_input=None;self.first_output=None
            self.received_samples=0;self.processed_samples=0;self.last_audio_sequence=None
            self.logging_error=False
            self.log=open(os.devnull,'w')
            self.upload_busy=False

        async def send(self,value):
            if self.closed:return
            value=public_message(value)
            if value is None:return
            async with self.send_lock:await self.ws.send_json(value)

        def record(self,value):
            if self.log.closed:return
            try:
                self.log.write(json.dumps({'time':time.time(),**value},ensure_ascii=False)+'\n');self.log.flush()
            except OSError:
                if not self.logging_error:
                    self.logging_error=True
                    asyncio.create_task(self.send({'type':'debug_state','enabled':False,'error':'日志存储不可写，语音会话继续运行。'}))

        def new_user_speech(self,wave):
            # Admit quiet speech, but require 200 ms so short input spikes do not arm a stop.
            onset=False;speech=False
            for offset in range(0,len(wave),320):
                frame=wave[offset:offset+320]
                if np.sqrt(np.mean(frame**2))>.001:
                    speech=True
                    self.silent_samples=0;self.voiced_samples+=len(frame)
                    if self.speech_onset_armed and self.voiced_samples>=3200:
                        onset=True;self.speech_onset_armed=False
                else:
                    self.voiced_samples=0;self.silent_samples+=len(frame)
                    if self.silent_samples>=3200:self.speech_onset_armed=True
            return onset,speech

        async def tool_event(self,event):await self.send({'type':'tool_event',**event})

        async def configure(self,names):
            # A Schema change becomes visible at the next native audio prefill.
            schemas=self.tools.expose(names)
            await self.send({'type':'tool_state',**self.tools.snapshot(),'hot_tools':schemas,'tick':self.tick})

        async def publish_tool_events(self):
            self.harness_executor.sync_generation(self.tools.generation)
            for event in self.tools.events_since(self.event_cursor):
                self.event_cursor=event['seq']
                # Keep the WS envelope separate from the inner tool event type.
                await self.send({'type':'tool_event','event':event})
                self.record({'type':'tool_event','event':event})
            await self.send({'type':'tool_state',**self.tools.snapshot(),'tick':self.tick})

        async def drive(self):
            try:
                while not self.closed:
                    if len(self.audio)<self.unit_size*2:
                        self.wake.clear()
                        if len(self.audio)>=self.unit_size*2:continue
                        await self.wake.wait();continue
                    n=self.unit_size;pcm=bytes(self.audio[:n*2]);del self.audio[:n*2]
                    self.processed_samples+=n
                    queued_ms=len(self.audio)/32.0
                    wave=np.frombuffer(pcm,dtype='<i2').astype(np.float32)/32768.0
                    voice=bool(np.sqrt(np.mean(wave**2))>.008)
                    onset,speech=self.new_user_speech(wave)
                    if onset:
                        self.interrupted_utterances.update(self.pending_playback)
                        if self.active_public:self.interrupted_utterances.add(str(self.turn))
                    if voice and len(self.incoming_voice_ticks)==2 and not any(self.incoming_voice_ticks):
                        self.tools.begin_user_speech()
                    self.incoming_voice_ticks.append(voice)
                    clock_observations=self.clock.before_input_unit(self.tick)
                    observations=clock_observations+self.tools.before_input_unit(self.tick)
                    await self.publish_tool_events()
                    owner.public_active=self.active_public
                    step=await owner.run(owner.step,wave,observations)
                    parsed=self.router.feed(step['ids']);text=parsed['text']
                    # The native listen return can hide an earlier public EOS in this unit.
                    eos=bool((step['end_of_turn']or parsed['ended'])and not parsed['private_only'])
                    # A normal EOS drains the final codec tail. Only an EOS
                    # following NEW user speech during this reply cancels it.
                    cancelled=eos and str(self.turn) in self.interrupted_utterances
                    for detail in parsed['error_details']:
                        self.record({'type':'private_router_rejection','tick':self.tick,**detail})
                        self.tools.report_error(ToolError(detail['code'],detail['message']),
                            action=detail['action_reference'], turn_quarantined=self.router.quarantined)
                    for action in parsed['actions']:
                        try:self.tools.submit_private(json.dumps(action,ensure_ascii=False),self.tick)
                        except Exception as exc:self.tools.report_error(exc,action=action)
                    await self.publish_tool_events()
                    out=step['wave'];duration=0.0 if cancelled else len(out)/24000.0
                    # A pause inside the original question can also arm an
                    # onset. Once that speech ends, continued public output
                    # means its later EOS is normal completion, not a stop.
                    if not speech and not eos and not parsed['private_only'] and (text or np.any(np.abs(out)>1e-8)):
                        self.interrupted_utterances.discard(str(self.turn))
                    if parsed['private_only']and np.any(np.abs(out)>1e-8)and not step['condition']['proven_public_flush']:
                        raise RuntimeError('Unproven audio in private-only unit; session rejected')
                    if cancelled:await owner.run(owner.discard_speech)
                    if text and not cancelled:self.active_public=True
                    # The native speech bridge already excludes private tokens/hidden states.
                    # Keep valid public codec flushes even in delegate-only units.
                    if eos and self.interrupted_utterances:
                        await self.send({'type':'playback_cancel','utterance_ids':sorted(self.interrupted_utterances),
                                         'reason':'model_eos_after_user_speech','tick':self.tick})
                        for utterance in self.interrupted_utterances:self.pending_playback.pop(utterance,None)
                    if not cancelled and len(out)and bool(np.any(np.abs(out)>1e-8)):
                        if self.first_output is None:self.first_output=time.perf_counter()
                        self.chunk+=1
                        self.pending_playback.setdefault(str(self.turn),set()).add(self.chunk)
                        encoded_pcm=np.rint(np.clip(out,-1,1)*32767).astype('<i2').tobytes()
                        encoded=base64.b64encode(encoded_pcm).decode()
                        await self.send({'type':'playback','utterance_id':str(self.turn),'chunk_seq':self.chunk,'text':text,
                                         'data':encoded,'format':'pcm_s16le','sample_rate':24000,'channels':1,'silent':False,
                                         'server_sent_at':time.time(),'tick':self.tick})
                    elif text and not cancelled:await self.send({'type':'text','utterance_id':str(self.turn),'text':text})
                    if eos:
                        if not cancelled:
                            await self.send({'type':'playback_end','utterance_id':str(self.turn),'tick':self.tick})
                        self.turn+=1;self.chunk=0;self.active_public=False;self.interrupted_utterances.clear()
                    value={'type':'timing','tick':self.tick,'prefill_ms':step['prefill_seconds']*1000,
                           'generation_ms':step['generation_seconds']*1000,'compute_ms':step['compute_seconds']*1000,
                           'queue_ms':queued_ms,'audio_seconds':duration,'input_seconds':n/16000.0,
                           'rtf_unit':step['compute_seconds']/(n/16000.0),
                           'context_tokens':step.get('context_after',context_length(owner.engine.d)[0]),
                           'context_metric':step.get('context_metric',context_length(owner.engine.d)[1]),
                           'generated_token_count':len(owner.engine.d.total_ids),
                           'queue_after_ms':len(self.audio)/32.0,
                           'input_received_seconds':self.received_samples/16000.,
                           'input_processed_seconds':self.processed_samples/16000.,
                           'private_conditioning_violations':0,'tool_results_injected':len(observations)}
                    await self.send({'type':'unit_trace','tick':self.tick,'public_text':text,
                        'is_listen':step['is_listen'],'end_of_turn':eos,'native_end_of_turn':step['end_of_turn'],
                        'output_samples':len(out),'input_samples':n,'observations':observations,
                        'private_actions':parsed['actions'],'errors':parsed['errors']})
                    stats.append(value);self.record(value);await self.send(value)
                    self.tick+=1;self.unit_size=16000
            except asyncio.CancelledError:raise
            except Exception as exc:
                self.record({'type':'error','error':repr(exc),
                             'tts_condition':getattr(exc,'tts_condition',None),
                             'raw_generated_token_ids':getattr(exc,'raw_generated_token_ids',None)})
                await self.send({'type':'fatal_error','code':'model','error':str(exc)})
                await self.ws.close(code=1011)

        async def receive(self):
            while not self.closed:
                message=await self.ws.receive_json();kind=message.get('type')
                if kind=='stop_session':return
                if kind=='audio':
                    if message.get('format')!='pcm16'or message.get('sample_rate')!=16000:raise ValueError('Expected mono PCM16 at16kHz')
                    encoded=message.get('data','')
                    if not isinstance(encoded,str)or len(encoded)>175000:raise ValueError('Input audio message too large')
                    data=base64.b64decode(encoded,validate=True)
                    if len(data)%2:raise ValueError('Incomplete PCM16 sample')
                    sequence=message.get('sequence')
                    if sequence is not None:
                        if type(sequence)is not int or sequence<0:raise ValueError('Invalid audio packet sequence')
                        if self.last_audio_sequence is not None and sequence!=self.last_audio_sequence+1:
                            raise ValueError('Input audio sequence gap; session stopped instead of silently skipping speech')
                        self.last_audio_sequence=sequence
                    if self.first_input is None:self.first_input=time.perf_counter()
                    self.received_samples+=len(data)//2
                    self.audio.extend(data)
                    if len(self.audio)>16000*2*30:raise ValueError('Inference backlog exceeded30s; reconnect. No input audio was skipped.')
                    self.wake.set()
                elif kind=='tools_config':
                    try:await self.configure(message.get('names',[]))
                    except Exception as exc:await self.send({'type':'command_error','error':str(exc)})
                elif kind=='tools_discover':
                    try:
                        candidates=tool_catalog.discover(message.get('query',''),message.get('top_k',3))
                        await self.send({'type':'tools_candidates','candidates':candidates,'origin':'manual','injected_into_model':False})
                    except Exception as exc:await self.send({'type':'command_error','error':str(exc)})
                elif kind=='tools_authorize':
                    try:
                        self.tools.authorize_personal(message.get('allowed'))
                        await self.publish_tool_events()
                    except Exception as exc:await self.send({'type':'command_error','error':str(exc)})
                elif kind=='interrupt':
                    # Manual host stop is not evidence the model learned voice STOP.
                    await self.send({'type':'command_error','error':'当前请用语音说“停止”或结束会话；未启用宿主强制EOS，避免冒充模型停止能力。'})
                elif kind in ('playback_ack','playback_started','playback_finished','client_timing'):
                    if kind=='playback_ack' and isinstance(message.get('utterance_id'),str) and type(message.get('chunks'))is int:
                        utterance=message['utterance_id']
                        pending=self.pending_playback.get(utterance)
                        if pending is not None:
                            pending.discard(message['chunks'])
                            if not pending:self.pending_playback.pop(utterance,None)
                    self.record({'type':'browser_receipt','receipt':message,'tick':self.tick})

    @app.websocket('/ws')
    async def websocket(ws:WebSocket):
        origin=ws.headers.get('origin')
        if origin and urlsplit(origin).netloc!=ws.headers.get('host'):
            await ws.close(code=1008);return
        await ws.accept()
        try:
            initial_tools=json.loads(ws.query_params['initial_tools'])if 'initial_tools'in ws.query_params else ['alert.bulletin','bike.docks','weather.day','timezone.offset']
            if type(initial_tools)is not list or len(initial_tools)>8 or any(type(n)is not str for n in initial_tools):raise ValueError('Choose zero to eight named initial tools')
            initial_tools=list(dict.fromkeys(initial_tools))
            for name in initial_tools:tool_catalog.schema(name)
            discovery_mode=ws.query_params.get('discovery_mode',application_proof['tool_discovery']['mode'])
            if discovery_mode not in ('legacy','function'):raise ValueError('Unknown discovery mode')
            authorized=ws.query_params.get('personal_authorized','false')
            if authorized not in ('true','false'):raise ValueError('Invalid initial personal authorization')
        except (ValueError,TypeError)as exc:
            await ws.send_json({'type':'fatal_error','code':'configuration','error':str(exc)})
            await ws.close(code=1008);return
        async with owner.lock:
            if not owner.ready or owner.active is not None:
                await ws.send_json({'type':'fatal_error','code':'busy','error':'One live session owns the native KV cache. Close the other session first.'})
                await ws.close(code=1008);return
            session=Session(ws,initial_tools,discovery_mode);session.tools.authorize_personal(authorized=='true');owner.active=session
        driving=None
        try:
            await owner.run(owner.prepare)
            await session.configure(initial_tools)
            await session.send({'type':'ready','session_id':session.id,'upload_token':session.token,'model':(owner.proof or {}).get('model_variant','Duopra-unverified'),
                                'native_unit_seconds':1.0,'native_first_samples':16560,'native_subsequent_samples':16000,'max_new_speak_tokens_per_chunk':50,'checkpoint':str(checkpoint),
                                'tool_state':session.tools.snapshot(),'manual_interrupt_supported':False,
                                'session_clock':session.clock.proof(),'deployment_application':application_proof})
            driving=asyncio.create_task(session.drive())
            await session.receive()
        except WebSocketDisconnect:pass
        except Exception as exc:
            session.record({'type':'error','error':repr(exc),'tick':session.tick})
            await session.send({'type':'fatal_error','code':'session','error':str(exc)})
        finally:
            session.closed=True
            try:session.log.close()
            except OSError:pass
            if driving:
                driving.cancel();await asyncio.gather(driving,return_exceptions=True)
            for task in session.tasks:task.cancel()
            await asyncio.gather(*session.tasks,return_exceptions=True)
            session.harness_executor.close()
            session.tools.close()
            await owner.run(owner.finish)
            async with owner.lock:
                if owner.active is session:owner.active=None
            try:await ws.close()
            except RuntimeError:pass

    @app.post('/api/sessions/{sid}/audio')
    async def upload(sid:str,request:Request):
        session=owner.active
        if session is None or session.id!=sid:raise HTTPException(404,'No active session')
        if not secrets.compare_digest(request.headers.get('x-venus-session-token',''),session.token):raise HTTPException(403,'Session token mismatch')
        if session.upload_busy:raise HTTPException(409,'An audio upload is already active')
        session.upload_busy=True
        from upload_audio import decode_audio,MAX_BYTES
        try:
            raw=bytearray()
            async for block in request.stream():
                if len(raw)+len(block)>MAX_BYTES:raise HTTPException(413,'Maximum 10 MiB')
                raw.extend(block)
            wave=await asyncio.to_thread(decode_audio,bytes(raw))
            if session.closed:raise HTTPException(409,'Session ended during upload')
        except BaseException as exc:
            session.upload_busy=False
            if isinstance(exc,(HTTPException,asyncio.CancelledError)):raise
            raise HTTPException(400,'Invalid or oversized audio file') from None
        async def feed():
            try:
                # File replay uses the same1s input clock as live microphone input.
                pcm=np.rint(np.clip(wave,-1,1)*32767).astype('<i2').tobytes()
                offset=0
                while offset<len(pcm)+16000*2*20:
                    if session.closed:return
                    samples=16560 if offset==0 else 16000
                    size=samples*2
                    block=pcm[offset:offset+size].ljust(size,b'\0')if offset<len(pcm)else bytes(size)
                    session.received_samples+=len(block)//2
                    session.audio.extend(block);session.wake.set()
                    if len(session.audio)>16000*2*30:raise RuntimeError('File replay backlog exceeded30s')
                    offset+=size
                    await asyncio.sleep(samples/16000.)
                await session.send({'type':'media_status','state':'complete','seconds':len(wave)/16000.0})
            except asyncio.CancelledError:raise
            except Exception as exc:await session.send({'type':'command_error','error':str(exc)})
            finally:session.upload_busy=False
        task=asyncio.create_task(feed());session.tasks.add(task);task.add_done_callback(session.tasks.discard)
        return {'accepted':True,'seconds':len(wave)/16000.0,'postroll_seconds':20}

    return app
