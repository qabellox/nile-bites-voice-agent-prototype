"""Nile Bites voice-agent development/demo UI backend.

Runs the EXISTING pipeline end to end (ASR -> LLM -> tools -> TTS) and
exposes it to a browser page. Development/demo only: no research data
collection, no methodology changes.
"""

import base64
import io
import os
import sys
import time
import traceback
from pathlib import Path

# Portable paths: the project root is derived from this file's location, so
# the harness runs on any machine. The secrets file is NEVER in the repo:
# NBVA_ENV_FILE wins, otherwise ~/.env (e.g. C:\Users\<you>\.env on Windows).
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from dotenv import load_dotenv

ENV_FILE = Path(os.environ.get('NBVA_ENV_FILE', Path.home() / '.env'))
load_dotenv(ENV_FILE, override=True)

import numpy as np
import sounddevice as sd
import soundfile as sf
from flask import Flask, jsonify, render_template_string, request

from nbva.asr.base import resample
from nbva.asr.providers import build_asr
from nbva.config.loader import load_config
from nbva.agent.app import build_app

SAMPLE_RATE = 16000
RECORD_SECONDS = 8.0

# Dev-only ASR language options. The config FILE is never modified; the
# choice applies per request so Arabic vs auto-detect can be compared live.
# Dev ASR engines (no vendor hardcoded):
#   local  = local faster-whisper small on CPU (frozen baseline).
#   hosted = remote whisper-large-v3 through an OpenAI-compatible audio
#            endpoint, driven by VOICE_ASR_API_URL + VOICE_ASR_API_KEY.
#            No Groq anywhere in this path.
ASR_ENGINES = {'local': 'local_whisper_small_cpu', 'hosted': 'hosted_whisper_large_v3'}
ASR_LANGS = {'auto': '', 'ar': 'ar', 'en': 'en'}

# Default hosted endpoint: HuggingFace inference router (free account tier).
# Override with VOICE_ASR_API_URL for any other OpenAI-compatible endpoint.
DEFAULT_HOSTED_ASR_URL = 'https://router.huggingface.co/hf-inference/models/openai/whisper-large-v3'


class _HostedHfAsr:
    """Remote whisper-large-v3 via the HuggingFace inference router.

    Plain binary-audio POST returning ``{"text": ...}`` (the router has no
    OpenAI-compatible audio route). Dev harness only; autonomous language.
    """

    name = 'hosted_hf'
    model = 'whisper-large-v3'
    language = ''
    sample_rate = 16000

    def __init__(self, api_key, endpoint):
        self._key = api_key
        self._endpoint = endpoint

    def describe(self):
        return {'provider': self.name, 'model': self.model,
                'endpoint': self._endpoint, 'language': self.language,
                'sample_rate': self.sample_rate}

    def transcribe(self, audio, sample_rate=None):
        import time as _time

        import requests

        from nbva.asr.base import ASRResult, resample as _resample

        started = _time.perf_counter()
        data = np.ascontiguousarray(np.asarray(audio, dtype=np.float32).reshape(-1))
        sr = int(sample_rate or self.sample_rate)
        if sr != 16000:
            data = _resample(data, sr, 16000)
        pcm = np.clip(data, -1.0, 1.0)
        pcm = (pcm * 32767.0).astype(np.int16)
        buf = io.BytesIO()
        import wave as _wave

        with _wave.open(buf, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(pcm.tobytes())
        # Free-tier hosted models sleep when idle. The router answers 503
        # with an estimated wait while loading; honor it with bounded
        # retries instead of failing the turn. Warm workers answer in ~1s.
        import time as _twait

        payload = None
        last_status = None
        deadline = _twait.monotonic() + 240.0
        attempt = 0
        while payload is None and _twait.monotonic() < deadline:
            attempt += 1
            try:
                resp = requests.post(
                    self._endpoint,
                    headers={'Authorization': f'Bearer {self._key}',
                             'Content-Type': 'audio/wav'},
                    data=buf.getvalue(), timeout=90)
            except requests.Timeout as exc:
                last_status = 'timeout'
                if _twait.monotonic() + 5.0 >= deadline:
                    raise TimeoutError(
                        'hosted ASR timed out repeatedly (cold model may be '
                        'overloaded; try again in a minute)') from exc
                _twait.sleep(5.0)
                continue
            last_status = resp.status_code
            if resp.status_code == 503:
                try:
                    wait_s = float(resp.json().get('estimated_time', 20.0))
                except Exception:
                    wait_s = 20.0
                _twait.sleep(min(max(wait_s, 5.0), 60.0))
                continue
            resp.raise_for_status()
            body = resp.json()
            payload = body if isinstance(body, dict) else {}
        if payload is None:
            raise TimeoutError(
                f'hosted ASR unavailable after retries (last={last_status}); '
                'the free-tier model is likely still loading or throttled')
        text = payload.get('text', '')
        return ASRResult(
            text=(text or '').strip(), language=self.language,
            provider=self.name, model=self.model,
            duration_ms=(float(_time.perf_counter() - started)) * 1000.0)


def _hosted_asr_provider():
    """Remote whisper ASR; needs VOICE_ASR_API_KEY in the environment."""
    api_key = (os.environ.get('VOICE_ASR_API_KEY') or '').strip()
    if not api_key:
        raise RuntimeError(
            f'VOICE_ASR_API_KEY is not set. Add it to {ENV_FILE} '
            '(VOICE_ASR_API_KEY=...) to enable the hosted ASR engine.')
    base_url = ((os.environ.get('VOICE_ASR_API_URL') or '').strip()
                or DEFAULT_HOSTED_ASR_URL)
    return _HostedHfAsr(api_key, base_url)


_hosted_asr_instance = None


def _asr_engine_for(engine_key: str):
    """Resolve a dev ASR engine (cached per engine)."""
    global _hosted_asr_instance
    if engine_key == 'hosted':
        if _hosted_asr_instance is None:
            _hosted_asr_instance = _hosted_asr_provider()
            print('[asr] hosted ASR provider ready (whisper-large-v3)',
                  flush=True)
        return _hosted_asr_instance
    raise ValueError(f'unknown ASR engine {engine_key!r}')

# Dev-only decoder prime (measured: cuts mixed-speech WER sharply at zero
# latency cost; e.g. C2 0.50->0.00, C3 0.86->0.14). Frozen default stays
# unprimed; applies to dev-harness providers only.
DEV_ASR_PRIME = ('عايز اتنين chicken burgers من فضلك. '
                 'I want two chicken burgers please. '
                 'خلي واحد منهم من غير cheese.')

cfg = load_config()
cfg.set('llm.provider', 'openai_compatible')
# Model + endpoint honor the user's .env (OPENAI_MODEL / OPENAI_BASE_URL),
# falling back to the last verified working values. A bare model name is
# qualified for OpenRouter, which lists models as vendor/name.
_env_model = (os.environ.get('OPENAI_MODEL') or '').strip() or 'openai/gpt-4o-mini'
_env_base = (os.environ.get('OPENAI_BASE_URL') or '').strip() or 'https://openrouter.ai/api/v1'
if '/' not in _env_model and 'openrouter' in _env_base:
    _env_model = 'openai/' + _env_model
cfg.set('llm.model', _env_model)
cfg.set('llm.base_url', _env_base)
cfg.set('llm.openai_compatible.base_url', _env_base)
print(f'[dev-ui] LLM model={_env_model} base={_env_base}', flush=True)
# Dev-harness-only auth wiring (shipped config stays pristine): read the
# hosted key from the environment instead of the local dummy fallback.
cfg.set('llm.openai_compatible.api_key_env', 'OPENAI_API_KEY')
if not os.environ.get('OPENAI_API_KEY'):
    raise SystemExit(
        f'OPENAI_API_KEY is not set. Put it in {ENV_FILE} '
        '(OPENAI_API_KEY=...) or export it before starting the dev UI server.'
    )
cfg.set('asr.language', '')
cfg.set('asr.sample_rate', SAMPLE_RATE)
cfg.set('asr.timeout_seconds', 30)
cfg.set('llm.timeout_seconds', 60)

agent = build_app(cfg, attach_player=False)
max_iter = getattr(agent.pipeline, 'max_tool_iterations', 6)

# Move the one-time ASR model load from first turn to startup: identical
# behavior afterwards, but the first request no longer pays load time.
try:
    _warm = getattr(agent.pipeline.asr, 'warmup', None)
    if callable(_warm):
        _warm()
        print('[dev-ui] ASR model warmed up', flush=True)
except Exception as _exc:
    print(f'[dev-ui] ASR warmup skipped: {_exc}', flush=True)


def _est_prompt_tokens(transcript_text):
    """Rough input-token estimate (chars/4): cost/latency visibility only."""
    try:
        import json as _json
        sys_prompt = getattr(agent.pipeline, 'system_prompt', '') or ''
        tools_blob = _json.dumps(agent.session.tools.llm_schemas(),
                                 ensure_ascii=False, default=str)
        hist_blob = _json.dumps(agent.session.message_history()[-6:],
                                ensure_ascii=False, default=str)
        total = len(sys_prompt) + len(transcript_text or '') + len(tools_blob) + len(hist_blob)
        return max(1, total // 4)
    except Exception:
        return None

# Lazily built per-language ASR providers (model loads once per language).
_asr_cache: dict = {}


def _asr_for(lang_key: str):
    code = ASR_LANGS.get(lang_key, '')
    if code not in _asr_cache:
        cfg.set('asr.language', code)
        try:
            provider = build_asr(cfg)
            # Prove the toggle took effect: the provider must carry the
            # requested language all the way to faster-whisper.
            actual = getattr(provider, 'language', None)
            print(f'[asr] built provider for {lang_key!r}: language={actual!r}', flush=True)
            # Dev-only decoder prime (measured accuracy win, zero latency
            # cost). Only applied where the provider supports it.
            if hasattr(provider, 'initial_prompt'):
                provider.initial_prompt = DEV_ASR_PRIME
            _asr_cache[code] = provider
        finally:
            cfg.set('asr.language', '')
    return _asr_cache[code]


def _warmup_default_asr():
    try:
        provider = _asr_for('auto')
        warm = getattr(provider, 'warmup', None)
        if callable(warm):
            warm()
            print('[asr] default model warmed up', flush=True)
    except Exception as exc:
        print(f'[asr] warmup failed (first request will be slower): {exc}', flush=True)

flask_app = Flask(__name__)

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Nile Bites Voice Agent v2</title>
<style>
body{font-family:Arial,sans-serif;text-align:center;padding:40px;background:#f0f0f0;}
h1{margin-bottom:5px;}
.ver{color:#888;font-size:12px;margin-bottom:20px;}
#status{padding:10px 24px;margin:20px 0;background:#9e9e9e;color:#fff;border-radius:20px;display:inline-block;min-width:150px;font-weight:bold;}
#status.listening{background:#f44336;animation:pulse 1s infinite;}
#status.processing{background:#ff9800;}
#status.speaking{background:#2196f3;}
#status.ready{background:#4caf50;}
@keyframes pulse{0%{opacity:1;}50%{opacity:.5;}100%{opacity:1;}}
#talkBtn{padding:20px 48px;font-size:24px;cursor:pointer;border:2px solid #4caf50;border-radius:10px;background:#4caf50;color:#fff;margin:20px;}
#talkBtn.recording{border-color:#f44336;background:#f44336;box-shadow:0 0 20px #f44336;}
#talkBtn:disabled{opacity:.6;cursor:wait;}
.panel{background:#fff;padding:12px;border-radius:8px;margin:10px auto;max-width:640px;text-align:left;font-family:monospace;font-size:14px;white-space:pre-wrap;}
.label{font-weight:bold;color:#333;}
#error{color:#b71c1c;}
</style>
</head>
<body>
<h1>Nile Bites Voice Agent</h1>
<div class="ver">dev UI v4 &mdash; auto-endpoint + barge-in (mic &rarr; ASR &rarr; LLM/tools &rarr; TTS)</div>
<div><span id="status" class="ready">Ready</span></div>
<div>ASR language: <b>automatic</b> (Arabic / English / mixed detected per utterance)</div>
Decode:
<select id="beamSel">
<option value="1" selected>Greedy (research default)</option>
<option value="5">Beam-5 (accuracy test)</option>
</select>
ASR engine:
<select id="engineSel">
<option value="hosted" selected>Hosted whisper-large-v3 (remote, fast)</option>
<option value="local">Local small CPU (frozen baseline)</option>
</select>
Mode:
<select id="modeSel">
<option value="seq" selected>Sequential (baseline)</option>
<option value="stream">Streaming (intervention dev)</option>
</select></div>
<button class="button" id="talkBtn" onclick="startTalk()">TALK</button>
<button class="button" id="stopBtn" onclick="stopTalk()" style="display:none;background:#f44336;border-color:#f44336;">STOP</button>
<div>Mic level: <span style="display:inline-block;width:220px;height:12px;background:#ddd;border-radius:6px;vertical-align:middle;"><span id="meter" style="display:block;height:100%;width:0%;background:#4caf50;border-radius:6px;"></span></span></div>
<div class="panel"><span class="label">ASR transcript:</span> <span id="transcript">...</span></div>
<div class="panel"><span class="label">Tools used:</span> <span id="tools">...</span></div>
<div class="panel"><span class="label">Agent response:</span> <span id="response">...</span></div>
<div class="panel"><span class="label">Timings (ms):</span> <span id="timings">...</span></div>
<div class="panel"><span class="label">Stream timeline:</span> <span id="timeline">...</span></div>
<div class="panel" id="error" style="display:none"></div>
<script>
var audioEl = null;
function setStatus(s, cls){var e=document.getElementById('status');e.innerText=s;e.className=cls||'';}
function showError(m){var e=document.getElementById('error');e.style.display='block';e.innerText='Error: '+m;}
function clearError(){var e=document.getElementById('error');e.style.display='none';e.innerText='';}
var recStream=null,recCtx=null,recProc=null,recChunks=null,recRate=16000,recStartT=0,recTimer=null,meterTimer=null;
var vadSpeaking=false,vadLastSpeechT=0;
var VAD_THRESH=0.08,VAD_END_SILENCE_MS=700,VAD_MIN_SPEECH_MS=800;
function setMeter(v){document.getElementById('meter').style.width=Math.min(100,Math.round(v*100))+'%';}
function encodeWAV(samples,rate){
  var n=samples.length,buf=new ArrayBuffer(44+n*2),v=new DataView(buf),i;
  function wstr(o,s){for(var k=0;k<s.length;k++)v.setUint8(o+k,s.charCodeAt(k));}
  wstr(0,'RIFF');v.setUint32(4,36+n*2,true);wstr(8,'WAVEfmt ');v.setUint32(16,16,true);
  v.setUint16(20,1,true);v.setUint16(22,1,true);v.setUint32(24,rate,true);
  v.setUint32(28,rate*2,true);v.setUint16(32,2,true);v.setUint16(34,16,true);
  wstr(36,'data');v.setUint32(40,n*2,true);
  for(i=0;i<n;i++){var s=Math.max(-1,Math.min(1,samples[i]));v.setInt16(44+i*2,s<0?s*0x8000:s*0x7FFF,true);}
  return new Blob([buf],{type:'audio/wav'});
}
function startTalk(){
  var btn=document.getElementById('talkBtn');
  if(btn.disabled)return;
  btn.disabled=true;clearError();
  if(typeof audioEl!=='undefined'&&audioEl){try{audioEl.pause();}catch(e){} setStatus('Listening (interrupted reply)','listening');}
  if(!navigator.mediaDevices||!navigator.mediaDevices.getUserMedia){showError('browser has no microphone API (use Chrome/Edge on localhost)');btn.disabled=false;return;}
  navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true}})
  .then(function(stream){
    recStream=stream;
    recCtx=new (window.AudioContext||window.webkitAudioContext)();
    recRate=recCtx.sampleRate;
    var src=recCtx.createMediaStreamSource(stream);
    var analyser=recCtx.createAnalyser();analyser.fftSize=512;src.connect(analyser);
    recProc=recCtx.createScriptProcessor(4096,1,1);
    recChunks=[];
    recProc.onaudioprocess=function(e){recChunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));};
    src.connect(recProc);recProc.connect(recCtx.destination);
    var tdata=new Uint8Array(analyser.fftSize);
    vadSpeaking=false;vadLastSpeechT=0;
    meterTimer=setInterval(function(){
      analyser.getByteTimeDomainData(tdata);
      var peak=0;for(var i=0;i<tdata.length;i++){var a=Math.abs(tdata[i]-128)/128;if(a>peak)peak=a;}
      setMeter(peak);
      var now=Date.now();
      if(peak>VAD_THRESH){vadSpeaking=true;vadLastSpeechT=now;}
      else if(vadSpeaking&&(now-vadLastSpeechT)>VAD_END_SILENCE_MS&&(now-recStartT)>VAD_MIN_SPEECH_MS){
        setStatus('Processing (auto-endpoint)','processing');
        stopTalk();
      }
    },100);
    recStartT=Date.now();
    btn.style.display='none';
    document.getElementById('stopBtn').style.display='inline-block';
    setStatus('Listening','listening');
    document.getElementById('transcript').innerText='...';
    document.getElementById('response').innerText='...';
    document.getElementById('tools').innerText='...';
    recTimer=setTimeout(function(){stopTalk();},20000);
  })
  .catch(function(e){btn.disabled=false;showError('mic denied/unavailable: '+e.message);setStatus('Ready','ready');});
}
function stopTalk(){
  var btn=document.getElementById('talkBtn');
  if(recTimer){clearTimeout(recTimer);recTimer=null;}
  if(meterTimer){clearInterval(meterTimer);meterTimer=null;}
  setMeter(0);
  document.getElementById('stopBtn').style.display='none';
  btn.style.display='inline-block';
  if(!recChunks||!recChunks.length){btn.disabled=false;setStatus('Ready','ready');showError('nothing recorded');return;}
  setStatus('Processing','processing');
  var total=0,i;
  for(i=0;i<recChunks.length;i++)total+=recChunks[i].length;
  var flat=new Float32Array(total),off=0;
  for(i=0;i<recChunks.length;i++){flat.set(recChunks[i],off);off+=recChunks[i].length;}
  try{recProc.disconnect();}catch(e){}
  try{recCtx.close();}catch(e){}
  if(recStream){recStream.getTracks().forEach(function(t){try{t.stop();}catch(e){}});}
  recChunks=null;
  var secs=flat.length/recRate;
  if(secs<0.4){btn.disabled=false;setStatus('Ready','ready');showError('clip too short ('+secs.toFixed(2)+'s). Hold TALK... press STOP after speaking.');return;}
  var wav=encodeWAV(flat,recRate);
  var lang='auto';
  var beam=document.getElementById('beamSel').value;
  var mode=document.getElementById('modeSel').value;
  var engine=document.getElementById('engineSel').value;
  var endpoint=(mode==='stream')?'/record-stream':'/record';
  fetch(endpoint+'?asr_lang='+encodeURIComponent(lang)+'&beam='+encodeURIComponent(beam)+'&asr_engine='+encodeURIComponent(engine),{method:'POST',headers:{'Content-Type':'audio/wav'},body:wav})
  .then(function(r){return r.json().then(function(d){return {ok:r.ok,data:d};});})
  .then(function(res){
    btn.disabled=false;
    if(!res.ok||res.data.error){showError((res.data&&res.data.error)||'request failed');setStatus('Ready','ready');return;}
    var d=res.data;
    document.getElementById('transcript').innerText='['+(d.asr_engine||'?')+' '+(d.asr_lang_setting||'?')+' detected:'+(d.asr_detected_language||d.asr_measured_condition||'?')+' beam:'+(d.asr_beam||'?')+'] '+(d.asr_transcript||'(empty)');
    document.getElementById('tools').innerText=(d.tool_calls&&d.tool_calls.length?d.tool_calls.map(function(t){return t.name+' '+JSON.stringify(t.arguments);}).join(' | '):'(none)')+((d.speculative_tool_calls&&d.speculative_tool_calls.length)?'  [spec-early: '+d.speculative_tool_calls.map(function(t){return t.name;}).join(',')+']':'');
    if(d.context){document.getElementById('tools').innerText+=' ; context: '+JSON.stringify(d.context);}
    document.getElementById('response').innerText='['+(d.response_lang||'?')+' via '+(d.tts_voice||'?')+'] '+(d.response_text||'(empty)');
    var s=d.stage_ms||{};
    document.getElementById('timings').innerText='ASR '+s.asr+' | LLM '+s.nlu+' | tools '+s.nlu_llm_tools+' | TTS '+d.tts_ms+' | total '+d.total_ms+' | prompt~'+d.prompt_tokens_est+'tok';
    if(d.timeline&&d.timeline.length){
      document.getElementById('timeline').innerText=d.timeline.map(function(e){return e.t+'s '+e.kind+(e.text?': '+e.text:'');}).join(' | ');
    }else{document.getElementById('timeline').innerText='(sequential mode: no incremental events)';}
    if(d.tts_audio_b64){
      setStatus('Speaking','speaking');
      if(audioEl){try{audioEl.pause();}catch(e){}}
      audioEl=new Audio('data:audio/wav;base64,'+d.tts_audio_b64);
      audioEl.onended=function(){setStatus('Ready','ready');};
      audioEl.onerror=function(){setStatus('Ready','ready');showError('browser could not play TTS audio');};
      audioEl.play().catch(function(e){setStatus('Ready','ready');showError('audio playback blocked: '+e.message);});
    }else{setStatus('Ready','ready');}
  })
  .catch(function(e){btn.disabled=false;showError(e.message);setStatus('Ready','ready');});
}
</script>
</body>
</html>
"""


def _wav_b64(mono_float32: np.ndarray, sample_rate: int) -> str:
    buf = io.BytesIO()
    sf.write(buf, np.ascontiguousarray(mono_float32, dtype=np.float32),
             int(sample_rate), format='WAV', subtype='PCM_16')
    return base64.b64encode(buf.getvalue()).decode('ascii')


@flask_app.route('/')
def index():
    return render_template_string(
        HTML, status='Ready', transcript='...', response='...')


@flask_app.route('/record', methods=['POST'])
def record():
    """Record from the server microphone and run the full agent loop.

    Always returns JSON (never HTML), so the frontend never sees a
    '<!doctype' parse error.
    """
    try:
        # Audio arrives as a WAV upload; options come as query args.
        asr_choice = str(request.args.get('asr_lang', 'auto')).lower()
        if asr_choice not in ASR_LANGS:
            asr_choice = 'auto'

        raw = request.data or b''
        if len(raw) < 2000:
            return jsonify({'error': 'no audio received (upload empty).'}), 200
        try:
            data_in, sr_in = sf.read(io.BytesIO(raw), dtype='float32', always_2d=False)
        except Exception as exc:
            return jsonify({'error': f'could not decode uploaded WAV: {type(exc).__name__}: {exc}'}), 200
        mono = np.ascontiguousarray(np.asarray(data_in, dtype=np.float32).reshape(-1))
        peak = float(np.max(np.abs(mono))) if mono.size else 0.0
        audio_seconds = float(mono.size) / float(sr_in or SAMPLE_RATE)
        if peak < 1e-4:
            return jsonify({'error': 'uploaded audio is silence (peak ~0). Check mic input level/device.'}), 200
        if audio_seconds < 0.4:
            return jsonify({'error': f'clip too short ({audio_seconds:.2f}s). Hold TALK while speaking.'}), 200

        # Resample to the ASR rate.
        pcm16k = resample(mono, int(sr_in or SAMPLE_RATE),
                          int(getattr(agent.asr, 'sample_rate', SAMPLE_RATE) or SAMPLE_RATE))

        # Dev ASR engine (default: hosted; 'local' keeps the frozen
        # baseline provider). Config file untouched.
        engine_choice = str(request.args.get('asr_engine', 'hosted')).lower()
        if engine_choice not in ASR_ENGINES:
            engine_choice = 'hosted'
        asr_provider = _asr_engine_for(engine_choice)
        # Dev-only decode width (local engine only; research default
        # beam_size=1 is preserved unless the UI explicitly asks for beam 5).
        # Hosted turbo decodes server-side; prime/beam do not apply to it.
        if engine_choice == 'local':
            asr_provider = _asr_for(asr_choice)
            beam_choice = str(request.args.get('beam', '1'))
            if hasattr(asr_provider, 'beam_size'):
                try:
                    asr_provider.beam_size = 5 if int(beam_choice) >= 5 else 1
                except (TypeError, ValueError):
                    asr_provider.beam_size = 1
        agent.pipeline.asr = asr_provider

        # ---- full existing pipeline: ASR -> LLM -> tools ----
        t0 = time.time()
        result = agent.pipeline.process_turn(audio=pcm16k, speak=False)
        total_ms = (time.time() - t0) * 1000.0

        asr_text = result.asr_text or ''
        response_text = result.response_text or ''
        tool_calls = [
            {'name': c.get('name'), 'arguments': c.get('arguments', {})}
            for c in (result.tool_calls or [])
        ]
        error = result.error
        lat = getattr(result.turn, 'latency', None)
        stage_ms = {
            'asr': getattr(lat, 'asr_ms', None),
            'nlu_llm_tools': getattr(lat, 'tools_ms', None),
            'nlu': getattr(lat, 'nlu_ms', None),
        }

        # ---- TTS synthesis for browser speaker playback ----
        # Voice is routed by the pipeline (English replies use the English
        # fallback voice); mirror that routing here so the spoken reply
        # matches what process_turn(speak=True) would play.
        tts_b64 = None
        tts_error = None
        tts_voice_used = None
        tts_ms = None
        if response_text and getattr(agent.tts, 'name', None) not in (None, 'null'):
            try:
                tts_voice_arg = None
                if getattr(agent.tts, 'name', None) == 'edge':
                    if agent.pipeline._detect_response_language(response_text) == 'en':
                        tts_voice_arg = getattr(agent.tts, 'fallback_voice', None) or None
                t0tts = time.time()
                tts_res = agent.tts.synthesize(
                    response_text, **({'voice': tts_voice_arg} if tts_voice_arg else {}))
                tts_ms = round((time.time() - t0tts) * 1000.0, 1)
                tts_voice_used = getattr(tts_res, 'voice', None)
                if tts_res.error:
                    tts_error = tts_res.error
                elif tts_res.audio is not None:
                    tts_b64 = _wav_b64(np.ascontiguousarray(
                        tts_res.audio.reshape(-1), dtype=np.float32),
                        int(tts_res.sample_rate or SAMPLE_RATE))
            except Exception as exc:
                tts_error = f'{type(exc).__name__}: {exc}'

        return jsonify({
            'asr_transcript': asr_text,
            'asr_engine': ASR_ENGINES[engine_choice],
            'asr_lang_setting': ASR_LANGS[asr_choice] or 'auto-detect',
            'asr_measured_condition': getattr(result.turn, 'measured_condition', None),
            'asr_english_pct': getattr(result.turn, 'english_pct', None),
            'asr_requested_language': getattr(asr_provider, 'language', None),
            'asr_detected_language': getattr(result.turn, 'asr_language', None),
            'asr_beam': getattr(asr_provider, 'beam_size', None),
            'response_text': response_text,
            'response_lang': agent.pipeline._detect_response_language(response_text),
            'tool_calls': tool_calls,
            'tts_audio_b64': tts_b64,
            'tts_voice': tts_voice_used,
            'tts_error': tts_error,
            'audio_seconds': round(audio_seconds, 2),
            'mic_peak': round(peak, 4),
            'total_ms': round(total_ms, 1),
            'stage_ms': stage_ms,
            'tts_ms': tts_ms,
            'prompt_tokens_est': _est_prompt_tokens(asr_text),
            'pipeline_error': error,
        })
    except Exception as exc:
        traceback.print_exc()
        return jsonify({'error': f'server error: {type(exc).__name__}: {exc}'}), 500


@flask_app.route('/record-stream', methods=['POST'])
def record_stream():
    """Streaming-intervention dev path: chunked ASR + incremental loop.

    Same response envelope as /record plus the event timeline, so the UI
    can show partial -> committed -> final happening while speech arrives.
    """
    from nbva.stream.loop import StreamingTurnLoop
    try:
        asr_choice = str(request.args.get('asr_lang', 'auto')).lower()
        if asr_choice not in ASR_LANGS:
            asr_choice = 'auto'

        raw = request.data or b''
        if len(raw) < 2000:
            return jsonify({'error': 'no audio received (upload empty).'}), 200
        try:
            data_in, sr_in = sf.read(io.BytesIO(raw), dtype='float32', always_2d=False)
        except Exception as exc:
            return jsonify({'error': f'could not decode uploaded WAV: {type(exc).__name__}: {exc}'}), 200
        mono = np.ascontiguousarray(np.asarray(data_in, dtype=np.float32).reshape(-1))
        peak = float(np.max(np.abs(mono))) if mono.size else 0.0
        audio_seconds = float(mono.size) / float(sr_in or SAMPLE_RATE)
        if peak < 1e-4:
            return jsonify({'error': 'uploaded audio is silence (peak ~0).'}), 200
        if audio_seconds < 0.4:
            return jsonify({'error': f'clip too short ({audio_seconds:.2f}s).'}), 200

        engine_choice = str(request.args.get('asr_engine', 'hosted')).lower()
        if engine_choice not in ASR_ENGINES:
            engine_choice = 'hosted'
        asr_provider = _asr_engine_for(engine_choice)
        if engine_choice == 'local':
            asr_provider = _asr_for(asr_choice)
            beam_choice = str(request.args.get('beam', '1'))
            if hasattr(asr_provider, 'beam_size'):
                try:
                    asr_provider.beam_size = 5 if int(beam_choice) >= 5 else 1
                except (TypeError, ValueError):
                    asr_provider.beam_size = 1
        agent.pipeline.asr = asr_provider

        # One loop per server lifetime: alias memory + session persist
        # across turns for multi-turn dev testing.
        global _stream_loop
        try:
            _stream_loop
        except NameError:
            _stream_loop = None
        if _stream_loop is None:
            _stream_loop = StreamingTurnLoop(
                agent, trace_dir=str(ROOT / 'logs' / 'stream-dev'))
        debrief = _stream_loop.run_clip(mono, int(sr_in or SAMPLE_RATE), realtime=True)

        segs = debrief.get('audio_segments') or []
        tts_b64 = None
        if segs:
            joined = np.concatenate([np.asarray(a, dtype=np.float32).reshape(-1) for a, _sr in segs])
            tts_b64 = _wav_b64(joined, int(segs[0][1] or SAMPLE_RATE))

        tl = [{'t': e.get('t'), 'kind': e.get('kind'),
               'text': (e.get('text') or '')[:80]} for e in debrief.get('events', [])]
        return jsonify({
            'asr_transcript': debrief.get('final_transcript', ''),
            'asr_engine': ASR_ENGINES[engine_choice],
            'asr_lang_setting': ASR_LANGS[asr_choice] or 'auto-detect',
            'asr_requested_language': getattr(asr_provider, 'language', None),
            'asr_detected_language': debrief.get('asr_detected_language'),
            'asr_beam': getattr(asr_provider, 'beam_size', None),
            'response_text': debrief.get('response_text', ''),
            'response_lang': agent.pipeline._detect_response_language(debrief.get('response_text', '')),
            'tool_calls': [{'name': c.get('name'), 'arguments': c.get('arguments', {})}
                           for c in (debrief.get('tool_calls') or [])],
            'speculative_tool_calls': [{'name': c.get('name'), 'arguments': c.get('arguments', {})}
                                       for c in (debrief.get('speculative_tool_calls') or [])],
            'tts_audio_b64': tts_b64,
            'tts_voice': (debrief.get('sentences') or [{}])[0].get('voice') if debrief.get('sentences') else None,
            'tts_error': None,
            'audio_seconds': round(audio_seconds, 2),
            'mic_peak': round(peak, 4),
            'total_ms': debrief.get('total_ms'),
            'stage_ms': {'asr': None, 'nlu_llm_tools': debrief.get('llm_ms'), 'nlu': None},
            'tts_ms': None,
            'prompt_tokens_est': _est_prompt_tokens(debrief.get('final_transcript', '')),
            'timeline': tl,
            'revisions': debrief.get('revisions', 0),
            'spec_stats': debrief.get('spec_stats', {}),
            'endpoint_ms': debrief.get('endpoint_ms'),
            'first_token_ms': debrief.get('first_token_ms'),
            'first_audio_ms': debrief.get('first_audio_ms'),
            'understanding': debrief.get('understanding'),
            'context': debrief.get('context'),
            'trace_path': debrief.get('trace_path'),
            'pipeline_error': None,
            'mode': 'streaming',
        })
    except Exception as exc:
        traceback.print_exc()
        return jsonify({'error': f'server error: {type(exc).__name__}: {exc}'}), 500


@flask_app.errorhandler(404)
def _not_found(_exc):
    return jsonify({'error': 'unknown route'}), 404


@flask_app.errorhandler(405)
def _method_not_allowed(_exc):
    return jsonify({'error': 'method not allowed'}), 405


if __name__ == '__main__':
    print('Nile Bites voice-agent dev UI on http://127.0.0.1:5001', flush=True)
    _warmup_default_asr()
    flask_app.run(host='127.0.0.1', port=5001, debug=False,
                  use_reloader=False, threaded=True)
