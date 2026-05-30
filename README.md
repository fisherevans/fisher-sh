# fisher-sh

Static landing page for [fisher.sh](https://fisher.sh).

Three plain files: `index.html`, `style.css`, `favicon.svg`. No build step,
no JS framework, no dependencies. Cloudflare Pages serves the directory
as-is and gives it the CF edge + TLS.

The blog lives at [log.fisher.sh](https://log.fisher.sh) ([source repo](https://github.com/fisherevans/log)).

## Cloudflare Pages setup

- Build command: *(none - static files served directly)*
- Output dir: `.` (repo root) or empty
- Custom domain: `fisher.sh`

## Local preview

```sh
python3 -m http.server 8080
# http://localhost:8080
```

## Edit

Edit `index.html` for content, `style.css` for styling. Push to `main`,
CF Pages auto-deploys.
