-- One typed row per capture. Parses the partition out of the s3 key and casts ts.
with raw as (
    select * from {{ source('landing', 'sensor_readings') }}
)
select
    device_id,
    site,
    cast(ts as timestamp)                          as captured_at,
    date_trunc('hour', cast(ts as timestamp))      as capture_hour,
    temperature_c,
    vibration_g,
    humidity_pct,
    s3_key
from raw
