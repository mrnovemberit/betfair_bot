# Immagine del bot Betfair. Contiene SOLO le dipendenze: il codice arriva da un
# bind mount del repo clonato sul server, cosi' un `git pull` aggiorna il bot senza
# ricostruire nulla e senza che l'immagine e il repo possano divergere in silenzio.
#
# Niente numpy/pandas qui (a differenza di altri bot su questo stesso server): verificato
# che bot/*.py non le importa e che ne' flumine ne' betfairlightweight le richiedono
# (pandas serve solo a backtest/*.py, mai eseguito in produzione) — quindi niente
# compilazione da sorgente per l'assenza di AVX/SSE4.1/4.2 sulla CPU del server.
FROM python:3.14-slim

# tzdata non e' opzionale: locale="italy" e i timestamp Betfair richiedono un database
# fusi orari funzionante.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/*

# PYTHONUNBUFFERED: senza, l'output resta nel buffer e i log docker/journalctl lo
# vedono solo a intervalli imprevedibili.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY deploy/requirements-bot.txt /tmp/requirements-bot.txt
RUN pip install --no-cache-dir -r /tmp/requirements-bot.txt

WORKDIR /app
CMD ["python", "bot/main.py"]
