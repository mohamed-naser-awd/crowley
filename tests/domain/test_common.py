import pytest

from crowley.domain.common import (
    ByteSize,
    Duration,
    Rate,
    SemVer,
    SemVerRange,
    TemplatePath,
    is_function_name,
    is_identifier,
    is_template_id,
    parse_template_ref,
    split_function_name,
)

# ── Duration / ByteSize / Rate ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "seconds"),
    [
        (1.5, 1.5),
        (0, 0.0),
        (30, 30.0),
        ("500ms", 0.5),
        ("20s", 20.0),
        ("2m", 120.0),
        ("1h", 3600.0),
        (" 1.5s ", 1.5),
    ],
)
def test_duration_parse(raw: object, seconds: float) -> None:
    assert Duration.parse(raw).seconds == seconds


@pytest.mark.parametrize("raw", ["", "10", "5d", "-1s", -1, True, None, "1.s", [1]])
def test_duration_rejects(raw: object) -> None:
    with pytest.raises(ValueError, match=r"duration"):
        Duration.parse(raw)


def test_duration_ordering_and_str() -> None:
    assert Duration.parse("1s") < Duration.parse("2s")
    assert str(Duration.parse("1500ms")) == "1.5s"


@pytest.mark.parametrize(
    ("raw", "size"),
    [
        (0, 0),
        (2048, 2048),
        ("512KB", 512 * 1024),
        ("10MB", 10 * 1024**2),
        ("1gb", 1024**3),
        ("12B", 12),
        ("1.5KB", 1536),
    ],
)
def test_bytesize_parse(raw: object, size: int) -> None:
    assert ByteSize.parse(raw).bytes == size


@pytest.mark.parametrize("raw", ["10", "10TB", -1, True, 1.5, "MB"])
def test_bytesize_rejects(raw: object) -> None:
    with pytest.raises(ValueError, match=r"size"):
        ByteSize.parse(raw)


@pytest.mark.parametrize(
    ("raw", "per_second"), [("5/s", 5.0), ("120/m", 2.0), ("3600/h", 1.0), (" 10 / s ", 10.0)]
)
def test_rate_parse(raw: str, per_second: float) -> None:
    assert Rate.parse(raw).per_second == per_second


@pytest.mark.parametrize("raw", ["5", "5/d", "0/s", "x/s", 5, None])
def test_rate_rejects(raw: object) -> None:
    with pytest.raises(ValueError, match=r"rate"):
        Rate.parse(raw)


def test_rate_strictness() -> None:
    assert Rate.parse("1/s").is_stricter_than(Rate.parse("2/s"))
    assert not Rate.parse("120/m").is_stricter_than(Rate.parse("2/s"))


# ── SemVer ────────────────────────────────────────────────────────────────────


def test_semver_parse_and_str() -> None:
    v = SemVer.parse("1.2.3-beta.1")
    assert (v.major, v.minor, v.patch, v.prerelease) == (1, 2, 3, ("beta", "1"))
    assert str(v) == "1.2.3-beta.1"
    assert str(SemVer.parse("1.2.3+build.5")) == "1.2.3"


@pytest.mark.parametrize("raw", ["1", "1.2", "01.2.3", "1.2.3.4", "v1.2.3", ""])
def test_semver_rejects(raw: str) -> None:
    with pytest.raises(ValueError, match=r"semantic version"):
        SemVer.parse(raw)


def test_semver_ordering() -> None:
    ordered = [
        "1.0.0-alpha",
        "1.0.0-alpha.1",
        "1.0.0-alpha.beta",
        "1.0.0-beta.2",
        "1.0.0-beta.11",
        "1.0.0",
        "1.0.1",
        "1.1.0",
        "2.0.0",
    ]
    versions = [SemVer.parse(v) for v in ordered]
    shuffled = [versions[i] for i in (5, 0, 8, 2, 7, 1, 4, 6, 3)]
    assert sorted(shuffled) == versions
    assert SemVer.parse("1.0.0") == SemVer.parse("1.0.0")
    assert SemVer.parse("1.0.0").__lt__("x") is NotImplemented


@pytest.mark.parametrize(
    ("rng", "inside", "outside"),
    [
        ("^1", ["1.0.0", "1.9.9"], ["0.9.9", "2.0.0"]),
        ("^1.2.3", ["1.2.3", "1.99.0"], ["1.2.2", "2.0.0"]),
        ("^0.2.3", ["0.2.3", "0.2.9"], ["0.3.0", "0.2.2"]),
        ("^0.0.3", ["0.0.3"], ["0.0.4", "0.0.2"]),
        ("^0.2", ["0.2.0", "0.2.5"], ["0.3.0"]),
        ("^0", ["0.0.1", "0.9.9"], ["1.0.0"]),
        ("~1.2.3", ["1.2.3", "1.2.9"], ["1.3.0", "1.2.2"]),
        ("~1.2", ["1.2.0", "1.2.9"], ["1.3.0"]),
        ("~1", ["1.0.0", "1.9.0"], ["2.0.0"]),
        (">=1.0, <2", ["1.0.0", "1.5.0"], ["0.9.0", "2.0.0"]),
        (">=1.0 <2", ["1.5.0"], ["2.1.0"]),
        (">1.2", ["1.3.0"], ["1.2.9"]),
        (">1.2.3", ["1.2.4"], ["1.2.3"]),
        ("<=1.2", ["1.2.9", "1.0.0"], ["1.3.0"]),
        ("<=1.2.3", ["1.2.3"], ["1.2.4"]),
        ("1.x", ["1.0.0", "1.4.2"], ["2.0.0", "0.9.0"]),
        ("1.2.x", ["1.2.0", "1.2.7"], ["1.3.0"]),
        ("1", ["1.0.0", "1.8.0"], ["2.0.0"]),
        ("=1.2.3", ["1.2.3"], ["1.2.4"]),
        ("1.2.3", ["1.2.3"], ["1.2.4"]),
        ("*", ["0.0.1", "99.0.0"], []),
        ("x", ["3.1.4"], []),
        (">*", [], ["1.0.0"]),
    ],
)
def test_semver_ranges(rng: str, inside: list[str], outside: list[str]) -> None:
    r = SemVerRange.parse(rng)
    for v in inside:
        assert r.contains(v), f"{v} should be in {rng}"
    for v in outside:
        assert not r.contains(SemVer.parse(v)), f"{v} should not be in {rng}"
    assert str(r) == rng


@pytest.mark.parametrize("raw", ["", "  ", "^abc", ">=1.a", "~"])
def test_semver_range_rejects(raw: str) -> None:
    with pytest.raises(ValueError, match=r"version"):
        SemVerRange.parse(raw)


# ── TemplatePath ──────────────────────────────────────────────────────────────


def test_template_path_rendering() -> None:
    path = TemplatePath.of("operations", "get_people").key("steps").index(0).key("with").key("url")
    assert str(path) == "operations.get_people.steps[0].with.url"
    assert str(TemplatePath()) == "<root>"
    assert str(TemplatePath.of("headers", "User-Agent")) == "headers.User-Agent"
    assert str(TemplatePath.of("hosts", "a.example.com")) == 'hosts["a.example.com"]'
    assert str(TemplatePath.of(0, "x")) == "[0].x"
    assert str(TemplatePath.of("q", 'a"b')) == 'q["a\\"b"]'


def test_template_path_navigation() -> None:
    path = TemplatePath.of("a").child(1)
    assert path.parent == TemplatePath.of("a")
    assert path.parent.parent.is_root
    assert not path.is_root


# ── identifiers and refs ──────────────────────────────────────────────────────


def test_identifier_rules() -> None:
    assert is_identifier("get_page_people")
    assert is_identifier("_x1")
    assert not is_identifier("GetPeople")
    assert not is_identifier("1abc")
    assert not is_identifier("a-b")
    assert is_function_name("http.get")
    assert is_function_name("local.fetch_json")
    assert not is_function_name("http")
    assert not is_function_name("a.b.c")
    assert is_template_id("examples/company-directory")
    assert not is_template_id("Examples/x")
    assert not is_template_id("noslash")


def test_split_function_name() -> None:
    assert split_function_name("paginate.by_cursor") == ("paginate", "by_cursor")
    with pytest.raises(ValueError, match=r"function name"):
        split_function_name("nope")


def test_parse_template_ref_by_id() -> None:
    ref = parse_template_ref("acme/login@^1#sign_in")
    assert ref.template_id == "acme/login"
    assert ref.version is not None
    assert ref.version.contains("1.4.0")
    assert ref.operation == "sign_in"
    assert ref.path is None
    assert str(ref) == "acme/login@^1#sign_in"
    bare = parse_template_ref("acme/login")
    assert bare.version is None
    assert bare.operation is None
    assert str(bare) == "acme/login"


@pytest.mark.parametrize("raw", ["./login.yml#login", "../shared/login.yaml", "login.yml#x"])
def test_parse_template_ref_by_path(raw: str) -> None:
    ref = parse_template_ref(raw)
    assert ref.path == raw.split("#")[0]
    assert ref.template_id is None
    assert str(ref) == raw


@pytest.mark.parametrize(
    "raw", ["acme", "acme/login#Bad-Op", "acme/login#", "ACME/x", "acme/login@^abc"]
)
def test_parse_template_ref_rejects(raw: str) -> None:
    with pytest.raises(ValueError, match=r"invalid"):
        parse_template_ref(raw)
