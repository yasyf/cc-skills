import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from string import Template

HEREDOC = re.compile(r"(?<!<)<<(?!<)-?\s*(['\"]?)(\w+)\1")
SHELL_FED = re.compile(r"(?:^|[\s;&|(])(?:\S*/)?(?:sh|bash|zsh|dash|fish)(?:\s+-\w+)*\s*$")
BLANK = re.compile(r"[^\S\n]+|#[^\n]*")
REDIRECTS = ("&>>", "<<<", "<<-", "&>", ">>", ">&", "<&", ">|", "<>", "<<", ">", "<")
CONTROLS = ("&&", "||", "|&", ";", "|", "&", "(", ")", "\n")
WORD = re.compile(r"""(?:[^\s;&|()<>'"\\]|\\.|'[^']*'|"(?:[^"\\]|\\.)*")+""", re.DOTALL)
QUOTING = re.compile(r"""'(?P<single>[^']*)'|"(?P<double>(?:[^"\\]|\\.)*)"|\\(?P<escaped>.)|(?P<bare>[^'"\\]+)""", re.DOTALL)
DOUBLE_QUOTED_ESCAPE = re.compile(r'\\([$`"\\])')
PIPES = ("|", "|&")


@dataclass(frozen=True)
class Stdin:
    """What a simple command reads on stdin by its own text: `|` from a pipe, `<<` a heredoc's body, `<<<` a here-string's word, or `<` the file it names, as a string.Template."""

    via: str
    text: str = ""


def runnable(command: str) -> str:
    """The command's shell lines, joined at their continuations, without the bodies of its heredocs but those a shell runs."""
    return scanned(command)[0]


def heredocs(command: str) -> list[str]:
    """The body of each heredoc the command opens, in the order it opens them."""
    return scanned(command)[1]


def scanned(command: str) -> tuple[str, list[str]]:
    """The command's runnable lines and the bodies of the heredocs it opens, read in one pass."""
    lines: list[str] = []
    bodies: list[list[str]] = []
    ending, ran = None, False
    for line in command.replace("\\\n", " ").splitlines():
        if ending is not None:
            if line.strip() == ending:
                ending = None
                continue
            bodies[-1].append(line)
            if ran:
                lines.append(line)
            continue
        lines.append(line)
        if opened := HEREDOC.search(line):
            ending, ran = opened[2], bool(SHELL_FED.search(line[: opened.start()]))
            bodies.append([])
    return "\n".join(line for line in lines if line.strip()), ["\n".join(body) for body in bodies]


def tokens(text: str) -> Iterator[tuple[str, str]]:
    """What the shell reads in text: `word` and each word, `control` and each control operator, a newline read as `;`, or a redirection operator and the word it names; a comment and the descriptor number written against a redirection are none of them."""
    at, redirect = 0, None
    while at < len(text):
        if blank := BLANK.match(text, at):
            at = blank.end()
        elif operator := next((operator for operator in REDIRECTS if text.startswith(operator, at)), None):
            at, redirect = at + len(operator), operator
        elif control := next((operator for operator in CONTROLS if text.startswith(operator, at)), None):
            at, redirect = at + len(control), None
            yield "control", ";" if control == "\n" else control
        elif word := WORD.match(text, at):
            at = word.end()
            if redirect:
                yield redirect, word[0]
            elif not (word[0].isdigit() and text.startswith(("<", ">"), at)):
                yield "word", word[0]
            redirect = None
        else:
            raise ValueError(f"no closing quotation in {text[at:]!r}")


def parts(text: str) -> Iterator[tuple[bool, str]]:
    """The words and control operators the shell reads in text, each with whether it is an operator, a newline read as `;`, and each word unquoted as a string.Template whose placeholders are the parameters the shell expands in it; a comment, a redirection, its target and the descriptor number written against it are none of them, so `2>&1` and `> out` are no command's words."""
    for kind, value in tokens(text):
        if kind == "control":
            yield True, value
        elif kind == "word":
            yield False, template(value)


def template(word: str) -> str:
    """A word unquoted as string.Template source, its `$` a placeholder only where the shell expands it: bare or in double quotes, unescaped."""
    pieces = []
    for quoted in QUOTING.finditer(word):
        if quoted["bare"] is not None:
            pieces.append(quoted["bare"])
        elif quoted["double"] is not None:
            pieces.append(DOUBLE_QUOTED_ESCAPE.sub(lambda escape: escape[1].replace("$", "$$"), quoted["double"]))
        else:
            pieces.append((quoted["escaped"] if quoted["single"] is None else quoted["single"]).replace("$", "$$"))
    return "".join(pieces)


def templates(text: str) -> list[list[str]]:
    """The words of each simple command in shell text, split at its control operators and parentheses, each a string.Template of the parameters the shell expands in it."""
    found: list[list[str]] = [[]]
    for operator, part in parts(text):
        if operator:
            found.append([])
        else:
            found[-1].append(part)
    return [words for words in found if words]


def stdins(command: str) -> list[Stdin | None]:
    """What each simple command of templates(runnable(command)) reads on stdin by its own text, in the same order; None for one that reads its caller's."""
    bodies = iter(heredocs(command))
    found: list[tuple[bool, Stdin | None]] = [(False, None)]
    for kind, value in tokens(runnable(command)):
        worded, stdin = found[-1]
        if kind == "control":
            found.append((False, Stdin("|") if value in PIPES else None))
        elif kind == "word":
            found[-1] = (True, stdin)
        elif kind in ("<<", "<<-"):
            found[-1] = (worded, Stdin("<<", next(bodies, "")))
        elif kind in ("<", "<<<"):
            found[-1] = (worded, Stdin(kind, template(value)))
    return [stdin for worded, stdin in found if worded]


def simple_commands(text: str) -> list[list[str]]:
    """The words of each simple command in shell text, split at its control operators and parentheses, each parameter left as written."""
    return [[expanded(word, {}) for word in words] for words in templates(text)]


def expanded(word: str, variables: Mapping[str, str]) -> str:
    """A word of templates with each parameter the variables set replaced by its value, unsplit as zsh leaves it, and any other left as written."""
    return Template(word).safe_substitute(variables)
