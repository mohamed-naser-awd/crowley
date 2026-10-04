import pytest

from crowley.domain.errors import ExecutionError, LimitError
from crowley.domain.expressions import HelperTable
from crowley.domain.values import Handle

from .conftest import Run


def test_every_spec_helper_is_registered() -> None:
    spec_helpers = """
        str int float bool type_of is_null default coalesce len trim lower upper replace split join
        starts_with ends_with contains pad_left pad_right slugify regex_match regex_find
        regex_find_all regex_replace round abs min max sum parse_number flatten unique sort sort_by
        map filter find any all first last range chunk zip group_by keys values entries merge pick
        omit get url_join url_parse url_encode url_decode query_get json_parse json_dump
        base64_encode base64_decode html_unescape md5 sha256 now parse_date format_date uuid
    """.split()  # noqa: SIM905
    table = HelperTable.builtin()
    assert set(spec_helpers) == set(table.helpers)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        # types & null handling
        ("str(12)", "12"),
        ("int('42')", 42),
        ("float('2.5')", 2.5),
        ("bool('yes')", True),
        ("type_of([])", "list"),
        ("is_null(null)", True),
        ("default(null, 'x')", "x"),
        ("default(0, 'x')", 0),
        ("coalesce(null, null, 3, 4)", 3),
        ("coalesce(null)", None),
        # strings
        ("len('héllo')", 5),
        ("len([1, 2])", 2),
        ("len({a: 1})", 1),
        ("trim('  a  ')", "a"),
        ("lower('AbC')", "abc"),
        ("upper('abc')", "ABC"),
        ("replace('a.b.c', '.', '-')", "a-b-c"),
        ("split('a,b,,c', ',')", ["a", "b", "", "c"]),
        ("join(['a', 1, true], '-')", "a-1-true"),
        ("starts_with('crowley', 'crow')", True),
        ("ends_with('crowley', 'ley')", True),
        ("contains('crowley', 'owl')", True),
        ("contains([1, 2], 2.0)", True),
        ("contains({a: 1}, 'a')", True),
        ("pad_left('7', 3, '0')", "007"),
        ("pad_right('ab', 4)", "ab  "),
        ("slugify('Héllo, World!  2026')", "hello-world-2026"),
        # regex
        ("regex_match('order 123', '\\\\d+')", True),
        ("regex_match('order', '\\\\d+')", False),
        ("regex_find('order 123-45', '(\\\\d+)-(\\\\d+)', 2)", "45"),
        ("regex_find('price: 12', 'price: (?P<n>\\\\d+)', 'n')", "12"),
        ("regex_find('abc', '\\\\d')", None),
        ("regex_find_all('a1 b2 c3', '\\\\d')", ["1", "2", "3"]),
        ("regex_find_all('a=1 b=2', '(\\\\w)=(\\\\d)')", [["a", "1"], ["b", "2"]]),
        ("regex_replace('a1b22', '\\\\d+', '#')", "a#b#"),
        # numbers
        ("round(2.5)", 3),
        ("round(-2.5)", -3),
        ("round(1.005, 2)", 1.01),
        ("round(7)", 7),
        ("abs(-3)", 3),
        ("min([3, 1, 2])", 1),
        ("min(3, 1.5)", 1.5),
        ("max('b', 'a')", "b"),
        ("max([2, 9, 4])", 9),
        ("sum([1, 2, 3.5])", 6.5),
        ("sum([])", 0),
        ("parse_number('1,234.5')", 1234.5),
        ("parse_number('1.234,5', 'de')", 1234.5),
        ("parse_number('$12k')", 12000),
        ("parse_number('3.4M followers')", 3400000),
        ("parse_number('-17')", -17),
        ("parse_number('12 kg')", 12),
        ("parse_number('1 234 567')", 1234567),
        ("parse_number('Total: 99.')", 99),
        # lists
        ("flatten([[1, [2]], 3])", [1, [2], 3]),
        ("flatten([[1, [2]], 3], 2)", [1, 2, 3]),
        ("flatten([[1]], 0)", [[1]]),
        ("unique([1, 1.0, 'a', 'a', {k: 1}, {k: 1}, true, 1])", [1, "a", {"k": 1}, True]),
        ("sort([3, 1, 2])", [1, 2, 3]),
        ("sort(['b', 'a'])", ["a", "b"]),
        ("sort_by([{n: 2}, {n: 1}], x => x.n)", [{"n": 1}, {"n": 2}]),
        ("map([1, 2], x => x * 10)", [10, 20]),
        ("filter([1, 2, 3, 4], x => x % 2 == 0)", [2, 4]),
        ("filter(['a', 'b'], (x, i) => i > 0)", ["b"]),
        ("find([1, 5, 7], x => x > 4)", 5),
        ("find([1], x => x > 4)", None),
        ("any([1, 5], x => x > 4)", True),
        ("all([1, 5], x => x > 4)", False),
        ("any([false, true])", True),
        ("all([])", True),
        ("first([4, 5])", 4),
        ("last([4, 5])", 5),
        ("first([])", None),
        ("last([])", None),
        ("range(0, 5)", [0, 1, 2, 3, 4]),
        ("range(10, 0, -3)", [10, 7, 4, 1]),
        ("chunk([1, 2, 3, 4, 5], 2)", [[1, 2], [3, 4], [5]]),
        ("zip([1, 2, 3], ['a', 'b'])", [[1, "a"], [2, "b"]]),
        ("group_by(['ab', 'ac', 'b'], s => s[0])", {"a": ["ab", "ac"], "b": ["b"]}),
        ("group_by([1, 2, 3], x => x % 2)", {"1": [1, 3], "0": [2]}),
        # objects
        ("keys({a: 1, b: 2})", ["a", "b"]),
        ("values({a: 1, b: 2})", [1, 2]),
        ("entries({a: 1})", [["a", 1]]),
        ("merge({a: 1, b: 1}, {b: 2}, {c: 3})", {"a": 1, "b": 2, "c": 3}),
        ("pick({a: 1, b: 2}, ['a', 'z'])", {"a": 1}),
        ("omit({a: 1, b: 2}, ['a'])", {"b": 2}),
        ("get({a: {b: [10, 20]}}, 'a.b[1]')", 20),
        ("get({a: {b: [10, 20]}}, 'a.b[-1]')", 20),
        ("get({a: 1}, 'a.b.c', 'dflt')", "dflt"),
        ("get({a: [1]}, ['a', 0])", 1),
        ("get({a: [1]}, 'a[3]')", None),
        # URLs
        ("url_join('https://x.com/a/b', '../c')", "https://x.com/c"),
        ("url_encode('a b/c')", "a%20b%2Fc"),
        ("url_decode('a%20b')", "a b"),
        ("query_get('https://x.com/?p=2&p=3&q=', 'p')", "2"),
        ("query_get('https://x.com/?q=', 'q')", ""),
        ("query_get('https://x.com/', 'q')", None),
        # encoding
        ("json_parse('{\"a\": [1, null]}')", {"a": [1, None]}),
        ("json_dump({a: [1, null]})", '{"a":[1,null]}'),
        ("base64_encode('hi')", "aGk="),
        ("base64_decode('aGk=')", "hi"),
        ("html_unescape('&lt;b&gt; &amp; &#39;')", "<b> & '"),
        ("md5('a')", "0cc175b9c0f1b6a831c399e269772661"),
        ("sha256('a')", "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb"),
    ],
)
def test_helpers(run: Run, source: str, expected: object) -> None:
    result = run(source)
    assert result == expected
    assert type(result) is type(expected)


def test_url_parse(run: Run) -> None:
    assert run("url_parse('https://user@ex.com:8443/p/a?x=1&y=2&y=3#top')") == {
        "scheme": "https",
        "host": "ex.com",
        "port": 8443,
        "path": "/p/a",
        "query": {"x": "1", "y": ["2", "3"]},
        "fragment": "top",
    }


def test_dates(run: Run) -> None:
    assert run("now()") == "2026-01-02T03:04:05Z"
    assert run("parse_date('2026-03-01T10:00:00+02:00')") == "2026-03-01T08:00:00Z"
    assert run("parse_date('2026-03-01 10:00')") == "2026-03-01T10:00:00Z"
    assert run("parse_date('01/03/2026', '%d/%m/%Y', '-05:00')") == "2026-03-01T05:00:00Z"
    assert run("parse_date('2026-07-01T12:00', null, 'Europe/Berlin')") == "2026-07-01T10:00:00Z"
    assert run("format_date('2026-03-01T08:00:00Z', '%Y/%m/%d %H:%M')") == "2026/03/01 08:00"
    assert run("format_date('2026-03-01T08:00:00Z', '%H:%M', '+0530')") == "13:30"
    assert run("format_date('2026-03-01T08:00:00', '%H')") == "08"


def test_uuid_is_deterministic_with_seeded_random(run: Run) -> None:
    first = run("uuid()")
    assert isinstance(first, str)
    assert len(first) == 36
    assert first[14] == "4"
    assert run("uuid()") != first


@pytest.mark.parametrize(
    "source",
    [
        "int(true)",
        "len(1)",
        "trim(1)",
        "split('a', '')",
        "join([null], ',')",
        "join('a', ',')",
        "contains(1, 1)",
        "contains('a', 1)",
        "pad_left('a', 3, 'xy')",
        "pad_left('a', 'x')",
        "regex_find('a', 'a', true)",
        "regex_find('a', 'a', 3)",
        "regex_match('a', '(')",
        "regex_replace('a', 'a', '\\\\9')",
        "round('1')",
        "round(1, 1.5)",
        "min([])",
        "min([1, 'a'])",
        "sum([1, 'a'])",
        "abs(null)",
        "parse_number('abc')",
        "parse_number('1', 'fr')",
        "parse_number('1..2')",
        "flatten([1], -1)",
        "flatten('x')",
        "sort([1, 'a'])",
        "filter([1], x => x)",
        "find([1], x => 1)",
        "any([1])",
        "range(0, 5, 0)",
        "range(0, 'a')",
        "chunk([1], 0)",
        "keys([1])",
        "merge({a: 1}, [1])",
        "pick({a: 1}, [1])",
        "get({a: 1}, [true])",
        "url_parse('http://x.com:abc/')",
        "json_parse('{bad')",
        "json_parse('NaN')",
        "json_dump(h)",
        "base64_decode('!!')",
        "parse_date('nope')",
        "parse_date('2026', null, 'Mars/Base')",
        "format_date('nope', '%Y')",
        "map([1], (a, b, c) => a)",
        "str(h)",
    ],
)
def test_helper_errors_are_e501(run: Run, source: str) -> None:
    with pytest.raises(ExecutionError) as info:
        run(source, h=Handle("x", None))
    assert info.value.code == "E501"


def test_range_limit(run: Run) -> None:
    with pytest.raises(LimitError) as info:
        run("range(0, 1000000)")
    assert info.value.code == "E703"


def test_regex_timeout(env_slow_regex: Run) -> None:
    with pytest.raises(LimitError) as info:
        env_slow_regex("regex_match(s, '(a|aa)+$')", s="a" * 60 + "!")
    assert info.value.code == "E703"


@pytest.fixture
def env_slow_regex() -> Run:
    from crowley.domain.expressions import EvalEnv, evaluate, parse_expression
    from crowley.infrastructure.regex import RegexLibEngine

    env = EvalEnv(regex=RegexLibEngine(timeout=0.01))

    def _run(source: str, /, **scope: object) -> object:
        return evaluate(parse_expression(source), scope, env)  # type: ignore[arg-type]

    return _run
