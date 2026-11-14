import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from string import Template

HEREDOC = re.compile(r"(?<!<)<<(?!<)-?\s*(['\"]?)(\w+)\1")
SHELL_FED = re.compile(r"(?:^|[\s;&|(])(?:\S*/)?(?:sh|bash|zsh|dash|fish)(?:\s+-\w+)*\s*$")
BLANK = re.compile(r"[^\S\n]+|#[^\n]*")
REDIRECTS = ("&>>", "<<<", "<<-", "&>", ">>", ">&", "<&", ">|", "<>", "<<", ">", "<")
CASE_ENDS = (";;&", ";;", ";&")
CONTROLS = ("&&", "||", "|&", *CASE_ENDS, ";", "|", "&", "(", ")", "\n")
KEYWORDS = {"!", "{", "if", "then", "elif", "else", "do", "while", "until"}
PARENS = [(True, "("), (True, ")")]
FUNCTION_NAME = re.compile(r"[^=$]+")
POSITIONAL = re.compile(r"(?P<index>[1-9]\d*)(?:(?P<colon>:)?-(?P<default>.*))?|[@*](?::(?P<start>[1-9]\d*))?", re.DOTALL)
SPREAD = re.compile(r"\$(?:@|\{@(?::(?P<start>[1-9]\d*))?\})")
UNEXPANDED = re.compile(r"[$`]")
WORD = re.compile(r"""(?:[^\s;&|()<>'"\\]|\\.|'[^']*'|"(?:[^"\\]|\\.)*")+""", re.DOTALL)
QUOTING = re.compile(r"""'(?P<single>[^']*)'|"(?P<double>(?:[^"\\]|\\.)*)"|\\(?P<escaped>.)|(?P<bare>[^'"\\]+)""", re.DOTALL)
DOUBLE_QUOTED_ESCAPE = re.compile(r'\\([$`"\\])')


def runnable(command: str) -> str:
    """The command's shell lines, joined at their continuations, without the bodies of its heredocs but those a shell runs."""
    return scanned(command)[0]


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


def simple_commands(text: str) -> list[list[str]]:
    """The words of each simple command in shell text, split at its control operators and parentheses, each parameter left as written."""
    return [[expanded(word, {}) for word in words] for words in templates(text)]


@dataclass
class Function:
    """A function definition: its name and the commands of its body."""

    name: str
    body: list


@dataclass
class Case:
    """A case statement: the word it selects on and its arms, each its patterns, its commands and whether it ends in `;&` or `;;&`, which go on to the next arm."""

    selector: str
    arms: list[tuple[list[str], list, bool]]


def grouped(text: str) -> list:
    """The commands of shell text as the shell groups them: each simple command's words as templates, each function definition with its body, and each case statement with its arms; a definition or case the text leaves unclosed reads as plain commands."""
    return Grouping(list(parts(text))).block(None)[0]


@dataclass
class Grouping:
    """A reading of shell text's words and control operators, from `parts`, into the commands `grouped` returns."""

    tokens: list[tuple[bool, str]]
    at: int = 0

    def block(self, closer: str | None) -> tuple[list, str | None]:
        """The commands from here to the `}` closing a function body when closer is `}`, to the end of a case arm when it is `arm`, or else to the end of the text, with what ended them: `}`, the arm's `;;`, `;&` or `;;&`, the `esac` after it, or None at the end of the text. A `}` standing alone anywhere closes, as zsh reads it."""
        nodes, words, depth = [], [], 0
        while self.at < len(self.tokens):
            operator, value = self.tokens[self.at]
            head = all(word in KEYWORDS for word in words)
            ended = (closer == "arm" and (value in CASE_ENDS if operator else head and value == "esac")) or (closer == "}" and not operator and value == "}" and not depth)
            if operator or ended:
                nodes += [words] if words else []
                words = []
            if ended:
                self.at += value != "esac"
                return nodes, value
            if operator:
                self.at += 1
            elif head and value == "case" and self.tokens[self.at + 1 : self.at + 3][1:] == [(False, "in")] and not self.tokens[self.at + 1][0]:
                nodes += self.case()
            elif not words and (defined := self.function()) is not None:
                nodes += defined
            else:
                depth += (head and value == "{") - (value == "}")
                words.append(value)
                self.at += 1
        return nodes + ([words] if words else []), None

    def case(self) -> list:
        """The case statement starting here, read past its `esac`, or its arms' commands as plain ones when the text leaves it unclosed."""
        selector, arms = self.tokens[self.at + 1][1], []
        self.at += 3
        while self.at < len(self.tokens):
            patterns = []
            while self.at < len(self.tokens) and self.tokens[self.at] != (True, ")"):
                operator, value = self.tokens[self.at]
                self.at += 1
                if not operator and value == "esac" and not patterns:
                    return [Case(selector, arms)]
                if not operator:
                    patterns.append(value)
            self.at += 1
            body, end = self.block("arm")
            arms.append((patterns, body, end in (";&", ";;&")))
            if end is None:
                break
        return [node for _, body, _ in arms for node in body]

    def function(self) -> list | None:
        """The function a definition starting here makes, read past its body, or its body's commands as plain ones when the text leaves it unclosed; None when no definition with a `{` body starts here."""
        name, after = self.tokens[self.at][1], self.at + 1
        if name == "function" and self.tokens[after : after + 1] and not self.tokens[after][0]:
            name, after = self.tokens[after][1], after + 1
        elif self.tokens[after : after + 2] != PARENS or not FUNCTION_NAME.fullmatch(name):
            return None
        after += 2 * (self.tokens[after : after + 2] == PARENS)
        while self.tokens[after : after + 1] == [(True, ";")]:
            after += 1
        if self.tokens[after : after + 1] != [(False, "{")]:
            return None
        self.at = after + 1
        body, end = self.block("}")
        return [Function(name, body)] if end == "}" else body


class Expansion(Template):
    """string.Template over the shell's parameters: its variables, and its positional ones as `$1`, `${1}`, `${1:-default}`, `${1-default}`, `$@`, `$*` and `${@:2}`."""

    idpattern = r"(?a:[_a-z][_a-z0-9]*|[1-9@*])"
    braceidpattern = r"(?a:[_a-z][_a-z0-9]*|[1-9]\d*(?::?-[^}]*)?|[@*](?::[1-9]\d*)?)"


@dataclass(frozen=True)
class Parameters:
    """The values a shell expands its parameters to: its variables, and its positional parameters once the call that binds them is known, an unset one expanding to nothing."""

    variables: Mapping[str, str]
    args: Sequence[str] | None = None

    def __getitem__(self, key: str) -> str:
        if not (found := POSITIONAL.fullmatch(key)):
            return self.variables[key]
        if self.args is None:
            raise KeyError(key)
        if not found["index"]:
            return " ".join(self.args[int(found["start"] or 1) - 1 :])
        value = self.args[int(found["index"]) - 1] if int(found["index"]) <= len(self.args) else None
        if found["default"] is not None and (value is None or (found["colon"] and not value)):
            return found["default"]
        return value or ""


def expanded(word: str, variables: Mapping[str, str], args: Sequence[str] | None = None) -> str:
    """A word of templates with each parameter the variables or the positional args set replaced by its value, unsplit as zsh leaves it, and any other left as written."""
    return Expansion(word).safe_substitute(Parameters(variables, args))


def spread(written: list[str], variables: Mapping[str, str], args: Sequence[str] | None) -> list[str]:
    """A command's words of templates expanded, a word that is all of `$@` or `${@:N}` spread to the positional args it names, as the shell spreads `"$@"`."""
    words = []
    for word in written:
        if args is not None and (found := SPREAD.fullmatch(word)):
            words += args[int(found["start"] or 1) - 1 :]
        else:
            words.append(expanded(word, variables, args))
    return words


def literal(word: str) -> bool:
    """Whether an expanded word is the text the shell reads, holding no parameter or substitution left unexpanded."""
    return not UNEXPANDED.search(word)
