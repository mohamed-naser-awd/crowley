"""The extractor contract (SPEC §13.8): every extractor, built-in or not, must pass these.

Use it from a test module with ``pytest.mark.parametrize("case", [ContractCase(...)])`` and call
:func:`check_contract`.
"""

from dataclasses import dataclass
from typing import Any

from crowley.application.extractors import BaseExtractor, ExtractionEngine
from crowley.domain.extractors import Query
from crowley.domain.values import Handle


@dataclass(frozen=True)
class ContractCase:
    extractor: BaseExtractor
    document: str | bytes
    language: str
    query: str
    """A query matching exactly two nodes of ``document``."""
    attr: str
    expected: list[Any]
    """What ``read(node, attr)`` returns for the two matches."""
    bad_query: str

    def __str__(self) -> str:
        return self.extractor.name


def check_contract(case: ContractCase) -> None:
    extractor = case.extractor
    spec = extractor.spec  # validates the declaration (E903 otherwise)
    assert spec.default_language in spec.query_languages
    assert spec.supports_attribute(spec.default_attribute)
    assert spec.supports_attribute(case.attr)
    assert extractor.node_type == f"{spec.name}.node"

    # three methods
    root = extractor.parse(case.document, base_url=None, media_type=None)
    nodes = extractor.select(root, case.query, case.language)
    assert isinstance(nodes, list)
    assert len(nodes) == 2
    assert [extractor.read(node, case.attr) for node in nodes] == case.expected
    assert extractor.select(object(), case.query, case.language) == []  # not a node

    # query validation
    assert extractor.check_query(case.language, case.query) is None
    assert extractor.check_query(case.language, case.bad_query) is not None

    # the shared engine works on top of the three methods
    engine = ExtractionEngine(extractor)
    handle = engine.handle(engine.root(case.document))
    assert isinstance(handle, Handle)
    assert handle.type_name == extractor.node_type
    every = engine.select(
        handle, Query(language=case.language, source=case.query), case.attr, every=True
    )
    assert every == case.expected
    first = engine.select(
        case.document, Query(language=case.language, source=case.query), case.attr, every=False
    )
    assert first == case.expected[0]
    response = {"body": case.document, "url": "https://example.com/page", "media_type": None}
    assert engine.root(response) is not None
