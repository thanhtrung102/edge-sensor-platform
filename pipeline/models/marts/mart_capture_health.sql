-- Per device/hour capture health: how many readings + frames we actually landed vs how
-- many the configured cadence (CAPTURE_FPS) should have produced. This is the data-quality
-- counterpart to the Prometheus capture-rate metric: Prometheus shows live rate, this shows
-- realized completeness once data is in the lake.
{% set expected_per_hour = var('expected_fps') * 3600 %}

with sensors as (
    select device_id, capture_hour, count(*) as readings
    from {{ ref('stg_sensor_readings') }}
    group by 1, 2
),
frames as (
    select device_id, capture_hour, count(*) as frames, sum(size_bytes) as frame_bytes
    from {{ ref('stg_frames') }}
    group by 1, 2
)
select
    s.device_id,
    s.capture_hour,
    s.readings,
    coalesce(f.frames, 0)                                          as frames,
    coalesce(f.frame_bytes, 0)                                     as frame_bytes,
    {{ expected_per_hour }}                                        as expected_captures,
    round(100.0 * s.readings / {{ expected_per_hour }}, 1)         as capture_rate_pct,
    -- frames and sensor readings are emitted together; a gap signals a partial upload
    s.readings - coalesce(f.frames, 0)                             as reading_frame_skew
from sensors s
left join frames f using (device_id, capture_hour)
order by s.device_id, s.capture_hour
