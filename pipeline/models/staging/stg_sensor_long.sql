-- Unpivot wide readings into (device, site, captured_at, sensor, value) long form so the
-- quality and anomaly marts can treat every sensor uniformly.
with wide as (
    select * from {{ ref('stg_sensor_readings') }}
)
unpivot wide
on temperature_c, vibration_g, humidity_pct
into name sensor value reading
