"""Container build files, published images and Kubernetes egress policy."""

from pathlib import Path

HERE = Path(__file__).parent

# Appended to the Helm values for a k8s run: Cilium egress for the auditor's
# service, DNS plus world, so the "can verify" property holds off Docker.
EGRESS_POLICY = (HERE / "egress.helm.yaml").read_text()

# The boxes, pulled by digest on Docker and Kubernetes alike: one image everywhere. The
# `images` workflow builds them (amd64 and arm64) from the Dockerfiles here, with Python
# packages exported from uv.lock at build time. Public: no registry login needed.
INVESTIGATOR_IMAGE = (
    "ghcr.io/generality-labs/inspect-audit-auditor@sha256:"
    "cde03f37170b9d112eff3fc98823c344154d51abf587de4df7146fa72a0be4e1"
)
AUDITOR_IMAGE = (
    "ghcr.io/generality-labs/inspect-audit-auditor@sha256:"
    "f0ed82536bde7b211a4973dc3b1b779431774922d8e1fef25931610d7c064b98"
)

__all__ = ["AUDITOR_IMAGE", "EGRESS_POLICY", "INVESTIGATOR_IMAGE"]
