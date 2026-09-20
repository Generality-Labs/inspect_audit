"""Catch the Helm version parsing regression before a paid Hawk pilot."""
from semver import Version


def test_helm_stdout_is_accepted_by_sandbox_version_parser():
    # k8s_sandbox._prereqs._parse_version removes 'v', but not the CLI newline.
    output = "v3.21.3+g1ad6e68\n"
    assert Version.parse(output[1:]).compare("3.13.0") > 0
