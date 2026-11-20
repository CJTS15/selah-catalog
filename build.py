#!/usr/bin/env python3
"""Builds Selah translation packages from eBible.org and writes catalog.json.

For each entry in allowlist.toml: download the USFM zip, write meta.txt (and
versification.txt), convert with selah-import, refuse the text if its verse
numbering differs from the KJV, gzip it into <id>.selah and record its size
and sha256. A translation that fails keeps its entry from the previous
catalog, so one broken source never removes a working download.

Standard library only. Usage:

    build.py --selah-import PATH --kjv KJV.sqlite --out DIR \\
             --url-base https://github.com/OWNER/REPO/releases/download/TAG/ \\
             [--previous catalog.json]

Writes DIR/packages/*.selah (new builds only) and DIR/site/catalog.json.
"""

import argparse
import csv
import datetime
import gzip
import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tomllib
import urllib.request
import zipfile

EBIBLE = "https://ebible.org/Scriptures/"
SCHEMA = 1
# Matches selah-core's package cap; larger zips are refused before download.
MAX_PACKAGE = 64 * 1024 * 1024
MAX_ZIP = 100 * 1024 * 1024
MAX_UNZIPPED = 300 * 1024 * 1024
USER_AGENT = "selah-catalog (https://github.com/CJTS15/selah-catalog)"


class Refused(Exception):
    """A translation that cannot be published this run."""


def fetch(url, limit):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise Refused(f"{url} is larger than {limit} bytes")
    return data


def unzip_usfm(data, target):
    """Extracts only *.usfm files and copr.htm, by base name, within a size cap."""
    total = 0
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info in archive.infolist():
            name = os.path.basename(info.filename)
            if info.is_dir() or name.startswith(".") or not (
                name.endswith(".usfm") or name == "copr.htm"
            ):
                continue
            total += info.file_size
            if total > MAX_UNZIPPED:
                raise Refused("zip unpacks to more than the size cap")
            with archive.open(info) as source, open(os.path.join(target, name), "wb") as out:
                shutil.copyfileobj(source, out)


def write_meta(path, entry, row):
    values = {
        "id": entry["id"],
        "name": entry.get("name") or row["title"],
        "abbreviation": entry["abbreviation"],
        "language": row["languageCode"],
        "language_name": entry.get("language_name") or row["languageName"],
        "direction": row["textDirection"] or "ltr",
        "licence": entry["licence"],
        "attribution": entry.get("attribution", ""),
        "notice": entry.get("notice", ""),
        "version": row["UpdateDate"],
    }
    with open(path, "w", encoding="utf-8") as meta:
        for key, value in values.items():
            value = " ".join(str(value).split())
            if value:
                meta.write(f"{key}={value}\n")
    return values


def check_numbering(db_path, kjv_path):
    """Refuses a text that would give verses KJV references the KJV lacks."""
    kjv = sqlite3.connect(kjv_path)
    kjv_books = {row[0] for row in kjv.execute("SELECT id FROM book")}
    kjv_verses = set(kjv.execute("SELECT book, chapter, verse FROM verse"))
    db = sqlite3.connect(db_path)
    mapped = {
        (b, c, v): (cb, cc, cv)
        for b, c, v, cb, cc, cv in db.execute(
            "SELECT book, chapter, verse, canon_book, canon_chapter, canon_verse FROM verse_map"
        )
    }
    bad = []
    for key in db.execute("SELECT book, chapter, verse FROM verse"):
        canonical = mapped.get(key, key)
        if canonical[0] is not None and canonical[0] in kjv_books and canonical not in kjv_verses:
            bad.append("%s %d:%d" % key)
    if bad:
        raise Refused(
            f"verse numbering differs from the KJV at {len(bad)} verses, e.g. {', '.join(bad[:5])}"
        )


def build(entry, row, args, work):
    if row is None:
        raise Refused("not in translations.csv")
    if row["Redistributable"] != "True" or row["downloadable"] != "True":
        raise Refused("eBible.org does not mark it redistributable and downloadable")
    source = os.path.join(work, entry["id"])
    os.makedirs(source)
    unzip_usfm(fetch(f"{EBIBLE}{entry['id']}_usfm.zip", MAX_ZIP), source)
    meta = write_meta(os.path.join(source, "meta.txt"), entry, row)
    if entry.get("versification"):
        with open(os.path.join(source, "versification.txt"), "w", encoding="utf-8") as out:
            out.write(entry["versification"])

    database = os.path.join(work, f"{entry['id']}.sqlite")
    result = subprocess.run(
        [args.selah_import, source, database], capture_output=True, text=True
    )
    if result.returncode != 0:
        raise Refused(f"selah-import: {result.stderr.strip()}")
    check_numbering(database, args.kjv)

    package = os.path.join(args.out, "packages", f"{entry['id']}.selah")
    with open(database, "rb") as db, open(package, "wb") as out:
        # mtime=0 and no file name keep packages byte-identical for identical input.
        with gzip.GzipFile(filename="", mode="wb", fileobj=out, mtime=0, compresslevel=9) as gz:
            shutil.copyfileobj(db, gz)
    with open(package, "rb") as packed:
        data = packed.read()
    if len(data) > MAX_PACKAGE:
        os.remove(package)
        raise Refused("package exceeds the size cap")
    return {
        "id": meta["id"],
        "name": meta["name"],
        "abbreviation": meta["abbreviation"],
        "language": meta["language"],
        "language_name": meta["language_name"],
        "direction": meta["direction"],
        "licence": meta["licence"],
        "version": meta["version"],
        "url": f"{args.url_base}{entry['id']}.selah",
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--selah-import", required=True)
    parser.add_argument("--kjv", required=True, help="KJV translation.sqlite built by selah-import")
    parser.add_argument("--out", required=True)
    parser.add_argument("--url-base", required=True)
    parser.add_argument("--previous", help="the catalog.json currently published")
    parser.add_argument("--allowlist", default=os.path.join(os.path.dirname(__file__), "allowlist.toml"))
    args = parser.parse_args()
    if not args.url_base.startswith("https://") or not args.url_base.endswith("/"):
        sys.exit("--url-base must be an https:// URL ending in /")

    with open(args.allowlist, "rb") as f:
        allowlist = tomllib.load(f)["translation"]
    previous = {}
    if args.previous and os.path.exists(args.previous):
        try:
            with open(args.previous, encoding="utf-8") as f:
                previous = {t["id"]: t for t in json.load(f)["translations"]}
        except (ValueError, KeyError) as err:
            print(f"ignoring previous catalog: {err}", file=sys.stderr)

    rows = {
        row["translationId"]: row
        for row in csv.DictReader(
            io.StringIO(fetch(EBIBLE + "translations.csv", 10 * 1024 * 1024).decode("utf-8-sig"))
        )
    }
    work = os.path.join(args.out, "work")
    for path in (work, os.path.join(args.out, "packages"), os.path.join(args.out, "site")):
        os.makedirs(path, exist_ok=True)

    entries, failed = [], []
    for entry in allowlist:
        try:
            entries.append(build(entry, rows.get(entry["id"]), args, work))
            print(f"built {entry['id']}")
        except Exception as err:  # one broken source must not stop the rest
            failed.append(entry["id"])
            print(f"FAILED {entry['id']}: {err}", file=sys.stderr)
            if entry["id"] in previous:
                entries.append(previous[entry["id"]])
                print(f"  keeping the previous {entry['id']} package", file=sys.stderr)

    catalog = {
        "schema": SCHEMA,
        "generated": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "translations": sorted(entries, key=lambda t: t["name"].casefold()),
    }
    with open(os.path.join(args.out, "site", "catalog.json"), "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=1)
        f.write("\n")
    shutil.rmtree(work)
    print(f"{len(entries)} translations in the catalog, {len(failed)} failed: {' '.join(failed)}")
    # A run where nothing was built is an error worth a red CI mark.
    if len(failed) == len(allowlist):
        sys.exit(1)


if __name__ == "__main__":
    main()
