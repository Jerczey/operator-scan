# It's safe to use the latest and greatest here
# hadolint ignore=DL3007
FROM registry.access.redhat.com/ubi9/python-312:latest

LABEL name="operator-scan" \
      summary="OpenShift Operator Lifecycle Scanner" \
      description="Web application that scans installed OLM operators and reports Red Hat support lifecycle phases." \
      io.k8s.display-name="Operator Lifecycle Scanner" \
      io.openshift.tags="openshift,operators,lifecycle,scanner"

USER 0

RUN mkdir -p /app/data/reports && \
    chown -R 1001:0 /app && \
    chmod -R g=u /app

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    find . -regex '^.*\(__pycache__\|\.py[co]\)$' -delete

COPY gunicorn.conf.py .
COPY run.py .
COPY app/ app/

USER 1001

EXPOSE 8080

CMD ["gunicorn", "-c", "gunicorn.conf.py", "run:app"]
