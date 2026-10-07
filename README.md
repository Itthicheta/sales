# Mama Pook Sales Web

Sales-growth dashboard for Mama Pook Noodles — the Sales Driver Tree made live
(9 sections: driver tree, volume/ticket levers, options, members, menu & promo,
monthly models, digest, Grab).

- **Live:** https://mamapook-sales.pages.dev (Cloudflare Pages project `mamapook-sales`)
- **Handoff / full spec:** [Project Sales Web.md](Project%20Sales%20Web.md)
- **Data:** Supabase schema `sales_web` (project xnmzlqqudizckjhchhpn), built from the
  data backbone (`~/mamapook-data`, github.com/Itthicheta/Mamapook-data).

## How to run
Runs automatically from the backbone's 30-min launchd pipeline
(`~/mamapook-data/pipelines/pos_ers/run_local.sh`). Manually:

```sh
~/mamapook-data/venv/bin/python ~/sales/tools/sales_tables.py   # rebuild sales_web tables (--check = row counts)
~/mamapook-data/venv/bin/python ~/sales/app/build/build.py      # bake payload (app_cache + app/site/data/data.json)
~/sales/app/deploy.sh                                           # build + wrangler pages deploy
```

## Secrets
None in this repo. Credentials live only in `~/mamapook-data/.env`, read via the
backbone's `shared.env.load()`.
