"""The container templates the package writes, as the files they will become.

A Dockerfile is a Dockerfile. Kept as text in a Python module it is escaped, indented
inside a string, and invisible to every tool that understands the format; kept here it
is a file you can lint, build and diff.
"""

from pathlib import Path

HERE = Path(__file__).parent

# The investigator's own box, beside the benchmark's: analysis tools, no benchmark packages
# (the benchmark's code runs in its own task; its source is copied in to read).
INVESTIGATOR_DOCKERFILE = (HERE / "investigator.Dockerfile").read_text()

# Appended to the Helm values for a k8s run: Cilium egress for the auditor's
# service, DNS plus world, so the "can verify" property holds off Docker.
EGRESS_POLICY = (HERE / "egress.helm.yaml").read_text()

__all__ = ["EGRESS_POLICY", "INVESTIGATOR_DOCKERFILE"]
