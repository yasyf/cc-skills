# cli-demo starter tape — fill the <PLACEHOLDERS> and delete this comment block.
#
# evp grammar rules that bite (verified against evp 0.13.0 — it runs VHS-format
# tapes but implements only a subset, so VHS examples will mislead you):
#   • One key per line. Write `Type "..."` then `Enter` on the NEXT line.
#     `Type "..." Enter` types the literal word "Enter" and never runs the command.
#   • Output is .svg / .gif / .json only (no .stats/.svgz). Screenshot must end .png.
#   • Keep every Output/Screenshot path under .cli-demo/ — evp writes relative to the
#     working directory (the repo root).
#   • Pacing here is Sleep-based: it's deterministic and can't stall. See the Wait tip
#     at the bottom before reaching for Wait.
#
# Story in three beats: setup (hidden) -> action -> payoff (held for a clean loop).
# f1/f2/f3.png are what the agent inspects each iteration.

Output .cli-demo/demo.svg

Require <TOOL>

Set Shell "bash --norc --noprofile"
Set Theme "Catppuccin Mocha"
Set FontSize 22
Set Width 1200
Set Height 600
Set Padding 24
Set WindowBar Rings
Set TypingSpeed 45ms
Set WaitTimeout 5s
Env PS1 "$ "

# --- setup: never show install / cd / clear ---
Hide
Type "clear"
Enter
Show
Sleep 800ms

# --- beat 1: context (what the tool is — e.g. --version, or a short subcommand) ---
Type "<TOOL> <CONTEXT_CMD>"
Enter
Sleep 1500ms
Screenshot .cli-demo/f1.png

# --- beat 2: the action (the one command worth showing) ---
Type "<TOOL> <DO_THE_THING>"
Enter
Sleep 1500ms
Screenshot .cli-demo/f2.png

# --- payoff hold: keep the result on screen for a clean loop point ---
Sleep 2500ms
Screenshot .cli-demo/f3.png

# Wait tip: for a command whose finish time is unpredictable (network, build), replace
# the `Sleep` after its `Enter` with `Wait /PATTERN/`. PATTERN must be text that is still
# VISIBLE on screen when the command finishes — evp's Wait scans the visible screen, so a
# pattern that scrolled off (e.g. the first `Usage:` line of a long `--help`) never matches
# and the Wait burns the whole WaitTimeout *into the animation*. PATTERN must also not
# appear in the line you typed, or Wait matches the echoed input and returns immediately.
