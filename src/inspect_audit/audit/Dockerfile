FROM python:3.13-slim
# TeX draws the LaTeX report here, in the box, wherever the audit runs (75f9361); the
# GL publisher's Chromium is installed on its first render instead (54d0d19)
RUN apt-get update && apt-get install -y --no-install-recommends librsvg2-bin ca-certificates curl git jq ripgrep poppler-utils \
        latexmk texlive-latex-extra texlive-fonts-recommended texlive-fonts-extra texlive-science \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir inspect-ai==INSPECT_VERSION pandas pyarrow matplotlib jupyter pyyaml scipy sympy pytest
WORKDIR /workspace
CMD ["sleep", "infinity"]
