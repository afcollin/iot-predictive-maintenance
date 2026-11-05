"""Simulerer en IoT-sensor monteret på en vindmølle og sender målinger til REST API'et.

Kræver kun Python 3.10+ (ingen pakker), så scriptet kan køres fra en vilkårlig computer,
der spiller rollen som IoT-enhed.

Eksempler:
    python simulate.py --url http://192.168.1.50:8000 --scenario degrading
    python simulate.py --metric oil_pressure --scenario spike --count 30
    python simulate.py --scenario normal --interval 0.5

Scenarier:
    normal     målinger omkring normalværdien, ingen anomalier
    degrading  komponenten slides gradvist: normal -> WARNING -> CRITICAL
    spike      normal drift med enkelte kritiske udslag
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request

# metric: (normalværdi, støj, værdi ved slutningen af "degrading", kritisk udslag ved "spike")
PROFILES = {
    "gearbox_vibration": (3.0, 0.4, 13.0, 12.5),
    "bearing_temperature": (60.0, 1.5, 100.0, 98.0),
    "oil_pressure": (3.0, 0.1, 0.8, 0.9),
}


def generate_value(metric: str, scenario: str, step: int, count: int) -> float:
    normal, noise, degraded, spike = PROFILES[metric]
    if scenario == "degrading":
        progress = step / max(count - 1, 1)
        base = normal + (degraded - normal) * progress**2  # sliddet accelererer
    elif scenario == "spike" and step % 7 == 6:
        base = spike
    else:
        base = normal
    return round(base + random.gauss(0, noise), 2)  # tilfældig målestøj (simulering, ikke kryptografi)


def send(url: str, api_key: str, payload: dict) -> dict:
    request = urllib.request.Request(
        f"{url.rstrip('/')}/api/v1/sensor-readings",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-API-Key": api_key},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read())


def main() -> int:
    parser = argparse.ArgumentParser(description="Simuleret IoT-sensor for Intelligent IoT Solutions A/S")
    parser.add_argument("--url", default=os.getenv("IOT_API_URL", "http://localhost:8000"))
    parser.add_argument("--api-key", default=os.getenv("IOT_API_KEY", "local-dev-key"))
    parser.add_argument("--asset", default="WTG-AAL-07", help="Anlæg (vindmølle)")
    parser.add_argument("--device", default="SNS-000123", help="Sensor-id")
    parser.add_argument("--metric", default="gearbox_vibration", choices=sorted(PROFILES))
    parser.add_argument("--scenario", default="degrading", choices=["normal", "degrading", "spike"])
    parser.add_argument("--count", type=int, default=20, help="Antal målinger")
    parser.add_argument("--interval", type=float, default=1.0, help="Sekunder mellem målinger")
    args = parser.parse_args()

    print(f"Sensor {args.device} på {args.asset} sender {args.count} målinger ({args.metric}) til {args.url}\n")
    for step in range(args.count):
        value = generate_value(args.metric, args.scenario, step, args.count)
        payload = {"asset_id": args.asset, "device_id": args.device, "metric": args.metric, "value": value}
        try:
            result = send(args.url, args.api_key, payload)
        except urllib.error.HTTPError as exc:
            print(f"[{step + 1:>3}] {value:>8}  HTTP {exc.code}: {exc.read().decode()}")
            continue
        except urllib.error.URLError as exc:
            print(f"Kan ikke nå API'et på {args.url}: {exc.reason}")
            return 1

        if result["anomaly_detected"]:
            status = f"ANOMALI {result['severity']:<8} alarm {result['alarm_action']}"
        else:
            status = "normal"
        print(f"[{step + 1:>3}] {value:>8}  {status}")
        time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    sys.exit(main())
