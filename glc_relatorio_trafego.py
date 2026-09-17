#!/usr/bin/env python3
"""
GLCtech - Agente de Relatório Semanal de Tráfego Web
----------------------------------------------------
Coleta dados do Google Analytics 4 (glctech.com.br e glctechsec.com),
gera um PDF com a identidade visual da GLCtech e envia por e-mail
(Zoho Mail / SMTP) para a diretoria.

Período: semana anterior completa (segunda a domingo), comparada
com a semana imediatamente anterior.

Uso:
  python glc_relatorio_trafego.py                 # execução normal (coleta + PDF + e-mail)
  python glc_relatorio_trafego.py --no-email      # gera o PDF sem enviar
  python glc_relatorio_trafego.py --mock          # dados fictícios (teste sem GA4)
  python glc_relatorio_trafego.py --date 2026-09-21  # simula execução em outra data
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import smtplib
import sys
import traceback
from collections import defaultdict
from email.message import EmailMessage
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle  # noqa: E402
from reportlab.lib.units import cm  # noqa: E402
from reportlab.pdfbase import pdfmetrics  # noqa: E402
from reportlab.pdfbase.pdfmetrics import registerFontFamily  # noqa: E402
from reportlab.pdfbase.ttfonts import TTFont  # noqa: E402
from reportlab.platypus import (  # noqa: E402
    Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)
from reportlab.platypus.flowables import HRFlowable  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / "config.env")

# --------------------------------------------------------------------------- #
# Configuração
# --------------------------------------------------------------------------- #
SITES = [
    {"key": "glctech", "nome": "GLCTech", "dominio": "glctech.com.br",
     "property_id": os.getenv("GA4_PROPERTY_GLCTECH", "512816238")},
    {"key": "glctechsec", "nome": "GLCTech Sec", "dominio": "glctechsec.com",
     "property_id": os.getenv("GA4_PROPERTY_GLCTECHSEC", "549308338")},
]
CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", str(BASE_DIR / "credentials" / "service-account.json"))
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REFRESH_TOKEN = os.getenv("GOOGLE_REFRESH_TOKEN", "")
SMTP_HOST = os.getenv("SMTP_HOST", "smtppro.zoho.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
MAIL_FROM = os.getenv("MAIL_FROM", SMTP_USER)
MAIL_FROM_NAME = os.getenv("MAIL_FROM_NAME", "GLCtech Relatórios")
MAIL_TO = [e.strip() for e in os.getenv("MAIL_TO", "diretoria@glctech.com.br").split(",") if e.strip()]
MAIL_CC = [e.strip() for e in os.getenv("MAIL_CC", "").split(",") if e.strip()]
ALERT_TO = [e.strip() for e in os.getenv("ALERT_TO", "").split(",") if e.strip()]
KEEP_REPORTS = int(os.getenv("KEEP_REPORTS", "26"))  # ~6 meses de histórico

OUTPUT_DIR = BASE_DIR / "output"
LOG_DIR = BASE_DIR / "logs"
LOGO = BASE_DIR / "assets" / "logo_glctech.png"
WORK_DIR = BASE_DIR / "output" / ".tmp"

# Paleta GLCtech (extraída do logo oficial)
GRAFITE = colors.HexColor("#2E2E2E")
VERMELHO = colors.HexColor("#BD2323")
CINZA = colors.HexColor("#5A5A5A")
CLARO = colors.HexColor("#F4F4F4")
VERDE_TXT = "#1E8449"
VERM_TXT = "#C0392B"
COR_SITE = {"GLCTech": "#BD2323", "GLCTech Sec": "#3E3E3E"}
COR_CANAL = {"Direct": "#3E3E3E", "Referral": "#BD2323", "Organic Search": "#7F1416",
             "Organic Social": "#9A9A9A", "Unassigned": "#CFCFCF"}
CANAL_PT = {"Direct": "Direto", "Referral": "Referência", "Organic Search": "Busca orgânica",
            "Organic Social": "Social orgânico", "Unassigned": "Não atribuído", "Paid Search": "Busca paga",
            "Paid Social": "Social pago", "Email": "E-mail", "Organic Video": "Vídeo orgânico",
            "Display": "Display", "Cross-network": "Multirrede", "Organic Shopping": "Shopping orgânico",
            "SMS": "SMS", "Mobile Push Notifications": "Push", "Affiliates": "Afiliados"}
DIAS = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]

LOG_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_DIR / "relatorio.log", encoding="utf-8"), logging.StreamHandler()],
)
log = logging.getLogger("glc-relatorio")


# --------------------------------------------------------------------------- #
# Períodos
# --------------------------------------------------------------------------- #
def periodos(hoje: dt.date):
    """Semana anterior completa (seg-dom) e a semana antes dela."""
    inicio = hoje - dt.timedelta(days=hoje.weekday() + 7)
    fim = inicio + dt.timedelta(days=6)
    return (inicio, fim), (inicio - dt.timedelta(days=7), inicio - dt.timedelta(days=1))


# --------------------------------------------------------------------------- #
# Coleta GA4
# --------------------------------------------------------------------------- #
METRICAS = ["sessions", "totalUsers", "newUsers", "screenPageViews", "engagementRate", "averageSessionDuration"]


def _run(client, prop, dims, mets, ini, fim, limit=1000, order_metric=None):
    from google.analytics.data_v1beta.types import (
        DateRange, Dimension, Metric, OrderBy, RunReportRequest,
    )
    req = RunReportRequest(
        property=f"properties/{prop}",
        dimensions=[Dimension(name=d) for d in dims],
        metrics=[Metric(name=m) for m in mets],
        date_ranges=[DateRange(start_date=ini.isoformat(), end_date=fim.isoformat())],
        limit=limit,
    )
    if order_metric:
        req.order_bys = [OrderBy(metric=OrderBy.MetricOrderBy(metric_name=order_metric), desc=True)]
    resp = client.run_report(req)
    out = []
    for row in resp.rows:
        d = [v.value for v in row.dimension_values]
        m = [float(v.value) for v in row.metric_values]
        out.append((d, m))
    return out


def _ga4_credentials():
    """Monta as credenciais para a GA4 Data API.

    Preferência: OAuth 2.0 com refresh_token (GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET /
    GOOGLE_REFRESH_TOKEN) - não depende de chave privada de service account, então não
    há segredo em formato de chave RSA para armazenar.

    Fallback: service account JSON (GOOGLE_APPLICATION_CREDENTIALS), mantido apenas por
    compatibilidade com configurações antigas.
    """
    if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and GOOGLE_REFRESH_TOKEN:
        from google.oauth2.credentials import Credentials as UserCredentials

        return UserCredentials(
            token=None,
            refresh_token=GOOGLE_REFRESH_TOKEN,
            client_id=GOOGLE_CLIENT_ID,
            client_secret=GOOGLE_CLIENT_SECRET,
            token_uri="https://oauth2.googleapis.com/token",
            scopes=["https://www.googleapis.com/auth/analytics.readonly"],
        )

    from google.oauth2 import service_account

    return service_account.Credentials.from_service_account_file(
        CREDENTIALS, scopes=["https://www.googleapis.com/auth/analytics.readonly"])


def coletar_ga4(site, atual, anterior):
    from google.analytics.data_v1beta import BetaAnalyticsDataClient

    creds = _ga4_credentials()
    client = BetaAnalyticsDataClient(credentials=creds)
    prop = site["property_id"]

    def totais(ini, fim):
        rows = _run(client, prop, [], METRICAS, ini, fim)
        return dict(zip(METRICAS, rows[0][1])) if rows else dict.fromkeys(METRICAS, 0.0)

    kpi_atual, kpi_ant = totais(*atual), totais(*anterior)

    diario = {}
    for d, m in _run(client, prop, ["date"], ["sessions"], *atual):
        diario[dt.datetime.strptime(d[0], "%Y%m%d").date()] = int(m[0])

    fontes = []
    for d, m in _run(client, prop, ["sessionDefaultChannelGroup", "sessionSourceMedium"],
                     ["sessions", "totalUsers", "engagementRate"], *atual, order_metric="sessions"):
        fontes.append({"canal": d[0], "origem": d[1], "sessoes": int(m[0]), "usuarios": int(m[1]), "engaj": m[2]})

    paginas = defaultdict(int)
    for d, m in _run(client, prop, ["pagePath"], ["screenPageViews"], *atual, limit=500):
        path = d[0].split("?")[0] or "/"
        paginas[path] += int(m[0])

    return {"kpi": kpi_atual, "kpi_ant": kpi_ant, "diario": diario, "fontes": fontes,
            "paginas": sorted(paginas.items(), key=lambda x: -x[1])[:8]}


def coletar_mock(site, atual, anterior):
    import random
    rnd = random.Random(site["key"] + atual[0].isoformat())
    dias = [atual[0] + dt.timedelta(i) for i in range(7)]
    diario = {d: rnd.randint(2, 22) for d in dias}
    s = sum(diario.values())
    fontes = [
        {"canal": "Direct", "origem": "(direct) / (none)", "sessoes": int(s * .6), "usuarios": int(s * .5), "engaj": .33},
        {"canal": "Organic Search", "origem": "google / organic", "sessoes": int(s * .15), "usuarios": int(s * .12), "engaj": .45},
        {"canal": "Organic Social", "origem": "linkedin / social", "sessoes": int(s * .15), "usuarios": 1, "engaj": .5},
        {"canal": "Referral", "origem": "github.com", "sessoes": s - int(s * .6) - 2 * int(s * .15), "usuarios": 2, "engaj": .7},
    ]
    kpi = {"sessions": s, "totalUsers": int(s * .7), "newUsers": int(s * .65), "screenPageViews": int(s * 1.6),
           "engagementRate": .38, "averageSessionDuration": 162.0}
    ant = {k: v * rnd.uniform(.6, 1.4) for k, v in kpi.items()}
    ant["engagementRate"] = .31
    paginas = [("/", int(s * .8)), ("/zabbix", 9), ("/veeam", 6), ("/kaspersky", 5), ("/trabalhe-conosco", 3)]
    return {"kpi": kpi, "kpi_ant": ant, "diario": diario, "fontes": fontes, "paginas": paginas}


# --------------------------------------------------------------------------- #
# Análise automática
# --------------------------------------------------------------------------- #
def var_pct(a, b):
    return None if not b else (a - b) / b * 100


def gerar_insights(dados):
    ins, recs = [], []
    for site in SITES:
        d = dados[site["nome"]]
        k, ka = d["kpi"], d["kpi_ant"]
        v = var_pct(k["sessions"], ka["sessions"])
        if v is not None:
            verbo = "cresceu" if v >= 0 else "caiu"
            ins.append(f"<b>{site['nome']} {verbo} {abs(v):.0f}% em sessões</b> "
                       f"({int(ka['sessions'])} → {int(k['sessions'])}), engajamento de "
                       f"{_fmt('engagementRate', ka['engagementRate'])} para {_fmt('engagementRate', k['engagementRate'])}.")
            if v <= -40:
                recs.append(f"<b>Investigar a queda do {site['nome']}</b>: verificar se a tag do GA4 continua "
                            f"instalada em todas as páginas e se houve mudanças recentes no site.")
        tot = sum(f["sessoes"] for f in d["fontes"]) or 1
        direto = sum(f["sessoes"] for f in d["fontes"] if f["canal"] == "Direct")
        if direto / tot >= .5:
            ins.append(f"<b>Tráfego direto em {direto/tot:.0%} no {site['nome']}</b>: canal sem rastreamento "
                       f"(pode incluir acessos internos e links sem UTM).")
        suspeitos = [f for f in d["fontes"] if f["usuarios"] <= 1 and f["sessoes"] >= 8]
        for f in suspeitos:
            ins.append(f"<b>Possível tráfego interno no {site['nome']}</b>: {f['sessoes']} sessões de "
                       f"<i>{f['origem']}</i> vieram de um único usuário.")
    org = sum(f["sessoes"] for s in SITES for f in dados[s["nome"]]["fontes"] if f["canal"] == "Organic Search")
    if org < 30:
        ins.append(f"<b>Busca orgânica baixa</b>: {org} sessões somando os dois sites na semana.")
        recs.append("<b>Priorizar SEO</b> nas páginas de serviço (Zabbix, Veeam, Kaspersky) para aumentar a busca orgânica.")
    if any(f["canal"] == "Direct" and f["sessoes"] / max(1, sum(x["sessoes"] for x in dados[s["nome"]]["fontes"])) >= .5
           for s in SITES for f in dados[s["nome"]]["fontes"]):
        recs.append("<b>Usar links com UTM</b> no LinkedIn, WhatsApp, e-mails e assinatura para reduzir o tráfego direto.")
    if any(f["usuarios"] <= 1 and f["sessoes"] >= 8 for s in SITES for f in dados[s["nome"]]["fontes"]):
        recs.append("<b>Revisar o filtro de tráfego interno</b> no GA4 (IPs do escritório, VPN e colaboradores).")
    return ins, recs


# --------------------------------------------------------------------------- #
# PDF
# --------------------------------------------------------------------------- #
def _fontes():
    for nome, arq in (("DV", "DejaVuSans.ttf"), ("DVB", "DejaVuSans-Bold.ttf"), ("DVI", "DejaVuSans-Oblique.ttf")):
        for base in ("/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu", str(BASE_DIR / "assets")):
            p = Path(base) / arq
            if p.exists():
                pdfmetrics.registerFont(TTFont(nome, str(p)))
                break
        else:
            raise FileNotFoundError(f"Fonte {arq} não encontrada (instale fonts-dejavu-core)")
    registerFontFamily("DV", normal="DV", bold="DVB", italic="DVI", boldItalic="DVB")


def _fmt(k, v):
    if k == "engagementRate":
        return f"{v*100:.1f}%".replace(".", ",")
    if k == "averageSessionDuration":
        return f"{int(v//60)}min {int(v % 60):02d}s"
    return f"{int(round(v))}"


def _delta(k, a, b):
    if k == "engagementRate":
        d = (a - b) * 100
        txt = f"{d:+.1f}".replace(".", ",") + " p.p."
    else:
        if not b:
            return "-"
        d = (a - b) / b * 100
        txt = f"{d:+.0f}%"
    return f'<font color="{VERDE_TXT if d >= 0 else VERM_TXT}">{txt}</font>'


def graficos(dados, atual):
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    dias = [atual[0] + dt.timedelta(i) for i in range(7)]
    labels = [f"{DIAS[d.weekday()]}\n{d:%d/%m}" for d in dias]

    fig, ax = plt.subplots(figsize=(8, 2.7), dpi=200)
    for s in SITES:
        n = s["nome"]
        ax.plot(labels, [dados[n]["diario"].get(d, 0) for d in dias], marker="o", ms=4, lw=2,
                color=COR_SITE[n], label=n)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    ax.grid(axis="y", alpha=.3)
    ax.set_ylabel("Sessões", fontsize=8)
    ax.tick_params(labelsize=7)
    ax.legend(fontsize=8, frameon=False)
    plt.tight_layout()
    f1 = WORK_DIR / "diario.png"
    plt.savefig(f1)
    plt.close()

    fig, axs = plt.subplots(1, 2, figsize=(8, 2.6), dpi=200)
    for ax, s in zip(axs, SITES):
        agg = defaultdict(int)
        for f in dados[s["nome"]]["fontes"]:
            agg[f["canal"]] += f["sessoes"]
        itens = sorted(agg.items(), key=lambda x: x[1])
        if not itens:
            ax.text(.5, .5, "Sem dados", ha="center", va="center")
            ax.axis("off")
            continue
        tot = sum(agg.values())
        ax.barh([CANAL_PT.get(k, k) for k, _ in itens], [v for _, v in itens],
                color=[COR_CANAL.get(k, "#B0B0B0") for k, _ in itens])
        for i, (_, v) in enumerate(itens):
            ax.text(v + max(agg.values()) * .02, i, f"{v} ({v/tot:.0%})", va="center", fontsize=7)
        ax.set_title(s["nome"], fontsize=9)
        ax.tick_params(labelsize=7)
        ax.set_xlim(0, max(agg.values()) * 1.35)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
    plt.tight_layout()
    f2 = WORK_DIR / "canais.png"
    plt.savefig(f2)
    plt.close()
    return f1, f2


def gerar_pdf(dados, atual, anterior, destino: Path):
    _fontes()
    S = lambda n, **k: ParagraphStyle(n, fontName=k.pop("f", "DV"), **k)  # noqa: E731
    h1 = S("h1", f="DVB", fontSize=20, textColor=GRAFITE, leading=24)
    h2 = S("h2", f="DVB", fontSize=13, textColor=GRAFITE, spaceBefore=12, spaceAfter=6, leading=16)
    h3 = S("h3", f="DVB", fontSize=10, textColor=VERMELHO, spaceBefore=8, spaceAfter=4)
    small = S("s", fontSize=8, textColor=CINZA, leading=11)
    cell = S("c", fontSize=8.5, leading=11)
    cellb = S("cb", f="DVB", fontSize=8.5, leading=11, textColor=colors.white)
    bul = S("bul", fontSize=9.5, leading=13.5, leftIndent=10, spaceAfter=3)
    P = lambda x, s=cell: Paragraph(str(x), s)  # noqa: E731

    lw, lh = 1940, 364
    W = A4[0] - 4 * cm

    def moldura(c, doc):
        c.saveState()
        c.setFillColor(GRAFITE)
        c.rect(0, A4[1] - .35 * cm, A4[0] * .72, .35 * cm, stroke=0, fill=1)
        c.setFillColor(VERMELHO)
        c.rect(A4[0] * .72, A4[1] - .35 * cm, A4[0] * .28, .35 * cm, stroke=0, fill=1)
        if doc.page > 1:
            w = 4.2 * cm
            c.drawImage(str(LOGO), A4[0] - 2 * cm - w, A4[1] - 1.45 * cm, width=w, height=w * lh / lw, mask="auto")
        c.setStrokeColor(colors.HexColor("#D9D9D9"))
        c.setLineWidth(.5)
        c.line(2 * cm, 1.6 * cm, A4[0] - 2 * cm, 1.6 * cm)
        c.setFont("DV", 7.5)
        c.setFillColor(CINZA)
        c.drawString(2 * cm, 1.2 * cm, "GLCtech · Monitoramento e Segurança em TI · Fonte: Google Analytics 4")
        c.setFillColor(VERMELHO)
        c.drawRightString(A4[0] - 2 * cm, 1.2 * cm, f"Página {doc.page}")
        c.restoreState()

    def tabela(rows, widths):
        t = Table(rows, colWidths=widths, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), GRAFITE), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, CLARO]),
            ("LINEBELOW", (0, -1), (-1, -1), .5, CINZA),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
        return t

    f_diario, f_canais = graficos(dados, atual)
    insights, recs = gerar_insights(dados)
    per = f"{atual[0]:%d/%m/%Y} a {atual[1]:%d/%m/%Y}"
    per_ant = f"{anterior[0]:%d/%m/%Y} a {anterior[1]:%d/%m/%Y}"

    st = [Image(str(LOGO), width=7.5 * cm, height=7.5 * cm * lh / lw, hAlign="LEFT"), Spacer(1, 14),
          Paragraph("Relatório Semanal de Tráfego Web", h1),
          Paragraph(f"GLCtech e GLCTech Sec · Semana de {per} (comparada a {per_ant})",
                    S("sub", fontSize=10, textColor=CINZA, leading=14)),
          Spacer(1, 6), HRFlowable(width="18%", thickness=2.5, color=VERMELHO, hAlign="LEFT", spaceAfter=4),
          Paragraph("Destaques da semana", h2)]
    st += [Paragraph("•&nbsp;&nbsp;" + t, bul) for t in insights] or [Paragraph("Sem destaques relevantes.", cell)]

    st.append(Paragraph("Indicadores principais", h2))
    nomes = {"sessions": "Sessões", "totalUsers": "Usuários", "newUsers": "Novos usuários",
             "screenPageViews": "Visualizações", "engagementRate": "Taxa de engajamento",
             "averageSessionDuration": "Duração média da sessão"}
    rows = [[P("Indicador", cellb)] + sum([[P(s["nome"], cellb), P("Var.", cellb)] for s in SITES], [])]
    for k, rot in nomes.items():
        r = [P(rot)]
        for s in SITES:
            a, b = dados[s["nome"]]["kpi"][k], dados[s["nome"]]["kpi_ant"][k]
            r += [P(_fmt(k, a)), P(_delta(k, a, b))]
        rows.append(r)
    st += [tabela(rows, [W * .30, W * .19, W * .16, W * .19, W * .16]), Spacer(1, 3),
           Paragraph("Variação em relação à semana anterior. Engajamento em pontos percentuais (p.p.).", small)]
    st.append(KeepTogether([Paragraph("Evolução diária de sessões", h2),
                            Image(str(f_diario), width=W, height=W * 2.7 / 8)]))

    st += [PageBreak(), Paragraph("Origem do tráfego por canal", h2), Image(str(f_canais), width=W, height=W * 2.6 / 8)]
    for s in SITES:
        rows = [[P("Canal", cellb), P("Origem / mídia", cellb), P("Sessões", cellb), P("Usuários", cellb), P("Engaj.", cellb)]]
        for f in dados[s["nome"]]["fontes"][:10]:
            rows.append([P(CANAL_PT.get(f["canal"], f["canal"])), P(f["origem"]), P(f["sessoes"]),
                         P(f["usuarios"]), P(f"{f['engaj']*100:.0f}%")])
        if len(rows) == 1:
            rows.append([P("Sem dados no período"), P(""), P(""), P(""), P("")])
        st.append(KeepTogether([Paragraph(f"{s['nome']} ({s['dominio']})", h3),
                                tabela(rows, [W * .20, W * .40, W * .13, W * .14, W * .13])]))

    st.append(Paragraph("Páginas mais acessadas", h2))
    p1, p2 = dados[SITES[0]["nome"]]["paginas"], dados[SITES[1]["nome"]]["paginas"]
    rows = [[P(SITES[0]["nome"], cellb), P("Views", cellb), P(SITES[1]["nome"], cellb), P("Views", cellb)]]
    for i in range(max(len(p1), len(p2), 1)):
        a = p1[i] if i < len(p1) else ("", "")
        b = p2[i] if i < len(p2) else ("", "")
        rows.append([P(a[0] if a[0] != "/" else "/ (home)"), P(a[1]), P(b[0] if b[0] != "/" else "/ (home)"), P(b[1])])
    st += [tabela(rows, [W * .36, W * .14, W * .36, W * .14]), Spacer(1, 3),
           Paragraph("Parâmetros de URL (ex.: fbclid, utm) são agrupados na página correspondente.", small)]

    if recs:
        st.append(KeepTogether([Paragraph("Recomendações", h2)] + [
            Paragraph(f"<b>{i}.</b>&nbsp; {t}", S(f"r{i}", fontSize=9.5, leading=13.5, spaceAfter=4))
            for i, t in enumerate(recs, 1)]))

    doc = SimpleDocTemplate(str(destino), pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm,
                            topMargin=2 * cm, bottomMargin=2 * cm,
                            title=f"Relatório Semanal de Tráfego - {per}", author="GLCtech")
    doc.build(st, onFirstPage=moldura, onLaterPages=moldura)
    return insights


# --------------------------------------------------------------------------- #
# E-mail
# --------------------------------------------------------------------------- #
def _smtp_send(msg: EmailMessage):
    if not (SMTP_USER and SMTP_PASSWORD):
        raise RuntimeError("SMTP_USER/SMTP_PASSWORD não configurados em config.env")
    if SMTP_PORT == 465:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=60) as s:
            s.login(SMTP_USER, SMTP_PASSWORD)
            s.send_message(msg)
    else:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=60) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASSWORD)
            s.send_message(msg)


def enviar_relatorio(pdf: Path, dados, atual, insights):
    per = f"{atual[0]:%d/%m} a {atual[1]:%d/%m/%Y}"
    linhas = ""
    for s in SITES:
        k, ka = dados[s["nome"]]["kpi"], dados[s["nome"]]["kpi_ant"]
        linhas += (f"<tr><td style='padding:6px 10px;border-bottom:1px solid #eee'><b>{s['nome']}</b></td>"
                   f"<td style='padding:6px 10px;border-bottom:1px solid #eee;text-align:right'>{int(k['sessions'])}</td>"
                   f"<td style='padding:6px 10px;border-bottom:1px solid #eee;text-align:right'>{_delta('sessions', k['sessions'], ka['sessions'])}</td>"
                   f"<td style='padding:6px 10px;border-bottom:1px solid #eee;text-align:right'>{int(k['totalUsers'])}</td>"
                   f"<td style='padding:6px 10px;border-bottom:1px solid #eee;text-align:right'>{_fmt('engagementRate', k['engagementRate'])}</td></tr>")
    destaques = "".join(f"<li style='margin-bottom:6px'>{i}</li>" for i in insights[:5])
    html = f"""<div style="font-family:Arial,Helvetica,sans-serif;color:#2E2E2E;max-width:640px">
<div style="height:5px;background:linear-gradient(90deg,#2E2E2E 72%,#BD2323 72%)"></div>
<h2 style="margin:18px 0 4px">Relatório Semanal de Tráfego Web</h2>
<p style="color:#5A5A5A;margin:0 0 16px">Semana de {per}</p>
<table style="border-collapse:collapse;width:100%;font-size:14px">
<tr style="background:#2E2E2E;color:#fff"><th style="padding:6px 10px;text-align:left">Site</th>
<th style="padding:6px 10px;text-align:right">Sessões</th><th style="padding:6px 10px;text-align:right">Var.</th>
<th style="padding:6px 10px;text-align:right">Usuários</th><th style="padding:6px 10px;text-align:right">Engaj.</th></tr>
{linhas}</table>
<h3 style="margin:20px 0 8px;color:#BD2323">Destaques</h3><ul style="padding-left:18px;font-size:14px">{destaques}</ul>
<p style="font-size:14px">O relatório completo segue em anexo (PDF).</p>
<p style="color:#8A8A8A;font-size:12px;margin-top:24px">Envio automático · GLCtech Monitoramento e Segurança em TI</p></div>"""

    msg = EmailMessage()
    msg["Subject"] = f"Relatório Semanal de Tráfego Web | {per}"
    msg["From"] = f"{MAIL_FROM_NAME} <{MAIL_FROM}>"
    msg["To"] = ", ".join(MAIL_TO)
    if MAIL_CC:
        msg["Cc"] = ", ".join(MAIL_CC)
    msg.set_content(f"Relatório Semanal de Tráfego Web - semana de {per}. O PDF segue em anexo.")
    msg.add_alternative(html, subtype="html")
    msg.add_attachment(pdf.read_bytes(), maintype="application", subtype="pdf", filename=pdf.name)
    _smtp_send(msg)


def enviar_alerta(erro: str):
    if not ALERT_TO:
        return
    try:
        msg = EmailMessage()
        msg["Subject"] = "[FALHA] Relatório Semanal de Tráfego Web"
        msg["From"] = f"{MAIL_FROM_NAME} <{MAIL_FROM}>"
        msg["To"] = ", ".join(ALERT_TO)
        msg.set_content(f"O agente de relatório semanal falhou em {dt.datetime.now():%d/%m/%Y %H:%M}.\n\n{erro}")
        _smtp_send(msg)
    except Exception:  # noqa: BLE001
        log.exception("Falha também ao enviar o alerta")


# --------------------------------------------------------------------------- #
def limpar_antigos():
    pdfs = sorted(OUTPUT_DIR.glob("Relatorio_Semanal_Trafego_*.pdf"))
    for p in pdfs[:-KEEP_REPORTS]:
        p.unlink(missing_ok=True)


def main():
    ap = argparse.ArgumentParser(description="Relatório semanal de tráfego GLCtech")
    ap.add_argument("--no-email", action="store_true", help="gera o PDF sem enviar")
    ap.add_argument("--mock", action="store_true", help="usa dados fictícios (teste)")
    ap.add_argument("--date", help="data de execução simulada (AAAA-MM-DD)")
    args = ap.parse_args()

    hoje = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
    atual, anterior = periodos(hoje)
    log.info("Iniciando relatório: semana %s a %s", atual[0], atual[1])

    try:
        coletor = coletar_mock if args.mock else coletar_ga4
        dados = {}
        for s in SITES:
            log.info("Coletando %s (property %s)", s["nome"], s["property_id"])
            dados[s["nome"]] = coletor(s, atual, anterior)

        pdf = OUTPUT_DIR / f"Relatorio_Semanal_Trafego_{atual[0]:%Y-%m-%d}_a_{atual[1]:%Y-%m-%d}.pdf"
        insights = gerar_pdf(dados, atual, anterior, pdf)
        log.info("PDF gerado: %s", pdf)

        if args.no_email:
            log.info("Envio desativado (--no-email)")
        else:
            enviar_relatorio(pdf, dados, atual, insights)
            log.info("E-mail enviado para %s", ", ".join(MAIL_TO + MAIL_CC))
        limpar_antigos()
        return 0
    except Exception:  # noqa: BLE001
        erro = traceback.format_exc()
        log.error("Falha na execução:\n%s", erro)
        if not args.no_email:
            enviar_alerta(erro)
        return 1


if __name__ == "__main__":
    sys.exit(main())
