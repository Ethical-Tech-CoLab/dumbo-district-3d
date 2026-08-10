"""
Build a review sheet so a person, not a heuristic, decides which photographs count as evidence.

This exists because the automatic screen could not do the job and should not have pretended to.
Telling an office interior from a street view turned out not to be decidable from the metadata or
from a cheap pixel test: measured across this corpus, sky coverage ran 0.16 to 0.79 for interiors and
0.51 to 1.00 for exteriors, so any threshold that caught the interiors also discarded good street
views. The honest answer is a human, and the contract said so all along by grading every record
`auto_screened` rather than `accepted`.

The output is one self-contained HTML file. It hotlinks the images from their source rather than
copying them, so no third-party bytes are stored, and it needs no server: open it, tick what should
be used, press Save, and drop the downloaded file next to it. `build_photo_corpus.py` reads that
file and treats the decisions as authoritative.

Usage:
    python scripts/build_review_sheet.py
    start review/photo-review.html
"""

from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SURVEY = REPO_ROOT / "viewer" / "public" / "district" / "photo-survey.json"
RAW = REPO_ROOT / "data" / "photos" / "photos.raw.json"
DECISIONS = REPO_ROOT / "data" / "photos" / "review-decisions.json"
# Written inside the viewer's public folder so it ships with the site and can be reviewed from a
# phone on the street, rather than only on the machine that happened to build the corpus.
OUT = REPO_ROOT / "viewer" / "public" / "review" / "index.html"

PAGE_HEAD = """<!doctype html>
<meta charset="utf-8">
<title>DUMBO photo review</title>
<style>
  :root {{ color-scheme: dark; --line:#30363d; --muted:#8b949e; --ok:#2e9e4f; --no:#c4453c; }}
  body {{ margin:0; background:#0d1117; color:#e6edf3;
         font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; }}
  header {{ position:sticky; top:0; z-index:5; background:#0d1117ee; backdrop-filter:blur(6px);
            border-bottom:1px solid var(--line); padding:14px 20px; }}
  h1 {{ margin:0 0 4px; font-size:19px; }}
  .sub {{ color:var(--muted); font-size:13px; }}
  .bar {{ display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-top:10px; }}
  button, select {{ background:#21262d; color:#e6edf3; border:1px solid var(--line);
                    border-radius:6px; padding:6px 12px; font-size:13px; cursor:pointer; }}
  button:hover {{ border-color:var(--muted); }}
  button.primary {{ background:#1f6feb; border-color:#1f6feb; }}
  .count {{ color:var(--muted); margin-left:auto; font-variant-numeric:tabular-nums; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(310px,1fr));
           gap:14px; padding:18px 20px; }}
  .card {{ border:1px solid var(--line); border-radius:8px; overflow:hidden; background:#161b22;
           display:flex; flex-direction:column; }}
  .card.inc {{ border-color:var(--ok); }}
  .card.exc {{ border-color:var(--no); opacity:.55; }}
  .thumb {{ width:100%; height:210px; object-fit:cover; background:#21262d; display:block; }}
  .thumb.failed {{ height:60px; object-fit:contain; color:var(--muted); font-size:11px; }}
  .zoom {{ display:block; }}
  .meta {{ padding:9px 11px; font-size:12px; color:var(--muted); flex:1; }}
  .meta b {{ color:#e6edf3; font-weight:600; display:block; margin-bottom:3px;
             font-size:12.5px; word-break:break-word; }}
  .flags {{ margin:5px 0; }}
  .flag {{ display:inline-block; padding:1px 6px; border-radius:10px; font-size:11px;
           border:1px solid var(--line); margin-right:4px; }}
  .flag.warn {{ color:#d29922; border-color:#493c17; }}
  .flag.good {{ color:var(--ok); border-color:#1c4028; }}
  .choices {{ display:flex; border-top:1px solid var(--line); }}
  .choices label {{ flex:1; text-align:center; padding:8px 0; cursor:pointer; font-size:12.5px;
                    border-right:1px solid var(--line); }}
  .choices label:last-child {{ border-right:none; }}
  .choices input {{ margin-right:5px; }}
  a {{ color:#58a6ff; }}
</style>
<header>
  <h1>DUMBO photo review</h1>
  <div class="sub">
    Tick <b>use</b> for photographs that genuinely show a DUMBO street or building exterior well
    enough to take a facade colour from. Tick <b>skip</b> for interiors, close-ups of objects,
    events, or anything where you cannot tell what building you are looking at.
    Everything left untouched stays as it is now: auto-screened, and treated as weaker evidence.
  </div>
  <div class="bar">
    <select id="filter">
      <option value="all">All {total}</option>
      <option value="undecided">Undecided only</option>
      <option value="attached">Currently used as facade evidence</option>
      <option value="screened">Auto-screened out</option>
    </select>
    <button id="allIn">Use all shown</button>
    <button id="allOut">Skip all shown</button>
    <button id="clear">Clear shown</button>
    <button class="primary" id="save">Save decisions</button>
    <span class="count" id="count"></span>
  </div>
</header>
<div class="grid" id="grid"></div>
<script>
const ITEMS = {items};
const EXISTING = {existing};
"""

PAGE_TAIL = """
// Decisions live in localStorage as they are made, so a dropped connection, a closed tab or a
// review done in several sittings on a phone does not lose the work. Save exports the same object
// as a file for the build to consume.
const STORAGE_KEY = 'd3d.photo.review';
function load() {
  try {
    return Object.assign({}, EXISTING, JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}'));
  } catch { return Object.assign({}, EXISTING); }
}
function persist() {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); } catch {}
}

const state = load();
const grid = document.getElementById('grid');
const filter = document.getElementById('filter');

function render() {
  const mode = filter.value;
  grid.innerHTML = '';
  let shown = 0;
  for (const it of ITEMS) {
    if (mode === 'undecided' && state[it.id]) continue;
    if (mode === 'attached' && !it.attached) continue;
    if (mode === 'screened' && !it.screened) continue;
    shown++;
    const card = document.createElement('div');
    const decision = state[it.id];
    card.className = 'card' + (decision === 'use' ? ' inc' : decision === 'skip' ? ' exc' : '');
    const flags = [];
    if (it.attached) flags.push('<span class="flag good">used on ' + it.attached + ' building(s)</span>');
    if (it.screened) flags.push('<span class="flag warn">auto-screened out</span>');
    if (it.sky !== null && it.sky !== undefined) flags.push('<span class="flag">sky ' + it.sky + '</span>');
    flags.push('<span class="flag">' + it.license + '</span>');
    card.innerHTML =
      '<a class="zoom" href="' + it.full + '" target="_blank" rel="noopener" title="Open full size">' +
      '<img class="thumb" ' + (shown <= 12 ? 'loading="eager" fetchpriority="high"' : 'loading="lazy"') +
      ' src="' + it.thumb + '" alt="" ' +
      'onerror="this.classList.add(\\'failed\\'); this.alt=\\'image unavailable — open source\\';"></a>' +
      '<div class="meta"><b>' + it.title + '</b>' +
      '<div class="flags">' + flags.join('') + '</div>' +
      (it.captured ? it.captured.slice(0, 10) + ' · ' : '') +
      (it.located ? 'located' : 'not located') +
      '<div style="margin-top:4px"><a href="' + it.page + '" target="_blank" rel="noopener">source</a></div>' +
      '</div>' +
      '<div class="choices">' +
      '<label><input type="radio" name="' + it.id + '" value="use"' +
        (decision === 'use' ? ' checked' : '') + '>use</label>' +
      '<label><input type="radio" name="' + it.id + '" value="skip"' +
        (decision === 'skip' ? ' checked' : '') + '>skip</label>' +
      '</div>';
    card.querySelectorAll('input').forEach((input) => {
      input.addEventListener('change', () => { state[it.id] = input.value; persist(); render(); });
    });
    grid.appendChild(card);
  }
  const used = Object.values(state).filter((v) => v === 'use').length;
  const skipped = Object.values(state).filter((v) => v === 'skip').length;
  document.getElementById('count').textContent =
    shown + ' shown · ' + used + ' use · ' + skipped + ' skip · ' +
    (ITEMS.length - used - skipped) + ' undecided';
}

function shownIds() {
  return [...grid.querySelectorAll('.choices input')].map((i) => i.name)
    .filter((v, i, a) => a.indexOf(v) === i);
}
function bulk(fn) { shownIds().forEach(fn); persist(); render(); }
document.getElementById('allIn').onclick = () => bulk((id) => state[id] = 'use');
document.getElementById('allOut').onclick = () => bulk((id) => state[id] = 'skip');
document.getElementById('clear').onclick = () => bulk((id) => delete state[id]);
filter.onchange = render;

document.getElementById('save').onclick = () => {
  const blob = new Blob([JSON.stringify(state, null, 1)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'review-decisions.json';
  a.click();
  alert('Saved review-decisions.json.\\n\\nPut it in data/photos/ and re-run:\\n' +
        '  python scripts/build_photo_corpus.py\\n  python scripts/build_scene_dressing.py --facades');
};

render();
</script>
"""


def review_thumbnail(url: str) -> str:
    """Drop tracking query strings. Sizing is handled by the API, not by string surgery.

    Substituting a smaller width into a Commons thumbnail path returns 400 unless that exact size
    happens to have been rendered before, and which sizes exist varies file by file. Review-sized
    URLs are therefore minted by the API at ingest time
    (`python scripts/ingest_photos.py --thumbs`) and read from the raw candidates here.
    """
    return (url or "").split("?", 1)[0]


def main() -> int:
    argparse.ArgumentParser(description=__doc__,
                            formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()

    if not SURVEY.exists():
        print("no corpus yet; run scripts/ingest_photos.py then scripts/build_photo_corpus.py")
        return 1
    survey = json.loads(SURVEY.read_text(encoding="utf-8"))
    existing = json.loads(DECISIONS.read_text(encoding="utf-8")) if DECISIONS.exists() else {}

    # Review-sized thumbnails live on the raw candidates rather than in the published survey: the
    # survey conforms to a shared contract and does not need a field that only this sheet uses.
    small: dict[str, str] = {}
    if RAW.exists():
        for record in json.loads(RAW.read_text(encoding="utf-8")):
            key = record.get("page_url") or record.get("image_url")
            if key and record.get("thumbnail_small_url"):
                small[key.split("?", 1)[0]] = record["thumbnail_small_url"]
    if not small:
        print("note: no review-sized thumbnails; run  python scripts/ingest_photos.py --thumbs")

    items = []
    for observation in survey["observations"]:
        credit = observation.get("attribution_text") or ""
        title = credit.split(" by ")[0] if " by " in credit else credit
        page_url = (observation.get("image_url") or "").split("?", 1)[0]
        full = review_thumbnail(observation.get("thumbnail_url") or "")
        items.append({
            "id": observation["observation_id"],
            "title": html.escape(title[:96]) or "(untitled)",
            "thumb": small.get(page_url) or full,
            "full": full,
            "page": observation.get("image_url") or "",
            "license": observation.get("license") or "?",
            "captured": observation.get("captured_at"),
            "located": observation.get("position_source") != "unknown",
            "sky": (observation.get("quality") or {}).get("sky_fraction"),
            "attached": len(observation.get("observes") or []),
            "screened": "screened out" in (observation.get("notes") or ""),
        })
    # Most useful first: things already influencing the model, then everything else.
    items.sort(key=lambda i: (-i["attached"], not i["located"], i["title"]))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    page = PAGE_HEAD.format(
        total=len(items),
        items=json.dumps(items),
        existing=json.dumps(existing),
    ) + PAGE_TAIL
    OUT.write_text(page, encoding="utf-8")

    decided = len(existing)
    print(f"wrote {OUT.relative_to(REPO_ROOT)}")
    print(f"  {len(items)} photographs, {sum(1 for i in items if i['attached'])} currently used, "
          f"{sum(1 for i in items if i['screened'])} auto-screened out")
    print(f"  {decided} decisions already recorded" if decided else "  no decisions recorded yet")
    print(f"\nOpen it, tick, press Save, and put review-decisions.json in "
          f"{DECISIONS.parent.relative_to(REPO_ROOT)}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
