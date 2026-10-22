FROM python:3.12-slim

WORKDIR /opt/oglasnik

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py scraper.py store.py mailer.py ./

RUN useradd --system --create-home --uid 10001 oglasnik \
    && mkdir -p /data \
    && chown oglasnik:oglasnik /data

USER oglasnik
ENV TZ=Europe/Zagreb
ENV DATABASE_PATH=/data/seen.sqlite
VOLUME /data

CMD ["python", "-u", "main.py"]
