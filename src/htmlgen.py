"""GitHub Pages용 정적 HTML 대시보드 생성.

수집은 넓게 하고, 거래유형·평형대 선택은 이 페이지의 UI에서 한다.
외부 CDN 없이 단일 파일로 완결된다 (오프라인/CSP 환경에서도 동작).
"""

from __future__ import annotations

import html
import json
from datetime import datetime

from .naver import Article
from .report import TradeSection, basis, fmt_delta, fmt_price
from .state import KST

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
.sub{color:var(--muted);font-size:.875rem;margin:0 0 28px}
h2{font-size:1.15rem;margin:0;letter-spacing:-.01em}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;
  padding:20px;margin-bottom:22px}
/* 단지 카드는 <details> — 제목줄(summary)을 눌러 접었다 폈다 한다 */
.chead{display:flex;flex-wrap:wrap;gap:10px;align-items:baseline;
  justify-content:space-between;margin-bottom:14px;
  cursor:pointer;list-style:none;user-select:none}
.chead::-webkit-details-marker{display:none}
.chead:hover h2{color:var(--accent)}
.chead h2::before{content:"▸";display:inline-block;width:1em;margin-right:2px;
  color:var(--muted);font-size:.9em}
.card[open] > .chead h2::before{content:"▾"}
.card:not([open]) > .chead{margin-bottom:0}
.csum{color:var(--muted);font-size:.8125rem;font-variant-numeric:tabular-nums}
.foldall{font:inherit;font-size:.8125rem;padding:3px 11px;border-radius:999px;
  background:var(--chip);border:1px solid var(--line);color:var(--accent);
  font-weight:600;cursor:pointer;margin-left:8px}
.foldall:hover{border-color:var(--accent)}
.basis{margin:0 0 12px;font-size:.8125rem;color:var(--muted)}
.card:not([open]) > .basis{display:none}
.controls{display:flex;flex-direction:column;gap:8px;margin-bottom:14px}
.row{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.row .lbl{font-size:.75rem;color:var(--muted);min-width:52px}
.row button{font:inherit;font-size:.8125rem;padding:5px 13px;cursor:pointer;
  border:1px solid var(--line);background:var(--card);color:var(--muted);
  border-radius:999px;transition:none}
.row button[aria-pressed="true"]{background:var(--accent);border-color:var(--accent);
  color:#fff;font-weight:600}
.row button:disabled{opacity:.38;cursor:not-allowed}
.row input[type="number"]{font:inherit;font-size:.8125rem;width:74px;padding:5px 8px;
  border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--fg);
  text-align:right;font-variant-numeric:tabular-nums}
.row input[type="number"]:focus{outline:2px solid var(--accent);outline-offset:-1px}
.row .unit{font-size:.8125rem;color:var(--muted)}
.row .tilde{color:var(--muted)}
.stats{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:14px}
.stat{background:var(--chip);border-radius:10px;padding:10px 14px;min-width:126px}
.stat .k{font-size:.75rem;color:var(--muted)}
.stat .v{font-size:1.05rem;font-weight:650;letter-spacing:-.01em}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{border-collapse:collapse;width:100%;min-width:760px;font-size:.875rem}
th,td{padding:9px 10px;text-align:left;border-bottom:1px solid var(--line);
  white-space:nowrap;vertical-align:top}
th{font-size:.75rem;color:var(--muted);font-weight:600;cursor:pointer;
  user-select:none;position:sticky;top:0;background:var(--card)}
th:hover{color:var(--fg)}
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
.refresh{display:inline-block;margin-left:8px;padding:3px 11px;border-radius:999px;
  background:var(--chip);border:1px solid var(--line);font-size:.8125rem;
  font-weight:600;color:var(--accent);white-space:nowrap}
.refresh:hover{text-decoration:none;border-color:var(--accent)}
/* 소요 시간 힌트. title 툴팁은 모바일에서 안 뜨므로 눈에 보이게도 적는다 */
.refresh small{font-weight:400;color:var(--muted);margin-left:5px}
.empty{color:var(--muted);padding:16px 0;font-size:.875rem}
footer{color:var(--muted);font-size:.8125rem;text-align:center;margin-top:32px}
#theme{position:fixed;top:14px;right:14px;font:inherit;font-size:1rem;
  background:var(--card);color:var(--fg);border:1px solid var(--line);
  border-radius:999px;width:38px;height:38px;cursor:pointer;line-height:1}
@media(max-width:640px){
  body{padding:16px 10px 48px}.card{padding:14px}
  .row .lbl{min-width:100%;margin-bottom:-2px}
}
"""

JS = """
function parseNum(v){ var n = parseFloat(v); return isNaN(n) ? null : n; }

function fmtPrice(manwon){
  if(!manwon || manwon <= 0) return '-';
  if(manwon >= 10000) return (manwon/10000).toFixed(2) + '억';
  return manwon.toLocaleString('ko-KR') + '만';
}

function setupCard(card){
  var table = card.querySelector('table');
  var rows  = Array.prototype.slice.call(table.tBodies[0].rows);
  var state = { trade: card.dataset.defaultTrade, group: 'all', onlyChanged: false };
  var goneData = JSON.parse(card.querySelector('.gone-data').textContent);
  var minInput = card.querySelector('[data-price-min]');
  var maxInput = card.querySelector('[data-price-max]');

  // 입력은 억 단위, 내부 비교는 만원 단위
  function bound(input){
    var v = parseNum(input.value);
    return v === null ? null : v * 10000;
  }

  function matches(row){
    if(row.dataset.trade !== state.trade) return false;
    if(state.group !== 'all' && row.dataset.group !== state.group) return false;
    if(state.onlyChanged && !row.dataset.state) return false;
    var lo = bound(minInput), hi = bound(maxInput);
    var p = parseNum(row.dataset.price);
    if(lo !== null && (p === null || p < lo)) return false;
    if(hi !== null && (p === null || p > hi)) return false;
    return true;
  }

  function render(){
    var shown = [];
    rows.forEach(function(row){
      var ok = matches(row);
      row.classList.toggle('hidden', !ok);
      if(ok) shown.push(row);
    });

    // 현재 거래유형 + 금액대 안에 실제로 존재하는 평형대만 활성화
    var groupsHere = {};
    var lo = bound(minInput), hi = bound(maxInput);
    rows.forEach(function(r){
      if(r.dataset.trade !== state.trade) return;
      var p = parseNum(r.dataset.price);
      if(lo !== null && (p === null || p < lo)) return;
      if(hi !== null && (p === null || p > hi)) return;
      groupsHere[r.dataset.group] = true;
    });
    card.querySelectorAll('[data-group-btn]').forEach(function(b){
      var g = b.dataset.groupBtn;
      b.disabled = (g !== 'all' && !groupsHere[g]);
      if(b.disabled && state.group === g){ state.group = 'all'; }
    });

    // 통계는 보이는 행에서 다시 계산
    var prices = shown.map(function(r){ return parseNum(r.dataset.price); })
                      .filter(function(v){ return v && v > 0; });
    var newN = shown.filter(function(r){ return r.dataset.state === 'new'; }).length;
    var chgN = shown.filter(function(r){ return r.dataset.state === 'changed'; }).length;
    var goneN = goneData[state.trade]
      ? goneData[state.trade].filter(function(g){
          return state.group === 'all' || String(g.group) === state.group;
        }).length
      : 0;

    var cells = [
      ['건수', shown.length + '건'],
      ['가격대', prices.length ? fmtPrice(Math.min.apply(null, prices)) + '~' +
                                 fmtPrice(Math.max.apply(null, prices)) : '-'],
      ['신규', newN + '건'],
      ['가격변동', chgN + '건'],
      ['소진', goneN + '건']
    ];
    card.querySelector('.stats').innerHTML = cells.map(function(c){
      return '<div class="stat"><div class="k">' + c[0] +
             '</div><div class="v">' + c[1] + '</div></div>';
    }).join('');

    // 접었을 때도 보이는 한 줄 요약
    var sum = state.trade + ' ' + shown.length + '건';
    if(prices.length) sum += ' · ' + fmtPrice(Math.min.apply(null, prices)) +
                             '~' + fmtPrice(Math.max.apply(null, prices));
    if(newN) sum += ' · 신규 ' + newN;
    if(chgN) sum += ' · 변동 ' + chgN;
    card.querySelector('.csum').textContent = sum;

    card.querySelector('.empty').style.display = shown.length ? 'none' : 'block';
    card.querySelector('.scroll').style.display = shown.length ? '' : 'none';

    var goneBox = card.querySelector('.gone');
    var list = (goneData[state.trade] || []).filter(function(g){
      return state.group === 'all' || String(g.group) === state.group;
    });
    if(list.length){
      goneBox.style.display = '';
      goneBox.textContent = '❌ 소진 ' + list.length + '건 — ' +
        list.slice(0,8).map(function(g){ return g.label; }).join(', ') +
        (list.length > 8 ? ' 외 ' + (list.length - 8) + '건' : '');
    } else {
      goneBox.style.display = 'none';
    }
  }

  card.querySelectorAll('[data-trade-btn]').forEach(function(btn){
    btn.addEventListener('click', function(){
      state.trade = btn.dataset.tradeBtn;
      card.querySelectorAll('[data-trade-btn]').forEach(function(b){
        b.setAttribute('aria-pressed', String(b === btn));
      });
      render();
    });
  });

  card.querySelectorAll('[data-group-btn]').forEach(function(btn){
    btn.addEventListener('click', function(){
      if(btn.disabled) return;
      state.group = btn.dataset.groupBtn;
      card.querySelectorAll('[data-group-btn]').forEach(function(b){
        b.setAttribute('aria-pressed', String(b === btn));
      });
      render();
    });
  });

  var chg = card.querySelector('[data-changed-btn]');
  chg.addEventListener('click', function(){
    state.onlyChanged = !state.onlyChanged;
    chg.setAttribute('aria-pressed', String(state.onlyChanged));
    render();
  });

  [minInput, maxInput].forEach(function(input){
    input.addEventListener('input', render);
  });
  card.querySelector('[data-price-reset]').addEventListener('click', function(){
    minInput.value = '';
    maxInput.value = '';
    render();
  });

  table.querySelectorAll('th').forEach(function(th, idx){
    th.addEventListener('click', function(){
      var dir = th.dataset.dir === 'asc' ? 'desc' : 'asc';
      table.querySelectorAll('th').forEach(function(o){ delete o.dataset.dir; });
      th.dataset.dir = dir;
      var body = table.tBodies[0];
      rows.sort(function(a, b){
        var x = a.cells[idx].dataset.sort, y = b.cells[idx].dataset.sort;
        var nx = parseNum(x), ny = parseNum(y);
        var r = (nx !== null && ny !== null) ? nx - ny
              : String(x).localeCompare(String(y), 'ko');
        return dir === 'asc' ? r : -r;
      });
      rows.forEach(function(r){ body.appendChild(r); });
    });
  });

  // 단지별 접힘 상태를 기억한다 (기본은 펼침)
  var foldKey = 'fold:' + card.dataset.key;
  try { if(localStorage.getItem(foldKey) === '1') card.open = false; } catch(e) {}
  card.addEventListener('toggle', function(){
    try { localStorage.setItem(foldKey, card.open ? '0' : '1'); } catch(e) {}
    syncFoldAll();
  });

  render();
}

var cards = Array.prototype.slice.call(document.querySelectorAll('.card'));
var foldAllBtn = document.getElementById('foldall');

function syncFoldAll(){
  if(!foldAllBtn) return;
  var anyOpen = cards.some(function(c){ return c.open; });
  foldAllBtn.textContent = anyOpen ? '전체 접기' : '전체 펼치기';
  foldAllBtn.dataset.action = anyOpen ? 'close' : 'open';
}

if(foldAllBtn){
  foldAllBtn.addEventListener('click', function(){
    var open = foldAllBtn.dataset.action === 'open';
    cards.forEach(function(c){ c.open = open; });
  });
}

cards.forEach(setupCard);
syncFoldAll();

var root = document.documentElement;
var themeBtn = document.getElementById('theme');
try { var saved = localStorage.getItem('theme'); if(saved) root.dataset.theme = saved; } catch(e) {}
themeBtn.addEventListener('click', function(){
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


def _row(article: Article, state: str, old_price: int, renumbered: bool = False) -> str:
    if state == "new":
        tag = '<span class="tag new">신규</span>'
    elif state == "changed":
        cls = "up" if article.price > old_price else "down"
        # 번호가 바뀐 재등록이면 링크도 바뀌었다는 뜻이라 짚어 준다
        note = " ↻" if renumbered else ""
        title = ' title="매물번호가 바뀐 재등록으로 추정됩니다"' if renumbered else ""
        tag = (f'<span class="tag {cls}"{title}>'
               f'{fmt_delta(article.price - old_price)}{note}</span>')
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
        price_cell += f" / {fmt_price(article.rent)}"

    order = 0 if state == "new" else 1 if state == "changed" else 2

    return (
        f'<tr data-trade="{_esc(article.trade_type)}" data-group="{article.pyeong_group}"'
        f' data-state="{state}" data-price="{article.price}">'
        f'<td data-sort="{order}">{tag}</td>'
        f'<td data-sort="{_esc(article.dong)}">{_esc(article.dong)}</td>'
        f'<td class="num" data-sort="{_floor_sort(article.floor)}">{_esc(article.floor)}</td>'
        f'<td class="num" data-sort="{article.pyeong:.1f}">{article.pyeong:.0f}평</td>'
        f'<td class="num" data-sort="{article.exclusive_sqm}">{article.exclusive_sqm:g}㎡</td>'
        f'<td class="num" data-sort="{article.price}">{price_cell}</td>'
        f'<td data-sort="{_esc(article.direction)}">{_esc(article.direction)}</td>'
        f'<td data-sort="{_esc(article.confirm_date)}">{_esc(article.confirm_date)}</td>'
        f'<td class="num" data-sort="{article.realtor_count}">{article.realtor_count}</td>'
        f'<td class="feat" data-sort="{_esc(article.feature)}">{_esc(article.feature)}</td>'
        f'<td data-sort="">'
        f'<a href="{article.url}" target="_blank" rel="noopener">보기</a></td>'
        f"</tr>"
    )


def _eok(manwon: int | None) -> str:
    """만원 → 억 단위 입력값 문자열. None이면 빈 문자열."""
    if manwon is None:
        return ""
    value = manwon / 10000
    return f"{value:g}"


def _card(complex_name: str, sections: list[TradeSection], index: int,
          price_focus: tuple[int | None, int | None] = (None, None)) -> str:
    trades = [s.trade_type for s in sections]
    rows: list[str] = []
    groups: set[int] = set()
    gone_data: dict[str, list[dict]] = {}

    for section in sections:
        new_ids = {a.article_number for a in section.diff.new}
        changed = {c.article.article_number: c for c in section.diff.changed}
        for a in sorted(section.articles, key=lambda x: (x.pyeong_group, x.price)):
            groups.add(a.pyeong_group)
            if a.article_number in new_ids:
                rows.append(_row(a, "new", 0))
            elif a.article_number in changed:
                c = changed[a.article_number]
                rows.append(_row(a, "changed", c.old_price, c.renumbered))
            else:
                rows.append(_row(a, "", 0))

        gone_data[section.trade_type] = [
            {
                "group": a.pyeong_group,
                "label": f"{a.dong}동 {a.floor}층 {fmt_price(a.price)}",
            }
            for a in section.diff.gone
        ]

    trade_btns = "".join(
        f'<button data-trade-btn="{_esc(t)}" aria-pressed="{"true" if i == 0 else "false"}">'
        f"{_esc(t)}</button>"
        for i, t in enumerate(trades)
    )
    group_btns = '<button data-group-btn="all" aria-pressed="true">전체</button>' + "".join(
        f'<button data-group-btn="{g}" aria-pressed="false">{g}평대</button>'
        for g in sorted(groups)
    )

    headers = ["", "동", "층", "평형", "전용", "가격", "방향", "확인일", "중개사", "특징", ""]
    default_trade = trades[0] if trades else ""

    # 신규·변동 배지가 '무엇과 비교한 결과'인지 밝힌다. 수집이 며칠 막히면
    # 어제가 아니라 며칠 전이 기준이 되므로 그냥 '전일 대비'라고 쓸 수 없다.
    bases = sorted({s.baseline_date for s in sections})
    basis_line = " · ".join(basis(b) for b in bases)

    return f"""<details class="card" data-default-trade="{_esc(default_trade)}"
 data-key="{_esc(complex_name)}" open>
<summary class="chead"><h2>{_esc(complex_name)}</h2><span class="csum"></span></summary>
<p class="basis">📅 {_esc(basis_line)}</p>
<div class="controls">
  <div class="row"><span class="lbl">거래유형</span>{trade_btns}</div>
  <div class="row"><span class="lbl">평형</span>{group_btns}</div>
  <div class="row"><span class="lbl">금액대</span>
    <input type="number" data-price-min step="0.1" min="0" inputmode="decimal"
           value="{_eok(price_focus[0])}" aria-label="최소 금액(억)">
    <span class="tilde">~</span>
    <input type="number" data-price-max step="0.1" min="0" inputmode="decimal"
           value="{_eok(price_focus[1])}" aria-label="최대 금액(억)">
    <span class="unit">억</span>
    <button data-price-reset>전체</button></div>
  <div class="row"><span class="lbl">보기</span>
    <button data-changed-btn aria-pressed="false">신규·변동만</button></div>
</div>
<div class="stats"></div>
<p class="empty" style="display:none">조건에 맞는 매물이 없습니다.</p>
<div class="scroll"><table id="t{index}">
<thead><tr>{"".join(f"<th>{h}</th>" for h in headers)}</tr></thead>
<tbody>{"".join(rows)}</tbody>
</table></div>
<p class="gone" style="display:none"></p>
<script type="application/json" class="gone-data">{json.dumps(gone_data, ensure_ascii=False)}</script>
</details>"""


def build(entries: list[tuple[str, list[TradeSection]]],
          now: datetime | None = None, repo: str = "",
          price_focus: tuple[int | None, int | None] = (None, None)) -> str:
    """entries: [(단지명, [TradeSection, ...]), ...] → 완결된 HTML 문서.

    repo 를 주면 "지금 갱신" 버튼이 그 저장소의 Actions 실행 화면으로 연결된다.
    정적 페이지는 네이버를 직접 호출할 수 없으므로(CORS + 세션 쿠키) 갱신을
    남에게 시켜야 하는데, 그 "남"이 Actions 에서 집 라즈베리파이로 바뀌었다.

    버튼 → refresh-request.yml 실행(아무 일도 하지 않는다) → 그 실행 기록을
    Pi 가 2분 안에 감지 → 수집 → docs/index.html 을 다시 커밋 → Pages 반영.
    그래서 눌러도 즉시 바뀌지 않는다 (DESIGN-PI.md §2.4 — 최대 약 7분).
    """
    # 러너는 UTC 라 KST 로 고정한다. 안 하면 '갱신 09:37' 이 '00:37' 로 찍힌다.
    now = now or datetime.now(KST)
    stamp = f"{now:%Y-%m-%d %H:%M} ({WEEKDAYS[now.weekday()]})"

    refresh = ""
    if repo:
        url = f"https://github.com/{repo}/actions/workflows/refresh-request.yml"
        # 누른 뒤 아무 일도 안 일어나는 것처럼 보이는 구간(폴링 2분 + 수집 3분 +
        # Pages 반영 2분)이 있다. 소요 시간을 버튼에 붙여 두지 않으면 고장으로 읽힌다.
        tip = ("Actions 의 [Run workflow] 를 눌러 갱신을 요청합니다. "
               "집 라즈베리파이가 수집해 다시 커밋하므로 반영까지 최대 약 7분 걸립니다.")
        refresh = (
            f'<a class="refresh" href="{_esc(url)}" target="_blank" rel="noopener" '
            f'title="{_esc(tip)}">🔄 지금 갱신<small>~7분</small></a>'
        )

    cards = [_card(name, sections, i, price_focus)
             for i, (name, sections) in enumerate(entries)]

    # 단지가 둘 이상일 때만 전체 접기/펼치기 버튼을 붙인다
    fold_all = ('<button id="foldall" class="foldall" type="button">전체 접기</button>'
                if len(entries) > 1 else "")

    total = sum(len(s.articles) for _, sections in entries for s in sections)
    new_total = sum(len(s.diff.new) for _, sections in entries for s in sections)
    trades = sorted({s.trade_type for _, sections in entries for s in sections})

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
<p class="sub">{stamp} 기준 · {" · ".join(trades)} 전체 {total}건 · 신규 {new_total}건 {refresh}{fold_all}</p>
{"".join(cards)}
<footer>네이버 부동산 매물 정보를 하루 1회 수집합니다. 실제 거래 전 반드시 원문을 확인하세요.</footer>
</div>
<script>{JS}</script>
</body>
</html>"""
