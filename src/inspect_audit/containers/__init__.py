"""Container build files and published images."""

from pathlib import Path

HERE = Path(__file__).parent

# The boxes, pulled by digest on Docker and Kubernetes alike: one image everywhere. The
# `images` workflow builds them (amd64 and arm64) from the Dockerfiles here, with Python
# packages exported from uv.lock at build time. Public: no registry login needed.
INVESTIGATOR_IMAGE = (
    "ghcr.io/generality-labs/inspect-audit-auditor@sha256:"
    "e3f69ee90aa0103d6597e0a28f0d582196fda90554962283dcbbdf4a187eabd9"
)
AUDITOR_IMAGE = (
    "ghcr.io/generality-labs/inspect-audit-auditor@sha256:"
    "11e169186d410a467078ee109f1dd1b424481d3f48998d9a00905e6672eadfee"
)

__all__ = ["AUDITOR_IMAGE", "INVESTIGATOR_IMAGE"]
