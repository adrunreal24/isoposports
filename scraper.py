#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scraper streamxhd.sbs — corre en GitHub Actions.
Estrategia: regex sobre texto plano (más robusto para este sitio).
Salida: output/eventos_YYYYMMDD.txt + .json
"""

import json
import logging
import os
import random
import re
import sys
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import List, Optional, Dict

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_URL = "https://streamxhd.sbs/"
AGENDA_URLS = [
    "https://streamxhd.sbs/",
    "https://streamxhd.sbs/?embed=1",
]
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "output"))

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]
HEADERS_BASE = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Referer": BASE_URL,
}
TIMEOUT = 25
LOG = logging.getLogger("streamxhd")


@dataclass
class Servidor:
    nombre: str
    url: str
    numero: Optional[int] = None


@dataclass
class Evento:
    hora: str
    titulo: str
    deporte: str = ""
    torneo: str = ""
    estado: str = ""
    equipos: List[str] = field(default_factory=list)
    servidores: List[Servidor] = field(default_factory=list)
    fecha: str = ""

    @property
    def fingerprint(self) -> str:
        return f"{self.fecha}|{self.hora}|{self.titulo}".lower().strip()


def build_session() -> requests.Session:
    s = requests.Session()
    s.mount("https://", HTTPAdapter(max_retries=Retry(
        total=3, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )))
    return s


def get_html(session: requests.Session, url: str) -> Optional[str]:
    headers = {**HEADERS_BASE, "User-Agent": random.choice(USER_AGENTS)}
    try:
        r = session.get(url, headers=headers, timeout=TIMEOUT)
        r.raise_for_status()
        r.encoding = r.apparent_encoding or "utf-8"
        return r.text
    except requests.RequestException as e:
        LOG.warning(f"GET {url} falló: {e}")
        return None


def fetch_agenda(session) -> Optional[str]:
    for url in AGENDA_URLS:
        LOG.info(f"Probando {url}")
        html = get_html(session, url)
        if html and "live1.php" in html:
            LOG.info(f"OK {url} ({len(html)} bytes)")
            return html
    return None


# ─── Regex principales ───────────────────────────────────────────────────────
URL_RE = re.compile(r"https?://streamxhd\.sbs/live1\.php\?stream=([A-Za-z0-9_\-]+)")
TIME_RE = re.compile(r"\b([0-2]?\d:[0-5]\d)\b")
DATE_RE = re.compile(
    r"(?:HOY|AYER)?\s*(?:Lunes|Martes|Mi[eé]rcoles|Jueves|Viernes|S[aá]bado|Domingo)[,\s]+"
    r"(\d{1,2})\s+de\s+([a-záéíóú]+)\s+de\s+(\d{4})",
    re.IGNORECASE,
)
MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}
DEPORTES = ("F1", "MotoGP", "NBA", "MLB", "NFL", "NHL", "UFC", "ESPN KnockOut",
            "Ligue 1", "La Liga", "Premier League", "Serie A", "Bundesliga",
            "Champions", "Europa League", "Copa")


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip()


def html_to_text(html: str) -> str:
    """Convierte HTML a texto plano con saltos de línea preservados."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    return text


def parse_fecha_encabezado(text: str) -> str:
    """Busca el primer 'HOY Sábado, 31 de octubre de 2026' del texto."""
    m = DATE_RE.search(text)
    if not m:
        return ""
    dia = int(m.group(1))
    mes = MESES.get(m.group(2).lower())
    anio = int(m.group(3))
    if mes:
        try:
            return date(anio, mes, dia).isoformat()
        except ValueError:
            return ""
    return ""


def parse_eventos(text: str, fecha_default: str) -> List[Evento]:
    """
    Parte el texto plano por 'Servidores disponibles N'.
    Cada bloque = un evento. Se extrae info del bloque + links posteriores.
    """
    eventos: List[Evento] = []

    # Partir por 'Servidores disponibles'
    partes = re.split(r"\n?Servidores disponibles\s*\d*\n?", text)
    if len(partes) < 2:
        LOG.warning("No se encontró 'Servidores disponibles' en el texto")
        return []

    # Para cada parte posterior a la primera, el "cuerpo" del evento viene ANTES
    # y los links vienen en la parte siguiente. Por eso iteramos sobre índices.
    for i in range(1, len(partes)):
        cuerpo = partes[i - 1]  # texto del evento (sin links aún)
        links_bloque = partes[i].split("Servidores disponibles")[0]  # hasta el próximo evento

        # Extraer servidores: buscar pares "nombre\nurl"
        servidores: List[Servidor] = []
        seen_urls = set()
        # Buscar cada URL y tomar las 1-2 líneas anteriores como nombre
        for m_url in URL_RE.finditer(links_bloque):
            url = m_url.group(0)
            if url in seen_urls:
                continue
            seen_urls.add(url)
            # Buscar nombre antes de la URL en el bloque de links
            start = m_url.start()
            prev_text = links_bloque[max(0, start - 80):start]
            lines_prev = [l.strip() for l in prev_text.split("\n") if l.strip()]
            nombre = "Desconocido"
            if lines_prev:
                # El último token con letras que no sea número ni "Servidor"
                for cand in reversed(lines_prev):
                    cand_clean = re.sub(r"^\d+\s+", "", cand).strip()
                    if cand_clean and not cand_clean.isdigit() and cand_clean != "Servidor":
                        nombre = cand_clean
                        break
            numero = None
            # número de servidor
            num_match = re.match(r"^(\d+)\s+", lines_prev[-1]) if lines_prev else None
            if num_match:
                numero = int(num_match.group(1))
            servidores.append(Servidor(nombre=nombre, url=url, numero=numero))

        if not servidores:
            continue

        # Analizar el cuerpo para título, equipos, hora, estado, deporte
        lineas = [_norm(l) for l in cuerpo.split("\n") if l.strip()]
        if not lineas:
            continue

        # Hora: última ocurrencia de HH:MM en el cuerpo
        hora = ""
        for l in reversed(lineas):
            if l.upper() in ("EN VIVO", "PRONTO", "FINALIZADO"):
                continue
            m = TIME_RE.fullmatch(l.strip())
            if m:
                hora = m.group(1)
                break

        # Estado
        estado = ""
        for l in lineas:
            lu = l.upper()
            if lu in ("EN VIVO", "PRONTO", "FINALIZADO"):
                estado = lu
                break

        # Equipos: buscar línea "X" seguida de "VS" seguida de "Y"
        equipos = []
        for idx_l, l in enumerate(lineas):
            if l.upper() == "VS" and idx_l > 0 and idx_l + 1 < len(lineas):
                eq1 = lineas[idx_l - 1]
                eq2 = lineas[idx_l + 1]
                # Evitar falsos positivos
                if eq1 and eq2 and eq1.upper() not in ("VS",) and eq2.upper() not in ("VS",):
                    equipos = [eq1, eq2]
                    break

        # Título: si hay equipos "X vs Y". Si no, buscar línea con "GP" o emoji de deporte.
        titulo = ""
        if equipos:
            titulo = f"{equipos[0]} vs {equipos[1]}"
        else:
            # Buscar línea que parezca título (contiene "GP " o una palabra >3 letras)
            for l in lineas:
                if l.upper() in ("EN VIVO", "PRONTO", "FINALIZADO", "VS"):
                    continue
                if TIME_RE.fullmatch(l):
                    continue
                if "GP " in l or len(l) > 5:
                    titulo = l
                    break

        # Deporte
        deporte = ""
        for tok in DEPORTES:
            if tok.lower() in cuerpo.lower():
                deporte = tok
                break
        if not deporte and "🏎" in cuerpo:
            deporte = "F1"
        elif not deporte and "🥊" in cuerpo:
            deporte = "Boxeo"
        elif not deporte and equipos:
            deporte = "Fútbol"

        if not titulo:
            continue

        eventos.append(Evento(
            hora=hora,
            titulo=titulo,
            deporte=deporte,
            torneo=deporte if deporte in DEPORTES else "",
            estado=estado,
            equipos=equipos,
            servidores=servidores,
            fecha=fecha_default,
        ))

    return eventos


def merge(eventos: List[Evento]) -> List[Evento]:
    idx: Dict[str, Evento] = {}
    for ev in eventos:
        k = ev.fingerprint
        if k not in idx:
            idx[k] = ev
            continue
        base = idx[k]
        existentes = {s.url for s in base.servidores}
        for s in ev.servidores:
            if s.url not in existentes:
                base.servidores.append(s)
                existentes.add(s.url)
    return sorted(idx.values(), key=lambda e: (e.hora or "99:99", e.titulo))


def write_txt(eventos, path: Path):
    now = datetime.now(timezone.utc).isoformat()
    lines = [
        "=" * 70,
        f"EVENTOS STREAMXHD — {now}",
        f"Total: {len(eventos)} eventos",
        "=" * 70,
        "",
    ]
    for ev in eventos:
        lines.append(f"── {ev.hora or '--:--'}  {ev.titulo}")
        if ev.equipos:
            lines.append(f"   {ev.equipos[0]}  VS  {ev.equipos[1]}")
        meta = " | ".join(x for x in [ev.deporte, ev.torneo, ev.estado] if x)
        if meta:
            lines.append(f"   [{meta}]")
        lines.append(f"   Servidores: {len(ev.servidores)}")
        for i, s in enumerate(ev.servidores, 1):
            lines.append(f"     {i}. {s.nombre}")
            lines.append(f"        {s.url}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_json(eventos, path: Path):
    path.write_text(
        json.dumps([asdict(e) for e in eventos], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    session = build_session()
    html = fetch_agenda(session)
    if not html:
        LOG.error("Sin HTML de agenda")
        return 1

    text = html_to_text(html)
    LOG.info(f"Texto plano: {len(text)} caracteres")

    fecha_ctx = parse_fecha_encabezado(text) or date.today().isoformat()
    LOG.info(f"Fecha detectada: {fecha_ctx}")

    eventos = parse_eventos(text, fecha_ctx)
    LOG.info(f"Eventos parseados: {len(eventos)}")

    if not eventos:
        # Guardar debug para inspeccionar
        (OUTPUT_DIR / "debug_text.txt").write_text(text[:20000], encoding="utf-8")
        LOG.error("Sin eventos parseados (debug_text.txt guardado)")
        return 1

    eventos = merge(eventos)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    write_txt(eventos, OUTPUT_DIR / f"eventos_{stamp}.txt")
    write_json(eventos, OUTPUT_DIR / f"eventos_{stamp}.json")
    LOG.info(f"{len(eventos)} eventos guardados")
    return 0


if __name__ == "__main__":
    sys.exit(main())
