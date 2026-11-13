# The auditor's box. Built in place for a local Docker run; for Kubernetes (Hawk) the
# `images` workflow (.github/workflows/images.yml) builds and pushes it, then pin its
# digest as AUDITOR_IMAGE in __init__.py. Python packages are pinned with hashes in
# auditor.requirements.txt, exported from uv.lock (group `auditor-box`); the base image by
# digest; only the Debian packages float with the base image's apt.
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
# TeX draws the LaTeX report here, in the box, wherever the audit runs (75f9361); the
# GL publisher's Chromium is installed on its first render instead (54d0d19)
RUN apt-get update && apt-get install -y --no-install-recommends librsvg2-bin ca-certificates curl git jq ripgrep poppler-utils \
        latexmk texlive-latex-extra texlive-fonts-recommended texlive-fonts-extra texlive-science \
    && rm -rf /var/lib/apt/lists/*
# analysis (pandas, pyarrow, scipy, sympy), figures (matplotlib), configs (pyyaml), the
# benchmark's own tests (pytest), and inspect-ai to read logs
COPY auditor.requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes --no-deps -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt
WORKDIR /workspace
CMD ["sleep", "infinity"]
