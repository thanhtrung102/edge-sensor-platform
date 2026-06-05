# Roadmap — telemetry plane → MQTT → AWS IoT Core (design note)

> Status: **design, not yet built.** The platform today is a two-plane edge-to-cloud pipeline
> (telemetry JSON objects + MCAP recording segments) that uploads directly to S3-compatible storage.
> This note describes the AWS-IoT-native ingestion path it maps onto, so the migration is a clear,
> ready-to-execute next step. It complements the project's existing OSS→AWS mapping
> (MinIO→S3, k3s→EKS, Prometheus/Grafana→CloudWatch, dbt-duckdb→Glue/Athena).

## Why this matters
A real robot/sensor fleet does **not** `PUT` to S3 from the device. It speaks **MQTT** to a broker
(**AWS IoT Core**), which routes via the rules engine to storage and stream processors. This is the
exact path in AWS's *Guidance for Physical AI for Robotics*:

> *Greengrass sends **MQTT** sensor data through **IoT Core** and **Data Firehose** to **S3**, while
> video streams flow via **Kinesis Video Streams** to S3.*

So the agent's current direct-S3 upload is the one place it diverges from the IoT-native architecture.
MQTT adds four things S3-direct can't give: **pub/sub fan-out** (lake + live control loop + alerting
from one publish), a **downlink** (cloud → device commands / OTA / model deploy), **protocol-level
liveness** (Last-Will-and-Testament = a cleaner device-offline signal than scraping a metric), and a
**tiny header** suited to high-rate scalars.

## Target architecture (per plane)
```
 TELEMETRY plane (small, high-rate scalars)
   agent --MQTT publish (QoS1, LWT)--> AWS IoT Core --rule--> Kinesis Data Firehose --> S3 (Glue/Athena)
                                                    \--rule--> Timestream / CloudWatch (live)
 RECORDING plane (heavy camera, already MCAP today)
   agent / Greengrass Stream Manager --> S3            (large MCAP segments, offline-tolerant)
   (real-time video, optional)        --> Kinesis Video Streams --> S3
 DOWNLINK
   IoT Core device shadow / commands --> agent (capture-rate change, OTA, model deploy)
```
- **Greengrass Stream Manager is the managed version of this repo's store-and-forward agent** —
  "transfer high-volume IoT data reliably… works with unstable connectivity." The buffering,
  retry, eviction, and capture-time-partition logic already built here is what it productizes.

## Implementation (local first, then AWS)
Local (mirrors IoT Core with no cloud account, on the existing stack):
1. **Broker** — `amqtt` (pure-python, in-repo, no extra container) or a single `eclipse-mosquitto`
   compose service + `k8s/70-mqtt.yaml`.
2. **agent** — gate on `TELEMETRY_TRANSPORT=mqtt|s3` (S3-direct stays default). On `mqtt`, publish each
   reading to `edge/{DEVICE_ID}/sensors` via `paho-mqtt` with QoS 1 and a Last-Will announcing
   device-offline. Recording plane (MCAP) is unchanged.
3. **bridge/mqtt_to_s3.py** — subscribe `edge/+/sensors`, batch, write `sensors/Y/M/D/H/*.json` to S3.
   This is the Firehose analogue; the dbt marts consume it transparently.
4. **Live gate** — subscribe and see messages; stop the agent → broker fires the LWT; bridge lands
   objects to MinIO; `extract.py` + `dbt build` still PASS (telemetry now arrives via MQTT→bridge).

AWS swap (one-for-one):
- broker → **AWS IoT Core** (X.509 device certs); bridge → an **IoT rule → Kinesis Data Firehose → S3**;
  device auth/identity → **IoT Core registry + Greengrass**; downlink → **device shadow**.

## Why it's deferred (not dropped)
MQTT/IoT is **domain-adjacent but not a listed requirement** in the target JD (which centers on
data pipeline + object storage + observability + AWS DevOps/EKS/IaC + on-site ops + hardware — all
already covered). The robotics-data-standard signal is already delivered by the **MCAP recording
plane**. This is the highest-value *next* step if the practical test calls for IoT-native ingestion;
it's specified here so it can be built in an afternoon without re-deciding the design.
