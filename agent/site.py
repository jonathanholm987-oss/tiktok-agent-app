"""Erzeugt die öffentliche Link-in-Bio-Seite."""
from __future__ import annotations

from html import escape

from .content import DISCLOSURE
from .core import Product, Settings

STYLE = """
:root{color-scheme:light dark;--bg:#f6f1e7;--fg:#14161a;--card:#fff;--muted:#5b5e66;--line:#ddd6c8}
@media (prefers-color-scheme:dark){:root{--bg:#14161a;--fg:#f6f1e7;--card:#20232a;--muted:#b0b3ba;--line:#343841}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:17px/1.45 system-ui,sans-serif}
main{max-width:560px;margin:auto;padding:32px 16px 48px}h1{font-size:30px;line-height:1.1;margin:0 0 8px}
p{margin:0 0 16px}.note{color:var(--muted);font-size:14px}
ul{list-style:none;margin:24px 0;padding:0;display:grid;gap:10px}
a.item{display:flex;gap:14px;align-items:center;padding:14px 16px;background:var(--card);color:inherit;
text-decoration:none;border:1px solid var(--line);border-radius:12px;font-weight:600}
.nr{flex:none;min-width:44px;font-weight:800;font-size:20px}.to{display:block;font-weight:400;font-size:14px;color:var(--muted)}
footer{margin-top:32px;padding-top:16px;border-top:1px solid var(--line);font-size:14px;color:var(--muted)}
footer h2{font-size:15px;margin:0 0 6px}
"""


def build_site(settings: Settings, products: list[Product]) -> None:
    amazon = settings.section("amazon")
    site = settings.section("site")
    tag = (amazon.get("associate_tag") or "").strip()
    domain = amazon.get("domain", "amazon.de")
    impressum = (settings.section("legal").get("impressum") or "").strip()

    visible = sorted(
        (p for p in products if p.active and (tag and not p.demo)), key=lambda p: p.nr
    )
    if visible:
        rows = "\n".join(
            f'<li><a class="item" rel="sponsored nofollow noopener" target="_blank" '
            f'href="{escape(p.link(domain, tag))}"><span class="nr">{p.nr}</span>'
            f'<span>{escape(p.name)}<span class="to">Zu Amazon *</span></span></a></li>'
            for p in visible
        )
    else:
        rows = '<li class="note">Noch keine Produkte eingetragen.</li>'
    impressum_html = "<br>".join(escape(line) for line in impressum.splitlines())
    page = f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>{escape(site.get("title", "Produkte"))}</title><style>{STYLE}</style></head>
<body><main>
<h1>{escape(site.get("title", "Produkte"))}</h1>
<p>{escape(site.get("intro", ""))}</p>
<p class="note">Werbung: * Alle Links sind Affiliate-Links. {DISCLOSURE}</p>
<ul>{rows}</ul>
<footer><h2>Impressum</h2><p>{impressum_html or "Noch nicht hinterlegt."}</p></footer>
</main></body></html>
"""
    target = settings.root / "site" / "index.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(page, encoding="utf-8")
