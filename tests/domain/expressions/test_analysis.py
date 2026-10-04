from crowley.domain.expressions import analyze, parse_expression


def refs(source: str) -> list[tuple[str, tuple[str | int, ...]]]:
    return [(r.root, r.path) for r in analyze(parse_expression(source)).references]


def test_constant_chains() -> None:
    assert refs("steps.rows[0].title") == [("steps", ("rows", 0, "title"))]
    assert refs("inputs['company']") == [("inputs", ("company",))]
    assert refs("prev") == [("prev", ())]


def test_dynamic_index_stops_the_chain() -> None:
    # The constant prefix (steps.rows) is still reported, plus the dynamic index.
    assert refs("steps.rows[i].title") == [("steps", ("rows",)), ("i", ())]
    analysis = analyze(parse_expression("vars.items[vars.n]"))
    assert {r.root for r in analysis.references} == {"vars"}


def test_lambda_params_are_not_roots() -> None:
    analysis = analyze(parse_expression("map(steps.rows, r => r.id + offset)"))
    assert analysis.roots == {"steps", "offset"}


def test_projection_body_is_not_a_root() -> None:
    assert refs("pages[*].items[*].id") == [("pages", ())]


def test_helper_calls_are_collected() -> None:
    analysis = analyze(parse_expression("len(map(xs, x => trim(x))) + default(y, 0)"))
    assert [(c.name, c.arg_count, c.lambda_positions) for c in analysis.calls] == [
        ("len", 1, ()),
        ("map", 2, (1,)),
        ("trim", 1, ()),
        ("default", 2, ()),
    ]


def test_all_node_kinds_are_walked() -> None:
    source = "{k: -a if not b and c in [d] else e[1:f:g]} == {k: h} or (j < k)"
    analysis = analyze(parse_expression(source))
    assert analysis.roots == set("abcdefghjk")
