import functools
import http.server
import json
import os
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path

from drive.state import DriveError

SITE = Path(__file__).resolve().parent / 'site'
AUDIT = r"""(() => {
  const visible = (node) => {
    const range = document.createRange();
    range.selectNodeContents(node);
    return [...range.getClientRects()].some((r) => r.width && r.height) && node.parentElement.checkVisibility({ visibilityProperty: true });
  };
  const faults = [];
  if (document.documentElement.dataset.kitState !== 'ready') faults.push(document.documentElement.dataset.kitError || 'the page did not finish drawing through the kit');
  if (!document.querySelector('[data-kit-component]')) faults.push('the page has no kit components');
  if (!document.body.innerText.trim()) faults.push('the page is blank');
  if (document.documentElement.scrollWidth > innerWidth + 2) faults.push('the page overflows horizontally');
  if ([...document.querySelectorAll('h1,h2,h3,p')].some((node) => node.textContent.trim() === window.pageHeader.title || node.textContent.trim() === window.pageHeader.goal)) faults.push('the page repeats its frame title or goal');
  const text = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let node;
  while (node = text.nextNode()) {
    if (!node.textContent.trim() || node.parentElement.closest('script,style,svg') || !visible(node)) continue;
    if (!node.parentElement.closest('[data-kit-component],h1,h2,h3,h4,button,label,summary,a,output,nav')) faults.push('plain text outside a kit component: ' + node.textContent.trim().slice(0, 100));
    if (/\b(?:POSTED|RECORDED|ORPHAN)\b|^\s*(?:DONE|WAITING|READY)\s+(?:https?:|o\d|j\d)/.test(node.textContent)) faults.push('a raw drive record is visible');
  }
  if ([...document.querySelectorAll('pre')].some((node) => !node.closest('[data-visual]') && node.checkVisibility())) faults.push('a plain text block is visible');
  return [...new Set([...faults, ...(window.pageErrors || [])])];
})()"""


def browser(session: str, *args: str, script: str | None = None) -> dict:
    try:
        done = subprocess.run(['agent-browser', '--session', session, *args, '--json'], input=script, text=True, capture_output=True, timeout=45, env=os.environ | {'AGENT_BROWSER_NAMESPACE': 'codex'})
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DriveError(f'page render check could not run agent-browser: {error}') from None
    if done.returncode:
        raise DriveError(f'page render check failed: {done.stderr.strip() or done.stdout.strip()}')
    result = json.loads(done.stdout)
    if not result['success']:
        raise DriveError(f"page render check failed: {result['error']}")
    return result['data']


def check(folder: Path, slice: dict, evidence: Path) -> list[str]:
    evidence.mkdir(parents=True, exist_ok=True)
    session = f'drive-page-check-{uuid.uuid4().hex}'
    with tempfile.TemporaryDirectory(prefix='render-', dir=evidence) as temp:
        root = Path(temp)
        for file in folder.iterdir():
            (root / file.name).symlink_to(file.resolve())
        for name in ('kit', 'vendor', 'visuals.js'):
            (root / name).symlink_to(SITE / name)
        payload = json.dumps(slice).replace('<', '\\u003c')
        bootstrap = f"<script>window.pageErrors=[];window.pageHeader={json.dumps({key: slice['page'][key] for key in ('title', 'goal')}).replace('<', '\\u003c')};const logError=console.error.bind(console);console.error=(...args)=>{{pageErrors.push(args.map(String).join(' '));logError(...args);}};addEventListener('error',e=>pageErrors.push(e.message));addEventListener('unhandledrejection',e=>pageErrors.push(String(e.reason)));addEventListener('load',()=>postMessage({{type:'drive:model',slice:{payload}}},'*'));</script>"
        (root / 'checked.html').write_text((folder / 'index.html').read_text().replace('<head>', '<head>' + bootstrap, 1))

        class Files(http.server.SimpleHTTPRequestHandler):
            def end_headers(self) -> None:
                self.send_header('Access-Control-Allow-Origin', '*')
                if self.path == '/checked.html':
                    self.send_header('Content-Security-Policy', 'sandbox allow-scripts')
                super().end_headers()

            def log_message(self, *args: object) -> None:
                pass

        with http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Files, directory=root)) as server:
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                paths = []
                for name, width, height in [('desktop', 1280, 900), ('narrow', 390, 844)]:
                    browser(session, 'set', 'viewport', str(width), str(height))
                    browser(session, 'open', f'http://127.0.0.1:{server.server_port}/checked.html')
                    browser(session, 'wait', '--fn', "['ready','failed'].includes(document.documentElement.dataset.kitState)")
                    faults = browser(session, 'eval', '--stdin', script=AUDIT)['result']
                    errors = browser(session, 'errors')['errors']
                    target = evidence / f'{name}.png'
                    browser(session, 'screenshot', str(target), '--full')
                    if faults or errors:
                        raise DriveError('page render check refused ' + name + ': ' + '; '.join(map(str, [*faults, *errors])))
                    paths.append(str(target))
                return paths
            finally:
                server.shutdown()
                browser(session, 'close')
