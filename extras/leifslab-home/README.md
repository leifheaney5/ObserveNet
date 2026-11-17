# leifslab.com start page

Not part of ObserveNet. A static start page for the `leifslab.com` service directory,
kept on this branch only so the server can fetch it through its existing clone.

- `index.html`, `styles.css`, `app.js`, `theme.js` — the page. It renders `/services.json`
  (id, name, url, description, category, status, status_note, checked_at) and works under a
  strict CSP (`script-src 'self'; style-src 'self'`, no inline scripts or styles).
- `install.sh` — installs the page as a new release beside the current one, keeps the live
  `services.json` and `favicon.svg`, switches `current`, and prints the rollback command.
- `fetch_icons.py` — run by the installer. Copies tool logos into `icons/` (the CSP only
  allows same-origin images) from the open-source `homarr-labs/dashboard-icons` collection
  and writes `icons/index.json`. Only tools whose identity is certain are mapped; anything
  else keeps a lettered tile. Logos are trademarks of their respective projects.
- `icons/` — logos bundled with the page (ObserveNet's own).

The site's own generator will overwrite this on its next publish unless these files
replace the ones in that project.
