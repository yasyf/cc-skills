#!/usr/bin/env python3
"""Print a design project's doc to its design-doc.pdf.

Usage: python3 build-pdf.py [dir]
`dir` is the design project directory (default: the current one). The
script serves that directory over HTTP on a free port, opens design-doc.html
(or index.html, when that is what the directory holds) in headless Chrome
driven over its debugging pipe, waits for the page to report every diagram
and connector rendered, runs the template's own print preparation, and
prints through its print stylesheet, so the PDF is the document the doc's
PDF button prints. A diagram that failed to render fails the build. The
page loads Mermaid from jsdelivr, so the build needs network access. Needs
Chrome or Chromium; set CHROME=/path/to/chrome if discovery misses yours,
and CHROME_ARGS to add command-line flags to the browser it launches.
"""
import argparse, base64, functools, json, os, select, shlex, shutil, subprocess, sys, tempfile, textwrap, threading, time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CHROME_TIMEOUT_S = 180
CONSOLE_KEPT = 40
STDERR_LINES = 20
NETWORK_HINT = "the page loads its diagrams from jsdelivr, so this needs network access"
NOT_READY = "the page never set data-ready; its diagrams or connectors did not finish rendering"
DOC_PAGES = ("design-doc.html", "index.html")
DOM_JS = "document.documentElement.outerHTML"
FAILED_JS = """({failed:[...document.querySelectorAll('[data-failed="1"]')].map(h=>(h.dataset.source||h.id||h.className||"diagram").split("\\n")[0].slice(0,60)),
 rendered:document.querySelectorAll("svg.mmd").length})"""
PREP_JS = "window.designDocPrepPrint().then(()=>document.fonts.ready).then(()=>new Promise(r=>requestAnimationFrame(()=>setTimeout(r,0))))"


def find_chrome():
    if os.environ.get("CHROME"):
        c = os.environ["CHROME"]
        if Path(c).exists() or shutil.which(c):
            return c
        print(f"build-pdf.py: CHROME={c} does not exist.", file=sys.stderr)
        sys.exit(2)
    for c in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
              "/Applications/Chromium.app/Contents/MacOS/Chromium"):
        if Path(c).exists():
            return c
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome", "msedge"):
        p = shutil.which(name)
        if p:
            return p
    return None


def require_chrome():
    chrome = find_chrome()
    if not chrome:
        print("no Chrome or Chromium found. Install Google Chrome, or set CHROME=/path/to/chrome and rerun.", file=sys.stderr)
        sys.exit(2)
    return chrome


def doc_page(root: Path):
    for name in DOC_PAGES:
        if (root / name).exists():
            return name
    return None


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def serve(root: Path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/"


def ready_js(timeout_s: float) -> str:
    return """new Promise(res=>{
 const t0=Date.now(), budget=%d;
 const state=timedOut=>({ready:document.documentElement.dataset.ready||"",exposed:!!window.designDocReady,timedOut:timedOut});
 let done=false;
 const settle=s=>{if(!done){done=true;res(s);}};
 const tick=()=>{
  if(done)return;
  if(window.designDocReady){window.designDocReady.then(()=>settle(state(false)),e=>settle(Object.assign(state(false),{error:String(e&&e.message||e)})));return;}
  if(Date.now()-t0>budget)return settle(state(true));
  setTimeout(tick,50);
 };
 setTimeout(()=>settle(state(true)),budget);
 tick();
})""" % int(timeout_s * 1000)


def chrome_args() -> list:
    return shlex.split(os.environ.get("CHROME_ARGS", ""))


class ChromeError(Exception):
    pass


def bind_debug_pipe(chrome_in: int, chrome_out: int):
    def in_child():
        src_in, src_out = os.dup(chrome_in), os.dup(chrome_out)
        os.dup2(src_in, 3)
        os.dup2(src_out, 4)
    return in_child


class Chrome:
    def __init__(self, chrome: str):
        self.profile = tempfile.mkdtemp(prefix="design-doc-chrome-")
        chrome_in, self.writer = os.pipe()
        self.reader, chrome_out = os.pipe()
        for fd in (chrome_in, chrome_out):
            os.set_inheritable(fd, True)
        self.log = Path(self.profile) / "chrome-stderr.log"
        with self.log.open("wb") as log:
            self.proc = subprocess.Popen(
                [chrome, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                 f"--user-data-dir={self.profile}", "--remote-debugging-pipe", *chrome_args(), "about:blank"],
                preexec_fn=bind_debug_pipe(chrome_in, chrome_out), close_fds=False,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log)
        os.close(chrome_in)
        os.close(chrome_out)
        self.buf, self.seq, self.console = b"", 0, []

    def close(self):
        try:
            self.send("Browser.close")
            self.proc.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired, ChromeError):
            self.proc.kill()
            self.proc.wait()
        for fd in (self.reader, self.writer):
            os.close(fd)
        shutil.rmtree(self.profile, ignore_errors=True)

    def send(self, method: str, params=None, session=None) -> int:
        self.seq += 1
        msg = {"id": self.seq, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        try:
            os.write(self.writer, json.dumps(msg).encode() + b"\0")
        except OSError as e:
            raise ChromeError(self.exit_reason(f"cannot write to Chrome: {e}"))
        return self.seq

    def stderr_tail(self) -> str:
        return "\n".join(self.log.read_text(errors="replace").strip().splitlines()[-STDERR_LINES:])

    def exit_reason(self, fallback: str) -> str:
        try:
            code = self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            return fallback
        return f"Chrome exited with status {code} before the page was ready"

    def note(self, msg: dict):
        method, params = msg.get("method"), msg.get("params") or {}
        if method == "Runtime.consoleAPICalled":
            self.console.append(f"{params.get('type', 'log')}: " + " ".join(describe(a) for a in params.get("args") or []))
        elif method == "Log.entryAdded":
            entry = params.get("entry") or {}
            where = entry.get("url") or entry.get("source") or ""
            self.console.append(f"{entry.get('level', 'info')}: {entry.get('text', '')}" + (f" [{where}]" if where else ""))
        elif method == "Runtime.exceptionThrown":
            detail = params.get("exceptionDetails") or {}
            self.console.append("exception: " + ((detail.get("exception") or {}).get("description") or detail.get("text") or ""))

    def take(self) -> dict:
        raw, _, self.buf = self.buf.partition(b"\0")
        msg = json.loads(raw)
        if "id" not in msg:
            self.note(msg)
        return msg

    def recv(self, timeout: float) -> dict:
        deadline = time.monotonic() + timeout
        while b"\0" not in self.buf:
            ready, _, _ = select.select([self.reader], [], [], max(0.0, deadline - time.monotonic()))
            if not ready:
                raise ChromeError(self.exit_reason(f"Chrome did not answer within {timeout:.0f}s"))
            chunk = os.read(self.reader, 1 << 16)
            if not chunk:
                raise ChromeError(self.exit_reason("Chrome closed its debugging pipe"))
            self.buf += chunk
        return self.take()

    def drain(self, timeout: float = 0.5):
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            ready, _, _ = select.select([self.reader], [], [], min(remaining, 0.05))
            if not ready:
                break
            try:
                chunk = os.read(self.reader, 1 << 16)
            except OSError:
                break
            if not chunk:
                break
            self.buf += chunk
        while b"\0" in self.buf:
            self.take()

    def call(self, method: str, params=None, session=None, timeout: float = CHROME_TIMEOUT_S) -> dict:
        ident = self.send(method, params, session)
        while True:
            msg = self.recv(timeout)
            if msg.get("id") != ident:
                continue
            if "error" in msg:
                raise ChromeError(f"{method}: {msg['error'].get('message', msg['error'])}")
            return msg.get("result") or {}


def describe(arg: dict) -> str:
    if "value" in arg:
        return str(arg["value"])
    return arg.get("description") or arg.get("unserializableValue") or arg.get("type") or ""


def open_page(chrome: Chrome, url: str) -> str:
    target = chrome.call("Target.createTarget", {"url": "about:blank"})["targetId"]
    session = chrome.call("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
    chrome.call("Page.enable", session=session)
    chrome.call("Runtime.enable", session=session)
    chrome.call("Log.enable", session=session)
    chrome.call("Page.navigate", {"url": url}, session=session)
    return session


def evaluate(chrome: Chrome, session: str, expression: str, timeout: float = CHROME_TIMEOUT_S):
    res = chrome.call("Runtime.evaluate", {"expression": expression, "awaitPromise": True, "returnByValue": True},
                      session=session, timeout=timeout)
    detail = res.get("exceptionDetails")
    if detail:
        exc = detail.get("exception") or {}
        raise ChromeError(exc.get("description") or detail.get("text") or "script failed")
    return (res.get("result") or {}).get("value")


def wait_ready(chrome: Chrome, session: str, timeout: float) -> dict:
    return evaluate(chrome, session, ready_js(timeout), timeout=timeout + 30)


def ready_problem(state: dict, timeout: float) -> str:
    if state.get("error"):
        return f"{NOT_READY}; the page reported {state['error']}"
    if not state.get("exposed"):
        return f"{NOT_READY}; it never exposed designDocReady within {timeout:.0f}s, so it did not load its registers"
    if state.get("timedOut"):
        return f"{NOT_READY}; designDocReady never resolved within {timeout:.0f}s"
    return NOT_READY


def diagnostics(chrome: Chrome) -> list:
    chrome.drain()
    out = []
    code = chrome.proc.poll()
    if code is not None:
        out.append(f"Chrome exited with status {code}")
    stderr = chrome.stderr_tail()
    out.append("Chrome stderr:\n" + textwrap.indent(stderr, "  ") if stderr else "Chrome wrote nothing to stderr")
    console = chrome.console[-CONSOLE_KEPT:]
    out.append("page console:\n" + textwrap.indent("\n".join(console), "  ") if console else "the page logged nothing to the console")
    return out


def print_page(chrome: Chrome, url: str, pdf: Path, timeout: float = CHROME_TIMEOUT_S):
    session = open_page(chrome, url)
    state = wait_ready(chrome, session, timeout)
    if state["ready"] != "1":
        raise ChromeError(ready_problem(state, timeout))
    diagrams = evaluate(chrome, session, FAILED_JS)
    if diagrams["failed"]:
        cause = "its source has a syntax error" if diagrams["rendered"] else f"no diagram rendered at all, so {NETWORK_HINT}"
        raise ChromeError("Mermaid did not render " + ", ".join(repr(f) for f in diagrams["failed"]) + f": {cause}")
    evaluate(chrome, session, PREP_JS)
    data = chrome.call("Page.printToPDF", {"printBackground": True, "preferCSSPageSize": True, "displayHeaderFooter": False}, session=session)["data"]
    pdf.write_bytes(base64.b64decode(data))


def build(root: Path) -> int:
    page = doc_page(root)
    if not page:
        print(f"build-pdf.py: {root} holds no design-doc.html or index.html.", file=sys.stderr)
        return 1
    chrome_path = require_chrome()
    pdf = root / "design-doc.pdf"
    pdf.unlink(missing_ok=True)
    chrome = Chrome(chrome_path)
    server, base = serve(root)
    try:
        print_page(chrome, base + page, pdf)
    except ChromeError as e:
        print("\n".join([f"build-pdf.py: {e}", *diagnostics(chrome)]), file=sys.stderr)
        return 1
    finally:
        server.shutdown()
        chrome.close()
    print(f"wrote {pdf} ({pdf.stat().st_size // 1024} KB)")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dir", nargs="?", default=".", help="design project directory")
    sys.exit(build(Path(ap.parse_args().dir)))


if __name__ == "__main__":
    main()
