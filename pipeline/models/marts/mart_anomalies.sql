-- Flag anomalous readings using a robust z-score (median + MAD) per device/sensor, so the
-- detection is resistant to the very spikes we are hunting. A reading is anomalous when its
-- modified z-score exceeds var('anomaly_sigma'). This is the batch analogue of the agent's
-- live edge_sensor_anomaly_total counter -- it independently recovers the injected spikes
-- from the landed data, which is how you'd reconcile edge detection against ground truth.
{% set sigma = var('anomaly_sigma') %}

with base as (
    select device_id, site, captured_at, capture_hour, sensor, reading
    from {{ ref('stg_sensor_long') }}
),
with_median as (
    select
        *,
        median(reading) over (partition by device_id, sensor)   as med
    from base
),
with_mad as (
    -- MAD = median absolute deviation; computed in its own pass because window
    -- functions cannot be nested inside one another.
    select
        *,
        median(abs(reading - med)) over (partition by device_id, sensor)  as mad
    from with_median
),
scored as (
    select
        *,
        case when mad = 0 then 0
             else 0.6745 * (reading - med) / mad end                       as robust_z
    from with_mad
)
select
    device_id,
    site,
    captured_at,
    capture_hour,
    sensor,
    reading,
    round(med, 3)                          as baseline_median,
    round(robust_z, 2)                     as robust_z,
    case
        when abs(robust_z) >= 2 * {{ sigma }} then 'critical'
        else 'warning'
    end                                    as severity
from scored
where abs(robust_z) >= {{ sigma }}
order by abs(robust_z) desc
