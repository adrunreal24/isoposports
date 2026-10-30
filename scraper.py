#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scraper streamxhd.sbs — usa Playwright (navegador real headless).
Corre en GitHub Actions.
"""

import json
import logging
import os
import re
import sys
import asyncio
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import List, Optional, Dict

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

BASE_URL = "https://streamxhd.sbs/"
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "output"))
TIMEOUT_MS = 45000
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


async def fetch_html() -> Optional[str]:
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            locale="es-ES",
            viewport={"width": 1366, "height": 900},
        )
        page = await context.new_page()
        try:
            LOG.info(f"Cargando {BASE_URL}")
            await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
            # esperar a que aparezcan links live1.php en el DOM
            try:
                await page.wait_for_selector('a[href*="live1.php"]', timeout=TIMEOUT_MS)
                LOG.info("Links live1.php detectados en el DOM")
            except Exception:
                LOG.warning("No aparecieron links live1.php (continuamos igual)")
            # Esperar extra por si renderiza más
            await page.wait_for_timeout(4000)
            # Scroll para forzar render de lazy content
            for _ in range(5):
                await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
                await page.wait_for_timeout(800)
            html = await page.content()
            LOG.info(f"HTML renderizado: {len(html)} bytes")
            return html
        except Exception as e:
            LOG.error(f"Error Playwright: {e}")
            return None
        finally:
            await context.close()
            await browser.close()


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text("\n", strip=True)


def parse_fecha_encabezado(text: str) -> str:
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
    eventos: List[Evento] = []
    partes = re.split(r"\n?Servidores disponibles\s*\d*\n?", text)
    LOG.info(f"Bloques 'Servidores disponibles': {len(partes) - 1}")
    if len(partes) < 2:
        return []

    for i in range(1, len(partes)):
        cuerpo = partes[i - 1]
        links_bloque = partes[i].split("Servidores disponibles")[0]

        servidores: List[Servidor] = []
        seen_urls = set()
        for m_url in URL_RE.finditer(links_bloque):
            url = m_url.group(0)
            if url in seen_urls:
                continue
            seen_urls.add(url)
            start = m_url.start()
            prev_text = links_bloque[max(0, start - 100):start]
            lines_prev = [l.strip() for l in prev_text.split("\n") if l.strip()]
            nombre = "Desconocido"
            numero = None
            for cand in reversed(lines_prev):
                cand_clean = re.sub(r"^\d+\s+", "", cand).strip()
                if cand_clean and not cand_clean.isdigit() and cand_clean != "Servidor":
                    nombre = cand_clean
                    m_num = re.match(r"^(\d+)\s+", cand)
                    if m_num:
                        numero = int(m_num.group(1))
                    break
            servidores.append(Servidor(nombre=nombre, url=url, numero=numero))

        if not servidores:
            continue

        lineas = [_norm(l) for l in cuerpo.split("\n") if l.strip()]
        if not lineas:
            continue

        hora = ""
        for l in reversed(lineas):
            if l.upper() in ("EN VIVO", "PRONTO", "FINALIZADO"):
                continue
            m = TIME_RE.fullmatch(l.strip())
            if m:
                hora = m.group(1)
                break

        estado = ""
        for l in lineas:
            lu = l.upper()
            if lu in ("EN VIVO", "PRONTO", "FINALIZADO"):
                estado = lu
                break

        equipos = []
        for idx_l, l in enumerate(lineas):
            if l.upper() == "VS" and 0 < idx_l < len(lineas) - 1:
                eq1 = lineas[idx_l - 1]
                eq2 = lineas[idx_l + 1]
                if eq1 and eq2 and eq1.upper() not in ("VS",) and eq2.upper() not in ("VS",):
                    equipos = [eq1, eq2]
                    break

        titulo = ""
        if equipos:
            titulo = f"{equipos[0]} vs {equipos[1]}"
        else:
            for l in lineas:
                if l.upper() in ("EN VIVO", "PRONTO", "FINALIZADO", "VS"):
                    continue
                if TIME_RE.fullmatch(l):
                    continue
                if "GP " in l or len(l) > 5:
                    titulo = l
                    break

        deporte = ""
        for tok in DEPORTES:
            if tok.lower() in cuerpo.lower():
                deporte = tok
                break
        if not deporte:
            if "🏎" in cuerpo:
                deporte = "F1"
            elif "🥊" in cuerpo:
                deporte = "Boxeo"
            elif equipos:
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

    html = asyncio.run(fetch_html())
    if not html:
        LOG.error("Sin HTML")
        return 1

    text = html_to_text(html)
    LOG.info(f"Texto plano: {len(text)} caracteres")

    # guardar texto completo para debug
    (OUTPUT_DIR / "debug_text.txt").write_text(text, encoding="utf-8")

    fecha_ctx = parse_fecha_encabezado(text) or date.today().isoformat()
    LOG.info(f"Fecha detectada: {fecha_ctx}")

    eventos = parse_eventos(text, fecha_ctx)
    LOG.info(f"Eventos parseados: {len(eventos)}")

    if not eventos:
        LOG.error("Sin eventos")
        return 1

    eventos = merge(eventos)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    write_txt(eventos, OUTPUT_DIR / f"eventos_{stamp}.txt")
    write_json(eventos, OUTPUT_DIR / f"eventos_{stamp}.json")
    LOG.info(f"{len(eventos)} eventos guardados")
    return 0


if __name__ == "__main__":
    sys.exit(main())
