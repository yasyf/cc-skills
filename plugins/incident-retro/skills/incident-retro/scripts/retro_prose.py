#!/usr/bin/env python3
"""Route every authored sentence of a retro through gpt-6-astra, and lock it there.

  retro.py prose <dir> [--field ADDR]… [--stale] [--batch N] [--dry-run] [--detach]
  retro.py prose <dir> --await
  retro.py prose <dir> --list

The command enumerates the prose a writer authors, builds one work order
pointing the model at the writing contract rather than restating it, calls
`codex-ask -m astra` as a subprocess, refuses a reply that moved a fact, writes
the returned text straight into retro.json and summary.html, and records the
model, run directory, log and per-field digest in prose.lock.json. `check
--strict` reads that lock, so a hand edit or another model's rewrite fails the
gate until this command runs again.
"""
import fcntl, hashlib, json, os, re, shlex, shutil, subprocess, sys, time
from pathlib import Path

CODEX_ASK = "codex-ask"
SLOP_COP = "slop-cop"
PROSE_MODEL = "astra"
PROSE_LOCK = "prose.lock.json"
WRITE_LOCK = ".prose.lock"
PROSE_TIMEOUT = 1800
PROSE_BATCH = 36
SLOP_ROUNDS = 2
SLOP_BUDGET = 3
FIELD_MARK = "<!-- field:"
SEPARATOR = "\n\n"
LEGACY = "legacy"
HEADLINE = "headline"
SUBTITLE = "subtitle"
REQUIRED_KINDS = ("short name", HEADLINE, SUBTITLE)
REPLY_KEYS = ("REPLY_FILE", "LOG_FILE")
WRITING_DOCS = "writing-docs"
SKILL_CACHE = Path.home() / ".claude" / "plugins" / "cache" / "skills"
LANES = Path.home() / ".cache" / "incident-retro" / "prose"
RUN_LOG = "run.log"
RUN_EXIT = "run.exit"
RUN_PID = "run.pid"
RUN_LOCK = "run.lock"
AWAIT_SECONDS = 540
AWAIT_POLL = 5
STILL_RUNNING = 75
PANEL = re.compile(r'(<section\b[^>]*\bclass="[^"]*\bxs-panel\b[^"]*"[^>]*>)(.*?)(</section>)', re.S | re.I)
PANEL_KIND = re.compile(r'\bdata-kind="([^"]*)"', re.I)
TAG = re.compile(r"<[^>]+>")
FACT = re.compile(r"https?://\S+|\b[\w.-]*[\w]*(?:_[\w.-]+)+\b|\b\d+(?:[.,:/-]\d+)*[a-zA-Z%]*\b")
FENCE = re.compile(r"`+")
NAME = re.compile(r"\b[A-Z][\w.-]*(?:[A-Z][\w.-]*)*\b")
SENTENCE_HEAD = re.compile(r"(?:^|[.!?)\]]\s+|\n\s*|[-*]\s+)([A-Z][\w.-]*)")
REPLY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["fields"],
    "properties": {
        "fields": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "text"],
                "properties": {"id": {"type": "string"}, "text": {"type": "string"}},
            },
        }
    },
}


class Busy(RuntimeError):
    pass


class Owner:
    """An exclusive claim on one retro, held for the life of the run.

    The lock file is never unlinked. Unlinking it while another run holds a flock on that inode
    hands the next run a fresh inode to lock, so two runs own the retro at once.
    """

    def __init__(self, root: Path):
        self.path = root / WRITE_LOCK
        self.handle = None

    def __enter__(self):
        self.handle = self.path.open("a+")
        try:
            fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.seek(0)
            held = self.handle.read().strip()
            self.handle.close()
            raise Busy(held or "another process")
        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(f"pid {os.getpid()} since {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
        self.handle.flush()
        return self

    def __exit__(self, *exc):
        self.handle.seek(0)
        self.handle.truncate()
        self.handle.flush()
        fcntl.flock(self.handle, fcntl.LOCK_UN)
        self.handle.close()


def write_atomic(path: Path, text: str):
    scratch = path.with_name(f"{path.name}.{os.getpid()}.part")
    scratch.write_text(text)
    scratch.replace(path)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def panels(fragment: str):
    for m in PANEL.finditer(fragment):
        kind = PANEL_KIND.search(m.group(1))
        yield (kind.group(1) if kind else "panel"), m


def summary_slots(root: Path):
    path = root / "summary.html"
    if not path.exists():
        return {}
    fragment = path.read_text()
    return {f"summary.html#{kind}": m.group(2).strip() for kind, m in panels(fragment)}


def write_summary(root: Path, values: dict):
    path = root / "summary.html"
    fragment = path.read_text()
    seen = {}

    def one(m):
        kind = PANEL_KIND.search(m.group(1))
        addr = f"summary.html#{kind.group(1) if kind else 'panel'}"
        if addr not in values:
            return m.group(0)
        seen[addr] = True
        return m.group(1) + "\n" + values[addr].strip() + "\n" + m.group(3)

    path.write_text(PANEL.sub(one, fragment))
    return set(seen)


def targets(retro, R: dict, root: Path) -> dict:
    """address -> {kind, text, holder, key} over every authored sentence."""
    out = {}
    meta = R.get("meta") or {}
    for key, kind in (("title", HEADLINE), ("subtitle", SUBTITLE)):
        out[f"meta.{key}"] = {"kind": kind, "holder": meta, "key": key}
    for sid, cfg in (meta.get("sections") or {}).items():
        if isinstance(cfg, dict):
            for key, kind in (("sub", "section opener"), ("takeaway", "section takeaway")):
                if key == "takeaway" and sid in retro.REFERENCE_SECTIONS:
                    continue
                out[f"meta.sections.{sid}.{key}"] = {"kind": kind, "holder": cfg, "key": key}
    for addr, entry in retro.short_named(R):
        out[f"{addr}.h"] = {"kind": "short name", "holder": entry, "key": "h"}
    for addr, holder, key in retro.prose_slots(R):
        out[addr] = {"kind": "plain twin" if key == "p" else "prose", "holder": holder, "key": key}
    for key, _ in retro.TWINNED:
        block = R.get(key)
        if isinstance(block, dict):
            out[f"{key}.p"] = {"kind": "plain twin", "holder": block, "key": "p"}
    for c in retro.entries(R, "causes"):
        out[f"{c.get('id')}.p"] = {"kind": "plain twin", "holder": c, "key": "p"}
    for addr, spec in out.items():
        value = spec["holder"].get(spec["key"])
        spec["text"] = value if isinstance(value, str) else ""
    for addr, text in summary_slots(root).items():
        out[addr] = {"kind": "summary panel", "holder": None, "key": None, "text": text}
    return out


def facts(text: str):
    """The tokens a rewrite may not move. Backticks are markup, so `8 GiB` and 8 GiB weigh the same."""
    bare = FENCE.sub("", TAG.sub(" ", text))
    tokens = sorted(FACT.findall(bare))
    heads = set(SENTENCE_HEAD.findall(bare))
    names = {n for n in NAME.findall(bare) if n not in heads or any(c.isupper() for c in n[1:])}
    return tokens, names


def fact_drift(before: str, after: str, source: str) -> list:
    out = []
    old_tokens, old_names = facts(before)
    new_tokens, new_names = facts(after)
    if before.strip():
        if old_tokens != new_tokens:
            gone = [t for t in old_tokens if new_tokens.count(t) < old_tokens.count(t)]
            added = [t for t in new_tokens if old_tokens.count(t) < new_tokens.count(t)]
            out.append("changed the record: " + "; ".join(filter(None, [
                "dropped " + ", ".join(gone) if gone else "", "invented " + ", ".join(added) if added else ""])))
        if old_names - new_names:
            out.append("dropped the name(s) " + ", ".join(sorted(old_names - new_names)))
        if new_names - old_names:
            out.append("invented the name(s) " + ", ".join(sorted(new_names - old_names)))
    else:
        allowed, allowed_names = facts(source)
        invented = [t for t in new_tokens if t not in allowed]
        if invented:
            out.append("names " + ", ".join(sorted(set(invented))) + ", which the entry it stands for does not")
        if new_names - allowed_names:
            out.append("names " + ", ".join(sorted(new_names - allowed_names)) + ", which the entry does not")
    return out


def contract_files(retro) -> list:
    out = [retro.REFERENCE / "writing.md", retro.SKILL / "SKILL.md"]
    versions = sorted((SKILL_CACHE / WRITING_DOCS).glob("*/skills/writing-docs"), reverse=True)
    if versions:
        out.append(versions[0] / "SKILL.md")
        out += sorted((versions[0] / "references").glob("*.md"))
    return [p for p in out if p.exists()]


def budgets(retro) -> list:
    return [
        f"meta.title is the headline: the failure named in {retro.DOC_TITLE_WORDS} words or fewer and "
        f"{retro.DOC_TITLE_CHARS} characters or fewer, no colon, no service, image, table or column name. It says "
        f"what broke and, where it fits, what caused it. It is not a sentence and takes no final period",
        f"meta.subtitle is the one sentence stating the mechanism: what changed, what that caused, and what broke, in "
        f"{retro.SUBTITLE_WORDS} words or fewer and {retro.SUBTITLE_CHARS} characters or fewer, no colon, no "
        f"identifier. The headline and the subtitle carry different words; the headline is not the subtitle truncated",
        f"a short name (h) is {retro.HANDLE_WORDS} words or fewer, a noun phrase with no trailing period, no register "
        f"id, and no sentence verb: it is what a collapsed row shows in place of the sentence",
        f"a timeline entry's text is {retro.ENTRY_WORDS} words or fewer and never more than {retro.ENTRY_WORDS_MAX}",
        f"a cause's text is {retro.CAUSE_BODY_WORDS} words or fewer; a decision's why is {retro.DECISION_BODY_WORDS}; "
        f"an unknown's why is {retro.UNKNOWN_BODY_WORDS}",
        f"a plain twin (p) is {retro.TWIN_WORDS} words or fewer, or a third of the wording it twins, and it names no "
        f"register id and no file path",
        f"a summary panel is one <h3 class=\"xs-head\"> headline of {retro.XS_HEAD_WORDS} words or fewer over a "
        f"<ul class=\"xs-points\"> of {retro.XS_POINTS} or fewer <li> points, each {retro.XS_POINT_WORDS} words or "
        f"fewer; the headline is the answer and the points are the evidence, with no prose outside them",
        f"a section takeaway is {retro.TAKEAWAY_WORDS} words or fewer, a section opener one line",
        f"a lesson is {retro.LESSON_WORDS} words or fewer, a glossary definition {retro.GLOSSARY_WORDS}, an open "
        f"question {retro.UNKNOWN_WORDS}, a recognize cell {retro.RECOGNIZE_WORDS}",
    ]


def revision_order(preamble: str, findings: dict, rules: dict, store: dict) -> str:
    lines = [preamble, "", "## The prose lint already read your last reply", "",
             "`slop-cop` ran over the text you returned and flagged the passages below. Its rule catalogue is the "
             "same file the work order names. Rewrite each field so the flagged passage is gone, keeping every fact, "
             "every citation and every budget above. A flagged passage is a defect in the writing, not a false "
             "positive to argue with; where a rule genuinely does not apply, write the sentence so the detector has "
             "nothing to match rather than repeating it unchanged.", ""]
    for addr, violations in findings.items():
        lines.append(f"### {addr}")
        lines.append("what you returned:")
        lines.append(store[addr]["landed"])
        lines.append("what the lint flagged:")
        lines += violation_lines(violations, rules)
        lines.append("")
    return "\n".join(lines)


def over_budget(retro, spec: dict) -> bool:
    text = spec["text"].strip()
    if spec["kind"] == HEADLINE:
        return len(text) > retro.DOC_TITLE_CHARS or retro.words(text) > retro.DOC_TITLE_WORDS
    if spec["kind"] == SUBTITLE:
        return len(text) > retro.SUBTITLE_CHARS or retro.words(text) > retro.SUBTITLE_WORDS
    return False


def work_order(retro, R: dict, root: Path, batch: list, store: dict, rules: Path = None) -> str:
    meta = R.get("meta") or {}
    lines = [
        "You are writing the prose of one blameless incident retrospective. Every sentence of this document is "
        "written by you and by no other model: a later hand edit fails the repository's gate, so this reply is the "
        "record.",
        "",
        "## Read the contract before you write",
        "",
        "These files state the voice, the structure and the rules. Read them; do not ask me to summarise them, and do "
        "not substitute your own house style for theirs.",
        "",
    ]
    lines += [f"- {p}" for p in contract_files(retro)]
    if rules:
        lines += [
            "",
            f"- {rules} — the `slop-cop` rule catalogue, every rule the prose lint enforces, as the lint itself "
            f"publishes it. Each entry carries a `description`, a `tip` and an `llmDirective`. Write to these rules "
            f"now, in the first draft. This command runs `slop-cop check` over whatever you return and hands the "
            f"violations back to you for a rewrite, so a sentence that trips a rule costs a round trip rather than "
            f"passing.",
        ]
    lines += [
        "",
        "Use American spelling throughout.",
        "",
        "## The retro you are writing",
        "",
        f"- title: {meta.get('title', '')}",
        f"- subtitle: {meta.get('subtitle', '')}",
        f"- tags: {', '.join(meta.get('tags') or [])}",
        f"- status: {meta.get('status', '')}",
        "",
        f"The whole record, as the page reads it, is in {root / 'retro.json'}. Read it for context before you write a "
        "single field: a short name has to stand for the entry it names, and a rewritten sentence has to stay true to "
        "the entry around it.",
        "",
        "## Budgets, which are gates and not suggestions",
        "",
    ]
    lines += [f"- {b}" for b in budgets(retro)]
    lines += [
        "",
        "## The facts are frozen",
        "",
        "Never change a time, a date, a duration, a count, a number, an identifier, a URL, a code span or a person's "
        "or service's name. Never convert a number between digits and words. Never add a fact the field did not "
        "already carry. The command that called you diffs those tokens before and after and discards a reply that "
        "moved one, so an invented number costs the whole batch. You are rewording, not reporting.",
        "",
        "Leave `(C1)`, `(T7)` and every other register citation exactly where it stands: the page renders them as "
        "handles. Leave `[^3]` footnote tokens and `[text](url)` links intact.",
        "",
        "## What to write",
        "",
        "Return one object per field below, keyed by the same id. Return the field's text and nothing else: no "
        "heading, no label, no markdown fence, no commentary. A field whose current text is empty is one you are "
        "writing for the first time, from the entry quoted beneath it.",
        "",
        "A field carrying a line marked REQUIRED takes that instruction from the operator who ran this command. It "
        "outranks your reading of the field: satisfy it, and do not return the current text unchanged.",
        "",
    ]
    for addr in batch:
        spec = store[addr]
        lines.append(f"### {addr}")
        lines.append(f"kind: {spec['kind']}")
        if spec["text"].strip():
            lines.append("current text:" + (" (over budget — rewrite it shorter)" if over_budget(retro, spec) else ""))
            lines.append(spec["text"])
        else:
            lines.append("current text: (empty — write it)")
        if spec.get("source_obj") is not None and (spec.get("grounded") or not spec["text"].strip()):
            lines.append("write it from this, and name nothing this does not:")
            lines.append(json.dumps(spec["source_obj"], indent=1, ensure_ascii=False))
        if spec.get("note"):
            lines.append(f"REQUIRED, from the operator, and it overrides your own judgement about this field: "
                         f"{spec['note']}. Return wording that satisfies it even when the current text already "
                         f"reads well; returning the current text unchanged does not answer this.")
        lines.append("")
    return "\n".join(lines)


def ask(question: str, schema: dict, lane: Path, timeout: float) -> dict:
    lane.mkdir(parents=True, exist_ok=True)
    qfile, sfile = lane / "question.md", lane / "schema.json"
    qfile.write_text(question)
    sfile.write_text(json.dumps(schema))
    argv = [CODEX_ASK, "-m", PROSE_MODEL, "--schema", str(sfile), str(qfile)]
    started = time.time()
    run = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    pointers = dict(line.split(": ", 1) for line in run.stdout.splitlines() if line.split(": ", 1)[0] in REPLY_KEYS)
    if run.returncode != 0 or "REPLY_FILE" not in pointers:
        raise RuntimeError(f"{' '.join(argv)} exited {run.returncode}\n{run.stdout}\n{run.stderr}".strip())
    reply = Path(pointers["REPLY_FILE"])
    return {
        "reply": json.loads(reply.read_text()),
        "run": str(reply.parent),
        "log": pointers.get("LOG_FILE", ""),
        "seconds": round(time.time() - started, 1),
    }


def slop_cop(text: str, deep: bool) -> list:
    if not shutil.which(SLOP_COP):
        return []
    argv = [SLOP_COP, "check", "-", "--lang=markdown", "--llm" if deep else "--llm-effort=off"]
    run = subprocess.run(argv, input=text, capture_output=True, text=True)
    try:
        report = json.loads(run.stdout)
    except ValueError:
        return []
    return report.get("violations") or []


def lint(landed: dict, deep: bool) -> dict:
    """address -> violations, linting the batch in one model pass and attributing by offset."""
    found = {a: slop_cop(t, False) for a, t in landed.items()}
    if not deep:
        return {a: v for a, v in found.items() if v}
    joined, spans = [], []
    at = 0
    for addr, text in landed.items():
        head = f"{FIELD_MARK} {addr}\n\n"
        at += len(head)
        spans.append((at, at + len(text), addr))
        joined.append(head + text)
        at += len(text) + len(SEPARATOR)
    for v in slop_cop(SEPARATOR.join(joined), True):
        start = v.get("startIndex", -1)
        for lo, hi, addr in spans:
            if lo <= start < hi:
                v["startIndex"] = start - lo
                found.setdefault(addr, []).append(v)
                break
    return {a: v for a, v in found.items() if v}


def rule_catalogue(lane: Path) -> Path:
    path = lane / "slop-cop-rules.json"
    if not path.exists():
        lane.mkdir(parents=True, exist_ok=True)
        path.write_text(subprocess.run([SLOP_COP, "rules", "--pretty"], capture_output=True, text=True).stdout)
    return path


def rule_directives() -> dict:
    out = subprocess.run([SLOP_COP, "rules"], capture_output=True, text=True).stdout
    try:
        catalogue = json.loads(out)
    except ValueError:
        return {}
    return {r["id"]: r for r in catalogue.get("rules") or []}


def violation_lines(violations: list, rules: dict) -> list:
    out = []
    for v in violations:
        rule = rules.get(v.get("ruleId")) or {}
        parts = [f"- {v.get('ruleId')}: {v.get('matchedText', '')!r}"]
        if v.get("explanation"):
            parts.append(f"  why it fired: {v['explanation']}")
        if rule.get("llmDirective"):
            parts.append(f"  the rule: {rule['llmDirective']}")
        if v.get("suggestedChange"):
            parts.append(f"  slop-cop suggests: {v['suggestedChange']}")
        out += parts
    return out


def load_lock(root: Path) -> dict:
    path = root / PROSE_LOCK
    if not path.exists():
        return {"model": "", "fields": {}}
    try:
        lock = json.loads(path.read_text())
    except ValueError:
        return {"model": "", "fields": {}}
    lock.setdefault("fields", {})
    return lock


def newly_required(retro, R: dict, root: Path, store: dict) -> list:
    """The fields 0.3.0 adds or tightens, which a pre-0.3.0 retro cannot already satisfy."""
    over = set()
    for i, t in enumerate(retro.entries(R, "timeline")):
        if isinstance(t.get("text"), str) and retro.words(retro.prose_only(t["text"])) > retro.ENTRY_WORDS:
            over.add(f"{t.get('id') or f'timeline[{i}]'}.text")
    for c in retro.entries(R, "causes"):
        if isinstance(c.get("text"), str) and retro.words(retro.prose_only(c["text"])) > retro.CAUSE_BODY_WORDS:
            over.add(f"{c.get('id')}.text")
    out = []
    for addr, spec in store.items():
        text, kind = spec["text"].strip(), spec["kind"]
        if kind in REQUIRED_KINDS and not text:
            out.append(addr)
        elif kind in (HEADLINE, SUBTITLE) and over_budget(retro, spec):
            out.append(addr)
        elif kind in ("summary panel", "section takeaway") or addr in over:
            out.append(addr)
    return sorted(out)


def grandfather(retro, R: dict, root: Path, store: dict, wanted: set, lock: dict):
    """Pin every field this migration leaves alone, once. A field the lock already knows keeps its
    provenance, so a hand edit cannot launder itself as legacy by running the migration again."""
    stamped = 0
    for addr, spec in store.items():
        if addr in wanted or not spec["text"].strip():
            continue
        if isinstance(lock["fields"].get(addr), dict):
            continue
        lock["fields"][addr] = {"sha256": digest(spec["text"]), "kind": LEGACY,
                                "grandfathered": retro.plugin_version(),
                                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        stamped += 1
    return stamped


def unlocked(retro, R: dict, root: Path) -> list:
    """Addresses whose current text carries no provenance from this command."""
    lock = load_lock(root)["fields"]
    out = []
    for addr, spec in targets(retro, R, root).items():
        text = spec["text"]
        if not text.strip():
            if spec["kind"] in REQUIRED_KINDS:
                out.append(addr)
            continue
        entry = lock.get(addr)
        if not (isinstance(entry, dict) and entry.get("sha256") == digest(text)):
            out.append(addr)
    return out


def check_lock(retro, rep, R: dict, root: Path):
    lock = load_lock(root)
    onset = ((R.get("timestamps") or {}).get("onset") or "")[:10] or (R.get("meta") or {}).get("date", "")
    legacy = sorted(a for a, f in lock["fields"].items() if isinstance(f, dict) and f.get("kind") == LEGACY)
    if legacy and onset > retro.LEGACY_CUTOFF:
        rep.err(f"{len(legacy)} field(s) carry {LEGACY} provenance on a retro that starts {onset}, after "
                f"incident-retro {retro.LEGACY_CUTOFF}; the migration path is for retros written before this "
                f"version, so run retro.py prose without --quick")
    missing = unlocked(retro, R, root)
    if missing:
        shown = ", ".join(missing[:6]) + (f" and {len(missing) - 6} more" if len(missing) > 6 else "")
        rep.strict_warn(f"{len(missing)} prose field(s) carry no astra provenance in {PROSE_LOCK} ({shown}); every "
                        f"sentence on the page is written by gpt-6-astra, so run retro.py prose to write them there")
    findings = sum(f.get("slop", 0) for f in lock["fields"].values() if isinstance(f, dict))
    if findings > SLOP_BUDGET:
        worst = sorted(((f.get("slop", 0), a) for a, f in lock["fields"].items() if isinstance(f, dict)), reverse=True)
        named = ", ".join(f"{a} ({n})" for n, a in worst[:5] if n)
        rep.strict_warn(f"the authored prose carries {findings} slop-cop finding(s), over the budget of "
                        f"{SLOP_BUDGET}: {named}; rerun retro.py prose on those fields")


def evidence_snapshot(root: Path, holder: dict) -> dict:
    file = holder.get("file")
    if not isinstance(file, str):
        return {}
    path = root / file
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except ValueError:
        return {}
    if isinstance(data.get("messages"), list):
        return {"channel_name": data.get("channel_name"),
                "messages": [m.get("text", "") for m in data["messages"] if isinstance(m, dict)]}
    return {k: data[k] for k in ("name", "author") if isinstance(data.get(k), str)}


def read_record(retro, root: Path):
    R = retro.load_retro(root, "prose")
    if R is None:
        return None, None
    store = targets(retro, R, root)
    meta, summary = R.get("meta") or {}, R.get("summary") or {}
    grounding = {"title": meta.get("title", ""), "subtitle": meta.get("subtitle", ""),
                 "summary": summary.get("text", ""), "summary_plain": summary.get("p", "")}
    for addr, spec in store.items():
        if spec["kind"] in (HEADLINE, SUBTITLE):
            drop = spec["key"] if over_budget(retro, spec) else None
            spec["source_obj"] = {k: v for k, v in grounding.items() if v and k != drop}
            spec["grounded"] = True
        elif not spec["text"].strip() and spec["holder"] is not None:
            obj = {k: v for k, v in spec["holder"].items() if isinstance(v, (str, int, float, bool))}
            obj.update(evidence_snapshot(root, spec["holder"]))
            spec["source_obj"] = obj
    return R, store


def attach_notes(store: dict, notes) -> str:
    """'ADDR=text' steers one field; bare text steers every field this run asks for."""
    problems = []
    for note in notes or []:
        addr, sep, text = note.partition("=")
        if sep and addr in store:
            store[addr]["note"] = text.strip()
        elif sep and addr.startswith("meta.") or sep and "." in addr:
            problems.append(addr)
        else:
            for spec in store.values():
                spec["note"] = note.strip()
    return ", ".join(problems)


def prose(args) -> int:
    retro = args.retro
    root = Path(args.dir)
    R, store = read_record(retro, root)
    if R is None:
        return 1
    lane_root = LANES / ((R.get("meta") or {}).get("slug") or root.resolve().name)
    runs = lane_root / f"detached-{digest(str(root.resolve()))[:12]}"
    if args.await_run:
        return await_detached(root, runs)
    if args.detach:
        return detach(root, runs, [a for a in sys.argv if a != "--detach"])
    unknown = attach_notes(store, getattr(args, "note", None))
    if unknown:
        print(f"prose: --note names {unknown}, which is not a prose field; "
              f"retro.py prose {root} --list names them", file=sys.stderr)
        return 1
    if args.list:
        lock = load_lock(root)["fields"]
        for addr in sorted(store):
            text = store[addr]["text"]
            state = "empty" if not text.strip() else (
                "locked" if (lock.get(addr) or {}).get("sha256") == digest(text) else "unlocked")
            print(f"{state:9} {store[addr]['kind']:16} {addr}")
        return 0
    if args.field:
        wanted = [f for f in args.field if f in store]
        for f in args.field:
            if f not in store:
                print(f"prose: {f} is not a prose field; retro.py prose {root} --list names them", file=sys.stderr)
                return 1
    elif args.quick:
        wanted = [a for a in newly_required(retro, R, root, store) if a in unlocked(retro, R, root)]
    elif args.stale:
        wanted = unlocked(retro, R, root)
    else:
        wanted = sorted(store)
    if not wanted and not args.quick:
        print("prose: every field already carries astra provenance")
        return 0
    rules_file = rule_catalogue(lane_root)
    if args.dry_run:
        print(work_order(retro, R, root, wanted[:args.batch], store, rules_file))
        return 0
    try:
        with Owner(root):
            R, store = read_record(retro, root)
            if R is None:
                return 1
            attach_notes(store, getattr(args, "note", None))
            wanted = [a for a in wanted if a in store]
            return write_prose(retro, R, root, args, store, wanted, lane_root, rules_file)
    except Busy as held:
        print(f"prose: {held} is already writing {root}; two runs overwrite each other's fields", file=sys.stderr)
        return 1


def await_command(root: Path) -> str:
    return shlex.join([sys.executable, str(Path(sys.argv[0]).resolve()), "prose", str(root.resolve()), "--await"])


def detached_pid(runs: Path):
    pid_file = runs / RUN_PID
    if not pid_file.exists():
        return None
    pid = int(pid_file.read_text())
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    return pid


def detach(root: Path, runs: Path, argv: list) -> int:
    runs.mkdir(parents=True, exist_ok=True)
    log, status = runs / RUN_LOG, runs / RUN_EXIT
    with (runs / RUN_LOCK).open("a") as claim:
        fcntl.flock(claim, fcntl.LOCK_EX)
        if not status.exists() and detached_pid(runs) is not None:
            print(f"prose: a detached run is already writing {root}; wait on it with {await_command(root)}",
                  file=sys.stderr)
            return 1
        status.unlink(missing_ok=True)
        with log.open("w") as out:
            child = subprocess.Popen(
                ["sh", "-c", '"$@"; echo $? > "$0.tmp" && mv "$0.tmp" "$0"', str(status), sys.executable, *argv],
                stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, start_new_session=True,
                env={**os.environ, "PYTHONUNBUFFERED": "1"})
        (runs / RUN_PID).write_text(str(child.pid))
    print(f"prose: detached pid {child.pid}, log {log}")
    print(f"AWAIT: {await_command(root)}")
    return 0


def await_detached(root: Path, runs: Path, seconds: float = AWAIT_SECONDS) -> int:
    log, status = runs / RUN_LOG, runs / RUN_EXIT
    if not log.exists():
        print(f"prose: no detached run recorded for {root}; start one with --detach", file=sys.stderr)
        return 1
    deadline = time.monotonic() + seconds
    while not status.exists() and detached_pid(runs) is not None and time.monotonic() < deadline:
        time.sleep(AWAIT_POLL)
    sys.stdout.write(log.read_text())
    if status.exists():
        code = int(status.read_text())
        print(f"prose: detached run exited {code}")
        return code
    if detached_pid(runs) is None:
        print("prose: the detached run died without an exit status; read the log above and run prose again",
              file=sys.stderr)
        return 1
    print(f"prose: still running after {seconds:.0f}s")
    print(f"AWAIT: {await_command(root)}")
    return STILL_RUNNING


def write_prose(retro, R: dict, root: Path, args, store: dict, wanted: list, lane_root: Path, rules_file: Path) -> int:
    lock = load_lock(root)
    rules = rule_directives()
    landed, refused, reported, written = {}, [], [], set()
    for n in range(0, len(wanted), args.batch):
        batch = wanted[n:n + args.batch]
        lane = lane_root / f"batch-{n // args.batch + 1}"
        order = work_order(retro, R, root, batch, store, rules_file)
        print(f"prose: asking {PROSE_MODEL} for {len(batch)} field(s) ({n + 1}–{n + len(batch)} of {len(wanted)})")
        batch_landed, question = {}, order
        for attempt in range(SLOP_ROUNDS + 1):
            try:
                got = ask(question, REPLY_SCHEMA, lane / f"round-{attempt + 1}", args.timeout)
            except (RuntimeError, subprocess.TimeoutExpired, ValueError) as e:
                print(f"ERROR: {e}", file=sys.stderr)
                return 1
            print(f"prose: {PROSE_MODEL} answered in {got['seconds']}s, run {got['run']}")
            answered = set()
            for item in got["reply"].get("fields") or []:
                addr, text = item.get("id"), (item.get("text") or "").strip()
                if addr not in store or not text:
                    refused.append(f"{addr}: the reply named a field this retro does not carry" if addr not in store
                                   else f"{addr}: the reply is empty")
                    continue
                spec = store[addr]
                before = "" if spec.get("grounded") else spec["text"]
                drift = fact_drift(before, text, json.dumps(spec.get("source_obj") or {}))
                if drift:
                    refused += [f"{addr}: {d}" for d in drift]
                    continue
                answered.add(addr)
                batch_landed[addr] = text
                spec["landed"] = text
                lock["fields"][addr] = {"sha256": digest(text), "run": got["run"], "log": got["log"],
                                        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            findings = lint({a: batch_landed[a] for a in sorted(answered)}, not args.quick)
            for addr in answered:
                lock["fields"][addr]["slop"] = len(findings.get(addr) or [])
            if not findings or attempt == SLOP_ROUNDS:
                if findings:
                    print(f"prose: {sum(len(v) for v in findings.values())} lint finding(s) survived "
                          f"{SLOP_ROUNDS} revision round(s) in {len(findings)} field(s)")
                break
            print(f"prose: slop-cop flagged {sum(len(v) for v in findings.values())} passage(s) in "
                  f"{len(findings)} field(s); asking {PROSE_MODEL} to rewrite those")
            question = revision_order(work_order(retro, R, root, sorted(findings), store, rules_file),
                                      findings, rules, store)
        for addr in batch:
            if addr not in batch_landed and not any(r.startswith(addr + ":") for r in refused):
                refused.append(f"{addr}: the reply never answered")
        for r in refused[len(reported):]:
            print(f"warn:  {r}")
        reported = list(refused)

        for addr, text in batch_landed.items():
            spec = store[addr]
            if spec["holder"] is not None:
                spec["holder"][spec["key"]] = text
        written |= write_summary(root, {a: t for a, t in batch_landed.items() if a.startswith("summary.html#")})
        retro.write_retro(root, R)
        lock["model"] = "gpt-6-astra"
        lock["command"] = f"{CODEX_ASK} -m {PROSE_MODEL}"
        lock["slop"] = sum(f.get("slop", 0) for f in lock["fields"].values())
        write_atomic(root / PROSE_LOCK, json.dumps(lock, indent=2, ensure_ascii=False) + "\n")
        landed.update(batch_landed)

    if args.quick:
        stamped = grandfather(retro, R, root, store, set(landed), lock)
        lock["slop"] = sum(f.get("slop", 0) for f in lock["fields"].values())
        write_atomic(root / PROSE_LOCK, json.dumps(lock, indent=2, ensure_ascii=False) + "\n")
        print(f"prose: pinned {stamped} pre-existing field(s) as {LEGACY} provenance at plugin "
              f"{retro.plugin_version()}; a later edit to one still has to go through {PROSE_MODEL}")
    print(f"prose: wrote {len(landed)} field(s), {len(written)} of them summary panels, and locked them to "
          f"gpt-6-astra in {PROSE_LOCK}")
    print(f"prose: {lock['slop']} slop-cop finding(s) across every locked field, budget {SLOP_BUDGET}")
    if refused:
        print(f"prose: {len(refused)} field(s) did not land; rerun with --field to ask again")
    return 0


def add_prose_parser(sub, retro):
    p = sub.add_parser("prose", help="write every authored sentence through gpt-6-astra and lock it in prose.lock.json")
    p.add_argument("dir")
    p.add_argument("--field", action="append", help="one field address to rewrite; repeatable")
    p.add_argument("--stale", action="store_true", help="only the fields carrying no astra provenance")
    p.add_argument("--quick", action="store_true", help="migrate a pre-0.3.0 retro: ask astra only for what this "
                   "version newly requires, skip the model lint rounds, and pin the rest as legacy provenance")
    p.add_argument("--list", action="store_true", help="print every prose field and whether it is locked")
    p.add_argument("--batch", type=int, default=PROSE_BATCH, help="fields per model call")
    p.add_argument("--timeout", type=float, default=PROSE_TIMEOUT, help="seconds to wait for one model call")
    p.add_argument("--note", action="append", metavar="[ADDR=]TEXT", help="steer the writing without writing it: "
                   "'ADDR=text' for one field, bare text for every field in this run; repeatable")
    p.add_argument("--dry-run", action="store_true", help="print the work order instead of calling the model")
    p.add_argument("--detach", action="store_true", help="start the run in its own session, print an AWAIT: line, "
                   "and return at once")
    p.add_argument("--await", dest="await_run", action="store_true", help=f"block up to {AWAIT_SECONDS}s on the "
                   f"detached run; exits with its status, or {STILL_RUNNING} and a fresh AWAIT: line while it runs")
    p.set_defaults(fn=prose, retro=retro)
