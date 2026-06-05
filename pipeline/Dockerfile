FROM python:3.12-slim
WORKDIR /pipeline
RUN pip install --no-cache-dir dbt-duckdb==1.9.1 duckdb==1.1.3 boto3==1.35.90 mcap==1.3.1
COPY . /pipeline
ENV DBT_PROFILES_DIR=/pipeline
# extract raw objects -> landing parquet, then build + test marts
CMD ["sh", "-c", "python extract.py && dbt build --profiles-dir ."]
