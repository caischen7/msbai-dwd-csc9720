import csv
import gzip
import io
import os
import re
import sys
import zipfile

from google.cloud import storage

PROJECT = "msbai-dwd-csc9720"
BUCKET_NAME = "msbai-dwd-csc9720-citibike-raw"
DEST_PREFIX = "uniform/"

OUT_HEADER = [
    "ride_id", "rideable_type", "started_at", "ended_at",
    "start_station_name", "start_station_id",
    "end_station_name", "end_station_id",
    "start_lat", "start_lng", "end_lat", "end_lng",
    "member_casual", "bikeid", "birth_year", "gender", "tripduration",
    "system", "source_file",
]

NULL_MARKERS = {"\\n", "null", "n/a", "na"}

SCHEMA_A_FIELDS = {
    "starttime", "stoptime", "start station id", "start station name",
    "start station latitude", "start station longitude",
    "end station id", "end station name",
    "end station latitude", "end station longitude",
    "bikeid", "usertype", "birth year", "gender", "tripduration",
}
SCHEMA_B_FIELDS = {
    "ride_id", "rideable_type", "started_at", "ended_at",
    "start_station_name", "start_station_id",
    "end_station_name", "end_station_id",
    "start_lat", "start_lng", "end_lat", "end_lng", "member_casual",
}

MONTH_FOLDER_RE = re.compile(r"(?:^|/)(\d{1,2})[_ ]([A-Za-z]+)/")
YYYYMM_RE = re.compile(r"(\d{6})")


def is_junk(name):
    base = name.rsplit("/", 1)[-1]
    if not base:
        return True
    if name.startswith("__MACOSX") or "/__MACOSX" in name:
        return True
    if base.startswith("."):
        return True
    return False


def output_name(object_name):
    base = object_name[:-4] if object_name.lower().endswith(".zip") else object_name
    if base.lower().endswith(".csv"):
        return DEST_PREFIX + base + ".gz"
    return DEST_PREFIX + base + ".csv.gz"


def detect_system(object_name):
    base = object_name.rsplit("/", 1)[-1]
    return "JC" if base.upper().startswith("JC-") else "NYC"


def get(row, field_idx, name):
    i = field_idx.get(name)
    if i is None or i >= len(row):
        return ""
    val = row[i]
    if val.strip().lower() in NULL_MARKERS:
        return ""
    return val


def dedup_descriptors(descriptors):
    """Drop per-month-folder CSVs when a canonical (non-folder) CSV for the
    same YYYYMM already exists among the descriptors (2013-style dupes)."""
    canonical_months = set()
    for full_name, _ in descriptors:
        if MONTH_FOLDER_RE.search(full_name):
            continue
        m = YYYYMM_RE.search(full_name.rsplit("/", 1)[-1])
        if m:
            canonical_months.add(m.group(1))

    result = []
    for full_name, payload in descriptors:
        if MONTH_FOLDER_RE.search(full_name):
            m = YYYYMM_RE.search(full_name.rsplit("/", 1)[-1])
            if m and m.group(1) in canonical_months:
                continue
        result.append((full_name, payload))
    return result


def iter_csv_streams(zf, path_prefix=""):
    direct_descriptors = []
    nested_zips = []
    for info in zf.infolist():
        name = info.filename
        if is_junk(name):
            continue
        full_name = path_prefix + name
        if name.lower().endswith(".csv"):
            direct_descriptors.append((full_name, (zf, info)))
        elif name.lower().endswith(".zip"):
            nested_zips.append((full_name, info))

    for full_name, (src_zf, info) in dedup_descriptors(direct_descriptors):
        yield full_name, src_zf.open(info)

    for full_name, info in nested_zips:
        inner_bytes = zf.read(info)
        izf = zipfile.ZipFile(io.BytesIO(inner_bytes))
        yield from iter_csv_streams(izf, path_prefix=full_name + "/")


def detect_schema(field_idx):
    keys = set(field_idx.keys())
    if "ride_id" in keys:
        return "B"
    if "tripduration" in keys or "starttime" in keys:
        return "A"
    raise ValueError(f"Unrecognized schema: {sorted(keys)}")


def transform_row(row, field_idx, era, system, source_file):
    if era == "B":
        return [
            get(row, field_idx, "ride_id"),
            get(row, field_idx, "rideable_type"),
            get(row, field_idx, "started_at"),
            get(row, field_idx, "ended_at"),
            get(row, field_idx, "start_station_name"),
            get(row, field_idx, "start_station_id"),
            get(row, field_idx, "end_station_name"),
            get(row, field_idx, "end_station_id"),
            get(row, field_idx, "start_lat"),
            get(row, field_idx, "start_lng"),
            get(row, field_idx, "end_lat"),
            get(row, field_idx, "end_lng"),
            get(row, field_idx, "member_casual"),
            "", "", "", "",
            system, source_file,
        ]
    else:
        usertype = get(row, field_idx, "usertype").strip().lower()
        if usertype == "subscriber":
            member_casual = "member"
        elif usertype == "customer":
            member_casual = "casual"
        else:
            member_casual = ""
        return [
            "",
            "classic_bike",
            get(row, field_idx, "starttime"),
            get(row, field_idx, "stoptime"),
            get(row, field_idx, "start station name"),
            get(row, field_idx, "start station id"),
            get(row, field_idx, "end station name"),
            get(row, field_idx, "end station id"),
            get(row, field_idx, "start station latitude"),
            get(row, field_idx, "start station longitude"),
            get(row, field_idx, "end station latitude"),
            get(row, field_idx, "end station longitude"),
            member_casual,
            get(row, field_idx, "bikeid"),
            get(row, field_idx, "birth year"),
            get(row, field_idx, "gender"),
            get(row, field_idx, "tripduration"),
            system, source_file,
        ]


def process_object(object_name):
    client = storage.Client(project=PROJECT)
    bucket = client.bucket(BUCKET_NAME)
    out_blob_name = output_name(object_name)
    out_blob = bucket.blob(out_blob_name)
    if out_blob.exists():
        return f"SKIP (exists): {object_name} -> {out_blob_name}"

    system = detect_system(object_name)
    in_blob = bucket.blob(object_name)
    local_in = f"/tmp/_uf_in_{os.getpid()}.zip"
    local_out = f"/tmp/_uf_out_{os.getpid()}.csv.gz"

    in_blob.download_to_filename(local_in)
    total_rows = 0
    try:
        with zipfile.ZipFile(local_in) as zf, gzip.open(local_out, "wt", newline="") as out_f:
            writer = csv.writer(out_f)
            writer.writerow(OUT_HEADER)
            for csv_name, stream in iter_csv_streams(zf):
                source_file = f"{object_name}::{csv_name}"
                text = io.TextIOWrapper(stream, encoding="utf-8-sig", newline="")
                reader = csv.reader(text)
                try:
                    header = next(reader)
                except StopIteration:
                    continue
                field_idx = {h.strip().lower(): i for i, h in enumerate(header)}
                era = detect_schema(field_idx)
                for row in reader:
                    if not row:
                        continue
                    writer.writerow(transform_row(row, field_idx, era, system, source_file))
                    total_rows += 1
                text.close()

        out_blob.upload_from_filename(local_out)
    finally:
        for p in (local_in, local_out):
            if os.path.exists(p):
                os.remove(p)

    return f"OK: {object_name} -> {out_blob_name} ({total_rows} rows)"


def main():
    client = storage.Client(project=PROJECT)
    bucket = client.bucket(BUCKET_NAME)
    blobs = list(client.list_blobs(bucket))
    objects = [
        b.name for b in blobs
        if not b.name.startswith(DEST_PREFIX) and b.name.lower() != "index.html"
        and b.name.lower().endswith(".zip")
    ]
    objects.sort()
    print(f"Found {len(objects)} archives to process", flush=True)

    for i, name in enumerate(objects, 1):
        try:
            result = process_object(name)
        except Exception as e:
            import traceback
            result = f"ERROR: {name}: {e}\n{traceback.format_exc()}"
        print(f"[{i}/{len(objects)}] {result}", flush=True)

    print("DONE", flush=True)


if __name__ == "__main__":
    main()
