"""Container build files, published images and Kubernetes egress policy."""

from pathlib import Path

HERE = Path(__file__).parent

# The build files for the boxes, each with its hashed requirements (exported from
# uv.lock), which a build copies in from its context; the published images below are
# built from them (see the comment atop each file)
INVESTIGATOR_DOCKERFILE = (HERE / "investigator.Dockerfile").read_text()
INVESTIGATOR_REQUIREMENTS = (HERE / "investigator.requirements.txt").read_text()
AUDITOR_DOCKERFILE = (HERE / "auditor.Dockerfile").read_text()
AUDITOR_REQUIREMENTS = (HERE / "auditor.requirements.txt").read_text()

# Appended to the Helm values for a k8s run: Cilium egress for the auditor's
# service, DNS plus world, so the "can verify" property holds off Docker.
EGRESS_POLICY = (HERE / "egress.helm.yaml").read_text()

# The published builds, pulled where an image cannot be built in place (Hawk, any
# Kubernetes) and by default everywhere. Public: no registry login needed.
INVESTIGATOR_IMAGE = (
    "ghcr.io/generality-labs/inspect-audit-auditor@sha256:"
    "cde03f37170b9d112eff3fc98823c344154d51abf587de4df7146fa72a0be4e1"
)
AUDITOR_IMAGE = (
    "ghcr.io/generality-labs/inspect-audit-auditor@sha256:"
    "f0ed82536bde7b211a4973dc3b1b779431774922d8e1fef25931610d7c064b98"
)

__all__ = [
    "AUDITOR_DOCKERFILE",
    "AUDITOR_IMAGE",
    "AUDITOR_REQUIREMENTS",
    "EGRESS_POLICY",
    "INVESTIGATOR_DOCKERFILE",
    "INVESTIGATOR_IMAGE",
    "INVESTIGATOR_REQUIREMENTS",
]
