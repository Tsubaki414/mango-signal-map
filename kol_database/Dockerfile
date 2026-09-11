FROM python:3.11-slim AS seedcheck

WORKDIR /seedcheck
COPY scripts/check_runtime_db.py scripts/check_runtime_db.py
COPY deploy_seed.db deploy_seed.db
RUN python scripts/check_runtime_db.py --db deploy_seed.db --seed-contract

FROM python:3.11-slim AS runtime

WORKDIR /app

COPY requirements.txt .
# Railway/build networks occasionally pause on larger wheels; make the
# install tolerant of transient read stalls without changing dependency
# resolution or baking a package cache into the image.
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN pip install --no-cache-dir --timeout 600 --retries 5 --index-url "$PIP_INDEX_URL" -r requirements.txt

COPY . .
# Copying from the validated stage makes the normal runtime image depend on
# the full seed contract.  BuildKit therefore cannot skip the seed audit.
# entrypoint.sh only copies this onto the mounted volume the first time the
# volume is empty, so every write after that (Solomon reviews, evidence
# confirm/reject, etc.) persists across redeploys instead of resetting to
# this baked-in snapshot.
COPY --from=seedcheck /seedcheck/deploy_seed.db /app/data/kol.seed.db

ENV DB_PATH=/data/kol.db
ENV PYTHONUNBUFFERED=1

RUN chmod +x entrypoint.sh
EXPOSE 8000
# Railway mounts the already-provisioned persistent volume at /data. Do not
# declare Docker's VOLUME instruction here: Railway rejects that instruction,
# and local runs can mount an explicit named volume when persistence is wanted.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import json,os,urllib.request; r=urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/healthz', timeout=3); assert r.status == 200; assert json.load(r)=={'status':'ok'}" || exit 1
CMD ["./entrypoint.sh"]
