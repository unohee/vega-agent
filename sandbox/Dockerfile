FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl git bash trash-cli procps \
    && rm -rf /var/lib/apt/lists/*

# 핵심 패키지만 설치 (mlx_env 주요 라이브러리 미러)
RUN pip install --no-cache-dir \
    numpy pandas matplotlib plotly \
    scipy scikit-learn \
    requests httpx \
    beautifulsoup4 \
    openpyxl xlsxwriter xlrd \
    python-docx python-pptx \
    yfinance \
    duckdb \
    rich

# 실행 환경
ENV PYTHONUNBUFFERED=1
ENV HOME=/sandbox_home
# /workspace/lib  — 에이전트가 만든 Python 모듈
# /workspace/site-packages — 에이전트가 pip install한 패키지
ENV PYTHONPATH=/workspace/lib:/workspace/site-packages
# pip install --target 기본 경로 (pip install <pkg> 만 써도 영속 경로로 설치됨)
ENV PIP_TARGET=/workspace/site-packages
WORKDIR /workspace

RUN mkdir -p /sandbox_home /workspace/lib /workspace/site-packages /workspace/history /output

CMD ["bash"]
