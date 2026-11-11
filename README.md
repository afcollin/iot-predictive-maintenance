# Asset Condition Monitoring – Predictive Maintenance MVP

Teknisk MVP for **Intelligent IoT Solutions A/S** (KEA 6. semester, fag 6.2, afleveringsopgave #2).
Et REST API modtager sensormålinger fra IoT-enheder på vindmøller, vurderer dem mod forretningsregler
og opretter alarmer til kontrolcentret, når en komponent er på vej mod nedbrud.

## Domænekobling

Løsningen realiserer bounded context'en **Asset Condition Monitoring**, som i delopgave 1 blev
klassificeret som virksomhedens eneste **core subdomain**. Den understøtter value streamen
*Prevent B2B Asset Failure* og løser hotspottet fra Event Storming: afvigelser opdages i dag
manuelt i Power BI.

| Event Storming-begreb | I koden |
|---|---|
| Domain event: *Sensor Value Received* | `EventType.SENSOR_VALUE_RECEIVED` |
| Beslutning: *Threshold-check* | `ThresholdRule.classify()` |
| Domain event: *Anomaly Detected* | `EventType.ANOMALY_DETECTED` |
| Handling: *alarm / notifikation* | `Alarm`-aggregatet og `Notifier` |
| Asset (kundens anlæg) ≠ IoT Device (sensoren) | `asset_id` og `device_id` |

## Arkitektur

```mermaid
flowchart LR
    D["IoT-enhed<br/>(simulator / Postman)"] -- "POST /sensor-readings<br/>X-API-Key" --> API
    subgraph C["Docker / Azure"]
        API["REST API<br/>FastAPI"] --> S["Application service<br/>event → beslutning → handling"]
        S --> DM["Domænemodel<br/>tærskelregler, events, Alarm"]
        S --> R["Repository"]
        R --> DB[("PostgreSQL<br/>domain_events + alarms")]
        S --> N["Notifier<br/>log / webhook"]
    end
    N --> K["Kontrolcenter<br/>Teams / Slack / Discord"]
```

Koden følger **ports & adapters**: domænemodellen (`app/domain.py`) er ren Python uden afhængighed
af web-framework eller database. API, database og notifikation er adaptere, der kan udskiftes.

```mermaid
sequenceDiagram
    participant D as IoT-enhed
    participant A as REST API
    participant S as MonitoringService
    participant DB as PostgreSQL
    participant N as Kontrolcenter
    D->>A: POST /api/v1/sensor-readings {value: 12.4}
    A->>S: handle_sensor_reading()
    S->>S: validering (kendt målepunkt, fysisk mulig værdi)
    S->>DB: gem SensorValueReceived
    S->>S: threshold-check → CRITICAL
    S->>DB: gem AnomalyDetected (causation_id → målingen)
    S->>DB: opret / opdatér alarm
    S->>N: notifikation (kun ny eller skærpet alarm)
    A-->>D: 201 {anomaly_detected: true, alarm_action: RAISED}
```

### Forretningsregler

| Målepunkt | Enhed | WARNING | CRITICAL | Retning |
|---|---|---|---|---|
| `gearbox_vibration` | mm/s | ≥ 7,1 | ≥ 11,0 | over |
| `bearing_temperature` | °C | ≥ 80 | ≥ 95 | over |
| `oil_pressure` | bar | ≤ 1,5 | ≤ 1,0 | under |

* **Fysisk umulige værdier** (fx 500 mm/s) afvises med `422` som mulig sensorfejl og gemmes ikke.
* **Højst én åben alarm pr. anlæg og målepunkt.** Gentagne anomalier tælles op på den åbne alarm
  i stedet for at oversvømme kontrolcentret. Kun nye og **skærpede** alarmer (WARNING → CRITICAL) notificeres.
* En operatør **kvitterer** for en alarm. Herefter giver en ny anomali en ny alarm.

## Kom i gang

Kræver [Docker Desktop](https://www.docker.com/products/docker-desktop/).

```bash
git clone <repo-url>
cd iot-predictive-maintenance
docker compose up -d --build
```

| Hvad | Adresse |
|---|---|
| Swagger (interaktiv API-dokumentation) | http://localhost:8000/docs |
| Health check | http://localhost:8000/health |
| Adminer (se databasen) | http://localhost:8080 – System: *PostgreSQL*, Server: `db`, Bruger: `iot`, Adgangskode: `iot-local-password`, Database: `iot_monitoring` |

API-nøglen er `local-dev-key`, medmindre andet er sat i `.env` (se `.env.example`).

### Send målinger fra en anden computer (IoT-enhed)

Simulatoren kræver kun Python, ingen pakker:

```bash
python device_simulator/simulate.py --url http://<ip-på-serveren>:8000 --scenario degrading
```

Scenariet `degrading` simulerer en gearkasse, der slides gradvist: normal → WARNING → CRITICAL.
Find serverens IP med `ipconfig`, og tillad port 8000 i Windows Firewall på serveren.

### Postman

Importér `postman/IoT-Predictive-Maintenance.postman_collection.json` og kør mapperne i rækkefølge.
Ret variablen `baseUrl`, hvis API'et kører på en anden maskine.

### Kør uden Docker (udvikling)

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (Linux/macOS: source .venv/bin/activate)
pip install -r requirements-dev.txt
set API_KEY=local-dev-key        # Linux/macOS: export API_KEY=local-dev-key
uvicorn app.main:create_app --factory --reload
```

Uden `DATABASE_URL` bruges en lokal SQLite-fil.

## API

| Metode | Endpoint | Formål |
|---|---|---|
| `POST` | `/api/v1/sensor-readings` | Modtag måling → events → evt. alarm |
| `GET` | `/api/v1/alarms` | List alarmer (filter: `status`, `asset_id`) |
| `GET` | `/api/v1/alarms/{id}` | Hent én alarm |
| `POST` | `/api/v1/alarms/{id}/acknowledge` | Kvittér for alarm |
| `GET` | `/api/v1/events` | List domain events (filter: `event_type`, `asset_id`) |
| `GET` | `/api/v1/thresholds` | List tærskelregler |
| `GET` | `/health` | Health check (uden nøgle) |

Alle `/api/v1`-endpoints kræver headeren `X-API-Key`.

## Test

```bash
pytest --cov=app
```

* `tests/test_domain.py` – unit tests af forretningsreglerne (uden database)
* `tests/test_api.py` – integrationstests af hele flowet gennem API'et til databasen
* `tests/test_notifier.py` – notifikation, herunder at en fejlende webhook ikke vælter modtagelsen
* `tests/test_security.py` – rate limiting, audit-log, sikker fejlhåndtering, sikkerhedsheadere og dataminimering
* `tests/test_concurrency.py` – to samtidige anomalier giver ikke to åbne alarmer (simulerer to instanser)

I alt 45 tests (97 % dækning). I pipelinen kommer smoke test, DAST og health check efter deploy oveni.

## CI/CD (GitHub Actions)

`.github/workflows/ci-cd.yml` kører ved hvert push og pull request:

```mermaid
flowchart LR
    P[git push / PR] --> T["Lint og test<br/>ruff + pytest + Bicep-validering"]
    P --> SEC["Sikkerhedsscanning<br/>gitleaks (secrets) · bandit (SAST) · pip-audit (SCA)"]
    T --> B["Docker build<br/>smoke test + OWASP ZAP (DAST)"]
    SEC --> B
    B -- "kun main" --> G["Publicér image<br/>ghcr.io"]
    G --> D["Deploy til Azure<br/>OIDC → ACR → Container Apps<br/>+ health check"]
```

Pull requests kører alt undtagen publicering og deploy. Kun `main` deployes, automatisk (continuous deployment).
Det image, der deployes, er præcis det, der blev testet (build once, deploy many).
DAST-rapporten fra OWASP ZAP gemmes som artefakt (`zap-dast-rapport`) på hver kørsel.
Dependabot (`.github/dependabot.yml`) foreslår ugentligt opdateringer af afhængigheder, actions og base-image.

### Branching, miljøer og rollback

| | |
|---|---|
| Branching | GitHub Flow: feature-branch → pull request med grønne checks → squash-merge til `main` |
| Miljøer | **dev**: lokal Docker Compose · **test**: flygtig stak i CI (smoke test + DAST) · **prod**: Azure Container Apps |
| Rollback | Hver deploy er en ny revision med image `sha-<commit>`. Tilbage til forrige version: `az containerapp update -n ca-iotpm-api -g rg-iot-predictive-maintenance --image <acr>/iot-predictive-maintenance:sha-<forrige>` eller en `git revert` på `main` |

## Monitorering og SLO

**SLO:** 99,5 % af kald til `/api/v1` uden serverfejl (5xx) pr. måned. Fejlbudget: 0,5 %.

| Alarmregel (Azure Monitor, `infra/main.bicep`) | Udløses når |
|---|---|
| `alert-iotpm-serverfejl` | ét eller flere HTTP 5xx-svar inden for 5 min |
| `alert-iotpm-genstarter` | mere end 2 genstarter af containeren inden for 15 min |

SLI'en måles i Log Analytics (appens JSON-logs ligger i `ContainerAppConsoleLogs_CL`):

```kusto
ContainerAppConsoleLogs_CL
| where ContainerAppName_s == "ca-iotpm-api"
| extend e = parse_json(Log_s)
| where e.msg == "HTTP-request" and tostring(e.path) startswith "/api/v1"
| summarize kald = count(), serverfejl = countif(toint(e.status_code) >= 500)
| extend fejlfri_procent = round(100.0 * (kald - serverfejl) / kald, 2)
```

Health probes (`/health` med svar 200) logges ikke, så loggen kun viser rigtige kald.

## Drift i Azure

Infrastrukturen er beskrevet som kode i `infra/main.bicep` og udrulles med `infra/deploy.ps1`.

```mermaid
flowchart LR
    GH["GitHub Actions"] -- "OIDC (ingen adgangskoder)" --> MI1["Managed identity<br/>id-iotpm-github"]
    MI1 -- AcrPush --> ACR["Container Registry"]
    MI1 -- "Contributor (kun app'en)" --> CA
    DEV["IoT-enhed / Postman"] -- HTTPS --> CA["Container App<br/>FastAPI · 0-2 instanser"]
    CA -- "id-iotpm-app: AcrPull" --> ACR
    CA -- "id-iotpm-app: Secrets User" --> KV["Key Vault<br/>api-key · database-url"]
    CA -- TLS --> PG[("PostgreSQL<br/>Flexible Server")]
    CA -- stdout --> LA["Log Analytics"]
```

| Ressource | Formål |
|---|---|
| Container Apps | Kører API'et serverless med HTTPS, health probes og autoskalering |
| PostgreSQL Flexible Server (B1ms) | Separat database for domain events og alarmer, kun TLS |
| Key Vault | API-nøgle og database-forbindelse. Appen henter dem via sin managed identity |
| Container Registry | Private images. Kun adgang via Entra ID-identiteter (admin-bruger slået fra) |
| Log Analytics | Samler appens JSON-logs til søgning og overvågning |
| Managed identities | `id-iotpm-app` (læse image og secrets) og `id-iotpm-github` (deploy). Least privilege |

```powershell
az login
./infra/deploy.ps1 -Location francecentral     # første gang ca. 10-15 min
az keyvault secret show --vault-name <kv-navn> --name api-key --query value -o tsv   # hent API-nøglen
```

Abonnementet (Azure for Students) tillader kun swedencentral, polandcentral, francecentral, germanywestcentral og austriaeast,
og PostgreSQL B1ms afvises i swedencentral og polandcentral. Derfor kører løsningen i **francecentral**.
Skoletenanten kræver MFA for at oprette ressourcer via CLI (`az login --claims-challenge ...`, som CLI'en selv foreslår).
Slet alt igen med `az group delete -n rg-iot-predictive-maintenance`.

## Sikkerhed og privacy by design

Defense in depth, lag for lag:

| Lag | Kontrol |
|---|---|
| Identitet og adgang | API-nøgle (`X-API-Key`) på alle forretnings-endpoints, sammenlignet i konstant tid. Nøglen ligger i en miljøvariabel, aldrig i koden |
| Netværk | Databasen er ikke udstillet uden for Docker-netværket. Containeren kører som ikke-root-bruger |
| Applikation og API | Inputvalidering af id'er, målepunkter og værdier (afviser `NaN`, `Infinity`, `<script>` og injection-forsøg). Alle SQL-forespørgsler er parametriserede via SQLAlchemy. Rate limiting (standard 120 kald/min pr. klient → `429` med `Retry-After`), også på forkerte nøgler mod brute force |
| Data | Telemetri fra vindmøller er ikke personoplysninger. Personoplysninger: medarbejder-id ved kvittering (`OP-117`, ikke navn) og klient-IP i audit-loggen (formål: sikkerhed). Opbevaring: logs 30 dage (Log Analytics), rå målinger 90 dage, anomalier og alarmer 2 år (GDPR art. 5) |
| Logning og audit | Hvert kald logges som JSON med tid, klient-IP, endpoint, statuskode og varighed. Afviste nøgler og rate limit-brud logges som `WARNING`. API-nøgler logges aldrig |
| Fejl sikkert | Global exception handler: klienten får en generisk besked og et `request_id`. Detaljerne står kun i loggen |
| Pipeline (shift-left) | Secret scanning (gitleaks), SAST (bandit), SCA (pip-audit), DAST (OWASP ZAP) og Dependabot. Secrets aldrig i repo (`.env` er i `.gitignore`) |
| Sikkerhedsheadere | `X-Content-Type-Options`, `Cross-Origin-Resource-Policy`, `X-Frame-Options` og `Cache-Control: no-store` – tilføjet efter fund i DAST |
| Samtidighed | Et partielt unikt indeks håndhæver "højst én åben alarm pr. anlæg og målepunkt", også med flere instanser |

## Projektstruktur

```
app/
  domain.py         domænemodel: målinger, tærskelregler, events, Alarm-aggregat
  service.py        application service: event → beslutning → handling
  persistence.py    PostgreSQL/SQLite via SQLAlchemy: event store + alarmer
  api.py            REST-endpoints
  schemas.py        API-kontrakt (bliver til Swagger)
  notifier.py       notifikation via log og webhook
  security.py       API-nøgle
  logging_setup.py  struktureret JSON-logging
  main.py           opstart, health check, fejlhåndtering
device_simulator/   IoT-enhed (kun standardbibliotek)
postman/            Postman-collection til demo
tests/
```
