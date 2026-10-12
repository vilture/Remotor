from typing import Never


def assert_never(value: Never) -> Never:
    raise RuntimeError(f"unexpected value: {value!r}")
