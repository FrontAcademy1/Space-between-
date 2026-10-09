from __future__ import annotations
import asyncio, json, os, threading, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from flask import Flask, jsonify, render_template, request, send_file
from qa_runner import run_qa

ROOT = Path(__file__).resolve().parent
app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 1 * 1024 * 1024
_lock = threading.Lock()
_state = {"running": False, "status": "idle", "message": "جاهز للاختبار", "events": [], "report": None}

def load_json(name):
    return json.loads((ROOT / name).read_text(encoding='utf-8'))

def save_state_event(event):
    with _lock:
        _state['events'].append(event)
        _state['events'] = _state['events'][-500:]
        _state['message'] = event.get('message', _state['message'])

def worker(username, password):
    try:
        cfg = load_json('config.json')
        answers = load_json('test_answers.json')
        result = asyncio.run(run_qa(cfg, answers, username, password, save_state_event))
        with _lock:
            _state['report'] = result
            _state['status'] = 'completed'
            _state['message'] = 'انتهى الاختبار'
    except Exception as exc:
        save_state_event({"time": datetime.now(timezone.utc).isoformat(), "status": "error", "message": f"توقف الاختبار: {type(exc).__name__}: {exc}"})
        with _lock:
            _state['status'] = 'failed'
    finally:
        with _lock:
            _state['running'] = False

def valid_target():
    cfg = load_json('config.json')
    url = cfg.get('base_url', '')
    parsed = urlparse(url)
    allowed = [h.lower() for h in cfg.get('allowed_hosts', [])]
    if parsed.scheme != 'https' or not parsed.hostname or parsed.hostname.lower() not in allowed:
        raise ValueError('رابط الاختبار يجب أن يكون HTTPS وموجودًا ضمن allowed_hosts في config.json')
    if cfg.get('mode') != 'authorized_staging_qa':
        raise ValueError('شغّل الوضع authorized_staging_qa فقط على بيئة اختبار مصرح بها.')

@app.get('/')
def index():
    cfg = load_json('config.json')
    return render_template('index.html', target=cfg.get('base_url', 'غير مضبوط'))

@app.get('/api/status')
def status():
    with _lock:
        # Never return credentials, cookies, browser profile, or page storage.
        return jsonify({k: _state[k] for k in ('running','status','message','events','report')})

@app.post('/api/start')
def start():
    with _lock:
        if _state['running']:
            return jsonify(ok=False, error='يوجد اختبار يعمل بالفعل'), 409
    try:
        valid_target()
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 400
    data = request.get_json(silent=True) or {}
    username = str(data.get('username', '')).strip()
    password = str(data.get('password', ''))
    if not username or not password:
        return jsonify(ok=False, error='اكتب كود حساب الاختبار وكلمة المرور.'), 400
    with _lock:
        _state.update(running=True, status='running', message='بدأ الاختبار', events=[], report=None)
    threading.Thread(target=worker, args=(username, password), daemon=True).start()
    # Local references disappear after thread start; credentials are never written to disk/logs.
    return jsonify(ok=True, message='بدأ الاختبار')

@app.post('/api/stop')
def stop():
    # Playwright runner checks this flag between questions; do not kill arbitrary processes.
    (ROOT / 'STOP_REQUESTED').write_text('stop', encoding='utf-8')
    return jsonify(ok=True, message='تم طلب إيقاف الاختبار')

@app.get('/api/report.csv')
def report_csv():
    path = ROOT / 'output' / 'report.csv'
    if not path.exists():
        return jsonify(error='لا يوجد تقرير بعد'), 404
    return send_file(path, as_attachment=True, download_name='sprix-qa-report.csv', mimetype='text/csv')

if __name__ == '__main__':
    os.makedirs(ROOT / 'output', exist_ok=True)
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', '8000')), debug=False)
