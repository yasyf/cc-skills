import re
import shlex
from collections.abc import Iterator

HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1")
SHELL_FED = re.compile(r"(?:^|[\s;&|(])(?:\S*/)?(?:sh|bash|zsh|dash|fish)(?:\s+-\w+)*\s*$")
BLANK = re.compile(r"[^\S\n]+|#[^\n]*")
REDIRECTS = ("&>>", "<<<", "<<-", "&>", ">>", ">&", "<&", ">|", "<>", "<<", ">", "<")
CONTROLS = ("&&", "||", "|&", ";", "|", "&", "(", ")", "\n")
WORD = re.compile(r"""(?:[^\s;&|()<>'"\\]|\\.|'[^']*'|"(?:[^"\\]|\\.)*")+""", re.DOTALL)


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
    """The words and control operators the shell reads in text, each with whether it is an operator, a newline read as `;`; a comment, a redirection, its target and the descriptor number written against it are none of them, so `2>&1` and `> out` are no command's words."""
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
                yield False, "".join(shlex.split(word[0]))
            target = False
        else:
            raise ValueError(f"no closing quotation in {text[at:]!r}")


def simple_commands(text: str) -> list[list[str]]:
    """The words of each simple command in shell text, split at its control operators and parentheses."""
    found: list[list[str]] = [[]]
    for operator, part in parts(text):
        if operator:
            found.append([])
        else:
            found[-1].append(part)
    return [words for words in found if words]
