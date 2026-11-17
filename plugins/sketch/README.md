# sketch

One skill, `sketch`, for explaining how a system works with one hand-drawn
board instead of prose or a generic diagram.

- `cc-sketch make` has Claude read the code and every piece of context you
  hand it, and writes a board: a title that states the design, a numbered
  main path, boxes that say what they do and what they hold, boundaries that
  say who owns what, and sticky notes carrying the numbers and the catch.
- `cc-sketch render` draws the board as an interactive page or a PNG.

Install:

```
claude plugin install sketch@skills
```
