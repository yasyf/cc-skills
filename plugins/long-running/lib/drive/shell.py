import re
from collections.abc import Iterator, Mapping
from string import Template

HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1")
SHELL_FED = re.compile(r"(?:^|[\s;&|(])(?:\S*/)?(?:sh|bash|zsh|dash|fish)(?:\s+-\w+)*\s*$")
BLANK = re.compile(r"[^\S\n]+|#[^\n]*")
REDIRECTS = ("&>>", "<<<", "<<-", "&>", ">>", ">&", "<&", ">|", "<>", "<<", ">", "<")
CONTROLS = ("&&", "||", "|&", ";", "|", "&", "(", ")", "\n")
WORD = re.compile(r"""(?:[^\s;&|()<>'"\\]|\\.|'[^']*'|"(?:[^"\\]|\\.)*")+""", re.DOTALL)
QUOTING = re.compile(r"""'(?P<single>[^']*)'|"(?P<double>(?:[^"\\]|\\.)*)"|\\(?P<escaped>.)|(?P<bare>[^'"\\]+)""", re.DOTALL)
DOUBLE_QUOTED_ESCAPE = re.compile(r'\\([$`"\\])')


def runnable(command: str) -> str:
    """The command's shell lines, joined at their continuations, without the bodies of its heredocs but those a shell runs."""
    lines: list[str] = []
    ending, ran = None, False
    for line in command.replace("\\\n", " ").splitlines():
        if ending is not None:
            if line.strip() == ending:
                ending = None
            elif ran:
                lines.append(line)
            continue
        lines.append(line)
        if opened := HEREDOC.search(line):
            ending, ran = opened[2], bool(SHELL_FED.search(line[: opened.start()]))
    return "\n".join(line for line in lines if line.strip())


def parts(text: str) -> Iterator[tuple[bool, str]]:
    """The words and control operators the shell reads in text, each with whether it is an operator, a newline read as `;`, and each word unquoted as a string.Template whose placeholders are the parameters the shell expands in it; a comment, a redirection, its target and the descriptor number written against it are none of them, so `2>&1` and `> out` are no command's words."""
    at, target = 0, False
    while at < len(text):
        if blank := BLANK.match(text, at):
            at = blank.end()
        elif redirect := next((operator for operator in REDIRECTS if text.startswith(operator, at)), None):
            at, target = at + len(redirect), True
        elif control := next((operator for operator in CONTROLS if text.startswith(operator, at)), None):
            at, target = at + len(control), False
            yield True, ";" if control == "\n" else control
        elif word := WORD.match(text, at):
            at = word.end()
            if not target and not (word[0].isdigit() and text.startswith(("<", ">"), at)):
                yield False, template(word[0])
            target = False
        else:
            raise ValueError(f"no closing quotation in {text[at:]!r}")


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


def simple_commands(text: str) -> list[list[str]]:
    """The words of each simple command in shell text, split at its control operators and parentheses, each parameter left as written."""
    return [[expanded(word, {}) for word in words] for words in templates(text)]


def expanded(word: str, variables: Mapping[str, str]) -> str:
    """A word of templates with each parameter the variables set replaced by its value, unsplit as zsh leaves it, and any other left as written."""
    return Template(word).safe_substitute(variables)
