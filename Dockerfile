# SafeOrbit 安轨 - Agent 运行期审计与治理层
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    SAFEORBIT_DB=/data/safeorbit.db \
    SAFEORBIT_REPORT_DIR=/reports/live \
    SAFEORBIT_CODE_REPORT_DIR=/reports/codeaudit \
    SAFEORBIT_SCA_REPORT_DIR=/reports/sca \
    SAFEORBIT_INFRA_REPORT_DIR=/reports/infra \
    SAFEORBIT_EVIDENCE_DIR=/reports/evidence \
    SAFEORBIT_SUMMARY_REPORT_DIR=/reports/summaries \
    SAFEORBIT_LOG_FORMAT=json

WORKDIR /app

# 先装依赖（独立于源码），再装本体：改代码不会使依赖层缓存失效
COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir -r requirements.txt -c constraints.txt

COPY pyproject.toml ./
COPY safeorbit ./safeorbit
RUN pip install --no-cache-dir --no-deps .

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh \
    && useradd --create-home --uid 1000 safeorbit \
    && mkdir -p /data /reports \
    && chown -R safeorbit:safeorbit /data /reports /app

USER safeorbit

VOLUME ["/data", "/reports"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=3).status == 200 else 1)"

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
