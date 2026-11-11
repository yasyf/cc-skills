import re

HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1")
SHELL_FED = re.compile(r"(?:^|[\s;&|(])(?:\S*/)?(?:sh|bash|zsh|dash|fish)(?:\s+-\w+)*\s*$")


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
