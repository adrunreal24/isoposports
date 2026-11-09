#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Extractor UNREALMAT v9 (Título exacto y clasificación perfecta)
"""

import asyncio
import base64
import json
import logging
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Tuple

import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

# ─── CONFIG ──────────────────────────────────────────────────────────────────
BASE_URLS = [
    "https://streamxhd.com/",
    "https://streamxhd.st/",
    "https://streamxhd.click/",
    "https://streamxhd.sbs/",
]

LOCAL_OUT = Path(__file__).parent / "output_local"
LOCAL_OUT.mkdir(exist_ok=True)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
TIMEOUT_MS = 30000
WAIT_AFTER_LOAD = 5000

CANALES_NOMBRES = {
    "disney": "DISNEY+", "disney1": "DISNEY+ 1", "disney2": "DISNEY+ 2",
    "disney3": "DISNEY+ 3", "disney4": "DISNEY+ 4", "disney5": "DISNEY+ 5",
    "disney6": "DISNEY+ 6", "disney7": "DISNEY+ 7", "disney8": "DISNEY+ 8",
    "disney9": "DISNEY+ 9", "disney10": "DISNEY+ 10", "disney11": "DISNEY+ 11",
    "disney12": "DISNEY+ 12", "disney13": "DISNEY+ 13", "disney14": "DISNEY+ 14",
    "disney15": "DISNEY+ 15", "disney16": "DISNEY+ 16",
    "espn": "ESPN", "espn2": "ESPN 2", "espn3": "ESPN 3", "espn4": "ESPN 4",
    "espn5": "ESPN 5", "espn6": "ESPN 6", "espn7": "ESPN 7",
    "espn2ar": "ESPN 2 AR", "espnar": "ESPN AR",
    "espnpremium": "ESPN PREMIUM", "espnplus1": "ESPN +", "espndeportes": "ESPN DEPORTES",
    "fox1ar": "FOX SPORTS 1 AR", "fox2ar": "FOX SPORTS 2 AR",
    "fox3ar": "FOX SPORTS 3 AR", "foxone": "FOX ONE",
    "fox_deportes_usa": "FOX DEPORTES USA",
    "l1max": "LIGA 1 MAX", "max1": "MAX 1", "max2": "MAX 2",
    "premiere1": "PREMIERE 1", "premiere2": "PREMIERE 2",
    "tigo1": "TIGO SPORTS 1", "tudn_usa": "TUDN USA",
    "winplus": "WIN SPORTS +", "winsports": "WIN SPORTS", "zapping_ec": "ZAPPING EC",
    "nba": "NBA TV", "nba1": "NBA LEAGUE PASS 1", "nba2": "NBA LEAGUE PASS 2",
    "nba3": "NBA LEAGUE PASS 3", "nba4": "NBA LEAGUE PASS 4",
    "nba5": "NBA LEAGUE PASS 5", "nba6": "NBA LEAGUE PASS 6",
    "nba7": "NBA LEAGUE PASS 7", "nba8": "NBA LEAGUE PASS 8",
    "nba9": "NBA LEAGUE PASS 9",
    "amazon1": "PRIME VIDEO 1", "amazon2": "PRIME VIDEO 2",
    "dsports": "DSPORTS", "daznlaliga": "DAZN LALIGA",
    "sporttvbr1": "SPORTV 1", "sporttvbr3": "SPORTV 3",
    "fanatiz1": "FANATIZ 1", "universo": "UNIVERSO",
    "tntsports": "TNT SPORTS", "laligahypermotion": "LALIGA HYPERMOTION",
}

LOG = logging.getLogger("extractor")
LOG.setLevel(logging.INFO)
fh = logging.FileHandler(LOCAL_OUT / "extractor.log.txt", mode="a", encoding="utf-8")
fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
LOG.addHandler(fh)
sh = logging.StreamHandler()
sh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
LOG.addHandler(sh)


# ─── HELPERS ─────────────────────────────────────────────────────────────────
def normalizar(texto: str) -> str:
    if not texto: return ""
    return unicodedata.normalize("NFKD", texto).encode("ASCII", "ignore").decode("utf-8").upper().strip()

def clave_dedup(titulo: str, hora: str) -> str:
    t = normalizar(titulo)
    t = re.sub(r"[\-–—_,\.\|]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return f"{t}__{(hora or '00:00').zfill(5)}"

def cargar_credenciales_github() -> Tuple[str, str, str]:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        LOG.error("❌ No se encontró token.")
        sys.exit(1)
    return token, "adrunreal24", "isoposports"

# ─── KEYWORDS ────────────────────────────────────────────────────────────────
F1_KEYWORDS = [normalizar(k) for k in ["F1", "MOTOGP", "MOTO GP", "PRACTICA", "FREE PRACTICE", "CLASIFICACION", "QUALY", "SPRINT", "GRAND PRIX", "GRAN PREMIO", "VERSTAPPEN", "HAMILTON", "LECLERC", "SAINZ", "ALONSO", "PEREZ", "RED BULL", "FERRARI", "MCLAREN", "FP1", "FP2", "FP3", "ON BOARD", "COLAPINTO", "QATAR"]]
NBA_KEYWORDS = [normalizar(k) for k in ["NBA", "BASKET", "BALONCESTO", "LAKERS", "CELTICS", "BULLS", "HEAT", "WARRIORS", "BUCKS", "SUNS", "MAVERICKS", "KNICKS", "BLAZERS", "NETS", "76ERS", "SIXERS", "NUGGETS", "CLIPPERS", "SPURS", "ROCKETS", "GRIZZLIES", "PELICANS", "THUNDER", "TIMBERWOLVES", "CAVALIERS", "PISTONS", "PACERS", "HORNETS", "WIZARDS", "MAGIC", "HAWKS", "RAPTORS", "KINGS", "JAZZ"]]
TENIS_KEYWORDS = [normalizar(k) for k in ["ATP", "WTA", "TENIS", "TENNIS", "FRITZ", "ALCARAZ", "DJOKOVIC", "NADAL", "SABALENKA", "MUCHOVA"]]
BOXEO_KEYWORDS = [normalizar(k) for k in ["BOXEO", "BOXING", "KNOCKOUT", "BXSTRS", "CANELO", "WBC", "WBA", "IBF", "WBO"]]
NFL_KEYWORDS = [normalizar(k) for k in ["NFL", "RAVENS", "JAGUARS", "CHIEFS", "PATRIOTS", "COWBOYS", "PACKERS", "49ERS", "EAGLES", "STEELERS", "BRONCOS", "VIKINGS", "BILLS"]]

FUTBOL_ESPECIFICO = [("CHAMPIONS", "UEFA CHAMPIONS LEAGUE"), ("EUROPA", "UEFA EUROPA LEAGUE"), ("LIBERTADORES", "COPA LIBERTADORES"), ("SUDAMERICANA", "COPA SUDAMERICANA"), ("PREMIER", "PREMIER LEAGUE"), ("BUNDESLIGA", "BUNDESLIGA"), ("SERIE A", "SERIE A"), ("LIGUE 1", "LIGUE 1"), ("LALIGA", "LALIGA EA SPORTS"), ("COPA", "COPA")]

# ─── SCRAPING ────────────────────────────────────────────────────────────────
STATUS_PATTERN = re.compile(r"^(EN VIVO|PRONTO|FINALIZADO)[ \t]*\n\s*(\d{1,2}:\d{2})(?:\s|$)", re.MULTILINE)
SERVER_URL_RE = re.compile(r"https?://streamxhd\.[a-z]+/live1\.php\?stream=([A-Za-z0-9_\-]+)")
EMOJI_HINT = {"🏎": "F1", "🏎️": "F1", "🥊": "BOXEO", "🎾": "TENIS", "⚽": "FUTBOL", "🏀": "NBA", "🏈": "NFL"}

def _is_emoji_line(line: str) -> bool:
    if not line or not line.strip(): return False
    return len(line.strip()) <= 4 and not any(c.isalnum() for c in line)

def _extraer_hint_emoji(lines: List[str]) -> str:
    for line in lines:
        for emoji, hint in EMOJI_HINT.items():
            if emoji in line: return hint
    return ""

async def _scrape_url(url: str) -> List[Dict]:
    eventos: List[Dict] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            context = await browser.new_context(user_agent=UA, locale="es-ES")
            page = await context.new_page()
            LOG.info(f"Probando: {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
            await page.wait_for_timeout(WAIT_AFTER_LOAD)

            soup = BeautifulSoup(await page.content(), "lxml")
            for tag in soup(["script", "style", "noscript"]): tag.decompose()
            texto = soup.get_text("\n", strip=True)

            matches = list(STATUS_PATTERN.finditer(texto))
            LOG.info(f"Detectados {len(matches)} marcadores en {url}")

            for idx, m in enumerate(matches):
                status = m.group(1)
                time_str = m.group(2).zfill(5)
                next_start = matches[idx + 1].start() if idx + 1 < len(matches) else len(texto)
                body_text = texto[m.end():next_start]

                # --- EXTRACCIÓN EXACTA DEL TÍTULO ---
                # Buscamos la línea que está justo antes de "Servidores disponibles"
                titulo = None
                title_match = re.search(r"\n([^\n]+)\nServidores disponibles", body_text)
                if title_match:
                    titulo = title_match.group(1).strip()
                
                # Fallback por si no hay servidores (usar el bloque ##)
                if not titulo:
                    detail_m = re.search(r"\n##\s*([^\n]+)", body_text)
                    if detail_m:
                        titulo = detail_m.group(1).strip()

                # Limpiar emojis y códigos raros si quedaron en el título
                if titulo:
                    titulo = re.sub(r"[\U00010000-\U0010ffff]", "", titulo).strip()
                    titulo = re.sub(r"^VS\s+", "", titulo)
                    titulo = re.sub(r"\s+VS$", "", titulo)

                if not titulo or len(titulo) <= 3:
                    continue

                # Detección de deporte (Emoji + Palabras clave)
                sport_hint = _extraer_hint_emoji(body_text[:300].split("\n"))

                servidores = []
                seen = set()
                for url_match in SERVER_URL_RE.finditer(body_text):
                    stream_id = url_match.group(1)
                    full_url = url_match.group(0)
                    if full_url not in seen:
                        seen.add(full_url)
                        servidores.append({"nombre": CANALES_NOMBRES.get(stream_id.lower(), stream_id.upper()), "url": full_url})

                eventos.append({
                    "id": f"evt_{idx}", "titulo": titulo, "hora": time_str, "status": status,
                    "servidores": servidores, "sport_hint": sport_hint
                })
            await context.close()
        except Exception as e:
            LOG.error(f"Error en {url}: {e}")
        finally:
            await browser.close()
    return eventos

async def obtener_eventos() -> List[Dict]:
    for url in BASE_URLS:
        eventos = await _scrape_url(url)
        if eventos: return eventos
    return []

# ─── DEDUPLICACIÓN Y CLASIFICACIÓN ───────────────────────────────────────────
def deduplicar_eventos(eventos: List[Dict]) -> List[Dict]:
    dedup: Dict[str, Dict] = {}
    for e in eventos:
        clave = clave_dedup(e["titulo"], e["hora"])
        if clave not in dedup:
            dedup[clave] = e
        else:
            urls_vistas = {s["url"] for s in dedup[clave]["servidores"]}
            for srv in e["servidores"]:
                if srv["url"] not in urls_vistas:
                    dedup[clave]["servidores"].append(srv)
            orden = {"EN VIVO": 3, "PRONTO": 2, "FINALIZADO": 1}
            if orden.get(e["status"], 0) > orden.get(dedup[clave]["status"], 0):
                dedup[clave]["status"] = e["status"]
            if not dedup[clave]["sport_hint"] and e.get("sport_hint"):
                dedup[clave]["sport_hint"] = e["sport_hint"]
    return list(dedup.values())

def clasificar_evento(titulo: str, sport_hint: str) -> Tuple[str, str]:
    t = normalizar(titulo)
    if sport_hint == "F1" or any(k in t for k in F1_KEYWORDS): return "f1", "FÓRMULA 1 / MOTOR"
    if sport_hint == "NBA" or any(k in t for k in NBA_KEYWORDS): return "nba", "NBA / BALONCESTO"
    if sport_hint == "TENIS" or any(k in t for k in TENIS_KEYWORDS): return "futbol", "TENIS"
    if sport_hint == "BOXEO" or any(k in t for k in BOXEO_KEYWORDS): return "futbol", "BOXEO"
    if sport_hint == "NFL" or any(k in t for k in NFL_KEYWORDS): return "futbol", "NFL"
    for key, torneo in FUTBOL_ESPECIFICO:
        if key in t: return "futbol", torneo
    return "futbol", "FÚTBOL INTERNACIONAL"

def clasificar_eventos(eventos: List[Dict]) -> Dict[str, List[Dict]]:
    clasificados = {"futbol": [], "nba": [], "f1": []}
    for e in eventos:
        deporte, torneo = clasificar_evento(e["titulo"], e.get("sport_hint", ""))
        e["torneo"] = torneo
        e.pop("sport_hint", None)
        clasificados[deporte].append(e)
    return clasificados

# ─── GITHUB ──────────────────────────────────────────────────────────────────
def subir_github(token: str, user: str, repo: str, path_repo: str, contenido: str) -> bool:
    api = f"https://api.github.com/repos/{user}/{repo}/contents/{path_repo}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    r = requests.get(api, headers=headers, timeout=20)
    sha = r.json().get("sha") if r.status_code == 200 else None
    data = {"message": f"chore: update {path_repo}", "content": base64.b64encode(contenido.encode()).decode(), "branch": "main"}
    if sha: data["sha"] = sha
    resp = requests.put(api, headers=headers, json=data, timeout=30)
    if resp.status_code in (200, 201): return True
    LOG.error(f"GitHub Error ({path_repo}): {resp.status_code} - {resp.text}")
    return False

# ─── MAIN ────────────────────────────────────────────────────────────────────
async def main():
    token, user, repo = cargar_credenciales_github()
    eventos_crudos = await obtener_eventos()
    if not eventos_crudos: return 1

    eventos = deduplicar_eventos(eventos_crudos)
    clasificados = clasificar_eventos(eventos)
    ahora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    LOG.info(f"Clasificación → Fútbol={len(clasificados['futbol'])} NBA={len(clasificados['nba'])} F1={len(clasificados['f1'])}")

    for deporte, lista in clasificados.items():
        payload = {"fecha_actualizacion": ahora, "total": len(lista), "eventos": lista}
        subir_github(token, user, repo, f"output/agenda_{deporte}.json", json.dumps(payload, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
