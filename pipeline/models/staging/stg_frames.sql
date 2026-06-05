-- Frames now come from MCAP recording segments (recording plane), so extract.py emits one row per
-- camera message with the capture time taken from the message log-time. Device is the first key
-- segment of the .mcap object: {device}/recordings/YYYY/MM/DD/HH/seg_*.mcap
with raw as (
    select * from {{ source('landing', 'frame_index') }}
)
select
    split_part(segment_key, '/', 1)             as device_id,
    segment_key                                 as s3_key,
    size_bytes,
    cast(captured_at as timestamp)              as captured_at,
    date_trunc('hour', cast(captured_at as timestamp)) as capture_hour
from raw
