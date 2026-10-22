# Oglasnik

Prati prodaju stanova na Index Oglasima za Zagrebačku županiju i Grad Zagreb i šalje jedan mail samo s oglasima koji još nisu poslani.

Pretraga je ista kao na stranici: kategorija prodaja stanova, te dvije županije, sort najnoviji. Program čita JSON koji ta stranica već koristi. Pojedinačni oglas se ne otvara. U mailu su naslov, cijena, cijena po m², lokacija, je li prodavač privatni ili agencija, i link.

Obnova starog oglasa ne šalje novi mail. Kod oglasa ostaje isti. Datumi objave i obnove služe da se zna dokle čitati stranice.

## Lokalno pokretanje

Treba Python 3.11 ili noviji. Vremena u mailu su u zoni Europe/Zagreb. Na Windowsu tu zonu daje paket `tzdata` iz `requirements.txt`.

```powershell
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

U `.env` upiši SMTP podatke. Za Gmail uključi potvrdu u dva koraka i napravi app password. U polja idu:

- `SMTP_HOST=smtp.gmail.com`
- `SMTP_PORT=587`
- `SMTP_USER` i `MAIL_FROM`: tvoja Gmail adresa
- `SMTP_PASSWORD`: app password
- `MAIL_TO`: adresa na koju stižu oglasi

`CHECK_INTERVAL_MINUTES` je cijeli broj, najmanje 1. Zadano je 5. `DATABASE_PATH` i ostale relativne putanje računaju se od mape projekta.

Pokretanje:

```powershell
python main.py
```

Prvi prolaz upiše sve trenutne oglase u `data/seen.sqlite` i odmah pošalje jedan testni mail. Broj oglasa u tom mailu je `TEST_LISTING_COUNT` u `.env`. Nula znači da se test ne šalje. Ti oglasi ostanu zapisani i ne šalju se ponovo. Prvi prolaz traje nekoliko minuta. Tek sljedeći ciklusi šalju oglase kojih u tom zapisu nema. Ako nema novih, mail ne stiže. Prozor mora ostati otvoren. Prekid je Ctrl+C.

Dok program radi na ovom računalu, Windows ne smije ući u sleep. Inače se ritam prekida.

Da se digne sam nakon prijave, u Task Scheduleru napravi zadatak:

- okidač: at log on
- program: puna putanja do `.venv\Scripts\python.exe`
- argument: `main.py`
- Start in: mapa ovog projekta

Ritam i dalje drži sam proces, ne zadatak koji se ponavlja svake minute.

`.env` i `data/` ne idu u git.

## Slanje Docker slike

Prijatelj treba dvije datoteke: sliku `oglasnik.tar` i `docker-compose.yml`. U sliku ne ulaze `.env` ni baza. On napravi svoj `.env` po `.env.example` i stavi ga u istu mapu kao compose datoteku. `DATABASE_PATH` u composeu je `/data/seen.sqlite`, pa se oglasi pamte i nakon ponovnog pokretanja.

Na ovom računalu, u mapi projekta:

```powershell
docker build -t oglasnik:latest .
docker save oglasnik:latest -o oglasnik.tar
```

Pošalji `oglasnik.tar` i `docker-compose.yml`. `.env` pošalji zasebno samo ako mail treba ići s tvog računa. Ako želiš da nastavi tamo gdje si stao, ugasi lokalni program i pošalji i `data\seen.sqlite`. Inače prvi start kod njega upiše postojeće oglase i pošalje testni mail s onoliko oglasa koliko piše u `TEST_LISTING_COUNT`.

Kod prijatelja:

```bash
docker load -i oglasnik.tar
docker compose up -d
docker compose logs -f
```

Ako šalješ svoju bazu, prije `up` je stavi umjesto praznog volumena. U `docker-compose.yml` zamijeni volume s:

```yaml
    volumes:
      - ./seen.sqlite:/data/seen.sqlite
```

Lokalni program tada mora biti ugašen.

## Kasnije na Linux serveru

Isti `main.py` radi na malom VPS-u koji ne spava. Dovoljan je 1 vCPU i 1 GB RAM-a, npr. Ubuntu. Program ne sluša nijedan ulazni port. Van treba HTTPS prema `index.hr` i SMTP, obično port 587.

Lokalni i serverski proces ne smiju raditi istovremeno. Baza nije dijeljena, pa bi oba poslala isti novi oglas.

### 1. Ugasi program na ovom računalu

U prozoru gdje radi `python main.py` pritisni Ctrl+C i pričekaj da ispiše da je zaustavljen. Tek tada je `data/seen.sqlite` cijela.

### 2. Pripremi server

Prijavi se s SSH ključem. Naredbe ispod su na serveru, kao korisnik koji smije `sudo`:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
sudo timedatectl set-timezone Europe/Zagreb
sudo useradd --system --create-home --shell /usr/sbin/nologin oglasnik
sudo mkdir -p /opt/oglasnik /var/lib/oglasnik
sudo chown oglasnik:oglasnik /opt/oglasnik /var/lib/oglasnik
```

### 3. Prebaci kod, `.env` i bazu

S ovog računala, u PowerShellu. `SERVER` zamijeni adresom servera.

```powershell
scp main.py scraper.py store.py mailer.py requirements.txt SERVER:/tmp/
scp .env SERVER:/tmp/oglasnik.env
scp data\seen.sqlite SERVER:/tmp/seen.sqlite
```

Na serveru:

```bash
sudo mv /tmp/main.py /tmp/scraper.py /tmp/store.py /tmp/mailer.py /tmp/requirements.txt /opt/oglasnik/
sudo mv /tmp/oglasnik.env /opt/oglasnik/.env
sudo mv /tmp/seen.sqlite /var/lib/oglasnik/seen.sqlite
sudo chown -R oglasnik:oglasnik /opt/oglasnik /var/lib/oglasnik
sudo chmod 600 /opt/oglasnik/.env
```

U `/opt/oglasnik/.env` stavi apsolutnu bazu:

```env
DATABASE_PATH=/var/lib/oglasnik/seen.sqlite
```

SMTP podaci ostaju isti kao lokalno. `.env` ne ide u git.

### 4. Instaliraj pakete

```bash
sudo -u oglasnik python3 -m venv /opt/oglasnik/.venv
sudo -u oglasnik /opt/oglasnik/.venv/bin/pip install -r /opt/oglasnik/requirements.txt
```

### 5. Pokreni ga kao servis

Spremi ovo u `/etc/systemd/system/oglasnik.service`:

```ini
[Unit]
Description=Index oglasi
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=oglasnik
WorkingDirectory=/opt/oglasnik
ExecStart=/opt/oglasnik/.venv/bin/python main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now oglasnik
sudo journalctl -u oglasnik -f
```

U logu ne smije pisati da kreće prvi prolaz. Ako piše, baza nije na `DATABASE_PATH` i program će upisati postojeće oglase, pa poslati jedan testni oglas. Ako vidiš da je interval pokrenut i da nema novih oglasa, selidba je uspjela. Prekid čitanja loga je Ctrl+C. Servis time ne staje.

Cron se ne koristi. Cron svake minute može pokrenuti drugi prolaz dok prvi još čita stranice.

### Ako nešto zapne

Neki SMTP poslužitelji odbiju slanje s adrese datacentra iako isti Gmail app password radi od kuće. To se vidi u `journalctl` kao greška prijave ili slanja.

Ako Index vraća 429 ili 403, u `.env` povećaj `CHECK_INTERVAL_MINUTES` i `PAGE_DELAY_SECONDS`, pa `sudo systemctl restart oglasnik`.

`/var/lib/oglasnik/seen.sqlite` je cijelo stanje. Ako se izgubi, program više ne zna što je već poslano. Povremeno je kopiraj, dok je servis zaustavljen (`sudo systemctl stop oglasnik`).
