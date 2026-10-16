import base64
import json
import random
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(
    page_title="Roleta de Prêmios · CENAP",
    page_icon="🎁",
    layout="centered",
)

# ---------------------------------------------------------------------------
# Prêmios: quantidade inicial + como aparecem na roleta
# ---------------------------------------------------------------------------
PREMIOS = {
    "Bloco de notas - CENAP": {"qtd": 200, "rotulo": "Bloco de notas", "icone": "📒", "cor": "#2F6BFF", "texto": "#FFFFFF"},
    "Caneta CENAP":           {"qtd": 100, "rotulo": "Caneta",         "icone": "🖊️", "cor": "#12305C", "texto": "#FFFFFF"},
    "Porta crachá (retrátil)": {"qtd": 200, "rotulo": "Porta crachá",  "icone": "🏷️", "cor": "#5B8CFF", "texto": "#FFFFFF"},
    "Bombom":                 {"qtd": 350, "rotulo": "Bombom",         "icone": "🍫", "cor": "#0E2A47", "texto": "#FFFFFF"},
    "Prêmio surpresas":       {"qtd": 1,   "rotulo": "Surpresa!",      "icone": "⭐", "cor": "#C8F031", "texto": "#0A1A33", "especial": True},
}

# Sorteio "de urna": a chance de cada prêmio é proporcional ao que resta dele.
# Com 850 brindes comuns e 1 surpresa, a surpresa sai em ~1 a cada 850 giros,
# num momento aleatório ao longo do evento.
SORTEIO_PROPORCIONAL = True

VOLTAS = 6  # voltas completas antes de parar

# ---------------------------------------------------------------------------
# Armazenamento do estoque (sobrevive a recarregar a página e à virada do dia)
#
# 1) Se os segredos GIST_TOKEN e GIST_ID estiverem configurados no Streamlit
#    Cloud, o estoque é salvo num GitHub Gist (fica salvo mesmo se o app
#    reiniciar ou "dormir" durante a noite).
# 2) Sem esses segredos, salva num arquivo estoque.json no servidor
#    (sobrevive a recarregar a página, mas zera se o app reiniciar).
# ---------------------------------------------------------------------------
ARQUIVO_LOCAL = Path(__file__).parent / "estoque.json"
NOME_NO_GIST = "estoque_roleta_cenap.json"
try:
    FUSO = ZoneInfo("America/Sao_Paulo")
except Exception:
    from datetime import timedelta, timezone
    FUSO = timezone(timedelta(hours=-3))


def _segredo(nome):
    try:
        return st.secrets.get(nome)
    except Exception:
        return None


GIST_TOKEN = _segredo("GIST_TOKEN")
GIST_ID = _segredo("GIST_ID")
USA_GIST = bool(GIST_TOKEN and GIST_ID)


def _cabecalhos():
    return {
        "Authorization": f"Bearer {GIST_TOKEN}",
        "Accept": "application/vnd.github+json",
    }


def _padrao():
    return {k: v["qtd"] for k, v in PREMIOS.items()}


def _normalizar(qtds):
    """Garante que todos os prêmios existem (útil se a lista de prêmios mudar)."""
    base = _padrao()
    for k in base:
        if k in qtds:
            base[k] = max(0, int(qtds[k]))
    return base


def _ler_gist():
    r = requests.get(f"https://api.github.com/gists/{GIST_ID}", headers=_cabecalhos(), timeout=10)
    r.raise_for_status()
    arq = r.json().get("files", {}).get(NOME_NO_GIST)
    if not arq or not arq.get("content"):
        return None
    return json.loads(arq["content"])


def _gravar_gist(conteudo):
    r = requests.patch(
        f"https://api.github.com/gists/{GIST_ID}",
        headers=_cabecalhos(),
        json={"files": {NOME_NO_GIST: {"content": conteudo}}},
        timeout=15,
    )
    r.raise_for_status()


def _ler_local():
    if ARQUIVO_LOCAL.exists():
        return json.loads(ARQUIVO_LOCAL.read_text(encoding="utf-8"))
    return None


@st.cache_resource
def armazem():
    """Estoque único, compartilhado por todas as abas e aparelhos."""
    a = {
        "lock": threading.Lock(),
        "fila_gist": threading.Lock(),
        "qtds": _padrao(),
        "atualizado": None,
        "erro": None,
    }
    dados = None
    if USA_GIST:
        try:
            dados = _ler_gist()
        except Exception as e:
            a["erro"] = f"Não consegui ler o Gist ({e}). Usando cópia local."
    if dados is None:
        try:
            dados = _ler_local()
        except Exception:
            dados = None
    if dados:
        a["qtds"] = _normalizar(dados.get("quantidades", {}))
        a["atualizado"] = dados.get("atualizado")
    return a


def _salvar(a):
    """Chamar com a["lock"] já adquirido."""
    a["atualizado"] = datetime.now(FUSO).strftime("%d/%m/%Y %H:%M:%S")
    conteudo = json.dumps(
        {"quantidades": a["qtds"], "atualizado": a["atualizado"]},
        ensure_ascii=False,
        indent=2,
    )
    try:
        ARQUIVO_LOCAL.write_text(conteudo, encoding="utf-8")
    except Exception:
        pass
    if USA_GIST:
        def enviar():
            # envia em segundo plano para a roleta não travar; em fila, na ordem
            with a["fila_gist"]:
                try:
                    _gravar_gist(conteudo)
                    a["erro"] = None
                except Exception as e:
                    a["erro"] = f"Falha ao salvar no Gist ({e}). A cópia local foi salva."
        threading.Thread(target=enviar, daemon=True).start()


ESTOQUE = armazem()

# Estado de cada tela (só a animação; o estoque é o compartilhado acima)
if "angulo" not in st.session_state:
    st.session_state.angulo = 0.0
    st.session_state.giro = None
    st.session_state.aviso = False


def quantidades():
    return dict(ESTOQUE["qtds"])


def disponiveis():
    return [p for p, q in ESTOQUE["qtds"].items() if q > 0]


def girar():
    with ESTOQUE["lock"]:
        lista = disponiveis()
        if not lista:
            st.session_state.aviso = True
            st.session_state.giro = None
            return
        st.session_state.aviso = False

        if SORTEIO_PROPORCIONAL:
            pesos = [ESTOQUE["qtds"][p] for p in lista]
            vencedor = random.choices(lista, weights=pesos, k=1)[0]
        else:
            vencedor = random.choice(lista)

        ESTOQUE["qtds"][vencedor] -= 1
        _salvar(ESTOQUE)

    # Ângulo final: o centro da fatia vencedora para embaixo do ponteiro (topo)
    n = len(lista)
    fatia = 360 / n
    centro = (lista.index(vencedor) + 0.5) * fatia
    deslocamento = random.uniform(-0.35, 0.35) * fatia
    alvo = (-(centro + deslocamento)) % 360
    inicio = st.session_state.angulo
    fim = inicio + 360 * VOLTAS + ((alvo - inicio) % 360)

    st.session_state.giro = {
        "id": time.time(),
        "lista": lista,  # fatias como estavam ANTES do sorteio
        "vencedor": vencedor,
        "inicio": inicio,
        "fim": fim,
    }
    st.session_state.angulo = fim % 360


def ajustar_estoque(novas):
    with ESTOQUE["lock"]:
        ESTOQUE["qtds"] = _normalizar(novas)
        _salvar(ESTOQUE)
    st.session_state.giro = None
    st.session_state.aviso = False


def reiniciar():
    ajustar_estoque(_padrao())
    st.session_state.angulo = 0.0


# ---------------------------------------------------------------------------
# Estilo da página (identidade CENAP / CAIC: marinho, azul elétrico, limão)
# ---------------------------------------------------------------------------
st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,700;12..96,800&family=Manrope:wght@500;600;700;800&display=swap');

.stApp {
  background:
    radial-gradient(1200px 600px at 50% -15%, #1D4A8F 0%, rgba(29,74,143,0) 60%),
    radial-gradient(800px 500px at 110% 110%, rgba(200,240,49,.10) 0%, rgba(200,240,49,0) 60%),
    #0A1A33;
  color: #F2F6FF;
  font-family: 'Manrope', sans-serif;
}
header[data-testid="stHeader"] { background: transparent; }
[data-testid="stToolbar"], #MainMenu, footer { visibility: hidden; }
.block-container { padding-top: 1.6rem; padding-bottom: 3rem; max-width: 760px; }

.hero { text-align: center; margin-bottom: .2rem; }
.kicker {
  display: inline-block; padding: .38rem 1rem; border-radius: 999px;
  background: rgba(200,240,49,.12); border: 1px solid rgba(200,240,49,.45);
  color: #C8F031; font-weight: 800; font-size: .78rem; letter-spacing: .16em; text-transform: uppercase;
}
.titulo {
  font-family: 'Bricolage Grotesque', sans-serif; font-weight: 800;
  font-size: clamp(2.4rem, 7vw, 3.6rem); line-height: 1; letter-spacing: -.02em;
  margin: .7rem 0 .4rem; color: #FFFFFF;
}
.titulo span { color: #C8F031; }
.sub { color: #B9C8E4; font-size: 1.1rem; font-weight: 600; margin: 0; }

/* Botão principal */
.stButton button[kind="primary"], button[data-testid="stBaseButton-primary"] {
  width: 100%; min-height: 64px; border: none; border-radius: 999px;
  background: #C8F031; color: #0A1A33;
  font-family: 'Bricolage Grotesque', sans-serif; font-weight: 800; font-size: 1.45rem;
  box-shadow: 0 12px 34px rgba(200,240,49,.30), inset 0 -4px 0 rgba(10,26,51,.18);
  transition: transform .12s ease, box-shadow .12s ease, background .12s ease;
}
.stButton button[kind="primary"] p, button[data-testid="stBaseButton-primary"] p {
  font-size: 1.45rem; font-weight: 800;
}
.stButton button[kind="primary"]:hover, button[data-testid="stBaseButton-primary"]:hover {
  background: #D9FF5C; color: #0A1A33; transform: translateY(-2px);
  box-shadow: 0 16px 40px rgba(200,240,49,.42), inset 0 -4px 0 rgba(10,26,51,.18);
}
.stButton button[kind="primary"]:active, button[data-testid="stBaseButton-primary"]:active {
  transform: translateY(1px); color: #0A1A33;
}

/* Estoque */
.estoque { margin-top: 1.6rem; }
.estoque-tit {
  color: #8FA6CF; font-weight: 800; font-size: .78rem; letter-spacing: .16em;
  text-transform: uppercase; text-align: center; margin-bottom: .8rem;
}
.chips { display: flex; flex-wrap: wrap; gap: .55rem; justify-content: center; }
.chip {
  display: flex; align-items: center; gap: .5rem; padding: .5rem .9rem .5rem .6rem;
  border-radius: 14px; background: rgba(255,255,255,.06); border: 1px solid rgba(255,255,255,.12);
  font-weight: 700; font-size: .95rem; color: #F2F6FF;
}
.chip .ci { font-size: 1.15rem; }
.chip .cq {
  font-size: .78rem; font-weight: 800; padding: .15rem .5rem; border-radius: 999px;
  background: rgba(47,107,255,.25); color: #BFD1FF;
}
.chip.especial { border-color: rgba(200,240,49,.55); background: rgba(200,240,49,.08); }
.chip.especial .cq { background: rgba(200,240,49,.22); color: #E4FF8A; }
.chip.esgotado { opacity: .38; text-decoration: line-through; }

[data-testid="stExpander"] { margin-top: 1.8rem; border-color: rgba(255,255,255,.12); }
</style>
""",
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Cabeçalho e logo (arquivo na raiz do repositório):
#   logo_cenap_icone.png  -> ícone + nome do CENAP, no miolo da roleta
# Se não existir, o miolo mostra só o texto "CENAP".
# ---------------------------------------------------------------------------
PASTA = Path(__file__).parent


@st.cache_data
def imagem_b64(nome):
    arq = PASTA / nome
    if not arq.exists():
        return None
    return "data:image/png;base64," + base64.b64encode(arq.read_bytes()).decode()


@st.cache_data
def proporcao(nome):
    """altura / largura de um PNG (lida do cabeçalho do arquivo)."""
    dados = (PASTA / nome).read_bytes()[16:24]
    largura = int.from_bytes(dados[:4], "big")
    altura = int.from_bytes(dados[4:], "big")
    return altura / largura


LOGO_MIOLO = imagem_b64("logo_cenap_icone.png")
PROP_MIOLO = proporcao("logo_cenap_icone.png") if LOGO_MIOLO else None

st.markdown(
    "".join(
        [
            '<div class="hero">',
            '<div class="kicker">CENAP · XXIII CAIC FAMERP</div>',
            '<div class="titulo">Roleta de <span>Prêmios</span></div>',
            '<p class="sub">Gire a roleta e descubra o seu brinde!</p>',
            "</div>",
        ]
    ),
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Roleta (SVG animado)
# ---------------------------------------------------------------------------
ROLETA_HTML = r"""
<!doctype html>
<html><head>
<meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,800&family=Manrope:wght@700;800&display=swap" rel="stylesheet">
<style>
  html,body{margin:0;padding:0;background:transparent;overflow:hidden;font-family:'Manrope',sans-serif;}
  #stage{position:relative;width:100%;max-width:500px;margin:0 auto;}
  svg{display:block;width:100%;height:auto;overflow:visible;
      filter:drop-shadow(0 18px 40px rgba(0,0,0,.45));}
  .lbl{font-family:'Manrope',sans-serif;font-weight:800;font-size:19px;letter-spacing:.01em;}
  .ico{font-size:26px;}
  .light{transition:opacity .2s;}
  .light.a{fill:#C8F031;animation:blink 1.4s infinite;}
  .light.b{fill:#FFFFFF;animation:blink 1.4s infinite .7s;}
  body.spinning .light.a,body.spinning .light.b{animation-duration:.35s;}
  body.spinning .light.b{animation-delay:.175s;}
  @keyframes blink{0%,100%{opacity:1}50%{opacity:.25}}
  #pointer{transform-box:fill-box;transform-origin:50% 0;}
  #pointer.flick{animation:flick .14s ease-out;}
  @keyframes flick{0%{transform:rotate(-20deg)}100%{transform:rotate(0)}}
  .hub-t{font-family:'Bricolage Grotesque',sans-serif;font-weight:800;}

  #overlay{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
           background:rgba(7,18,38,.55);backdrop-filter:blur(3px);opacity:0;pointer-events:none;
           transition:opacity .35s ease;border-radius:50%;}
  #overlay.show{opacity:1;pointer-events:auto;cursor:pointer;}
  .card{background:#FFFFFF;color:#0A1A33;border-radius:28px;padding:26px 34px 24px;text-align:center;
        width:72%;box-shadow:0 24px 60px rgba(0,0,0,.45);transform:scale(.6);
        transition:transform .45s cubic-bezier(.2,1.4,.4,1);border:4px solid #C8F031;}
  #overlay.show .card{transform:scale(1);}
  .card .tag{display:inline-block;background:#0A1A33;color:#C8F031;font-weight:800;font-size:13px;
             letter-spacing:.16em;text-transform:uppercase;padding:6px 14px;border-radius:999px;}
  .card .icon{font-size:58px;line-height:1;margin:14px 0 6px;}
  .card .name{font-family:'Bricolage Grotesque',sans-serif;font-weight:800;font-size:30px;line-height:1.05;}
  .card .hint{margin-top:10px;font-size:14px;font-weight:700;color:#4A5F82;}
  .card.especial{background:#C8F031;border-color:#FFFFFF;}
  .card.especial .hint{color:#0A1A33;}
</style>
</head>
<body>
<div id="stage">
  <svg id="svg" viewBox="0 0 520 540" xmlns="http://www.w3.org/2000/svg"></svg>
  <div id="overlay"><div class="card" id="card">
    <div class="tag" id="r-tag">Parabéns!</div>
    <div class="icon" id="r-icon"></div>
    <div class="name" id="r-name"></div>
    <div class="hint">Retire seu brinde no stand do CENAP</div>
  </div></div>
</div>
<script src="https://cdn.jsdelivr.net/npm/canvas-confetti@1.9.3/dist/confetti.browser.min.js"></script>
<script>
const D = __DADOS__;
const NS = "http://www.w3.org/2000/svg";
const CX = 260, CY = 275, R = 225;
const svg = document.getElementById("svg");

function el(tag, attrs, parent){
  const e = document.createElementNS(NS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(e);
  return e;
}
function pt(r, a){ const t = a*Math.PI/180; return [CX + r*Math.sin(t), CY - r*Math.cos(t)]; }

// Aro externo + luzes
el("circle", {cx:CX, cy:CY, r:R+24, fill:"#0A1A33", stroke:"#2F6BFF", "stroke-width":5}, svg);
for (let k = 0; k < 24; k++){
  const [x, y] = pt(R+12, k*15);
  el("circle", {cx:x, cy:y, r:4.6, class:"light " + (k%2 ? "b" : "a")}, svg);
}

// Fatias
const wheel = el("g", {id:"wheel"}, svg);
const segs = D.segmentos, n = segs.length;
const rotulos = [];
function ajustarRotulos(){
  const MAX = 106;  // espaço livre entre o miolo e o ícone
  rotulos.forEach(t => {
    let fs = 19; t.style.fontSize = fs + "px";
    while (fs > 12 && t.getComputedTextLength() > MAX){ fs -= 1; t.style.fontSize = fs + "px"; }
  });
}
if (n === 0){
  el("circle", {cx:CX, cy:CY, r:R, fill:"#12305C"}, wheel);
  const t = el("text", {x:CX, y:CY+95, "text-anchor":"middle", fill:"#8FA6CF", class:"lbl"}, svg);
  t.textContent = "Prêmios esgotados";
} else {
  const fatia = 360 / n;
  segs.forEach((s, i) => {
    if (n === 1){
      el("circle", {cx:CX, cy:CY, r:R, fill:s.cor}, wheel);
    } else {
      const a0 = i*fatia, a1 = (i+1)*fatia;
      const [x0, y0] = pt(R, a0), [x1, y1] = pt(R, a1);
      el("path", {d:`M${CX},${CY} L${x0},${y0} A${R},${R} 0 ${fatia > 180 ? 1 : 0} 1 ${x1},${y1} Z`,
                  fill:s.cor, stroke:"#0A1A33", "stroke-width":3, "stroke-linejoin":"round"}, wheel);
    }
    const c = (i + 0.5) * fatia;
    const g = el("g", {transform:`rotate(${c-90} ${CX} ${CY})`}, wheel);
    // ícone perto da borda, texto entre o centro e o ícone (encolhe se não couber)
    const ic = el("text", {x:CX+200, y:CY, "text-anchor":"middle", "dominant-baseline":"central",
                           class:"ico", transform:`rotate(90 ${CX+200} ${CY})`}, g);
    ic.textContent = s.icone;
    const t = el("text", {x:CX+178, y:CY, "text-anchor":"end", "dominant-baseline":"central",
                          fill:s.texto, class:"lbl"}, g);
    t.textContent = s.rotulo;
    rotulos.push(t);
  });
}
ajustarRotulos();
if (document.fonts && document.fonts.ready) document.fonts.ready.then(ajustarRotulos);
// brilho sutil por cima das fatias
el("circle", {cx:CX, cy:CY, r:R, fill:"none", stroke:"rgba(255,255,255,.18)", "stroke-width":2}, wheel);

// Centro fixo
el("circle", {cx:CX, cy:CY, r:66, fill:"#0A1A33"}, svg);
el("circle", {cx:CX, cy:CY, r:58, fill:"#FFFFFF", stroke:"#C8F031", "stroke-width":5}, svg);
if (D.logo){
  // logo do CENAP no miolo (ícone + nome)
  const LW = 88, LH = LW * D.logoProp;
  el("image", {href:D.logo, x:CX-LW/2, y:CY-LH/2, width:LW, height:LH}, svg);
} else {
  const h1 = el("text", {x:CX, y:CY-4, "text-anchor":"middle", "dominant-baseline":"central",
                         fill:"#0A1A33", class:"hub-t", "font-size":22}, svg);
  h1.textContent = "CENAP";
  const h2 = el("text", {x:CX, y:CY+19, "text-anchor":"middle", "dominant-baseline":"central",
                         fill:"#2F6BFF", "font-weight":800, "font-size":10, "letter-spacing":"2"}, svg);
  h2.textContent = "CAIC 2026";
}

// Ponteiro
const pointer = el("g", {id:"pointer"}, svg);
el("path", {d:`M${CX-24},${CY-R-38} L${CX+24},${CY-R-38} L${CX},${CY-R+18} Z`,
            fill:"#C8F031", stroke:"#0A1A33", "stroke-width":5, "stroke-linejoin":"round"}, pointer);
el("circle", {cx:CX, cy:CY-R-30, r:6, fill:"#0A1A33"}, pointer);

function setRot(a){ wheel.setAttribute("transform", `rotate(${a} ${CX} ${CY})`); }
function flick(){ pointer.classList.remove("flick"); pointer.getBoundingClientRect(); pointer.classList.add("flick"); }

function mostrarResultado(){
  const v = segs.find(s => s.nome === D.vencedor);
  document.getElementById("r-icon").textContent = v.icone;
  document.getElementById("r-name").textContent = v.nome;
  const card = document.getElementById("card");
  if (v.especial){ card.classList.add("especial"); document.getElementById("r-tag").textContent = "Prêmio surpresa!"; }
  const ov = document.getElementById("overlay");
  ov.classList.add("show");
  ov.addEventListener("click", () => ov.classList.remove("show"));
  if (window.confetti){
    const cores = ["#C8F031", "#2F6BFF", "#FFFFFF", "#5B8CFF"];
    confetti({particleCount:150, spread:90, startVelocity:42, origin:{y:.45}, colors:cores});
    if (v.especial){
      setTimeout(() => confetti({particleCount:120, angle:60, spread:70, origin:{x:0, y:.6}, colors:cores}), 250);
      setTimeout(() => confetti({particleCount:120, angle:120, spread:70, origin:{x:1, y:.6}, colors:cores}), 450);
    }
  }
}

setRot(D.inicio);
if (D.animar && n > 0){
  document.body.classList.add("spinning");
  const dur = 6500, t0 = performance.now(), fatia = 360 / n;
  const ease = t => 1 - Math.pow(1 - t, 4);
  let ultimo = Math.floor(D.inicio / fatia);
  function frame(now){
    const t = Math.min(1, (now - t0) / dur);
    const a = D.inicio + (D.fim - D.inicio) * ease(t);
    setRot(a);
    const k = Math.floor(a / fatia);
    if (k !== ultimo){ ultimo = k; flick(); }
    if (t < 1) requestAnimationFrame(frame);
    else { document.body.classList.remove("spinning"); setTimeout(mostrarResultado, 250); }
  }
  requestAnimationFrame(frame);
}
</script>
</body></html>
"""


def segmento(nome):
    p = PREMIOS[nome]
    return {
        "nome": nome,
        "rotulo": p["rotulo"],
        "icone": p["icone"],
        "cor": p["cor"],
        "texto": p["texto"],
        "especial": p.get("especial", False),
    }


giro = st.session_state.giro
if giro:
    dados = {
        "segmentos": [segmento(p) for p in giro["lista"]],
        "inicio": giro["inicio"],
        "fim": giro["fim"],
        "animar": True,
        "vencedor": giro["vencedor"],
        "id": giro["id"],
    }
    st.session_state.giro = None  # a animação toca uma vez só
else:
    dados = {
        "segmentos": [segmento(p) for p in disponiveis()],
        "inicio": st.session_state.angulo,
        "fim": st.session_state.angulo,
        "animar": False,
        "vencedor": None,
    }
dados["logo"] = LOGO_MIOLO
dados["logoProp"] = PROP_MIOLO

html_roleta = ROLETA_HTML.replace("__DADOS__", json.dumps(dados))
if hasattr(st, "iframe"):  # Streamlit novo
    st.iframe(html_roleta, height=545)
else:  # Streamlit antigo
    components.html(html_roleta, height=545)

# ---------------------------------------------------------------------------
# Botão + estoque
# ---------------------------------------------------------------------------
def botao_largo(rotulo, **kw):
    """Botão ocupando a coluna inteira (funciona em versões novas e antigas do Streamlit)."""
    try:
        return st.button(rotulo, width="stretch", **kw)
    except TypeError:
        return st.button(rotulo, use_container_width=True, **kw)


_, meio, _ = st.columns([1, 2, 1])
with meio:
    botao_largo("Girar a roleta!", type="primary", on_click=girar, key="btn_girar")

if st.session_state.aviso:
    st.warning("Não há mais prêmios disponíveis para sortear!")

# Para o público: só os nomes dos prêmios ainda disponíveis, sem quantidades
chips = []
for nome in disponiveis():
    p = PREMIOS[nome]
    classes = "chip especial" if p.get("especial") else "chip"
    chips.append(
        f'<div class="{classes}"><span class="ci">{p["icone"]}</span>'
        f'<span class="cn">{nome}</span></div>'
    )

if chips:
    st.markdown(
        '<div class="estoque"><div class="estoque-tit">Prêmios na roleta</div>'
        f'<div class="chips">{"".join(chips)}</div></div>',
        unsafe_allow_html=True,
    )

# ---------------------------------------------------------------------------
# Controle do estoque: só aparece abrindo o link com ?admin=cenap no final
# ---------------------------------------------------------------------------
# Defina SENHA_ADMIN nos Secrets do Streamlit Cloud; "cenap" é só o padrão.
SENHA_ADMIN = _segredo("SENHA_ADMIN") or "cenap"

try:
    modo_admin = st.query_params.get("admin") == SENHA_ADMIN
except AttributeError:  # versões antigas do Streamlit
    modo_admin = st.experimental_get_query_params().get("admin", [""])[0] == SENHA_ADMIN

if modo_admin:
    with st.expander("⚙️ Controle do estoque", expanded=False):
        if USA_GIST:
            st.caption("💾 Salvando no GitHub Gist: o estoque fica guardado mesmo se o app reiniciar.")
        else:
            st.caption(
                "💾 Salvando só no servidor: sobrevive a recarregar a página, "
                "mas zera se o app reiniciar. Configure GIST_TOKEN e GIST_ID para ficar 100% seguro."
            )
        if ESTOQUE["atualizado"]:
            st.caption(f"Última atualização: {ESTOQUE['atualizado']}")
        if ESTOQUE["erro"]:
            st.error(ESTOQUE["erro"])

        qtds = quantidades()
        st.dataframe(
            [
                {"Prêmio": nome, "Inicial": PREMIOS[nome]["qtd"], "Restante": q, "Sorteados": PREMIOS[nome]["qtd"] - q}
                for nome, q in qtds.items()
            ],
            hide_index=True,
        )
        st.caption(f"Total sorteado: {sum(PREMIOS[n]['qtd'] - q for n, q in qtds.items())}")

        with st.form("form_ajuste"):
            st.markdown("**Corrigir quantidades restantes**")
            novas = {}
            for i, (nome, q) in enumerate(qtds.items()):
                novas[nome] = st.number_input(nome, min_value=0, step=1, value=int(q), key=f"aj_{i}_{q}")
            if st.form_submit_button("Salvar ajustes"):
                ajustar_estoque(novas)
                st.rerun()

        confirmar = st.checkbox("Quero zerar tudo e voltar às quantidades iniciais", key="conf_reset")
        st.button("Reiniciar estoque", on_click=reiniciar, key="btn_reset", disabled=not confirmar)
