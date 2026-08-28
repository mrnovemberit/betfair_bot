# Deploy sul server domestico

Progetto indipendente sul mini-PC Linux di casa (Ubuntu 24.04, `192.168.1.200`),
accanto a videosorveglianza (Frigate), n8n e Trading_Server (Interactive Brokers) —
rete Docker propria (`betfair`), nessuna comunicazione con gli altri stack.

**Chi esegue questi comandi**: l'unico SSH già configurato verso il server da questa
macchina (`~/.ssh/config`, host `trading-server`, utente `claude-trading`) è confinato
a `/opt/trading` e senza sudo/docker — non può eseguire questi passi. Vanno lanciati
dall'utente reale con permessi docker sul server (`mike_november`).

## Prerequisiti

```bash
docker --version && docker compose version && free -h
```

## 1. Namespace dedicato

```bash
sudo install -d -o "$USER" -g "$USER" /opt/betfair
git clone https://github.com/mrnovemberit/betfair_bot.git /opt/betfair
cd /opt/betfair
```

## 2. Segreti (non nel repo, copiati a mano dopo il clone)

```bash
cp .env.example .env && chmod 600 .env
# Compilare .env con le credenziali Betfair reali. BETFAIR_CERT_PATH/BETFAIR_KEY_PATH
# vanno lasciati a /app/certs/client-2048.crt e /app/certs/client-2048.key (path
# dentro il container — non dipendono dall'host).

mkdir -p certs
# Dalla macchina locale (non sul server):
#   scp certs/client-2048.crt certs/client-2048.key mike_november@192.168.1.200:/opt/betfair/certs/
chmod 600 certs/client-2048.*

cd deploy
cp .env.example .env && chmod 600 .env
# Compilare: PUID/PGID (id -u / id -g), TZ. Lasciare LIVE=false e CONFIRM_LIVE vuoto.
cd ..
```

## 3. Build e avvio (sempre in paper la prima volta)

```bash
docker compose -f deploy/docker-compose.yml build bot
docker compose -f deploy/docker-compose.yml up -d bot
docker compose -f deploy/docker-compose.yml logs -f bot
```

Atteso nei log: `Login Betfair OK (cert-based, app_key=DELAYED)` poi
`[ltd] aggiunta | mode=paper | N mercato/i`.

## 4. Verifica risorse

Vincolo: ~1GiB RAM libero condiviso con Frigate/n8n/ib-gateway.

```bash
docker stats betfair-bot --no-stream
free -h
```

## 5. Riavvio giornaliero (copre il limite "un solo screening per avvio")

Lo screener gira una sola volta all'avvio del container — nessun re-screening
periodico (Flumine non lo supporta nativamente). Un riavvio giornaliero in un
orario sicuro (nessuna partita può essere in corso) rilancia lo screener per la
giornata successiva, con `hours_ahead=20` in `strategies.toml` a coprire l'intera
finestra fino al riavvio successivo.

```bash
sudo cp docs/systemd/betfair-bot-restart.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now betfair-bot-restart.timer
systemctl list-timers 'betfair-*'
```

**Guarda la colonna NEXT**: deve dire le 08:00 Europe/Rome. Se dice un'ora diversa
il fuso non è quello che credi.

## 6. Periodo canary

Far girare il bot sul server in paper per le stesse ≥4 settimane già richieste dal
progetto (CLAUDE.md) — non è un periodo aggiuntivo, coincide con l'obbligo esistente.
Se gira in parallelo al bot locale (Windows), confrontare `logs/trades_ltd.csv` fra
le due esecuzioni: i verdetti devono coincidere.

## 7. Passaggio a live — solo dopo canary + decisione esplicita

```bash
# strategies.toml: [ltd] mode = "live"
# deploy/.env:     LIVE=true, CONFIRM_LIVE=yes
docker compose -f deploy/docker-compose.yml up -d --force-recreate bot
docker compose -f deploy/docker-compose.yml logs -f bot   # monitorare con attenzione
```

## 8. Aggiornare il codice

```bash
cd /opt/betfair && git pull
```

Bind mount: nessun rebuild necessario, tranne se cambia `deploy/requirements-bot.txt`
(in quel caso: `docker compose -f deploy/docker-compose.yml build bot && docker compose -f deploy/docker-compose.yml up -d bot`).

## Guasti noti

- **"Certificato/chiave non trovati"**: `BETFAIR_CERT_PATH`/`BETFAIR_KEY_PATH` nel
  `.env` non è `/app/certs/...` (path Windows locale copiato per errore).
- **CertsError silenzioso nei log di Flumine** (non un errore esplicito di
  `main.py`): la validazione fail-fast in `build_client()` dovrebbe intercettarlo
  prima — se compare comunque, i certificati non sono leggibili dall'utente
  `PUID:PGID` del container (permessi file sbagliati sul server).
- **`docker compose stop` manda SIGTERM, non SIGINT**: il blocco
  `except KeyboardInterrupt` di `main.py` (logout pulito) potrebbe non scattare.
  Non bloccante — la sessione scade da sola dopo 20 minuti (locale `italy`) —
  ma è un miglioramento futuro possibile (`signal.signal(SIGTERM, ...)`).

## Delegare a Claude Code in futuro

Se in seguito si vuole delegare l'operatività quotidiana come già avviene per
`Trading_Server`, va creato un utente di servizio dedicato (es. `claude-betfair`),
stesso pattern restrittivo di `claude-trading` ma confinato a `/opt/betfair`, senza
sudo/docker diretto. Non necessario per questo primo deploy.
