#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# ///
"""Ask every judge fixture of Sonnet and Jev, then score each backend on the same answers and print one line per case and a summary per backend.

    judge-eval.py FIXTURES [--runs N] [--out FILE] [--answers FILE]

A fixture is one JSON file: `kind`, `expect` (`yes`, `no`, `not-yes` or `any`), `ruling`, `words` (a list of
`{at, words}`, oldest first), `answered` (what his words answered, or null) and `artifact`. Fixtures carry the
owner's words, so they live in the drive's state folder, never in this repository. Each run asks Sonnet twice
and Jev once per case, and every backend's verdict is replayed from those answers through `judge.decide`, so
the backends are compared on identical answers. It calls models, so it never runs in CI; `--answers` writes
the bare verdicts it got, with no owner words, for `tests/fixtures/judge-answers.json`, which CI replays.
"""

from __future__ import annotations

import argparse
import json
import statistics
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from long_running import judge

PARALLEL = 6


def passed(expect: str, verdict: str) -> bool:
    match expect:
        case "yes":
            return verdict == "yes"
        case "no" | "not-yes":
            return verdict != "yes"
        case _:
            return True


def answers(case: Path) -> dict:
    fixture = json.loads(case.read_text())
    evidence = judge.owner_words(fixture["ruling"], [(said["at"], said["words"]) for said in fixture["words"]], fixture["answered"])
    what, question = judge.KINDS[fixture["kind"]]
    text = fixture["artifact"][: judge.ARTIFACT_CHARS]
    prompt = judge.PROMPT.format(evidence=evidence, artifact=what, text=text, question=question)
    state = f"{evidence}\n\nThe artifact is {what}:\n~~~~~~~~\n{text}\n~~~~~~~~"
    return {
        "case": case.stem,
        "expect": fixture["expect"],
        "sonnet": lambda: judge.sonnet(prompt, judge.JUDGE_SECONDS, None),
        "jev": lambda: judge.jev(state, question, judge.JUDGE_SECONDS, None),
    }


def replay(backend: judge.Backend, sonnet: list[judge.Answer], jev: list[judge.Answer]) -> tuple[judge.Verdict, list[judge.Answer]]:
    used: list[judge.Answer] = []
    queues = {"sonnet": iter(sonnet), "jev": iter(jev)}

    def take(model: str) -> judge.Verdict:
        used.append(answer := next(queues[model]))
        return answer.verdict

    return judge.decide(backend, lambda: take("sonnet"), lambda: take("jev")), used


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("fixtures", type=Path)
    parser.add_argument("--runs", type=int, default=2, help="score every case this many times, to see a judge wobble")
    parser.add_argument("--out", type=Path, help="also write every scored verdict as one JSON line here")
    parser.add_argument("--answers", type=Path, help="also write each case's expected verdict and the bare answers of every run here")
    args = parser.parse_args()
    cases = [answers(case) for case in sorted(args.fixtures.glob("*.json"))]
    jobs = [(case, model) for case in cases for model, count in (("sonnet", 2 * args.runs), ("jev", args.runs)) for _ in range(count)]
    with ThreadPoolExecutor(PARALLEL) as pool:
        asked = list(pool.map(lambda job: (job[0]["case"], job[1], job[0][job[1]]()), jobs))
    rows = []
    for case in cases:
        sonnet = [answer for name, model, answer in asked if name == case["case"] and model == "sonnet"]
        jev = [answer for name, model, answer in asked if name == case["case"] and model == "jev"]
        for run in range(args.runs):
            for backend in judge.BACKENDS:
                verdict, used = replay(backend, sonnet[2 * run : 2 * run + 2], jev[run : run + 1])
                rows.append(
                    {
                        "case": case["case"],
                        "backend": backend,
                        "run": run,
                        "expect": case["expect"],
                        "verdict": verdict.verdict,
                        "pass": passed(case["expect"], verdict.verdict),
                        "seconds": round(sum(answer.seconds for answer in used), 1),
                        "cost_usd": sum(answer.cost_usd for answer in used),
                        "tokens": sum(answer.tokens for answer in used),
                        "asks": [f"{'sonnet' if answer.p_yes is None else 'jev'}:{answer.verdict.verdict}" for answer in used],
                        "p_yes": next((answer.p_yes for answer in jev[run : run + 1]), None),
                        "sentence": verdict.sentence,
                    }
                )
    for row in rows:
        print(f"{'PASS' if row['pass'] else 'FAIL'} {row['case']} {row['backend']} run {row['run']}: expect {row['expect']}, got {row['verdict']} from {' '.join(row['asks'])} in {row['seconds']:g} s | {row['sentence']}")
    for backend in judge.BACKENDS:
        mine = [row for row in rows if row["backend"] == backend]
        misses = sum(row["expect"] == "yes" and row["verdict"] != "yes" for row in mine)
        false_flags = sum(row["expect"] in ("no", "not-yes") and row["verdict"] == "yes" for row in mine)
        seconds = [row["seconds"] for row in mine]
        print(
            f"{backend}: {sum(row['pass'] for row in mine)}/{len(mine)} pass, {misses} missed, {false_flags} false flags;"
            f" median {statistics.median(seconds):g} s, slowest {max(seconds):g} s;"
            f" {100 * statistics.mean(row['cost_usd'] for row in mine):.2f} cents and {statistics.mean(row['tokens'] for row in mine):.0f} tokens a case"
        )
    if args.out:
        args.out.write_text("".join(json.dumps(row) + "\n" for row in rows))
    if args.answers:
        bare = {
            case["case"]: {
                "expect": case["expect"],
                "runs": [
                    {
                        "sonnet": [answer.verdict.verdict for name, model, answer in asked if name == case["case"] and model == "sonnet"][2 * run : 2 * run + 2],
                        "jev": [answer.p_yes for name, model, answer in asked if name == case["case"] and model == "jev"][run : run + 1],
                    }
                    for run in range(args.runs)
                ],
            }
            for case in cases
        }
        args.answers.write_text(json.dumps(bare, indent=1) + "\n")


if __name__ == "__main__":
    main()
