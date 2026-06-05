-- Fails if any device/sensor/hour landed a missing (null) sensor value.
select device_id, sensor, capture_hour, null_readings
from {{ ref('mart_sensor_quality') }}
where null_readings > 0
