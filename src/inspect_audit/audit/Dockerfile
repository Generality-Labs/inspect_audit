FROM python:3.13-slim
# TeX and Chromium draw the report here, in the box, wherever the audit runs: the
# Inspect host (Hawk's runner, say) need not have either
RUN apt-get update && apt-get install -y --no-install-recommends librsvg2-bin ca-certificates curl git jq ripgrep poppler-utils \
        latexmk texlive-latex-extra texlive-fonts-recommended texlive-fonts-extra texlive-science \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir inspect-ai==INSPECT_VERSION pandas pyarrow matplotlib jupyter pyyaml scipy sympy pytest playwright==1.64.1 \
    && python -m playwright install --with-deps chromium
WORKDIR /workspace
CMD ["sleep", "infinity"]
