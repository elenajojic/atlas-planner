"""Self contained HTML views: week grid + scorecards, and the 4 year heatmap."""
from __future__ import annotations

import html

from .data_source import user_home

PALETTE = ["#2F6FDE", "#E0712C", "#1E9E77", "#B8509B", "#C9A100", "#4AA3C8", "#D24B4B", "#6E5BD0", "#5E8C31"]
CSS = """
:root{--ink:#14213D;--muted:#5B667A;--line:#E3E7EE;--bg:#F7F8FB;--blue:#00274C;--maize:#FFCB05}
*{box-sizing:border-box}body{margin:0;font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,sans-serif;color:var(--ink);background:var(--bg)}
header{background:var(--blue);color:#fff;padding:18px 28px;display:flex;align-items:baseline;gap:14px}
header h1{font-size:19px;margin:0;font-weight:650}header .tag{background:var(--maize);color:var(--blue);font-weight:700;font-size:11px;padding:2px 8px;border-radius:99px}
header .sub{opacity:.75;font-size:13px}
main{padding:20px 28px;max-width:1280px;margin:auto}
.tabs{display:flex;gap:8px;margin-bottom:14px;flex-wrap:wrap}.tabs button{border:1px solid var(--line);background:#fff;padding:7px 14px;border-radius:8px;cursor:pointer;font:inherit}
.tabs button.on{background:var(--blue);color:#fff;border-color:var(--blue)}
.row{display:grid;grid-template-columns:1fr 330px;gap:18px}@media(max-width:900px){.row{grid-template-columns:1fr}}
.card{background:#fff;border:1px solid var(--line);border-radius:12px;padding:16px}
.week{display:grid;grid-template-columns:56px repeat(5,1fr);position:relative}
.week .hd{font-weight:600;font-size:12px;color:var(--muted);text-align:center;padding-bottom:6px}
.col{position:relative;border-left:1px solid var(--line)}.tcol{position:relative}
.hr{position:absolute;left:0;right:0;border-top:1px dashed var(--line)}.tl{position:absolute;right:6px;font-size:11px;color:var(--muted);transform:translateY(-7px)}
.ev{position:absolute;left:3px;right:3px;border-radius:7px;padding:4px 6px;font-size:11.5px;color:#fff;overflow:hidden;line-height:1.25;box-shadow:0 1px 2px rgba(0,0,0,.12)}
.ev b{display:block;font-size:12px}.ev.commit{background:repeating-linear-gradient(45deg,#E9ECF2,#E9ECF2 6px,#F4F6F9 6px,#F4F6F9 12px);color:var(--muted);border:1px solid #D5DAE3}
.k{display:grid;grid-template-columns:1fr 1fr;gap:10px}.k div{background:var(--bg);border-radius:9px;padding:9px 11px}
.k .v{font-size:19px;font-weight:700}.k .l{font-size:11.5px;color:var(--muted)}
h3{margin:16px 0 8px;font-size:13px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}
table{width:100%;border-collapse:collapse;font-size:13px}td,th{padding:7px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{font-size:11.5px;color:var(--muted);font-weight:600}
.pill{display:inline-block;padding:1px 8px;border-radius:99px;font-size:11.5px;font-weight:600}
.low{background:#E5F5EC;color:#17733F}.medium{background:#FFF4D6;color:#8A6200}.high{background:#FDE6E6;color:#A82828}
.warn{background:#FFF8E1;border:1px solid #F3DE9B;border-radius:9px;padding:8px 11px;margin:6px 0;font-size:13px}
.bar{height:8px;border-radius:5px;background:var(--line);overflow:hidden}.bar i{display:block;height:100%;background:var(--blue)}
.heat{display:grid;gap:10px}.term{display:grid;grid-template-columns:120px 1fr 90px 90px;gap:12px;align-items:center;background:#fff;border:1px solid var(--line);border-radius:10px;padding:10px 14px}
.chips span{display:inline-block;margin:2px 4px 2px 0;padding:2px 8px;border-radius:6px;font-size:12px;background:var(--bg)}
small{color:var(--muted)}
"""


def _ev_html(g, color, t0, px_per_min):
    top = (g["start_min"] - t0) * px_per_min
    h = max(18, (g["end_min"] - g["start_min"]) * px_per_min - 2)
    cls = "ev commit" if g["kind"] == "commitment" else "ev"
    style = "" if g["kind"] == "commitment" else f"background:{color}"
    where = f" · {html.escape(g['where'])}" if g.get("where") else ""
    return (f'<div class="{cls}" style="top:{top}px;height:{h}px;{style}">'
            f'<b>{html.escape(g["label"])}</b>{html.escape(g["kind"] if g["kind"] != "commitment" else "")} '
            f'{g["start"]}{where}</div>')


def week_html(result: dict, title: str = "Atlas Planner") -> str:
    plans = result.get("plans", [])
    tabs, panes = [], []
    for i, p in enumerate(plans):
        colors = {c["course"]: PALETTE[j % len(PALETTE)] for j, c in enumerate(p["courses"])}
        t0 = min([g["start_min"] for g in p["grid"]] + [8 * 60]) // 60 * 60
        t1 = (max([g["end_min"] for g in p["grid"]] + [18 * 60]) + 59) // 60 * 60
        ppm = 0.9
        height = (t1 - t0) * ppm
        cols = ['<div class="hd"></div>'] + [f'<div class="hd">{d}</div>' for d in ["Mon", "Tue", "Wed", "Thu", "Fri"]]
        tcol = "".join(f'<div class="tl" style="top:{(h - t0) * ppm}px">{(h // 60 - 1) % 12 + 1}{"am" if h < 720 else "pm"}</div>'
                       for h in range(t0, t1 + 1, 60))
        cols.append(f'<div class="tcol" style="height:{height}px">{tcol}</div>')
        for d in ["Mon", "Tue", "Wed", "Thu", "Fri"]:
            lines = "".join(f'<div class="hr" style="top:{(h - t0) * ppm}px"></div>' for h in range(t0, t1 + 1, 60))
            evs = "".join(_ev_html(g, colors.get(g["label"], "#888"), t0, ppm) for g in p["grid"] if g["day"] == d)
            cols.append(f'<div class="col" style="height:{height}px">{lines}{evs}</div>')
        g = p["gpa"]
        prot = "".join(f'<div class="l">{html.escape(x["name"])}: {x["found_h"]}/{x["needed_h"]}h {"✓" if x["fits"] else "✗"}</div>'
                       for x in p["protected"])
        loads = p["day_load"]
        mx = max(loads.values()) or 1
        loadbars = "".join(f'<tr><td style="width:40px">{d}</td><td><div class="bar"><i style="width:{v / mx * 100:.0f}%"></i></div></td>'
                           f'<td style="width:40px">{v}</td></tr>' for d, v in loads.items())
        rows = ""
        for c in p["courses"]:
            pg = c["predicted_grade"] or {}
            inst = html.escape((c["instructor"] or "").split(" | ")[0])
            flags = html.escape(" | ".join((c["instructor"] or "").split(" | ")[1:]))
            back = f'<br><small>backup: {html.escape(", ".join(c["backup_sections"]))}</small>' if c["backup_sections"] else ""
            rows += (f'<tr><td><b style="color:{colors[c["course"]]}">{c["course"]}</b><br><small>{html.escape(c["title"])}</small></td>'
                     f'<td>{c["sections"]}<br><small>{html.escape(c["times"])}</small></td>'
                     f'<td>{inst}<br><small>{flags}</small></td>'
                     f'<td><b>{pg.get("range", "?")}</b><br><small>{pg.get("confidence", "")} confidence, typical {pg.get("typical_for_course", "?")}</small></td>'
                     f'<td>{c["difficulty_0_100"]}<br><small>workload {c["atlas"]["workload_pct"]}%, median {c["atlas"]["median_grade"]}</small></td>'
                     f'<td><span class="pill {c["seat_risk"]}">{c["seat_risk"]}</span>{back}</td>'
                     f'<td><small>{html.escape(", ".join(c["counts_toward"]))}</small></td></tr>')
        warns = "".join(f'<div class="warn">{html.escape(w)}</div>' for w in p["warnings"] + p["combos"])
        tabs.append(f'<button class="{"on" if i == 0 else ""}" onclick="show({i})">Option {p["rank"]} · {p["score"]:.0f}</button>')
        panes.append(f'''<section class="pane" style="display:{"block" if i == 0 else "none"}">
<div class="row"><div class="card"><div class="week">{"".join(cols)}</div></div>
<div class="card"><div class="k">
<div><div class="v">{p["credits"]:g}</div><div class="l">credits</div></div>
<div><div class="v">{p["workload_index"]}</div><div class="l">workload index</div></div>
<div><div class="v">{g["term_gpa"]}</div><div class="l">predicted term GPA</div></div>
<div><div class="v">{g["bcpm_after"]}</div><div class="l">BCPM after (now {g["bcpm_now"]})</div></div>
<div><div class="v">{p["heaviest_day"]}</div><div class="l">heaviest day</div></div>
<div><div class="v">{p["dead_time_h"]}h</div><div class="l">dead time between classes</div></div></div>
<h3>Day by day effort</h3><table>{loadbars}</table>
<h3>Protected time</h3>{prot}
<h3>Lunch</h3><div class="l">{"Every class day ✓" if not p["lunch_missing_days"] else "Missing: " + ", ".join(p["lunch_missing_days"])}</div>
</div></div>
{warns}
<div class="card" style="margin-top:16px"><table><tr><th>Course</th><th>Sections & times</th><th>Instructor</th><th>Your predicted grade</th><th>Difficulty</th><th>Seat risk</th><th>Counts toward</th></tr>{rows}</table></div>
</section>''')
    js = "function show(i){document.querySelectorAll('.pane').forEach((p,j)=>p.style.display=j==i?'block':'none');document.querySelectorAll('.tabs button').forEach((b,j)=>b.className=j==i?'on':'')}"
    return (f'<!DOCTYPE html><html><head><meta charset="utf-8"><title>{html.escape(title)}</title><style>{CSS}</style></head><body>'
            f'<header><h1>{html.escape(title)}</h1><span class="tag">{html.escape(result.get("term", ""))}</span>'
            f'<span class="sub">{html.escape(result.get("source_label", ""))}</span></header><main>'
            f'<div class="tabs">{"".join(tabs)}</div>{"".join(panes)}</main><script>{js}</script></body></html>')


def _heat(v, lo, hi):
    t = 0 if hi == lo else max(0, min(1, (v - lo) / (hi - lo)))
    # green (light) -> maize (medium) -> red (heavy)
    stops = [(0.0, (46, 160, 90)), (0.5, (240, 190, 20)), (1.0, (210, 60, 60))]
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        if t <= t1:
            f = (t - t0) / (t1 - t0)
            return "rgb({},{},{})".format(*(int(a + (b - a) * f) for a, b in zip(c0, c1)))
    return "rgb(210,60,60)"


def heatmap_html(plan: dict, title: str = "4 year plan") -> str:
    hm = plan["heatmap"]
    lo, hi = 3.0, 5.5   # fixed scale so colors mean the same thing every time
    rows = ""
    for pt in plan["terms"]:
        h = next(x for x in hm if x["term"] == pt["term"])
        chips = "".join(f'<span title="predicted {html.escape(str(c["predicted_grade"]))}">{c["course"]}</span>' for c in pt["courses"])
        rows += (f'<div class="term" style="border-left:10px solid {_heat(h["workload_index"] or lo, lo, hi)}">'
                 f'<div><b>{pt["name"]}</b>{"<br><small>abroad</small>" if pt["abroad"] else ""}</div>'
                 f'<div class="chips">{chips or "<small>off</small>"}</div>'
                 f'<div><b>{h["workload_index"]}</b><br><small>workload</small></div>'
                 f'<div><b>{h["predicted_gpa"] or "-"}</b><br><small>pred. GPA</small></div></div>')
    st = plan["application_story"]
    notes = "".join(f"<li>{html.escape(n)}</li>" for n in st["notes"])
    unmet = "".join(f'<div class="warn">{html.escape(u)}</div>' for u in plan["unmet_requirements"])
    g = plan["gpa_projection"]
    return (f'<!DOCTYPE html><html><head><meta charset="utf-8"><title>{html.escape(title)}</title><style>{CSS}</style></head><body>'
            f'<header><h1>{html.escape(title)}</h1><span class="tag">story {st["score"]}/100</span></header><main>'
            f'<div class="row"><div class="heat">{rows}<small>Left bar: green light term, maize normal, red heavy (workload index 3.0 to 5.5).</small></div><div class="card"><div class="k">'
            f'<div><div class="v">{g["cumulative_after"]}</div><div class="l">projected cumulative GPA</div></div>'
            f'<div><div class="v">{g["bcpm_after"]}</div><div class="l">projected BCPM</div></div>'
            f'<div><div class="v">{"✓" if plan["mcat"]["ready"] else "✗"}</div><div class="l">MCAT prereqs by {plan["mcat"]["term"]}</div></div>'
            f'<div><div class="v">{plan["heaviest_term"]}</div><div class="l">heaviest term</div></div></div>'
            f'<h3>Application story</h3><ul>{notes}</ul><small>{html.escape(st["disclaimer"])}</small>{unmet}</div></div>'
            f'</main></body></html>')


def write_view(name: str, content: str, open_browser: bool = False) -> str:
    d = user_home() / "views"
    d.mkdir(exist_ok=True)
    p = d / f"{name}.html"
    p.write_text(content)
    if open_browser:
        import webbrowser
        webbrowser.open(p.as_uri())
    return str(p)
