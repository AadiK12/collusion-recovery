FROM python:3.11-slim@sha256:b7a9e5f6a93d6d4a31f129bbd4bdee69efccb6ded0fb230d78aafaf37af98377
RUN python -m pip install --no-cache-dir pytest==9.1.1
COPY runner.py /opt/induction/runner.py
RUN chmod 0555 /opt/induction/runner.py
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0 HOME=/work LANG=C.UTF-8
USER 65534:65534
WORKDIR /work
ENTRYPOINT ["/usr/local/bin/python", "-I", "/opt/induction/runner.py"]
