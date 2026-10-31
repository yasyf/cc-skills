from __future__ import annotations

import re

TEST_FILE = re.compile(
    r"(?:^|/)(?:tests?|testing|testdata|fixtures|__snapshots__|__mocks__|e2e|evals|fakes?|mocks?|testkit|testutils?)/|(?:^|/)[a-z0-9_-]+(?:test|testkit|testutils?|harness)/"
    r"|(?:^|/)tests?\.\w+$|(?:^|/)test_[^/]*$|_test\.\w+$|\.(?:test|spec|e2e)\.\w+$|(?:^|/)(?:fake|mock)s?_[^/]*$|_(?:fake|mock)s?\.\w+$"
)


def test_file(path: str) -> bool:
    return TEST_FILE.search(path) is not None
