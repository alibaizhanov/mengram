"""`mengram local map` — one HTML page that shows what a memory folder holds.

Three guided views, in the order a person asks the questions:
  1. Who you are      — entities grouped by type, their facts and relations
  2. What happened    — episodes on a timeline, with outcomes
  3. What your agent learned — workflows as step chains with per-step records,
                        versions with the belief that broke, and the quarantine

The page is self-contained (no scripts or fonts fetched from anywhere), is
rendered from the same files `memfmt` reads, and sends nothing. It exists so
the folder is not a black box: after an import, this is the answer to
"what do you know about me?".
"""

from __future__ import annotations

import datetime as _dt
import json
from html import escape
from pathlib import Path

from .store import LocalStore

MAP_FILE = "memory-map.html"

_TYPE_ORDER = ["person", "organization", "company", "project", "product", "tool", "technology",
               "service", "place", "location", "concept", "event", "other"]


def map_path(store: LocalStore) -> Path:
    return Path(store.root) / MAP_FILE


def write_map(store: LocalStore, quarantine: list | None = None, out: Path | None = None,
              model: str = "") -> Path:
    """Render and write the page. Returns the path written."""
    path = Path(out) if out else map_path(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_map(store, quarantine or [], model=model), encoding="utf-8")
    return path


# ---- data ---------------------------------------------------------------------


def _reliability_word(p) -> str:
    return getattr(p, "reliability", None) or "untested"


def _record(success: int | None, fail: int | None) -> str:
    return f"{success or 0}✓/{fail or 0}✗"


def _group_entities(entities) -> list[tuple[str, list]]:
    groups: dict[str, list] = {}
    for e in entities:
        kind = (e.entity_type or "other").strip().lower()
        groups.setdefault(kind, []).append(e)
    order = {k: i for i, k in enumerate(_TYPE_ORDER)}
    keys = sorted(groups, key=lambda k: (order.get(k, len(order)), k))
    return [(k, sorted(groups[k], key=lambda e: -len(e.facts))) for k in keys]


def _episode_sort_key(ep) -> str:
    return ep.happened or ""


def summarise(store: LocalStore, quarantine: list) -> dict:
    """The numbers on the cards. Also what tests check."""
    m = store.memory
    runs = sum(p.success_count + p.fail_count for p in m.procedures)
    revisions = sum(len(p.evolution) for p in m.procedures)
    tested = [p for p in m.procedures if p.success_count + p.fail_count]
    return {
        "entities": len(m.entities),
        "facts": sum(len(e.facts) for e in m.entities),
        "relations": sum(len(e.relations) for e in m.entities),
        "episodes": len(m.episodes),
        "procedures": len(m.procedures),
        "tested": len(tested),
        "runs": runs,
        "revisions": revisions,
        "quarantined": len(quarantine),
        "failures_with_reason": sum(1 for p in m.procedures if p.last_failure),
    }


# ---- rendering ----------------------------------------------------------------


def _e(text) -> str:
    return escape(str(text if text is not None else ""), quote=True)


def _entity_card(e) -> str:
    facts = "".join(f"<li>{_e(f)}</li>" for f in e.facts[:6])
    more = f'<li class="more">+{len(e.facts) - 6} more</li>' if len(e.facts) > 6 else ""
    rels = "".join(
        f'<span class="chip" title="{_e(r.detail or "")}">{_e(r.type)} → {_e(r.target)}</span>'
        for r in e.relations[:8])
    know = "".join(f'<div class="know"><b>{_e(k.title or k.type)}</b> {_e((k.content or "")[:160])}</div>'
                   for k in e.knowledge[:2])
    search = _e(" ".join([e.name] + e.facts + [r.target for r in e.relations]).lower())
    return (f'<article class="card entity" data-search="{search}">'
            f'<h4>{_e(e.name)}</h4><ul>{facts}{more}</ul>'
            f'{("<div class=chips>" + rels + "</div>") if rels else ""}{know}</article>')


def _view_who(store: LocalStore) -> str:
    m = store.memory
    if not m.entities:
        return '<p class="empty">No entities yet. Import your Claude Code sessions or add a conversation.</p>'
    cols = []
    for kind, ents in _group_entities(m.entities):
        cards = "".join(_entity_card(e) for e in ents)
        cols.append(f'<section class="col"><h3>{_e(kind)} <small>{len(ents)}</small></h3>{cards}</section>')
    profile = f'<blockquote class="profile">{_e(m.profile.strip())}</blockquote>' if m.profile else ""
    return profile + '<div class="cols">' + "".join(cols) + "</div>"


def _view_happened(store: LocalStore) -> str:
    eps = sorted(store.memory.episodes, key=_episode_sort_key, reverse=True)
    if not eps:
        return '<p class="empty">No episodes yet — events and decisions land here as they are extracted.</p>'
    rows = []
    for ep in eps[:200]:
        val = (ep.valence or "").lower()
        badge = f'<span class="badge {_e(val)}">{_e(ep.outcome or val or "")}</span>' if (ep.outcome or val) else ""
        who = "".join(f'<span class="chip">{_e(p)}</span>' for p in ep.participants[:6])
        ctx = f'<div class="ctx">{_e(ep.context)}</div>' if ep.context else ""
        search = _e(" ".join([ep.summary, ep.context or "", ep.outcome or ""] + list(ep.participants)).lower())
        rows.append(f'<li class="ep" data-search="{search}"><time>{_e((ep.happened or "")[:10]) or "—"}</time>'
                    f'<div><p>{_e(ep.summary)}</p>{ctx}<div class="chips">{who}{badge}</div></div></li>')
    return '<ol class="timeline">' + "".join(rows) + "</ol>"


def _step_node(i: int, s) -> str:
    rec = _record(s.success_count, s.fail_count) if (s.success_count or s.fail_count) else ""
    tag = f'<span class="tag">{_e(rec)}</span>' if rec else '<span class="tag dim">untested</span>'
    detail = f'<div class="detail">{_e(s.detail)}</div>' if s.detail else ""
    return f'<li class="step"><span class="n">{i}</span><div><div class="act">{_e(s.action)}</div>{detail}{tag}</div></li>'


def _procedure_card(p) -> str:
    rel = _reliability_word(p)
    cls = "ok" if "reliable" in rel else ("mid" if "expected" in rel else "dim")
    steps = "".join(_step_node(i, s) for i, s in enumerate(p.steps, 1))
    trigger = f'<div class="trigger">When: {_e(p.trigger)}</div>' if p.trigger else ""
    pre = "".join(f'<span class="chip">{_e(x)}</span>' for x in p.preconditions[:6])
    fail = ""
    if p.last_failure:
        when = f" · {_e(p.last_failed[:10])}" if p.last_failed else ""
        fail = f'<div class="fail"><b>Last failure{when}:</b> {_e(p.last_failure)}</div>'
    evo = ""
    if p.evolution:
        items = "".join(
            f'<li>v{r.version_before} → v{r.version_after}'
            f'{(" · " + _e(r.date)) if r.date else ""}'
            f'{(" · " + _record(r.success_count, r.fail_count)) if (r.success_count or r.fail_count) else ""}'
            f': {_e(r.reason)}</li>' for r in p.evolution)
        evo = f'<details><summary>{len(p.evolution)} revision{"s" if len(p.evolution) != 1 else ""}</summary><ul class="evo">{items}</ul></details>'
    search = _e(" ".join([p.name, p.trigger or "", p.last_failure or ""] + [s.action for s in p.steps]).lower())
    return (f'<article class="card proc" data-search="{search}">'
            f'<header><h4>{_e(p.name)}</h4><span class="rel {cls}">v{p.version} · {_e(rel)} · {_record(p.success_count, p.fail_count)}</span></header>'
            f'{trigger}{("<div class=chips>" + pre + "</div>") if pre else ""}'
            f'<ol class="steps">{steps}</ol>{fail}{evo}</article>')


def _view_learned(store: LocalStore, quarantine: list) -> str:
    procs = sorted(store.memory.procedures, key=lambda p: -(p.success_count + p.fail_count))
    if not procs and not quarantine:
        return ('<p class="empty">No workflows yet. They are extracted from how you actually work, and each run '
                'you record with <code>mengram local feedback</code> changes the number.</p>')
    out = "".join(_procedure_card(p) for p in procs)
    if quarantine:
        items = []
        for q in quarantine:
            regs = "".join(f'<li>{_e(r.get("procedure") or r.get("name") or "")}: {_e(r.get("reason") or r.get("detail") or "")}</li>'
                           for r in (q.get("regressions") or []) if isinstance(r, dict))
            if not regs:
                regs = "".join(f"<li>{_e(r)}</li>" for r in (q.get("regressions") or []))
            steps = "".join(f'<li>{_e(s.get("action"))}</li>' for s in (q.get("proposed_steps") or []))
            items.append(
                f'<article class="card quarantine"><header><h4>{_e(q.get("procedure"))} v{_e(q.get("version"))}</h4>'
                f'<span class="rel bad">quarantined{(" · " + _e(q.get("date"))) if q.get("date") else ""}</span></header>'
                f'<div class="fail"><b>Why the fix was proposed:</b> {_e(q.get("reason"))}</div>'
                f'{("<div class=fail><b>Belief that broke:</b> " + _e(q.get("violated_assumption")) + "</div>") if q.get("violated_assumption") else ""}'
                f'<div class="fail"><b>Why it was not shipped — it would break:</b><ul class="evo">{regs}</ul></div>'
                f'<details><summary>Proposed steps</summary><ol class="evo">{steps}</ol></details></article>')
        out += ('<h3 class="qh">Quarantine <small>revisions the regression gate refused</small></h3>' + "".join(items))
    return out


_CSS = """
:root{--bg:#F4F3FA;--card:#fff;--el:#F3F0FB;--bd:#E9E5F4;--tx:#191524;--t2:#6B6580;--t3:#A29BB6;--ac:#7C3AED;--acd:rgba(124,58,237,.10);
--green:#059669;--amber:#D97706;--red:#DC2626;--blue:#2563EB}
@media (prefers-color-scheme:dark){:root{--bg:#0b0b14;--card:#12121f;--el:#171729;--bd:#23233a;--tx:#e8e8f0;--t2:#9898b0;--t3:#5a5a78;--ac:#a855f7;--acd:rgba(168,85,247,.14);
--green:#34d399;--amber:#fbbf24;--red:#f87171;--blue:#60a5fa}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:14px/1.5 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.wrap{max-width:1180px;margin:0 auto;padding:28px 24px 64px}
header.top{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;flex-wrap:wrap}
h1{margin:0;font-size:24px;letter-spacing:-.01em}h1 small{display:block;font-size:12px;color:var(--t2);font-weight:400;margin-top:4px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin:22px 0}
.stat{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:12px 14px}.stat b{display:block;font-size:22px;letter-spacing:-.02em}.stat span{color:var(--t2);font-size:12px}
nav.views{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:8px 0 18px}
nav.views button{background:var(--card);border:1px solid var(--bd);color:var(--tx);padding:8px 14px;border-radius:999px;cursor:pointer;font:inherit}
nav.views button.on{background:var(--ac);border-color:var(--ac);color:#fff}nav.views button.play{margin-left:auto;color:var(--t2)}
nav.views input{flex:1 1 200px;min-width:160px;padding:8px 12px;border:1px solid var(--bd);border-radius:999px;background:var(--card);color:var(--tx);font:inherit}
.view{display:none}.view.on{display:block}.note{color:var(--t2);margin:0 0 14px}
.cols{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px;align-items:start}
.col h3{margin:0 0 8px;font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--t2)}.col h3 small{color:var(--t3);font-weight:400;margin-left:6px}
.card{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:12px 14px;margin:0 0 10px}
.card h4{margin:0 0 6px;font-size:14px}.card ul{margin:0;padding-left:18px;color:var(--t2)}.card li.more{list-style:none;color:var(--t3);margin-left:-18px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}.chip{background:var(--el);border:1px solid var(--bd);border-radius:999px;padding:2px 9px;font-size:12px;color:var(--t2)}
.know{margin-top:8px;font-size:12px;color:var(--t2)}.know b{color:var(--tx)}
.profile{margin:0 0 16px;padding:12px 16px;border-left:3px solid var(--ac);background:var(--acd);border-radius:0 12px 12px 0;white-space:pre-wrap;color:var(--tx)}
.timeline{list-style:none;margin:0;padding:0;border-left:2px solid var(--bd)}.ep{display:grid;grid-template-columns:96px 1fr;gap:12px;padding:8px 0 14px 16px;position:relative}
.ep:before{content:"";position:absolute;left:-6px;top:15px;width:10px;height:10px;border-radius:50%;background:var(--ac)}
.ep time{color:var(--t2);font-size:12px;padding-top:2px}.ep p{margin:0}.ctx{color:var(--t2);font-size:12px;margin-top:4px}
.badge{border-radius:999px;padding:2px 9px;font-size:12px;background:var(--el);border:1px solid var(--bd)}.badge.positive,.badge.success{color:var(--green)}.badge.negative,.badge.failure{color:var(--red)}
.proc header{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap}.rel{font-size:12px;border-radius:999px;padding:2px 10px;background:var(--el);border:1px solid var(--bd)}
.rel.ok{color:var(--green)}.rel.mid{color:var(--amber)}.rel.dim{color:var(--t2)}.rel.bad{color:var(--red)}
.trigger{color:var(--t2);font-size:13px;margin:2px 0 6px}
.steps{list-style:none;display:flex;gap:0;overflow-x:auto;padding:10px 0 4px;margin:6px 0 0}
.step{display:flex;gap:8px;align-items:flex-start;min-width:180px;max-width:240px;padding:0 18px 0 0;position:relative;flex:0 0 auto}
.step+.step:before{content:"→";position:absolute;left:-14px;top:2px;color:var(--t3)}
.step .n{flex:0 0 22px;height:22px;border-radius:50%;background:var(--acd);color:var(--ac);font-size:12px;display:grid;place-items:center;font-weight:600}
.step .act{font-size:13px}.step .detail{font-size:12px;color:var(--t2)}.tag{display:inline-block;margin-top:4px;font-size:11px;padding:1px 8px;border-radius:999px;background:var(--el);border:1px solid var(--bd);color:var(--green)}.tag.dim{color:var(--t3)}
.fail{margin-top:8px;font-size:13px;color:var(--t2)}.fail b{color:var(--tx)}
details{margin-top:8px;font-size:13px;color:var(--t2)}summary{cursor:pointer}.evo{margin:6px 0 0;padding-left:18px}
.qh{margin:22px 0 10px;font-size:14px}.qh small{color:var(--t2);font-weight:400;margin-left:8px}.quarantine{border-color:rgba(220,38,38,.35)}
.empty{color:var(--t2);background:var(--card);border:1px dashed var(--bd);border-radius:12px;padding:18px}
footer{margin-top:36px;color:var(--t3);font-size:12px}[hidden]{display:none!important}
"""

_JS = """
(function(){
  var views=[].slice.call(document.querySelectorAll('.view')),btns=[].slice.call(document.querySelectorAll('nav.views button[data-view]'));
  var cur=0,timer=null;
  function show(i){cur=(i+views.length)%views.length;views.forEach(function(v,j){v.classList.toggle('on',j===cur)});btns.forEach(function(b,j){b.classList.toggle('on',j===cur)});location.hash='view='+views[cur].id;}
  btns.forEach(function(b,i){b.addEventListener('click',function(){stop();show(i)})});
  var play=document.getElementById('play');function stop(){if(timer){clearInterval(timer);timer=null;play.textContent='▸ Play story'}}
  play.addEventListener('click',function(){if(timer){stop();return}play.textContent='‖ Stop';timer=setInterval(function(){show(cur+1)},6000)});
  document.addEventListener('keydown',function(e){if(e.key==='ArrowRight'){stop();show(cur+1)}if(e.key==='ArrowLeft'){stop();show(cur-1)}});
  var q=document.getElementById('q');q.addEventListener('input',function(){var s=q.value.trim().toLowerCase();
    [].slice.call(document.querySelectorAll('[data-search]')).forEach(function(el){el.hidden=!!s&&el.getAttribute('data-search').indexOf(s)<0})});
  var m=/view=([a-z-]+)/.exec(location.hash);var start=m?views.findIndex(function(v){return v.id===m[1]}):0;show(start<0?0:start);
})();
"""


def render_map(store: LocalStore, quarantine: list, model: str = "") -> str:
    s = summarise(store, quarantine)
    today = _dt.date.today().isoformat()
    folder = _e(Path(store.root).resolve())
    stats = [
        (s["entities"], "entities"), (s["facts"], "facts"), (s["episodes"], "episodes"),
        (s["procedures"], "workflows"), (s["runs"], "runs recorded"), (s["revisions"], "revisions"),
        (s["quarantined"], "quarantined"),
    ]
    stat_html = "".join(f'<div class="stat"><b>{n}</b><span>{_e(label)}</span></div>' for n, label in stats)
    views = [
        ("who", "01 · Who you are", "Entities grouped by type, with the facts and relations the extractor kept.", _view_who(store)),
        ("happened", "02 · What happened", "Events and decisions, newest first, with their outcome.", _view_happened(store)),
        ("learned", "03 · What your agent learned",
         "Workflows as step chains. The record on a step is per-step; a revision keeps the counts of the steps it left alone.",
         _view_learned(store, quarantine)),
    ]
    nav = "".join(f'<button data-view="{vid}">{_e(label)}</button>' for vid, label, _, _ in views)
    body = "".join(f'<section class="view" id="{vid}"><p class="note">{_e(note)}</p>{html}</section>'
                   for vid, _, note, html in views)
    model_line = f" · model: {_e(model)}" if model else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Memory map — {folder}</title><style>{_CSS}</style></head>
<body><div class="wrap">
<header class="top"><h1>Your memory, as your agent sees it<small class="mono">{folder}{model_line} · {today}</small></h1></header>
<div class="stats">{stat_html}</div>
<nav class="views">{nav}<input id="q" type="search" placeholder="filter…"><button id="play" class="play">▸ Play story</button></nav>
{body}
<footer>Rendered by <code>mengram local map</code> from the Markdown files in this folder. Nothing on this page was uploaded anywhere.
Re-run after an import or a <code>feedback</code> to refresh. Summary: <span class="mono">{_e(json.dumps(s))}</span></footer>
</div><script>{_JS}</script></body></html>
"""
