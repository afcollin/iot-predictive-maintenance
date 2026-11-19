# Incident management – Asset Condition Monitoring

Runbook for drift af API'et 24/7. Processen bygger på Atlassians Incident Management Handbook
(ITIL 4 og SRE/DevOps) og følger princippet *you build it, you run it*: teamet, der har bygget servicen, har også vagten.

Der er to niveauer, og de må ikke blandes sammen:

| Niveau | Hvad er en "incident"? | Hvem reagerer? |
|---|---|---|
| **Domænet** (det, produktet gør) | En anomali på kundens anlæg → `AnomalyDetected` → alarm | Operatøren i kontrolcentret kvitterer (`AlarmAcknowledged`) |
| **Servicen** (det, denne runbook handler om) | API'et, databasen eller platformen svigter, så alarmer ikke når frem | Vagthavende udvikler/driftstekniker |

En incident i servicen er alvorlig, fordi kundernes anlæg så er uovervågede, mens alt *ser* roligt ud.

## 1. Detektering

Målet er at opdage fejl, før kunderne gør (lav MTTD).

| Kilde | Hvad den fanger | Hvor |
|---|---|---|
| Azure Monitor `alert-iotpm-serverfejl` | 5xx-svar i en periode på 5 min | Produktion, e-mail via action group `ag-iotpm-drift` |
| Azure Monitor `alert-iotpm-genstarter` | Mere end 2 genstarter på 15 min (crash loop) | Produktion |
| Health probes på `/health` | API eller database svarer ikke → Container Apps genstarter instansen | Produktion |
| Prometheus-regler (`observability/prometheus/alerts.yml`) | API/DB nede, 5xx, kritisk alarm ikke kvitteret i 15 min, mange afviste målinger, mange 401/429 | Dev (`localhost:9090/alerts`) |
| Grafana-dashboard | Trends: anomalier, svartider, fejlrate, MTTA | Dev (`localhost:3000`) |
| Audit-log (KQL i Log Analytics) | Hvem kaldte hvad, hvornår, med hvilket resultat. Find `request_id` fra fejlsvar | Produktion |

## 2. Alvorsgrad (assessment)

| Grad | Kriterium | Eksempler | Reaktion |
|---|---|---|---|
| **SEV1** | Ingen eller næsten ingen overvågning af kundernes anlæg | API eller database nede, ingen målinger modtages | Kvittering inden for 15 min, døgnet rundt. Alle roller udpeges |
| **SEV2** | Forringet service eller sikkerhedsrisiko | 5xx-fejl, forsinkede alarmer, kritisk alarm ikke kvitteret, mistænkelig adgang (mange 401/429) | Kvittering inden for 1 time, døgnet rundt |
| **SEV3** | Begrænset påvirkning | Én defekt sensor (mange afviste målinger), langsomme svar uden fejl | Næste arbejdsdag |

Er man i tvivl, vælges den højeste grad. Graden kan sænkes senere.

## 3. Forløb (incident response)

1. **Detektér** og opret et issue med skabelonen *Incident* (GitHub → Issues → New issue).
2. **Åbn kommunikationskanal**: én Teams-kanal pr. incident, så al koordinering sker ét sted.
3. **Vurdér** påvirkning og alvorsgrad (tabellen ovenfor).
4. **Kommunikér** til interessenter (afsnit 5).
5. **Eskalér** til de rette personer: vagthavende → tech lead → platform/Azure-support eller leverandør.
6. **Uddelegér** rollerne (afsnit 4).
7. **Løs**: genopret servicen først (fx rollback til forrige image-tag, se README), find rodårsagen bagefter.

## 4. Roller

| Rolle | Ansvar | I casens organisation |
|---|---|---|
| **Incident manager** | Leder forløbet, træffer beslutninger, eskalerer | Vagthavende i IT/OT Operations |
| **Tech lead** | Finder årsagen og retter fejlen | Udvikleren på vagt fra teamet bag servicen |
| **Kommunikationsansvarlig** | Informerer kunder, kontrolcenter og ledelse | Managed Services / 24/7 kontrolcentret |

Teamet har en vagtplan (on-call rotation), så vagten deles og ikke afhænger af én person.

## 5. Kommunikation

- **Internt**: Teams-kanalen og incident-issuet er den fælles sandhed. Opdatér issuet med tidslinjen undervejs.
- **Eksternt**: Managed Services-kunder informeres efter SLA, fx via en statusside og e-mail.
- **Tjekliste for hver besked**: Hvad er påvirkningen? Hvor mange kunder og anlæg er ramt? Kender vi årsagen?
  Hvornår forventer vi at være oppe? Hvornår kommer næste opdatering?
- Når incidenten er løst, sendes en afsluttende besked, der siger klart, at den er løst.

## 6. Post Incident Review (PIR)

- Holdes senest 5 arbejdsdage efter SEV1 og SEV2 med skabelonen *Post Incident Review*.
- **Blameless**: vi spørger, hvorfor systemet tillod fejlen, ikke hvem der lavede den. Ellers skjuler folk fejl.
- Rodårsag med 5 x hvorfor. Hvert tiltag bliver et issue med ejer og deadline.
- *Never have the same incident twice*: et typisk tiltag er en test, der genskaber fejlen, så CI/CD-pipelinen
  stopper den næste gang, og en alarmregel, der havde opdaget den hurtigere.

## 7. KPI'er

| KPI | Betydning | Kilde |
|---|---|---|
| **MTTD** (mean time to detect) | Tid fra fejlen opstår, til den opdages | Alarmtidspunkt vs. starttidspunkt i PIR |
| **MTTA** (mean time to acknowledge) | Tid fra alarm, til nogen tager ansvar | Incident-issue. I domænet: `iot_alarm_time_to_acknowledge_seconds` |
| **MTTR** (mean time to resolve) | Tid fra detektering til genoprettet service | Incident-issue |
| **MTBF** (mean time between failures) | Tid mellem incidents. Højere er bedre | Incident-issues over tid |
| **SLO 99,5 % tilgængelighed** | Error budget ca. 3,6 timer pr. måned | Azure Monitor, `/health` |
| **Detektionstid p95** | Tid fra sensormåling til `AnomalyDetected` (domænets MTTD) | `iot_anomaly_detection_seconds` |

Nøgletallene for domænet (MTTA og detektionstid) er de vigtigste for kunden: de viser, om produktets løfte
om at opdage fejl på vindmøllen i tide bliver holdt.
