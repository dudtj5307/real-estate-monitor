"""GitHub Pages용 정적 HTML 대시보드 생성.

외부 CDN 없이 단일 파일로 완결된다 (오프라인/CSP 환경에서도 동작).
"""

from __future__ import annotations

import html
from datetime import datetime

from .naver import Article
from .report import TradeSection, fmt_delta, fmt_price

WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]

CSS = """
*,*::before,*::after{box-sizing:border-box}
:root{
  --bg:#f6f7f9; --card:#fff; --fg:#1a1d21; --muted:#6b7280; --line:#e3e6ea;
  --accent:#2563eb; --new:#0f9d58; --new-bg:#e6f4ea;
  --up:#d93025; --up-bg:#fce8e6; --down:#1a73e8; --down-bg:#e8f0fe;
  --chip:#eef1f5;
}
@media (prefers-color-scheme:dark){
  :root{
    --bg:#14171a; --card:#1c2024; --fg:#e6e8ea; --muted:#9aa3ad; --line:#2c3238;
    --accent:#7aa7ff; --new:#5fd08a; --new-bg:#12351f;
    --up:#ff8a80; --up-bg:#3a1c19; --down:#8ab4f8; --down-bg:#16263f;
    --chip:#252b31;
  }
}
:root[data-theme="dark"]{
  --bg:#14171a; --card:#1c2024; --fg:#e6e8ea; --muted:#9aa3ad; --line:#2c3238;
  --accent:#7aa7ff; --new:#5fd08a; --new-bg:#12351f;
  --up:#ff8a80; --up-bg:#3a1c19; --down:#8ab4f8; --down-bg:#16263f;
  --chip:#252b31;
}
:root[data-theme="light"]{
  --bg:#f6f7f9; --card:#fff; --fg:#1a1d21; --muted:#6b7280; --line:#e3e6ea;
  --accent:#2563eb; --new:#0f9d58; --new-bg:#e6f4ea;
  --up:#d93025; --up-bg:#fce8e6; --down:#1a73e8; --down-bg:#e8f0fe;
  --chip:#eef1f5;
}
body{
  margin:0;padding:24px 16px 64px;background:var(--bg);color:var(--fg);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Malgun Gothic",
    "Apple SD Gothic Neo",Roboto,sans-serif;
  font-size:15px;line-height:1.55;-webkit-text-size-adjust:100%;
}
.wrap{max-width:1180px;margin:0 auto}
h1{font-size:1.5rem;margin:0 0 4px;letter-spacing:-.02em}
.sub{color:var(--muted);font-size:.875rem;margin-bottom:28px}
h2{font-size:1.15rem;margin:0;letter-spacing:-.01em}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;
  padding:20px;margin-bottom:22px}
.chead{display:flex;flex-wrap:wrap;gap:10px;align-items:baseline;
  justify-content:space-between;margin-bottom:16px}
.stats{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:16px}
.stat{background:var(--chip);border-radius:10px;padding:10px 14px;min-width:132px}
.stat .k{font-size:.75rem;color:var(--muted)}
.stat .v{font-size:1.05rem;font-weight:650;letter-spacing:-.01em}
.filters{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:14px}
.filters button{font:inherit;font-size:.8125rem;padding:5px 12px;cursor:pointer;
  border:1px solid var(--line);background:var(--card);color:var(--muted);border-radius:999px}
.filters button[aria-pressed="true"]{background:var(--accent);border-color:var(--accent);
  color:#fff;font-weight:600}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{border-collapse:collapse;width:100%;min-width:760px;font-size:.875rem}
th,td{padding:9px 10px;text-align:left;border-bottom:1px solid var(--line);
  white-space:nowrap;vertical-align:top}
th{font-size:.75rem;color:var(--muted);font-weight:600;cursor:pointer;
  user-select:none;position:sticky;top:0;background:var(--card)}
th:hover{color:var(--fg)}
th::after{content:"";opacity:.45;font-size:.7em}
th[data-dir="asc"]::after{content:" ▲"}
th[data-dir="desc"]::after{content:" ▼"}
td.num{text-align:right;font-variant-numeric:tabular-nums}
td.feat{white-space:normal;min-width:220px;color:var(--muted);font-size:.8125rem}
tr.hidden{display:none}
.tag{display:inline-block;font-size:.7rem;font-weight:700;padding:2px 7px;
  border-radius:6px;letter-spacing:.02em}
.tag.new{background:var(--new-bg);color:var(--new)}
.tag.up{background:var(--up-bg);color:var(--up)}
.tag.down{background:var(--down-bg);color:var(--down)}
.price{font-weight:650;font-variant-numeric:tabular-nums}
.old{color:var(--muted);text-decoration:line-through;font-size:.8em;margin-right:4px}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
.gone{margin-top:12px;font-size:.8125rem;color:var(--muted)}
.empty{color:var(--muted);padding:16px 0}
footer{color:var(--muted);font-size:.8125rem;text-align:center;margin-top:32px}
#theme{position:fixed;top:14px;right:14px;font:inherit;font-size:1rem;
  background:var(--card);color:var(--fg);border:1px solid var(--line);
  border-radius:999px;width:38px;height:38px;cursor:pointer;line-height:1}
@media(max-width:640px){body{padding:16px 10px 48px}.card{padding:14px}}
"""

JS = """
document.querySelectorAll('table').forEach(function(table){
  table.querySelectorAll('th').forEach(function(th,idx){
    th.addEventListener('click',function(){
      var dir = th.dataset.dir === 'asc' ? 'desc' : 'asc';
      table.querySelectorAll('th').forEach(function(o){delete o.dataset.dir});
      th.dataset.dir = dir;
      var body = table.tBodies[0];
      var rows = Array.prototype.slice.call(body.rows);
      rows.sort(function(a,b){
        var x = a.cells[idx].dataset.sort, y = b.cells[idx].dataset.sort;
        var nx = parseFloat(x), ny = parseFloat(y);
        var r = (!isNaN(nx) && !isNaN(ny)) ? nx-ny : String(x).localeCompare(String(y),'ko');
        return dir === 'asc' ? r : -r;
      });
      rows.forEach(function(r){body.appendChild(r)});
    });
  });
});

document.querySelectorAll('.filters').forEach(function(bar){
  var table = document.getElementById(bar.dataset.target);
  bar.addEventListener('click', function(ev){
    var btn = ev.target.closest('button');
    if(!btn) return;
    bar.querySelectorAll('button').forEach(function(b){
      b.setAttribute('aria-pressed', String(b === btn));
    });
    var key = btn.dataset.filter;
    Array.prototype.forEach.call(table.tBodies[0].rows, function(row){
      var show = key === 'all'
        || row.dataset.group === key
        || (key === 'changed' && row.dataset.state !== '');
      row.classList.toggle('hidden', !show);
    });
  });
});

var root = document.documentElement;
var btn = document.getElementById('theme');
var saved = null;
try { saved = localStorage.getItem('theme'); } catch(e) {}
if(saved) root.dataset.theme = saved;
btn.addEventListener('click', function(){
  var dark = root.dataset.theme
    ? root.dataset.theme === 'dark'
    : matchMedia('(prefers-color-scheme:dark)').matches;
  root.dataset.theme = dark ? 'light' : 'dark';
  try { localStorage.setItem('theme', root.dataset.theme); } catch(e) {}
});
"""


def _esc(s: str) -> str:
    return html.escape(s or "", quote=True)


def _floor_sort(floor: str) -> float:
    """'10/20' -> 10, '중/20' -> 근사값. 정렬용."""
    head = (floor or "").split("/")[0]
    approx = {"저": 3.0, "중": 8.0, "고": 14.0}
    if head in approx:
        return approx[head]
    try:
        return float(head)
    except ValueError:
        return -1.0


def _row(article: Article, state: str, old_price: int) -> str:
    if state == "new":
        tag = '<span class="tag new">신규</span>'
    elif state == "changed":
        up = article.price > old_price
        cls = "up" if up else "down"
        tag = f'<span class="tag {cls}">{fmt_delta(article.price - old_price)}</span>'
    else:
        tag = ""

    if state == "changed":
        price_cell = (
            f'<span class="old">{fmt_price(old_price)}</span>'
            f'<span class="price">{fmt_price(article.price)}</span>'
        )
    else:
        price_cell = f'<span class="price">{fmt_price(article.price)}</span>'
    if article.rent:
        price_cell += f' / {fmt_price(article.rent)}'

    link = f'<a href="{article.url}" target="_blank" rel="noopener">보기</a>'

    return (
        f'<tr data-group="{article.pyeong_group}" data-state="{state}">'
        f'<td data-sort="{0 if state == "new" else 1 if state == "changed" else 2}">{tag}</td>'
        f'<td data-sort="{_esc(article.dong)}">{_esc(article.dong)}</td>'
        f'<td class="num" data-sort="{_floor_sort(article.floor)}">{_esc(article.floor)}</td>'
        f'<td class="num" data-sort="{article.pyeong:.1f}">{article.pyeong:.0f}평</td>'
        f'<td class="num" data-sort="{article.exclusive_sqm}">{article.exclusive_sqm:g}㎡</td>'
        f'<td class="num" data-sort="{article.price}">{price_cell}</td>'
        f'<td data-sort="{_esc(article.direction)}">{_esc(article.direction)}</td>'
        f'<td data-sort="{_esc(article.confirm_date)}">{_esc(article.confirm_date)}</td>'
        f'<td class="num" data-sort="{article.realtor_count}">{article.realtor_count}</td>'
        f'<td class="feat" data-sort="{_esc(article.feature)}">{_esc(article.feature)}</td>'
        f'<td data-sort="">{link}</td>'
        f'</tr>'
    )


def _stats(articles: list[Article]) -> str:
    groups: dict[int, list[Article]] = {}
    for a in articles:
        groups.setdefault(a.pyeong_group, []).append(a)

    cells = [f'<div class="stat"><div class="k">전체</div>'
             f'<div class="v">{len(articles)}건</div></div>']
    for group in sorted(groups):
        prices = [a.price for a in groups[group] if a.price > 0]
        span = f"{fmt_price(min(prices))}~{fmt_price(max(prices))}" if prices else "-"
        cells.append(
            f'<div class="stat"><div class="k">{group}평대 · {len(groups[group])}건</div>'
            f'<div class="v">{span}</div></div>'
        )
    return f'<div class="stats">{"".join(cells)}</div>'


def _section(complex_name: str, section: TradeSection, table_id: str) -> str:
    articles = sorted(section.articles, key=lambda a: (a.pyeong_group, a.price))
    new_ids = {a.article_number for a in section.diff.new}
    changed = {c.article.article_number: c.old_price for c in section.diff.changed}

    if not articles:
        return (f'<div class="card"><div class="chead"><h2>{_esc(complex_name)}</h2>'
                f'<span class="sub">{_esc(section.trade_type)}</span></div>'
                f'<p class="empty">조건에 맞는 매물이 없습니다.</p></div>')

    rows = []
    for a in articles:
        if a.article_number in new_ids:
            rows.append(_row(a, "new", 0))
        elif a.article_number in changed:
            rows.append(_row(a, "changed", changed[a.article_number]))
        else:
            rows.append(_row(a, "", 0))

    groups = sorted({a.pyeong_group for a in articles})
    buttons = ['<button data-filter="all" aria-pressed="true">전체</button>']
    buttons += [f'<button data-filter="{g}" aria-pressed="false">{g}평대</button>'
                for g in groups]
    buttons.append('<button data-filter="changed" aria-pressed="false">신규·변동만</button>')

    gone = ""
    if section.diff.gone:
        names = ", ".join(
            f"{_esc(a.dong)}동 {_esc(a.floor)}층 {fmt_price(a.price)}"
            for a in section.diff.gone[:8]
        )
        more = f" 외 {len(section.diff.gone) - 8}건" if len(section.diff.gone) > 8 else ""
        gone = f'<p class="gone">❌ 소진 {len(section.diff.gone)}건 — {names}{more}</p>'

    headers = ["", "동", "층", "평형", "전용", "가격", "방향", "확인일", "중개사", "특징", ""]

    return f"""<div class="card">
<div class="chead"><h2>{_esc(complex_name)}</h2><span class="sub">💰 {_esc(section.trade_type)}</span></div>
{_stats(articles)}
<div class="filters" data-target="{table_id}">{"".join(buttons)}</div>
<div class="scroll"><table id="{table_id}">
<thead><tr>{"".join(f"<th>{h}</th>" for h in headers)}</tr></thead>
<tbody>{"".join(rows)}</tbody>
</table></div>
{gone}
</div>"""


def build(entries: list[tuple[str, list[TradeSection]]],
          now: datetime | None = None) -> str:
    """entries: [(단지명, [TradeSection, ...]), ...] → 완결된 HTML 문서."""
    now = now or datetime.now()
    stamp = f"{now:%Y-%m-%d %H:%M} ({WEEKDAYS[now.weekday()]})"

    cards = []
    for i, (name, sections) in enumerate(entries):
        for j, section in enumerate(sections):
            cards.append(_section(name, section, f"t{i}_{j}"))

    total = sum(len(s.articles) for _, sections in entries for s in sections)
    new_total = sum(len(s.diff.new) for _, sections in entries for s in sections)

    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>부동산 모니터</title>
<style>{CSS}</style>
</head>
<body>
<button id="theme" type="button" aria-label="테마 전환">◐</button>
<div class="wrap">
<h1>🏠 부동산 모니터</h1>
<p class="sub">{stamp} 기준 · 전체 {total}건 · 신규 {new_total}건</p>
{"".join(cards)}
<footer>네이버 부동산 매물 정보를 하루 1회 수집합니다. 실제 거래 전 반드시 원문을 확인하세요.</footer>
</div>
<script>{JS}</script>
</body>
</html>"""
