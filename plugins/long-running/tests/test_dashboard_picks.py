import picks

TABLE = """**From:** the coordinator.

| Option | For | Against |
|---|---|---|
| **1. One credential, read-write** (recommended by the coordinator), as you said | One mint per team. | A client bug could overwrite a run. |
| **2. A second read-only credential over run folders**, minted on first read | Runs stay safe. | Two mints per team. |
| **Keep the per-run mint** (recommended) | Checks the viewer. | A mint per set of runs. |

This also decides when the run reader lands."""


def option(option_id, label, **fields):
    return {"id": option_id, "label": label, "hint": "a short caption", "facts": [], "detail": {}, "recommended": False, **fields}


def choice(*options):
    return {"kind": "choice", "id": "pick", "prompt": "", "options": list(options), "answer": None}


def page(md):
    return {"kind": "page", "id": "why", "md": md}


def three():
    return choice(option("a", "1: one credential, read-write", recommended=True), option("b", "2: second read-only credential"), option("c", "3: keep the per-run mint"))


def by_id(parts):
    return {found["id"]: found for found in next(part for part in parts if part["kind"] == "choice")["options"]}


def test_an_option_table_moves_into_its_options_and_leaves_the_context():
    parts = picks.fold_picks([page(TABLE), three()])
    assert parts[0]["md"] == "**From:** the coordinator.\n\nThis also decides when the run reader lands."
    options = by_id(parts)
    assert options["a"]["facts"] == [{"label": "For", "value": "One mint per team.", "tone": "good"}, {"label": "Against", "value": "A client bug could overwrite a run.", "tone": "bad"}]
    assert options["a"]["hint"] == ""
    assert options["a"]["note"] == "(recommended by the coordinator), as you said"
    assert "title" not in options["a"]
    assert options["b"]["title"] == "2. A second read-only credential over run folders"
    assert options["b"]["note"] == "minted on first read"
    assert options["c"]["note"] == ""


def test_a_row_reaches_the_option_it_names_whatever_order_the_table_lists_them():
    parts = picks.fold_picks([page(TABLE), choice(option("c", "3: keep the per-run mint"), option("a", "1: one credential, read-write"), option("b", "2: second read-only credential"))])
    assert by_id(parts)["c"]["facts"][0]["value"] == "Checks the viewer."
    assert by_id(parts)["a"]["facts"][0]["value"] == "One mint per team."


def test_rows_that_name_no_option_keep_the_order_the_card_gave():
    table = "| Option | For | Against |\n|---|---|---|\n| **Adopt it** | Clean. | More rows. |\n| **No rule** | Nothing to do. | Drift. |"
    parts = picks.fold_picks([page(table), choice(option("yes", "Yes, every attribute names its writer"), option("no", "No rule"))])
    assert by_id(parts)["yes"]["facts"][0]["value"] == "Clean."
    assert by_id(parts)["no"]["facts"][0]["value"] == "Nothing to do."


def test_other_columns_keep_their_label_and_read_as_neutral_or_a_cost():
    table = "| Option | What happens | Cost |\n|---|---|---|\n| Land it | It ships. | A build. |\n| Wait | Nothing. | A day. |"
    parts = picks.fold_picks([page(table), choice(option("x", "Land it"), option("y", "Wait"))])
    assert by_id(parts)["x"]["facts"] == [{"label": "What happens", "value": "It ships.", "tone": "default"}, {"label": "Cost", "value": "A build.", "tone": "bad"}]


def test_a_table_that_is_not_one_row_per_option_stays_in_the_context():
    short = "| Option | For |\n|---|---|\n| Land it | It ships. |"
    numbers = "| At 1,014 sends | Today | With the fix |\n|---|---|---|\n| Land it | 24 s | 8 s |\n| Wait | 24 s | 24 s |"
    parts = picks.fold_picks([page(f"{short}\n\n{numbers}"), choice(option("x", "Land it"), option("y", "Wait"))])
    assert parts[0]["md"] == f"{short}\n\n{numbers}"
    assert by_id(parts)["x"]["facts"] == [] and by_id(parts)["x"]["hint"] == "a short caption"


def test_a_list_repeating_the_picks_drops_and_a_list_saying_more_stays():
    md = "Pick one:\n\n- **Land it** (recommended)\n- Wait\n- Land it needs a green build"
    parts = picks.fold_picks([page(md), choice(option("x", "Land it (Recommended)"), option("y", "Wait"))])
    assert parts[0]["md"] == "Pick one:\n\n- Land it needs a green build"


def test_a_page_that_only_repeated_the_picks_has_nothing_left_to_show():
    parts = picks.fold_picks([page("- Land it\n- Wait"), choice(option("x", "Land it"), option("y", "Wait"))])
    assert parts[0]["md"] == ""


def test_a_summary_drops_its_recommended_sentence_only_when_a_badge_carries_it():
    badge = [{"recommended": True}]
    summary = "One permission per credential. Recommended: the 1.5 s read, as you said. This replaces the earlier question."
    assert picks.context_summary(summary, badge) == "One permission per credential. This replaces the earlier question."
    assert picks.context_summary(summary, [{"recommended": False}]) == summary
