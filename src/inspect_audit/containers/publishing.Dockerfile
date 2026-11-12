FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    latexmk texlive-latex-extra texlive-fonts-recommended texlive-fonts-extra texlive-science \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /report
ENTRYPOINT ["latexmk"]
