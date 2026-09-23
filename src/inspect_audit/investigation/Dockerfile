FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends librsvg2-bin ca-certificates curl git jq ripgrep poppler-utils latexmk texlive-latex-extra texlive-fonts-recommended texlive-fonts-extra \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir inspect-ai==INSPECT_VERSION pandas pyarrow matplotlib jupyter pyyaml scipy sympy pytest
RUN apt-get update && apt-get install -y --no-install-recommends texlive-science \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace
CMD ["sleep", "infinity"]
