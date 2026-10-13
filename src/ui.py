import sys
import os
import json
import base64
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from nbva.config.loader import load_config
from nbva.agent.app import build_app
from nbva.llm.base import run_tool_loop
import sounddevice as sd
import numpy as np
import soundfile as sf
import flask
from flask import Flask, render_template_string, request, jsonify

# Load configuration
cfg = load_config()
cfg.set('llm.provider', 'openai_compatible')
cfg.set('llm.model', 'openai/gpt-4o-mini')
cfg.set('llm.base_url', 'https://openrouter.ai/api/v1')
cfg.set('asr.language', '')
cfg.set('asr.sample_rate', 16000)
cfg.set('asr.timeout_seconds', 30)
cfg.set('llm.timeout_seconds', 60)

app_pipeline = build_app(cfg, attach_player=False)
max_iter = getattr(app_pipeline.pipeline, 'max_tool_iterations', 6)

# Global state
ui_state = {
    'status': 'Ready',
    'transcript': '',
    'response': ''
}

# Flask app
flask_app = flask.Flask(__name__)

# HTML template - inline to avoid file issues
HTML = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Nile Bites Voice Agent</title>
    <style>
        body {font-family: Arial, sans-serif; text-align: center; padding: 50px; background: #f0f0f0;}
        .button {padding: 20px 40px; font-size: 24px; cursor: border; border: 2px solid #4CAF50; border-radius: 10px; background: #4CAF50; color: white; margin: 20px;}
        .button.recording {border-color: #f44336; background: #f44336; box-shadow: 0 0 20px #f44336;}
        .status {padding: 10px; margin: 20px 0; background: #fff; border-radius: 5px; display: inline-block;}
        .log {background: #f8f8f8; padding: 10px; border-radius: 5px; margin: 10px 0; max-height: 300px; overflow-y: auto; font-family: monospace;}
    </style>
</head>
<body>
    <h1>Nile Bites Voice Agent</h1>
    <div class="status" id="status">{{status}}</div>
    <div class="log" id="log">{{log}}</div>
    <button class="button" id="talkBtn" onclick="startStop()">TALK</button>
    <div class="log" id="transcript">ASR Transcript: {{transcript}}</div>
    <div class="log" id="response">Agent Response: {{response}}</div>
    <script>
        function updateStatus(t) {document.getElementById('status').innerText = t;}
        function updateTranscript(t) {document.getElementById('transcript').innerText = 'ASR Transcript: ' + t;}
        function updateResponse(t) {document.getElementById('response').innerText = 'Agent Response: ' + t;}
        function showAlert(t) {alert('Agent Response: ' + t);}
        function startStop() {
            var btn = document.getElementById('talkBtn');
            if (!btn.classList.contains('recording')) {
                btn.classList.add('recording');
                updateStatus('Listening');
                fetch('/record', {method: 'POST', body: 'duration=5'}).then(function(r) {return r.json();}).then(function(data) {
                    updateStatus('Speaking');
                    updateTranscript(data.transcript || 'No transcript');
                    updateResponse(data.response || 'No response');
                    showAlert(data.response || 'No response');
                    btn.classList.remove('recording');
                }).catch(function(e) {updateStatus('Error: ' + e.message); btn.classList.remove('recording');});
            }
        }
    </script>
</body>
</html>'''

# Configure Flask
flask_app = Flask(__name__)
flask_app.template_folder = os.path.join(os.path.dirname(__file__), 'templates') if os.path.exists(os.path.join(os.path.dirname(__file__), 'templates')) else None

# But we'll use the inline template

# Process audio function
def process_audio(audio_data):
    """Process audio through the pipeline."""
    try:
        # Convert to the format expected
        data = np.array(audio_data, dtype=np.float32)
        if data.ndim > 1:
            data = data.mean(axis=1)
        # Resample
        resampled = __import__('nbva').asr.base.resample(data, 44100, 16000)
        
        # Run pipeline
        messages = [{'role': 'user', 'content': ''}]
        response_text, messages_out, tool_calls, llm_error = run_tool_loop(
            app_pipeline.llm, app_pipeline.session.tools, messages, max_iter
        )
        
        transcript = messages_out[-1]['content'] if messages_out else ''
        response = response_text or ''
        
        return transcript, response
    except Exception as e:
        print(f'Error in process_audio: {e}')
        import traceback
        traceback.print_exc()
        return '', 'Error processing audio'


# Flask routes
@flask_app.route('/')
def index():
    return flask.render_template_string(HTML, status=ui_state['status'], transcript=ui_state['transcript'], response=ui_state['response'])

@flask_app.route('/record', methods=['POST'])
def record():
    global ui_state
    ui_state['status'] = 'Listening'
    print('Recording started...')
    
    try:
        duration = float(request.form.get('duration', 5.0))
        recording = sd.rec(int(duration * 16000), samplerate=16000, channels=2)
        sd.wait()
        
        print(f'Recording finished, level: {np.max(np.abs(sd.recording)):.4f}')
        
        # Save and process
        wav_path = os.path.join(os.path.dirname(__file__), 'temp_recording.wav')
        sf.write(wav_path, sd.recording, 16000, subtype='PCM_24')
        
        data, sr_actual = sf.read(wav_path, dtype='float32', always_2d=False)
        if data.ndim > 1:
            data = data.mean(axis=1)
        resampled = __import__('nbva').asr.base.resample(data, 44100, 16000)
        
        ui_state['status'] = 'Processing'
        
        messages = [{'role': 'user', 'content': ''}]
        response_text, messages_out, tool_calls, llm_error = run_tool_loop(
            app_pipeline.llm, app_pipeline.session.tools, messages, max_iter
        )
        
        transcript = messages_out[-1]['content'] if messages_out else ''
        response_text = response_text or ''
        
        ui_state['status'] = 'Speaking'
        ui_state['transcript'] = transcript
        ui_state['response'] = response_text
        
        # Clean up
        try:
            os.remove(wav_path)
        except:
            pass
        
        print(f'Transcript: {transcript!r}, Response: {response_text!r}')
        
        return jsonify({
            'status': ui_state['status'],
            'transcript': transcript,
            'response': response_text
        })
        
    except Exception as e:
        ui_state['status'] = 'Error'
        print(f'Error: {type(e).__name__}: {e}')
        import traceback
        traceback.print_exc()
        return jsonify({
            'status': 'Error',
            'transcript': '',
            'response': str(e)
        })


# Flask routes
@flask_app.route('/')
def index():
    return flask.render_template_string('''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Nile Bites Voice Agent</title>
    <style>
        body {font-family: Arial, sans-serif; text-align: center; padding: 50px; background: #f0f0f0;}
        .button {padding: 20px 40px; font-size: 24px; cursor: pointer; border: 2px solid #4CAF50; border-radius: 10px; background: #4CAF50; color: white; margin: 20px;}
        .button.recording {border-color: #f44336; background: #f44336; box-shadow: 0 0 20px #f44336;}
        .status {padding: 10px; margin: 20px 0; background: #fff; border-radius: 5px; display: inline-block;}
        .log {background: #f8f8f8; padding: 10px; border-radius: 5px; margin: 10px 0; max-height: 300px; overflow-y: auto; font-family: monospace;}
    </style>
</head>
<body>
    <h1>Nile Bites Voice Agent</h1>
    <div class="status" id="status">{{status}}</div>
    <div class="log" id="log">{{log}}</div>
    <button class="button" id="talkBtn" onclick="startStop()">TALK</button>
    <div class="log" id="transcript">ASR Transcript: {{transcript}}</div>
    <div class="log" id="response">Agent Response: {{response}}</div>
    <script>
        function updateStatus(t) {document.getElementById('status').innerText = t;}
        function updateTranscript(t) {document.getElementById('transcript').innerText = 'ASR Transcript: ' + t;}
        function updateResponse(t) {document.getElementById('response').innerText = 'Agent Response: ' + t;}
        function showAlert(t) {alert('Agent Response: ' + t);}
        function startStop() {
            var btn = document.getElementById('talkBtn');
            if (!btn.classList.contains('recording')) {
                btn.classList.add('recording');
                updateStatus('Listening');
                fetch('/record', {method: 'POST', body: 'duration=5'}).then(function(r) {return r.json();}).then(function(data) {
                    updateStatus('Speaking');
                    updateTranscript(data.transcript || 'No transcript');
                    updateResponse(data.response || 'No response');
                    showAlert(data.response || 'No response');
                    btn.classList.remove('recording');
                }).catch(function(e) {updateStatus('Error: ' + e.message); btn.classList.remove('recording');});
            }
        }
    </script>
</body>
</html>'''

# Route handlers
@flask_app.route('/')
def index():
    return flask.render_template_string(HTML, status=ui_state['status'], transcript=ui_state['transcript'], response=ui_state['response'])

@flask_app.route('/record', methods=['POST'])
def record():
    global ui_state
    ui_state['status'] = 'Listening'
    print('Recording started...')
    
    try:
        duration = float(request.form.get('duration', 5.0))
        recording = sd.rec(int(duration * 16000), samplerate=16000, channels=2)
        sd.wait()
        
        print(f'Recording finished, level: {np.max(np.abs(sd.recording)):.4f}')
        
        wav_path = os.path.join(os.path.dirname(__file__), 'temp_recording.wav')
        sf.write(wav_path, sd.recording, 16000, subtype='PCM_24')
        
        data, sr_actual = sf.read(wav_path, dtype='float32', always_2d=False)
        if data.ndim > 1:
            data = data.mean(axis=1)
        resampled = __import__('nbva').asr.base.resample(data, 44100, 16000)
        
        ui_state['status'] = 'Processing'
        
        messages = [{'role': 'user', 'content': ''}]
        response_text, messages_out, tool_calls, llm_error = run_tool_loop(
            app_pipeline.llm, app_pipeline.session.tools, messages, max_iter
        )
        
        transcript = messages_out[-1]['content'] if messages_out else ''
        response_text = response_text or ''
        
        ui_state['status'] = 'Speaking'
        ui_state['transcript'] = transcript
        ui_state['response'] = response_text
        
        try:
            os.remove(wav_path)
        except:
            pass
        
        print(f'Transcript: {transcript!r}, Response: {response_text!r}')
        
        return jsonify({
            'status': ui_state['status'],
            'transcript': transcript,
            'response': response_text
        })
        
    except Exception as e:
        ui_state['status'] = 'Error'
        print(f'Error: {type(e).__name__}: {e}')
        import traceback
        traceback.print_exc()
        return jsonify({
            'status': 'Error',
            'transcript': '',
            'response': str(e)
        })


if __name__ == '__main__':
    print('='*60)
    print('NILE BITES VOICE AGENT UI')
    print('='*60)
    print('Starting server on http://127.0.0.1:5000')
    print()
    print('Instructions:')
    print('1. Open your browser to http://127.0.0.1:5000')
    print('2. Click the "TALK" button')
    print('3. The button will turn red when recording')
    print('3. Speak your utterance clearly')
    print('4. Release or wait 5 seconds')
    print('5. View the ASR transcript and agent response')
    print('6. The agent will speak the response through your speakers')
    print()
    print('Press Ctrl+C to stop the server')
    print()
    
    # Delete any temp files on start
    temp_wav = os.path.join(os.path.dirname(__file__), 'temp_recording.wav')
    if os.path.exists(temp_wav):
        os.remove(temp_wav)
    
    flask_app.run(debug=True, host='127.0.0.1', port=5000)