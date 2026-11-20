# Selah catalog

Openly licensed Bible translations for [Selah](https://github.com/CJTS15/selah),
a local-first Bible reader for GNOME. Selah reads
`https://cjts15.github.io/selah-catalog/catalog.json` when you open its
Translations dialog and downloads the packages listed there.

## How it works

A weekly GitHub Actions run (`.github/workflows/publish.yml`):

1. Builds `selah-import` from Selah at the commit in `selah-ref`.
2. Reads eBible.org's `translations.csv` and, for each entry in
   `allowlist.toml`, downloads the USFM zip and converts it.
3. Refuses a text whose verse numbering differs from the King James Version,
   which Selah stores bookmarks and highlights by, unless the entry's
   `versification` lines account for every difference.
4. Gzips each database into `<id>.selah`, uploads the packages as a dated
   GitHub Release, then publishes `catalog.json` (size and sha256 of each
   package) on GitHub Pages.

A translation that fails to download, convert or pass the numbering check
keeps its previous package, so one broken source never removes a working
download. Selah checks the size and sha256 of every download and rebuilds
the database before installing it.

## Adding a translation

1. Find it on [eBible.org](https://ebible.org/download.php) and read its
   `copr.htm`. Only public domain, CC0, CC BY and CC BY-SA texts qualify;
   never "no derivatives".
2. Add an entry to `allowlist.toml` with the licence, attribution, any notice
   the licence requires, and the review date.
3. Run the build locally (below). If the numbering check fails, compare the
   listed verses with the KJV text and add `versification` lines only where
   both texts were read. Texts with their own numbering scheme (for example
   Vulgate or Septuagint numbering) wait for versification support in Selah.

```sh
cargo build --release --manifest-path ../selah/Cargo.toml -p selah-core --bin selah-import
../selah/target/release/selah-import ../selah/data/translations/eng-kjv kjv.sqlite
python3 build.py --selah-import ../selah/target/release/selah-import --kjv kjv.sqlite \
  --out out --url-base https://example.org/
```

## Licences

The build scripts are GPL-3.0-or-later. Each translation keeps its own
licence, which travels in the package metadata and is shown in Selah's About
dialog. CC BY-SA texts are redistributed under the same licence.
