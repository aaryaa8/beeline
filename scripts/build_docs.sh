#!/usr/bin/env bash
# Rebuild docs/ (the permanent GitHub Pages demo) from web/.
#
# docs/ is a static, backend-free copy of the map that auto-plays the guided
# demo. GitHub Pages serves it at https://aaryaa8.github.io/beeline/. Run this
# after any change to web/index.html, web/app.js, or web/style.css, then commit
# and push docs/.
#
#   bash scripts/build_docs.sh && git add docs && git commit -m "rebuild docs" && git push
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
REPO_URL="https://github.com/aaryaa8/beeline"

rm -rf docs && mkdir -p docs
cp web/index.html web/app.js web/style.css docs/
cp -r web/fonts docs/fonts

# 1) make asset paths relative (Pages serves under /beeline/, not root)
sed -i '' -E \
  -e 's#href="/fonts/nunito.css"#href="fonts/nunito.css"#' \
  -e 's#href="/style.css[^"]*"#href="style.css"#' \
  -e 's#src="/app.js[^"]*"#src="app.js"#' docs/index.html
# fonts css lives in docs/fonts/, woff2 are siblings
sed -i '' 's#url(/fonts/nunito-#url(nunito-#g' docs/fonts/nunito.css

# 2) point the QR at the repo (a static host has no /join backend)
python3 - "$REPO_URL" <<'PY'
import sys, re, pathlib
repo = sys.argv[1]
p = pathlib.Path("docs/app.js"); t = p.read_text()
t2 = re.sub(r"try \{ return location\.origin \+ '/join'; \} catch \(e\) \{ return '/join'; \}",
            f"return '{repo}';", t)
if t2 == t:
    # tolerate a refactor of joinURL(); just warn
    print("  [warn] joinURL body not found verbatim; QR may still point at /join")
p.write_text(t2)
PY

# 3) inject: static marker + hide backend-only controls + banner + auto-start
python3 - "$REPO_URL" <<'PY'
import sys, pathlib
repo = sys.argv[1]
p = pathlib.Path("docs/index.html"); t = p.read_text()
head = ('''<script>document.documentElement.setAttribute('data-static','1');</script>
<style>
  html[data-static] #qr, html[data-static] #btn-config, html[data-static] #btn-live,
  html[data-static] #meta, html[data-static] #btn-move, html[data-static] #btn-recharge,
  html[data-static] #btn-intro, html[data-static] #btn-demo, html[data-static] #mode-badge { display:none !important; }
  #static-banner {
    position: fixed; left: 50%; transform: translateX(-50%); top: 16px; z-index: 50;
    background: var(--panel); color: var(--text); border: 1px solid var(--line);
    border-radius: 999px; padding: 7px 15px; font-size: 13px; font-weight: 700;
    box-shadow: 0 8px 24px var(--shadow); display: flex; gap: 10px; align-items: center;
    font-family: "Nunito", system-ui, sans-serif;
  }
  #static-banner .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--open); box-shadow: 0 0 8px var(--open); }
  #static-banner a { color: var(--accent); text-decoration: none; }
  @media (max-width: 900px){ #static-banner { top: 8px; font-size: 12px; } }
</style>
</head>''')
t = t.replace("</head>", head, 1)
banner = ('<body>\n<div id="static-banner"><span class="dot"></span>'
          '<span>Beeline &middot; auto-playing demo</span>'
          f'<a href="{repo}" target="_blank" rel="noopener">code + live app &#8599;</a></div>')
t = t.replace("<body>", banner, 1)
t = t.replace("</body>",
              "<script>setTimeout(function(){var b=document.getElementById('btn-fill'); if(b) b.click();}, 2200);</script>\n</body>", 1)
p.write_text(t)
print("  docs/ rebuilt")
PY

# 4) skip Jekyll (pure static)
touch docs/.nojekyll
echo "done. review docs/, then: git add docs && git commit -m 'rebuild docs' && git push"
