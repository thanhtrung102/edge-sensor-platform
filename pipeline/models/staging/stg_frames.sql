-- Parse device + partition out of the frame object key:
--   {device}/frames/YYYY/MM/DD/HH/frame_YYYYMMDDThhmmss......jpg
with raw as (
    select * from {{ source('landing', 'frame_index') }}
),
parsed as (
    select
        s3_key,
        size_bytes,
        split_part(s3_key, '/', 1)                          as device_id,
        regexp_extract(s3_key, 'frame_(\d{8}T\d{6})', 1)    as stamp
    from raw
)
select
    device_id,
    s3_key,
    size_bytes,
    strptime(stamp, '%Y%m%dT%H%M%S')                        as captured_at,
    date_trunc('hour', strptime(stamp, '%Y%m%dT%H%M%S'))    as capture_hour
from parsed
where stamp <> ''
