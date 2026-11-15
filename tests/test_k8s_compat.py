"""Catch the Helm version parsing regression before a paid Hawk pilot."""


def test_helm_stdout_is_accepted_by_the_sandbox_version_parser() -> None:
    # Helm prints its version with a leading 'v' and a trailing newline; the k8s
    # sandbox's own parser (not a copy of it) must accept that as a recent Helm
    from k8s_sandbox._prereqs import _parse_version  # pyright: ignore[reportPrivateUsage]

    assert _parse_version("v3.21.3+g1ad6e68\n").compare("3.13.0") > 0
