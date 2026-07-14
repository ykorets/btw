# Behind the Watt site

The site is a static Astro project. The public mirror is the only data source;
the site repository does not keep a second copy of generated JSON or CSV.

```sh
npm ci
BTW_DATA_DIR=/absolute/path/to/mirror/data npm run dev
```

Production build:

```sh
BTW_DATA_DIR=/absolute/path/to/mirror/data npm run check
BTW_DATA_DIR=/absolute/path/to/mirror/data npm run build
```

GitHub Pages checks out the `mirror` branch and supplies `_mirror/data` during
the build. Astro generates `/`, every `/facility/{slug}/` dossier, and
`/sitemap.xml` as crawlable static files. `announcements.json` is a separate
third-party project-pipeline layer and is never included in verified operating
capacity.
