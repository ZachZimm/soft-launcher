#!/usr/bin/env python3
"""Drive the real Tauri WebKit UI, local decision backend and safe native builtins.

Requires WebKitWebDriver inside an isolated virtual display. Preserves frontend
settings and external services. Never replaces an existing process or restores
host focus. All input stays in the launcher's DOM; no global key injection.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]


def request(base, method, path, data=None, timeout=70):
    raw = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(base + path, data=raw, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            value = json.load(response)["value"]
    except urllib.error.HTTPError as error:
        value = json.loads(error.read())["value"]
    if isinstance(value, dict) and value.get("error") and 'stacktrace' in value:
        raise RuntimeError(f"WebDriver {value['error']}: {value.get('message', '')}")
    return value


def launchers():
    result = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            exe = (entry / 'exe').resolve(strict=True)
            if exe.name == 'soft-launcher':
                result.append((int(entry.name), exe))
        except (OSError, RuntimeError):
            pass
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, default=ROOT / 'src-tauri/target/debug/soft-launcher')
    parser.add_argument('--output', type=Path, default=ROOT / 'src-tauri/desktop-functional-green.json')
    parser.add_argument('--isolated-display', action='store_true', help='Acknowledge this command runs inside the provisioned private display')
    parser.add_argument('--wait-seconds', type=int, default=65)
    parser.add_argument('--expect-production', action='store_true')
    parser.add_argument('--expect-cold', action='store_true', help='Require the existing local model to be unloaded; never unload or restart it')
    args = parser.parse_args()
    if not args.isolated_display or os.environ.get('SOFT_LAUNCHER_ISOLATED_QA') != '1':
        parser.error('Refusing GUI launch: run inside a provisioned isolated display with SOFT_LAUNCHER_ISOLATED_QA=1 and --isolated-display')
    args.output = args.output.resolve()
    binary = args.binary.resolve(strict=True)
    evidence = {'at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'binary': str(binary.relative_to(ROOT)),
                'binarySha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
                'automationOnly': 'TAURI_WEBVIEW_AUTOMATION=true', 'settingsChanged': False,
                'isolatedDisplay': True, 'focusRestored': False,
                'display': os.environ.get('DISPLAY'), 'gdkBackend': os.environ.get('GDK_BACKEND'),
                'hostWaylandUnset': 'WAYLAND_DISPLAY' not in os.environ,
                'hostHyprlandUnset': 'HYPRLAND_INSTANCE_SIGNATURE' not in os.environ,
                'checks': [], 'passed': False}
    driver = None
    native_pid = None
    session = None
    base = 'http://127.0.0.1:4455'
    try:
        if args.expect_cold:
            with urllib.request.urlopen('http://127.0.0.1:11434/api/ps', timeout=2) as response:
                model_before = json.load(response)
            evidence['loadedModelsBefore'] = len(model_before.get('models', []))
            assert evidence['loadedModelsBefore'] == 0, 'Cold run requires natural model expiry; no service state was changed'
        if launchers():
            raise RuntimeError('Existing launcher detected; this harness never replaces existing processes')
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        env = os.environ.copy()
        env['TAURI_WEBVIEW_AUTOMATION'] = 'true'
        env['SOFT_LAUNCHER_CATALOGUE'] = str(ROOT / 'catalogue.toml')
        driver = subprocess.Popen(['WebKitWebDriver', '--host=127.0.0.1', f'--port={port}'],
                                  env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  start_new_session=False)
        for _ in range(100):
            try:
                request(base, 'GET', '/status', timeout=1)
                break
            except (OSError, RuntimeError):
                time.sleep(.05)
        created = request(base, 'POST', '/session', {'capabilities': {'alwaysMatch': {
            'webkitgtk:browserOptions': {'binary': str(binary)}}}})
        session = created['sessionId']
        native_pid = created['capabilities'].get('webkit:processID')

        def js(script):
            return request(base, 'POST', f'/session/{session}/execute/sync', {'script': script, 'args': []})

        def wait(script, label, seconds=None):
            start = time.monotonic()
            while time.monotonic() - start < (seconds or args.wait_seconds):
                result = js(script)
                if result:
                    evidence['checks'].append({'check': label, 'ms': round((time.monotonic() - start) * 1000)})
                    return result
                time.sleep(.08)
            diagnostic = js("return {native:!!window.__TAURI_INTERNALS__,calc:!!document.querySelector('#launcher-card-calc'),empty:document.querySelector('.empty-text')?.textContent?.trim(),error:document.querySelector('.error')?.textContent?.trim()?.slice(0,160)}")
            evidence['failure'] = {'check': label, 'diagnostic': diagnostic}
            raise AssertionError(label)

        wait("return !!window.__TAURI_INTERNALS__ && !!document.querySelector('#launcher-input')", 'real Tauri webview mounted')
        evidence['origin'] = js('return location.origin')
        if args.expect_production:
            assert evidence['origin'] not in ('http://127.0.0.1:5173', 'http://localhost:5173'), 'Expected embedded production assets'
        js("""
window.__functionalSmoke={executions:[],previews:[],contexts:[],requests:[]};
const originalFetch=window.__functionalOriginalFetch??window.fetch;
window.__functionalOriginalFetch=originalFetch;
window.fetch=async function(...args){
 const target=new URL(typeof args[0]==='string'?args[0]:args[0].url,location.href);
 const ipc=target.protocol==='ipc:'||target.hostname==='ipc.localhost';
 let payload;
 if(ipc&&['/execute','/preview'].includes(target.pathname)){
  payload=JSON.parse(args[1].body);
  if(!['calc','convert'].includes(payload?.request?.actionId))return new Response(JSON.stringify('Smoke refuses unsafe action'),{headers:{'Content-Type':'application/json','Tauri-Response':'error'}});
 }
 const start=performance.now();
 const item={path:target.pathname};
 if(target.pathname==='/v1/systemone'){
  const request=JSON.parse(args[1].body), state=JSON.parse(request.state);
  const bytes=new TextEncoder().encode(request.state).length;
  Object.assign(item,{stateBytes:bytes,stateEstimatedTokens:Math.ceil(bytes/3),questions:Object.keys(request.questions).length,choices:Object.keys(request.questions.action?.criteria??{}).length,privateFreeWarmup:state.input===''&&state.window===null&&state.clipboard?.head===''&&state.recent.length===0&&state.candidates.length===0});
  window.__functionalSmoke.requests.push(item);
 }
 try{
  const response=await originalFetch(...args);
  if(ipc&&['/execute','/preview','/get_state'].includes(target.pathname)){
   const result=await response.clone().json();
   if(target.pathname==='/get_state'&&result.catalogue)window.__functionalSmoke.contexts.push({actions:result.catalogue.actions.length,candidates:result.candidates.length,recent:result.recent.length,apps:result.apps.length,filesIndexed:result.filesIndexed,warnings:result.warnings.length,clipboardHeadChars:result.clipboard.head.length});
   if(target.pathname==='/preview')window.__functionalSmoke.previews.push({actionId:payload.request.actionId,args:payload.request.args,text:result.text});
   if(target.pathname==='/execute')window.__functionalSmoke.executions.push({actionId:payload.request.actionId,args:payload.request.args,status:result.status,text:result.text});
  }
  item.status=response.status;item.ms=Math.round(performance.now()-start);return response;
 }catch(e){item.name=e.name;item.ms=Math.round(performance.now()-start);throw e}
};
return true;
""")

        def input_text(value):
            js("const i=document.querySelector('#launcher-input');i.value=" + json.dumps(value) + ";i.dispatchEvent(new Event('input',{bubbles:true}));return true")
            time.sleep(.12)

        def execute_card(action, expected):
            wait("return window.__functionalSmoke.previews.some(x=>x.actionId===" + json.dumps(action) + "&&x.text===" + json.dumps(expected) + ")", f'read-only {action} preview {expected}', seconds=5)
            visible_result = str(float(expected.split(' ')[0])).removesuffix('.0') if action == 'convert' else expected
            wait("const c=document.querySelector('#launcher-card-" + action + "');return c?.querySelector('.result-line.ready .answer')?.textContent.includes(" + json.dumps(visible_result) + ")", f'{action} card renders preview before execution', seconds=5)
            wait(f"const c=document.querySelector('#launcher-card-{action}');return document.querySelector('.state.ok')&&c&&!c.closest('.dimmed')&&!c.classList.contains('invalid')", f'{action} current card available')
            js(f"document.querySelector('#launcher-card-{action}').dispatchEvent(new PointerEvent('pointerdown',{{bubbles:true,button:0,pointerId:1}}));return true")
            time.sleep(.22)
            js(f"document.querySelector('#launcher-card-{action}')?.dispatchEvent(new PointerEvent('pointerup',{{bubbles:true,button:0,pointerId:1}}));return true")
            wait("return window.__functionalSmoke.executions.some(x=>x.actionId===" + json.dumps(action) + "&&x.status==='executed'&&x.text===" + json.dumps(expected) + ")", f'native {action} result {expected}', seconds=5)
            wait("return document.body.innerText.includes(" + json.dumps(expected) + ")", 'native result rendered', seconds=5)

        if args.expect_cold:
            assert js("return !!document.querySelector('.state.busy')"), 'Cold startup must expose loading state'
            input_text('123*456')
            js("const i=document.querySelector('#launcher-input');i.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));i.dispatchEvent(new KeyboardEvent('keyup',{key:'Enter',bubbles:true}));return true")
            js("window.__TAURI_INTERNALS__.invoke('hide_window');return true")
            time.sleep(.15)
            subprocess.run([str(binary), '--toggle'], cwd=ROOT, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        input_text('2+2')
        wait("return !!document.querySelector('.state.ok')", 'initial current decision ready')
        assert js('return window.__functionalSmoke.executions.length') == 0, 'Startup or early Enter executed an old input'
        assert not js("return document.querySelector('.strip .error')?.getAttribute('title')?.startsWith('collector:')??false"), 'Optional collector warning was promoted to an error'
        execute_card('calc', '4')
        # Replace an in-flight request; wait beyond debounce, below the normal model latency.
        input_text('123*456')
        time.sleep(.12)
        input_text('7*6')
        wait("const c=document.querySelector('#launcher-card-calc');return c&&!c.closest('.dimmed')&&c.textContent.includes('7*6')", 'latest replacement input rendered')
        execute_card('calc', '42')
        assert not any(x['args'].get('expr') == '123*456' for x in js('return window.__functionalSmoke.executions'))
        # Dismiss/reopen via the exact native command and single-instance toggle.
        js("window.__TAURI_INTERNALS__.invoke('hide_window');return true")
        time.sleep(.1)
        subprocess.run([str(binary), '--toggle'], cwd=ROOT, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        input_text('1 km to m')
        execute_card('convert', '1000.000000 m')
        evidence['trace'] = js('return window.__functionalSmoke')
        evidence['modelResourceDurationsMs'] = js("return performance.getEntriesByType('resource').filter(entry=>new URL(entry.name,location.href).pathname==='/v1/systemone').map(entry=>Math.round(entry.duration))")
        assert evidence['trace']['contexts'], 'Real native context was not collected'
        assert all(context['candidates'] <= 5 and context['recent'] <= 5 and context['clipboardHeadChars'] <= 200 for context in evidence['trace']['contexts']), 'Collected context exceeded native bounds'
        assert all(request['stateEstimatedTokens'] < 1500 for request in evidence['trace']['requests']), 'Decision state exceeded token budget'
        if args.expect_cold:
            with urllib.request.urlopen('http://127.0.0.1:11434/api/ps', timeout=2) as response:
                models_after = json.load(response).get('models', [])
            evidence['expectedModelLoadedAfter'] = any(model.get('name') == 'clef-flash-q8:latest' for model in models_after)
            assert evidence['expectedModelLoadedAfter'], 'Cold request did not load the expected model'
            warmups = [request for request in evidence['trace']['requests'] if request['privateFreeWarmup']]
            assert len(warmups) == 1 and warmups[0].get('status') == 200, 'Cold readiness must use one uninterrupted private-free request'
            evidence['checks'].append({'check': 'cold load survives typing, early Enter, dismiss and reopen; expected model usable'})
        evidence['passed'] = True
    except Exception as error:
        evidence['error'] = str(error)
    finally:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(evidence, indent=2) + '\n')
        if driver:
            if session:
                try:
                    request(base, 'DELETE', f'/session/{session}', timeout=2)
                except Exception:
                    pass
            if native_pid:
                try:
                    os.kill(native_pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            driver.terminate()
            try:
                driver.wait(timeout=3)
            except subprocess.TimeoutExpired:
                driver.kill()

    print(json.dumps({'passed': evidence['passed'], 'checks': evidence['checks'], 'error': evidence.get('error'), 'output': str(args.output.relative_to(ROOT))}, indent=2))
    return 0 if evidence['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
