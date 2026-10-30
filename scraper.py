#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scraper streamxhd.sbs — corre en GitHub Actions.
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
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_URL = "https://streamxhd.sbs/"
AGENDA_URLS = [
    "https://streamxhd.sbs/",
    "https://streamxhd.sbs/?embed=1",
    "https://streamxhd.sbs/index.php",
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
TIMEOUT = 20
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


URL_RE = re.compile(r"https?://streamxhd\.sbs/live1\.php\?stream=[A-Za-z0-9_\-]+")
TIME_RE = re.compile(r"\b([0-2]?\d:[0-5]\d)\b")


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip()


def parse_html_blocks(html: str, fecha: str) -> List[Evento]:
    soup = BeautifulSoup(html, "lxml")
    eventos, seen = [], set()
    for a in soup.find_all("a", href=True):
        if "live1.php?stream=" not in a["href"]:
            continue
        block = a
        for _ in range(8):
            p = block.parent
            if p is None or p.name in ("html", "body"):
                break
            block = p
            if TIME_RE.search(_norm(block.get_text(" ", strip=True))):
                break
        if id(block) in seen:
            continue
        seen.add(id(block))
        servers, surls = [], set()
        for link in block.find_all("a", href=True):
            if "live1.php?stream=" in link["href"]:
                url = urljoin(BASE_URL, link["href"])
                if url in surls:
                    continue
                surls.add(url)
                parent = link.find_parent(["div", "li", "td", "tr", "p"]) or link
                txt = _norm(parent.get_text(" ", strip=True))
                txt = URL_RE.sub("", txt).replace("Ver aquí", "").replace("Copiar", "")
                nm = re.sub(r"^\d+\s*", "", txt).strip() or "Desconocido"
                num = None
                m = re.match(r"^(\d+)\s+(.*)$", txt)
                if m:
                    num, nm = int(m.group(1)), m.group(2)
                servers.append(Servidor(nombre=nm, url=url, numero=num))
        if not servers:
            continue
        txt = _norm(block.get_text(" ", strip=True))
        m_t = TIME_RE.search(txt)
        if not m_t:
            continue
        estado = next((t for t in ("EN VIVO", "PRONTO", "FINALIZADO") if t in txt.upper()), "")
        equipos = []
        m_vs = re.search(r"([A-Za-z0-9À-ÿ\.\-\s]{2,40})\s+(?:VS|vs)\s+([A-Za-z0-9À-ÿ\.\-\s]{2,40})", txt)
        if m_vs:
            equipos = [_norm(m_vs.group(1)), _norm(m_vs.group(2))]
        h = block.find(["h2", "h3", "h4"])
        titulo = _norm(h.get_text(" ", strip=True)) if h else (f"{equipos[0]} vs {equipos[1]}" if equipos else txt[:80])
        titulo = re.sub(r"^\d{1,2}:\d{2}\s*", "", titulo)
        titulo = re.sub(r"\b(EN VIVO|PRONTO|FINALIZADO)\b", "", titulo, flags=re.I).strip()
        deporte = "F1" if ("🏎" in txt or "GP " in txt or "F1" in txt) else ("NBA" if "NBA" in txt else ("Fútbol" if equipos else ""))
        torneo = next((t for t in ("NBA League Pass","Ligue 1","La Liga","Premier League","Serie A","Bundesliga","F1","NBA","Champions","Copa") if t.lower() in txt.lower()), "")
        eventos.append(Evento(hora=m_t.group(1), titulo=titulo, deporte=deporte,
                              torneo=torneo, estado=estado, equipos=equipos,
                              servidores=servers, fecha=fecha))
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
    fecha = date.today().isoformat()
    eventos = merge(parse_html_blocks(html, fecha))
    if not eventos:
        LOG.error("Sin eventos parseados")
        return 1
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    write_txt(eventos, OUTPUT_DIR / f"eventos_{stamp}.txt")
    write_json(eventos, OUTPUT_DIR / f"eventos_{stamp}.json")
    LOG.info(f"{len(eventos)} eventos guardados")
    return 0


if __name__ == "__main__":
    sys.exit(main())
