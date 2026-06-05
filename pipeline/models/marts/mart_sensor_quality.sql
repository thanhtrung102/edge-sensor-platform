-- Per device/sensor/hour distribution summary: count, central tendency, spread, range,
-- and a null/missing count. Feeds quality dashboards and catches drift or stuck sensors.
select
    device_id,
    sensor,
    capture_hour,
    count(*)                                   as n,
    count(*) - count(reading)                  as null_readings,
    round(avg(reading), 3)                     as avg_reading,
    round(median(reading), 3)                  as median_reading,
    round(stddev_samp(reading), 3)             as stddev_reading,
    round(min(reading), 3)                     as min_reading,
    round(max(reading), 3)                     as max_reading
from {{ ref('stg_sensor_long') }}
group by device_id, sensor, capture_hour
order by device_id, sensor, capture_hour
