"""TIM Executive Dashboard (financial) — /tim-exec.

Monthly P&L by business unit (Consolidados, Importación de Muebles) from
Rogelio's monthly close, in USD, vs budget (BP) and vs prior year. Same design
language as the /audit executive view (exec_template.py): rust palette, KPI
tiles, segmented controls, EN/ES.

Data is embedded (one close at a time). To publish a new month, replace CLOSE
below with the new statements; test_tim_exec.py checks every line ties out.

Who may open it: only the addresses in TIM_EXEC_USERS (comma separated, set in
Render). Without that setting it is DEFAULT_USERS below (Bill, 2026-10-15).
Anyone else who is signed in to the library gets "not found", and the home page
tile is cut out for them (same pattern as the Commission Report).
"""
import functools
import json
import os
import re

from flask import Blueprint, abort, redirect, request, session, url_for

tim_exec_bp = Blueprint("tim_exec", __name__)

# Line order and type. kind: "rev" (more is better) or "cost" (less is better).
# total=True rows are bold subtotals.
LINES = [
    ("direct_rev",   "Direct revenue",         "Ingresos directos",          "rev",  False),
    ("other_rev",    "Other-company revenue",  "Ingresos otras empresas",    "rev",  False),
    ("total_rev",    "Total revenue",          "Ingresos totales",           "rev",  True),
    ("direct_cost",  "Direct cost",            "Costo directo",              "cost", False),
    ("op_exp",       "Operating expense",      "Gastos de operación",        "cost", False),
    ("admin_fac",    "Admin facilities",       "Instalaciones admin.",       "cost", False),
    ("other_cost",   "Other-company cost",     "Costo otras empresas",       "cost", False),
    ("total_cos",    "Total cost of sales",    "Costo de ventas total",      "cost", True),
    ("direct_margin","Direct margin",          "Margen directo",             "rev",  True),
    ("admin_exp",    "Admin expense",          "Gastos de administración",   "cost", False),
    ("selling_exp",  "Selling expense",        "Gastos de venta",            "cost", False),
    ("other_exp",    "Other-company expense",  "Gastos otras empresas",      "cost", False),
    ("corp_exp",     "Corporate expense",      "Gastos corporativos",        "cost", False),
    ("total_exp",    "Total expense",          "Gastos totales",             "cost", True),
    ("op_profit",    "Operating profit",       "Utilidad de operación",      "rev",  True),
    ("cif",          "Financial result (CIF)", "Resultado financiero (CIF)", "cost", False),
    ("taxes",        "Taxes",                  "Impuestos",                  "cost", False),
    ("net",          "Net profit",             "Utilidad neta",              "rev",  True),
]
KEYS = [k for k, *_ in LINES]

# Each line: [actual 2026, BP 2026, 2025] in USD.
CLOSE = {
    "period_en": "September 2026",
    "period_es": "Septiembre 2026",
    "source": "Rogelio Franco (Finance) monthly close, 9 Oct 2026",
    "fx_note_en": "USD at Finance's blended rate (MXN ≈ 18.1 at Sept month-end).",
    "fx_note_es": "USD al tipo combinado de Finanzas (MXN ≈ 18.1 al cierre de septiembre).",
    "units": {
        "cons": {
            "month": {
                "direct_rev": [244507, 237748, 190633], "other_rev": [231, 0, 231],
                "total_rev": [244738, 237748, 190864],
                "direct_cost": [77092, 146849, 57170], "op_exp": [5214, 10137, 1709],
                "admin_fac": [-77, 0, 491], "other_cost": [81127, 0, 51130],
                "total_cos": [163356, 156986, 110500],
                "direct_margin": [81382, 80762, 80363],
                "admin_exp": [10821, 11006, 13381], "selling_exp": [6503, 21765, 4867],
                "other_exp": [19310, 0, 15534], "corp_exp": [8898, 8712, 7511],
                "total_exp": [45532, 41482, 41292],
                "op_profit": [35850, 39280, 39071],
                "cif": [-473, 1355, -138], "taxes": [0, 0, 11132],
                "net": [36323, 37925, 28077],
            },
            "ytd": {
                "direct_rev": [1689494, 1650887, 1188761], "other_rev": [131675, 0, 8323],
                "total_rev": [1821169, 1650887, 1197084],
                "direct_cost": [435108, 1013860, 342001], "op_exp": [45793, 90007, 17364],
                "admin_fac": [17829, 0, 11429], "other_cost": [627258, 0, 313844],
                "total_cos": [1125988, 1103867, 684638],
                "direct_margin": [695181, 547020, 512446],
                "admin_exp": [99972, 99907, 102264], "selling_exp": [33472, 196637, 36806],
                "other_exp": [177742, 0, 131995], "corp_exp": [74972, 78259, 68361],
                "total_exp": [386158, 374803, 339426],
                "op_profit": [309023, 172217, 173021],
                "cif": [-1172, 5214, 3328], "taxes": [14220, 0, 11132],
                "net": [295975, 167003, 158561],
            },
        },
        "imp": {
            "month": {
                "direct_rev": [6426, 37150, 19570], "other_rev": [331, 0, 3446],
                "total_rev": [6757, 37150, 23016],
                "direct_cost": [227, 26005, 6057], "op_exp": [3333, 8225, 293],
                "admin_fac": [20, 0, 3564], "other_cost": [10645, 0, 9483],
                "total_cos": [14225, 34230, 19397],
                "direct_margin": [-7468, 2920, 3620],
                "admin_exp": [14, 0, 6511], "selling_exp": [1919, 4752, 909],
                "other_exp": [717, 0, 962], "corp_exp": [4468, 4673, 3771],
                "total_exp": [7118, 9425, 12154],
                "op_profit": [-14586, -6505, -8534],
                "cif": [79, 150, -240], "taxes": [0, 0, 0],
                "net": [-14665, -6655, -8294],
            },
            "ytd": {
                "direct_rev": [111708, 257190, 203981], "other_rev": [1485, 0, 27266],
                "total_rev": [113192, 257190, 231247],
                "direct_cost": [38531, 180033, 41418], "op_exp": [27312, 71526, 6841],
                "admin_fac": [18334, 0, 22563], "other_cost": [52095, 0, 80961],
                "total_cos": [136272, 251559, 151781],
                "direct_margin": [-23080, 5631, 79466],
                "admin_exp": [240, 552, 62028], "selling_exp": [10357, 24706, 18663],
                "other_exp": [12200, 0, 22681], "corp_exp": [37645, 41980, 34325],
                "total_exp": [60443, 67238, 137698],
                "op_profit": [-83522, -61607, -58232],
                "cif": [868, 1141, -255], "taxes": [14220, 0, 0],
                "net": [-98610, -62748, -57978],
            },
        },
    },
}


def with_total(close=CLOSE):
    """Add the 'total' unit (Consolidados + Import Muebles), line by line."""
    units = dict(close["units"])
    total = {}
    for per in ("month", "ytd"):
        total[per] = {k: [units["cons"][per][k][i] + units["imp"][per][k][i] for i in range(3)]
                      for k in KEYS}
    units["total"] = total
    return units


# Gustavo, Ericka, Diana, Fernanda Mora, Rogelio, Sergio Garza Cantú, Bill.
DEFAULT_USERS = (
    "gustavogonzalez@thelsa.com",
    "erickamartinez@thelsa.com",
    "dianaaguirre@thelsa.com",
    "fernandamora@thelsa.com",
    "rogeliofranco@thelsa.com",
    "sergiogarza@thelsa.com",
    "bbrill@thelsa.com",
    "bill.brill@inflectionpointnow.com",
)


def allowed_users() -> set:
    raw = os.environ.get("TIM_EXEC_USERS", "")
    listed = {e.strip().lower() for e in raw.replace(";", ",").split(",") if e.strip()}
    return listed or set(DEFAULT_USERS)


def may_view(email) -> bool:
    return (email or "").strip().lower() in allowed_users()


_TILE = re.compile(r"<!-- TIM_EXEC_TILE_START.*?<!-- TIM_EXEC_TILE_END -->\n?", re.S)


def home_tile(html: str, email) -> str:
    """The library home page with the TIM Executive tile left in only for someone
    who may open the dashboard. For anyone else the tile is cut out."""
    return html if may_view(email) else _TILE.sub("", html)


def _restricted(f):
    @functools.wraps(f)
    def wrapped(*args, **kwargs):
        email = session.get("user_email")
        if not email:
            return redirect(url_for("login", next=request.url))
        if not may_view(email):
            abort(404)
        return f(*args, **kwargs)
    return wrapped


def render_page(close=CLOSE):
    data = {
        "units": with_total(close),
        "lines": [{"k": k, "en": en, "es": es, "kind": kind, "total": tot}
                  for k, en, es, kind, tot in LINES],
        "meta": {k: close[k] for k in ("period_en", "period_es", "source", "fx_note_en", "fx_note_es")},
    }
    return PAGE.replace("/*__DATA__*/null", json.dumps(data).replace("</", "<\\/"))


@tim_exec_bp.route("/tim-exec")
@_restricted
def tim_exec():
    return render_page()


PAGE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TIM Executive Dashboard</title>
<style>
  :root{--rust:#b0472f;--rust-dark:#8f3a26;--ink:#2b2b2b;--muted:#7d7d78;
        --line:#eae7e3;--bg:#f4f3f1;--card:#fff;--green:#2e7d32;--amber:#a86b12;--red:#c0392b;--tint:#faf3f0;}
  *{box-sizing:border-box}
  body{margin:0;font-family:-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
       background:var(--bg);color:var(--ink);font-size:14px;line-height:1.45}
  body:not(.es) .es, body.es .en{display:none}
  header{background:var(--card);border-bottom:1px solid var(--line);padding:18px 30px;
         display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px}
  header h1{font-size:21px;margin:0;font-weight:800;letter-spacing:-.2px}
  header h1 .accent{color:var(--rust)}
  .meta{font-size:12px;color:var(--muted);margin-top:3px}
  .back{font-size:13px;color:var(--rust);text-decoration:none;font-weight:600}
  .controls{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
  .seg{display:inline-flex;border:1px solid var(--line);border-radius:9px;overflow:hidden;background:var(--card)}
  .seg button{appearance:none;border:none;background:none;padding:7px 12px;font-size:12px;font-weight:700;
              color:var(--muted);cursor:pointer;border-right:1px solid var(--line)}
  .seg button:last-child{border-right:none}
  .seg button.on{background:var(--rust);color:#fff}
  main{padding:20px 30px;max-width:1200px;margin:0 auto}
  .note{background:var(--tint);border:1px solid var(--line);border-radius:12px;padding:10px 14px;
        font-size:12.5px;color:var(--muted);margin-bottom:8px}
  .note b{color:var(--ink)}
  h2{font-size:12px;text-transform:uppercase;letter-spacing:1px;color:var(--muted);margin:24px 0 10px;font-weight:700}
  .grid{display:grid;gap:14px;grid-template-columns:repeat(4,1fr)}
  .grid2{display:grid;gap:14px;grid-template-columns:repeat(2,1fr)}
  @media(max-width:900px){.grid{grid-template-columns:repeat(2,1fr)}}
  @media(max-width:620px){.grid,.grid2{grid-template-columns:1fr} main,header{padding-left:16px;padding-right:16px}}
  .tile{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:15px 17px}
  .tile .label{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}
  .tile .value{font-size:25px;font-weight:800;margin-top:5px;letter-spacing:-.4px;font-variant-numeric:tabular-nums}
  .tile .sub{font-size:12px;color:var(--muted);margin-top:3px}
  .good{color:var(--green)}.warn{color:var(--amber)}.bad{color:var(--red)}
  .tbl{overflow-x:auto;margin-top:10px}
  table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:14px;overflow:hidden}
  th,td{text-align:left;padding:9px 12px;border-bottom:1px solid var(--line);font-size:13px;white-space:nowrap}
  th{background:var(--tint);color:var(--rust-dark);font-weight:700;font-size:11px;text-transform:uppercase;letter-spacing:.5px}
  td.r,th.r{text-align:right} tr:last-child td{border-bottom:none}
  tr.tot td{font-weight:800;background:#fcfbfa}
  .num{font-variant-numeric:tabular-nums}
  .bar{display:flex;align-items:center;gap:9px;font-size:12px;margin:8px 0}
  .bar .name{width:120px}
  .bar .track{flex:1;height:10px;background:var(--line);border-radius:6px;overflow:hidden}
  .bar .fill{display:block;height:100%;background:var(--rust)}
  .bar .fill.neg{background:var(--red)}
  .bar .v{width:110px;text-align:right;font-variant-numeric:tabular-nums}
  ul.read{margin:6px 0 0;padding-left:18px} ul.read li{margin:4px 0}
  footer{color:var(--muted);font-size:11.5px;text-align:center;padding:22px}
</style></head>
<body>
<header>
  <div>
    <h1><span class="en">TIM Executive</span><span class="es">Tablero Ejecutivo</span> <span class="accent"><span class="en">Dashboard</span><span class="es">TIM</span></span></h1>
    <div class="meta"><a class="back" href="/">← <span class="en">Automation Library</span><span class="es">Biblioteca de automatizaciones</span></a>
      · <span class="en">Finance close</span><span class="es">Cierre de Finanzas</span> · <b id="perLabel"></b></div>
  </div>
  <div class="controls">
    <div class="seg" id="unitSeg"><button data-v="total"><span class="en">Total TIM</span><span class="es">Total TIM</span></button><button data-v="cons">Consolidados</button><button data-v="imp">Import Muebles</button></div>
    <div class="seg" id="perSeg"><button data-v="ytd"><span class="en">YTD</span><span class="es">Acumulado</span></button><button data-v="month"><span class="en">Month</span><span class="es">Mes</span></button></div>
    <div class="seg" id="cmpSeg"><button data-v="1"><span class="en">vs Budget</span><span class="es">vs Presupuesto</span></button><button data-v="2"><span class="en">vs 2025</span><span class="es">vs 2025</span></button></div>
    <div class="seg" id="langSeg"><button data-v="en">EN</button><button data-v="es">ES</button></div>
  </div>
</header>
<main>
  <div class="note" id="fxNote"></div>

  <h2><span class="en">Headline</span><span class="es">Resumen</span></h2>
  <div class="grid" id="kpis"></div>

  <h2><span class="en">By business unit</span><span class="es">Por unidad de negocio</span></h2>
  <div class="grid2">
    <div class="tile"><div class="label"><span class="en">Revenue</span><span class="es">Ingresos</span></div><div id="barsRev"></div></div>
    <div class="tile"><div class="label"><span class="en">Net profit</span><span class="es">Utilidad neta</span></div><div id="barsNet"></div></div>
  </div>

  <h2><span class="en">What the numbers say</span><span class="es">Lectura de los números</span></h2>
  <div class="tile">
    <ul class="read en">
      <li><b>Consolidados is the engine.</b> YTD revenue is up 52% on 2025 and net profit is close to double both budget and last year.</li>
      <li><b>Import Muebles is losing money.</b> YTD net is −$98.6K, worse than budget (−$62.7K) and 2025 (−$58.0K). In September, cost of sales ran at 210% of revenue.</li>
      <li><b>Read margin and profit, not individual cost lines, against budget.</b> The budget put almost all cost in "Direct cost" and "Selling expense"; actuals spread it across other lines (including other-company lines budgeted at zero). Large line variances are mostly reclassification, not savings.</li>
    </ul>
    <ul class="read es">
      <li><b>Consolidados es el motor.</b> Los ingresos acumulados crecen 52% vs 2025 y la utilidad neta casi duplica tanto el presupuesto como el año anterior.</li>
      <li><b>Import Muebles pierde dinero.</b> La utilidad neta acumulada es −$98.6K, peor que el presupuesto (−$62.7K) y que 2025 (−$58.0K). En septiembre el costo de ventas fue 210% de los ingresos.</li>
      <li><b>Contra presupuesto, lea el margen y la utilidad, no cada línea de costo.</b> El presupuesto cargó casi todo el costo en "Costo directo" y "Gastos de venta"; el real lo reparte en otras líneas (incluidas las de otras empresas, presupuestadas en cero). Las grandes variaciones por línea son sobre todo reclasificación, no ahorro.</li>
    </ul>
  </div>

  <h2><span class="en">Profit &amp; loss</span><span class="es">Estado de resultados</span></h2>
  <div class="tbl"><table id="pl"></table></div>

  <footer><span class="en">Source:</span><span class="es">Fuente:</span> <span id="src"></span></footer>
</main>
<script>
(function(){
  var D = /*__DATA__*/null;
  function get(k, d){ try { return localStorage.getItem(k) || d; } catch(e){ return d; } }
  function put(k, v){ try { localStorage.setItem(k, v); } catch(e){} }
  var dl = (navigator.language || '').toLowerCase().indexOf('es') === 0 ? 'es' : 'en';
  var S = {unit: get('timx.unit','total'), per: get('timx.per','ytd'), cmp: get('timx.cmp','1'), lang: get('timx.lang', dl)};
  if (['total','cons','imp'].indexOf(S.unit) < 0) S.unit = 'total';
  if (['ytd','month'].indexOf(S.per) < 0) S.per = 'ytd';
  if (['1','2'].indexOf(S.cmp) < 0) S.cmp = '1';
  if (['en','es'].indexOf(S.lang) < 0) S.lang = 'en';
  var KIND = {}; D.lines.forEach(function(l){ KIND[l.k] = l.kind; });
  var UNAME = {total:{en:'Total TIM',es:'Total TIM'}, cons:{en:'Consolidados',es:'Consolidados'}, imp:{en:'Import Muebles',es:'Import Muebles'}};

  function T(en, es){ return S.lang === 'es' ? es : en; }
  function fmt(v){ var f = new Intl.NumberFormat(S.lang==='es'?'es-MX':'en-US',{maximumFractionDigits:0});
    return (v < 0 ? '−' : '') + '$' + f.format(Math.abs(Math.round(v))); }
  function sfmt(v){ return (v > 0 ? '+' : '') + fmt(v); }
  function pct(a, b){ if (!b) return null; return (a - b) / Math.abs(b) * 100; }
  function pfmt(p){ return p === null ? '—' : (p > 0 ? '+' : (p < 0 ? '−' : '')) + Math.abs(p).toFixed(1) + '%'; }
  function share(v, rev){ if (!rev) return '—'; var x = (v / rev * 100).toFixed(1); return (x === '-0.0' ? '0.0' : x) + '%'; }
  function cls(k, diff){ if (!diff) return ''; var good = KIND[k] === 'rev' ? diff > 0 : diff < 0; return good ? 'good' : 'bad'; }

  function kpis(U){
    var c = +S.cmp, cl = T(c===1?'budget':'2025', c===1?'presupuesto':'2025'), rev = U.total_rev[0];
    return [['total_rev','Total revenue','Ingresos totales',false],['direct_margin','Direct margin','Margen directo',true],
            ['op_profit','Operating profit','Utilidad de operación',true],['net','Net profit','Utilidad neta',true]].map(function(x){
      var a = U[x[0]][0], b = U[x[0]][c], d = a - b;
      var valCls = x[3] ? (a < 0 ? 'bad' : '') : '';
      return '<div class="tile"><div class="label">'+T(x[1],x[2])+'</div><div class="value '+valCls+'">'+fmt(a)+'</div>'+
        '<div class="sub">'+(x[3] ? share(a, rev)+' '+T('of revenue','de ingresos')+' · ' : '')+
        '<span class="'+cls(x[0], d)+'"><b>'+sfmt(d)+'</b> ('+pfmt(pct(a,b))+')</span> '+T('vs','vs')+' '+cl+' ('+fmt(b)+')</div></div>';
    }).join('');
  }
  function bars(key){
    var vals = ['cons','imp'].map(function(u){ return [u, D.units[u][S.per][key][0]]; });
    var mx = Math.max.apply(null, vals.map(function(v){ return Math.abs(v[1]); })) || 1;
    return vals.map(function(v){
      return '<div class="bar"><span class="name">'+UNAME[v[0]][S.lang]+'</span><span class="track"><span class="fill'+(v[1]<0?' neg':'')+
        '" style="width:'+(Math.abs(v[1])/mx*100).toFixed(1)+'%"></span></span><span class="v '+(v[1]<0?'bad':'')+'">'+fmt(v[1])+'</span></div>';
    }).join('');
  }
  function table(U){
    var c = +S.cmp, rev = U.total_rev[0];
    var h = '<tr><th>'+T('Line','Concepto')+'</th><th class="r">'+T('Actual 2026','Real 2026')+'</th><th class="r">'+T('% rev.','% ingr.')+
      '</th><th class="r">'+(c===1?T('Budget 2026','Presupuesto 2026'):'2025')+'</th><th class="r">'+T('Variance','Variación')+'</th><th class="r">%</th></tr>';
    D.lines.forEach(function(l){
      var a = U[l.k][0], b = U[l.k][c], d = a - b;
      h += '<tr'+(l.total?' class="tot"':'')+'><td>'+l[S.lang]+'</td><td class="r num '+(l.kind==='rev'&&a<0?'bad':'')+'">'+fmt(a)+
        '</td><td class="r num">'+share(a, rev)+'</td><td class="r num">'+fmt(b)+'</td><td class="r num '+cls(l.k,d)+'">'+sfmt(d)+
        '</td><td class="r num '+cls(l.k,d)+'">'+pfmt(pct(a,b))+'</td></tr>';
    });
    return h;
  }
  function render(){
    document.body.className = S.lang === 'es' ? 'es' : '';
    document.documentElement.lang = S.lang;
    var U = D.units[S.unit][S.per];
    var perTxt = S.per === 'ytd' ? T('YTD through ','Acumulado a ') + (S.lang==='es'?D.meta.period_es:D.meta.period_en)
                                 : (S.lang==='es'?D.meta.period_es:D.meta.period_en);
    document.getElementById('perLabel').textContent = perTxt;
    document.getElementById('fxNote').innerHTML = '<b>'+UNAME[S.unit][S.lang]+' · '+perTxt+'</b> — '+(S.lang==='es'?D.meta.fx_note_es:D.meta.fx_note_en)+
      ' '+T('Green = favorable, red = unfavorable.','Verde = favorable, rojo = desfavorable.');
    document.getElementById('kpis').innerHTML = kpis(U);
    document.getElementById('barsRev').innerHTML = bars('total_rev');
    document.getElementById('barsNet').innerHTML = bars('net');
    document.getElementById('pl').innerHTML = table(U);
    document.getElementById('src').textContent = D.meta.source;
    [['unitSeg','unit'],['perSeg','per'],['cmpSeg','cmp'],['langSeg','lang']].forEach(function(c){
      document.querySelectorAll('#'+c[0]+' button').forEach(function(b){ b.classList.toggle('on', b.getAttribute('data-v') === S[c[1]]); });
    });
  }
  [['unitSeg','unit'],['perSeg','per'],['cmpSeg','cmp'],['langSeg','lang']].forEach(function(c){
    document.querySelectorAll('#'+c[0]+' button').forEach(function(b){
      b.addEventListener('click', function(){ S[c[1]] = b.getAttribute('data-v'); put('timx.'+c[1], S[c[1]]); render(); });
    });
  });
  render();
})();
</script>
</body></html>
"""
