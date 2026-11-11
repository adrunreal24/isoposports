#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import asyncio, base64, json, logging, os, re, sys, unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Tuple
import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

BASE_URLS = ["https://streamxhd.com/", "https://streamxhd.st/", "https://streamxhd.click/", "https://streamxhd.sbs/"]
LOCAL_OUT = Path(__file__).parent / "output_local"
LOCAL_OUT.mkdir(exist_ok=True)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
TIMEOUT_MS = 30000
WAIT_AFTER_LOAD = 5000

CANALES_NOMBRES = {
    "disney": "DISNEY+", "disney1": "DISNEY+ 1", "disney2": "DISNEY+ 2", "disney3": "DISNEY+ 3", "disney4": "DISNEY+ 4", "disney5": "DISNEY+ 5", "disney6": "DISNEY+ 6", "disney7": "DISNEY+ 7", "disney8": "DISNEY+ 8", "disney9": "DISNEY+ 9", "disney10": "DISNEY+ 10", "disney11": "DISNEY+ 11", "disney12": "DISNEY+ 12", "disney13": "DISNEY+ 13", "disney14": "DISNEY+ 14", "disney15": "DISNEY+ 15", "disney16": "DISNEY+ 16",
    "espn": "ESPN", "espn2": "ESPN 2", "espn3": "ESPN 3", "espn4": "ESPN 4", "espn5": "ESPN 5", "espn6": "ESPN 6", "espn7": "ESPN 7", "espn2ar": "ESPN 2 AR", "espnar": "ESPN AR", "espnpremium": "ESPN PREMIUM", "espnplus1": "ESPN +", "espnplus2": "ESPN + 2", "espndeportes": "ESPN DEPORTES",
    "fox1ar": "FOX SPORTS 1 AR", "fox2ar": "FOX SPORTS 2 AR", "fox3ar": "FOX SPORTS 3 AR", "foxone": "FOX ONE", "fox_deportes_usa": "FOX DEPORTES USA",
    "l1max": "LIGA 1 MAX", "max1": "MAX 1", "max2": "MAX 2", "premiere1": "PREMIERE 1", "premiere2": "PREMIERE 2", "tigo1": "TIGO SPORTS 1", "tudn_usa": "TUDN USA", "winplus": "WIN SPORTS +", "winsports": "WIN SPORTS", "zapping_ec": "ZAPPING EC", "vix1": "VIX 1",
    "nba": "NBA TV", "nba1": "NBA LEAGUE PASS 1", "nba2": "NBA LEAGUE PASS 2", "nba3": "NBA LEAGUE PASS 3", "nba4": "NBA LEAGUE PASS 4", "nba5": "NBA LEAGUE PASS 5", "nba6": "NBA LEAGUE PASS 6", "nba7": "NBA LEAGUE PASS 7", "nba8": "NBA LEAGUE PASS 8", "nba9": "NBA LEAGUE PASS 9",
    "amazon1": "PRIME VIDEO 1", "amazon2": "PRIME VIDEO 2", "dsports": "DSPORTS", "daznlaliga": "DAZN LALIGA", "sporttvbr1": "SPORTV 1", "sporttvbr3": "SPORTV 3", "fanatiz1": "FANATIZ 1", "universo": "UNIVERSO", "tntsports": "TNT SPORTS", "laligahypermotion": "LALIGA HYPERMOTION"
}

LOG = logging.getLogger("extractor")
LOG.setLevel(logging.INFO)
sh = logging.StreamHandler()
sh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
LOG.addHandler(sh)

def normalizar(texto: str) -> str:
    if not texto: return ""
    return unicodedata.normalize("NFKD", texto).encode("ASCII", "ignore").decode("utf-8").upper().strip()

def clave_dedup(titulo: str, hora: str) -> str:
    t = re.sub(r"[\-–—_,\.\|]+", " ", normalizar(titulo))
    t = re.sub(r"\s+", " ", t).strip()
    return f"{t}__{(hora or '00:00').zfill(5)}"

F1_KEYWORDS = [normalizar(k) for k in ["F1", "MOTOGP", "MOTO GP", "PRACTICA", "FREE PRACTICE", "CLASIFICACION", "QUALY", "SPRINT", "GRAND PRIX", "GRAN PREMIO", "VERSTAPPEN", "HAMILTON", "LECLERC", "SAINZ", "ALONSO", "PEREZ", "RED BULL", "FERRARI", "MCLAREN", "FP1", "FP2", "FP3", "ON BOARD", "COLAPINTO", "QATAR"]]
NBA_KEYWORDS = [normalizar(k) for k in ["NBA", "BASKET", "BALONCESTO", "LAKERS", "CELTICS", "BULLS", "HEAT", "WARRIORS", "BUCKS", "SUNS", "MAVERICKS", "KNICKS", "BLAZERS", "NETS", "76ERS", "SIXERS", "NUGGETS", "CLIPPERS", "SPURS", "ROCKETS", "GRIZZLIES", "PELICANS", "THUNDER", "TIMBERWOLVES", "CAVALIERS", "PISTONS", "PACERS", "HORNETS", "WIZARDS", "MAGIC", "HAWKS", "RAPTORS", "KINGS", "JAZZ"]]
TENIS_KEYWORDS = [normalizar(k) for k in ["ATP", "WTA", "TENIS", "TENNIS", "FRITZ", "ALCARAZ", "DJOKOVIC", "NADAL", "SABALENKA", "MUCHOVA"]]
BOXEO_KEYWORDS = [normalizar(k) for k in ["BOXEO", "BOXING", "KNOCKOUT", "BXSTRS", "CANELO", "WBC", "WBA", "IBF", "WBO"]]
NFL_KEYWORDS = [normalizar(k) for k in ["NFL", "RAVENS", "JAGUARS", "CHIEFS", "PATRIOTS", "COWBOYS", "PACKERS", "49ERS", "EAGLES", "STEELERS", "BRONCOS", "VIKINGS", "BILLS"]]
NHL_KEYWORDS = [normalizar(k) for k in ["NHL", "AVALANCHE", "MAPLE LEAFS", "WILD", "CANADIENS", "CAPITALS", "SENATORS", "DEVILS", "JETS", "PANTHERS", "BLUE JACKETS", "PREDATORS", "ISLANDERS", "SHARKS", "OILERS", "RANGERS", "FLYERS", "CANUCKS"]]

SERVER_URL_RE = re.compile(r"https?://streamxhd\.[a-z]+/(live[12]\.php\?stream=[A-Za-z0-9_\-]+)")

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
            articles = soup.find_all("article", class_="event")
            LOG.info(f"Encontrados {len(articles)} partidos válidos en {url}")

            for idx, art in enumerate(articles):
                event_id = art.get("data-event-card")
                meta_text = ""
                if event_id:
                    try:
                        meta_text = base64.b64decode(event_id).decode('utf-8', errors='ignore')
                    except Exception:
                        pass

                h2 = art.find("h2", class_="event-title")
                titulo = h2.text.strip() if h2 else None
                if not titulo and meta_text:
                    partes = meta_text.split('|')
                    if len(partes) > 0:
                        titulo = partes[0]

                if not titulo or len(titulo) < 3:
                    continue

                time_div = art.find("div", class_="event-time") or art.find("span", class_="compact-list-time")
                time_str = "00:00"
                fecha_completa = None # <-- AÑADIDO: Extraer la fecha con zona horaria
                if time_div:
                    fecha_completa = time_div.get("data-full-time") or time_div.get("data-time")
                    match = re.search(r"(\d{1,2}:\d{2})", time_div.text)
                    if match:
                        time_str = match.group(1).zfill(5)

                status_elem = art.find(class_="status") or art.find(class_="compact-list-status")
                status_text = status_elem.text.strip().upper() if status_elem else "PRONTO"
                if "VIVO" in status_text: status_text = "EN VIVO"
                elif "FINAL" in status_text: status_text = "FINALIZADO"
                elif "PRONTO" in status_text: status_text = "PRONTO"

                torneo = "DEPORTE"
                if meta_text:
                    partes = meta_text.split('|')
                    if len(partes) >= 4:
                        torneo = partes[-1]
                if torneo == "DEPORTE":
                    league_tag = art.find("span", class_="compact-list-league")
                    if league_tag: torneo = league_tag.text.strip()

                sport_hint = ""
                tags = art.find_all("span", class_="tag")
                for tag in tags:
                    if "Football" in tag.text: sport_hint = "FUTBOL"
                    elif "Basketball" in tag.text: sport_hint = "NBA"
                    elif "Hockey" in tag.text: sport_hint = "NHL"
                    elif "Tennis" in tag.text: sport_hint = "TENIS"
                    elif "Racing" in tag.text or "Motor" in tag.text: sport_hint = "F1"

                servidores = []
                seen = set()
                for a_tag in art.find_all("button", class_="compact-server-action") + art.find_all("button", class_="server"):
                    stream_url = a_tag.get("data-url") or a_tag.get("data-copy") or a_tag.get("data-target")
                    if stream_url and "iframe" in stream_url:
                        m = re.search(r"src=\"(https?://[^\"]+)\"", stream_url)
                        if m: stream_url = m.group(1)
                    
                    if stream_url and "streamxhd" in stream_url:
                        match = SERVER_URL_RE.search(stream_url)
                        if match:
                            stream_id = match.group(1).split("stream=")[-1].lower()
                            if stream_url not in seen:
                                seen.add(stream_url)
                                servidores.append({"nombre": CANALES_NOMBRES.get(stream_id, stream_id.upper()), "url": stream_url})

                if not servidores: continue

                eventos.append({
                    "id": f"evt_{idx}", 
                    "titulo": titulo, 
                    "hora": time_str, 
                    "fecha_completa": fecha_completa, # <-- Guardamos la fecha
                    "status": status_text,
                    "servidores": servidores, 
                    "torneo": torneo, 
                    "sport_hint": sport_hint
                })
            
            if eventos: return eventos
            await context.close()
        except Exception as e:
            LOG.error(f"Error en {url}: {e}")
        finally:
            await browser.close()
    return eventos

async def obtener_eventos() -> List[Dict]:
    for url in BASE_URLS:
        try:
            eventos = await _scrape_url(url)
            if eventos: return eventos
        except Exception as e:
            LOG.error(f"Fallo total en {url}: {e}")
    return []

def deduplicar_eventos(eventos: List[Dict]) -> List[Dict]:
    dedup: Dict[str, Dict] = {}
    for e in eventos:
        clave = clave_dedup(e["titulo"], e["hora"])
        if clave not in dedup:
            dedup[clave] = e
        else:
            urls_vistas = {s["url"] for s in dedup[clave]["servidores"]}
            for srv in e["servidores"]:
                if srv["url"] not in urls_vistas: dedup[clave]["servidores"].append(srv)
            if e["status"] == "EN VIVO": dedup[clave]["status"] = "EN VIVO"
            elif e["status"] == "PRONTO" and dedup[clave]["status"] == "FINALIZADO": dedup[clave]["status"] = "PRONTO"
    return list(dedup.values())

def clasificar_evento(titulo: str, torneo: str, sport_hint: str) -> Tuple[str, str]:
    t = normalizar(titulo)
    torneo_upper = normalizar(torneo)
    
    if sport_hint == "NHL" or "NHL" in torneo_upper or "HOCKEY" in torneo_upper or any(k in t for k in NHL_KEYWORDS):
        return "descartar", "NHL"

    if sport_hint == "F1" or "F1" in torneo_upper or "MOTOR" in torneo_upper or any(k in t for k in F1_KEYWORDS): return "f1", torneo
    if sport_hint == "NBA" or "NBA" in torneo_upper or "BASKET" in torneo_upper or any(k in t for k in NBA_KEYWORDS): return "nba", torneo
    
    return "futbol", torneo

def clasificar_eventos(eventos: List[Dict]) -> Dict[str, List[Dict]]:
    clasificados = {"futbol": [], "nba": [], "f1": []}
    for e in eventos:
        deporte, torneo_final = clasificar_evento(e["titulo"], e.get("torneo", ""), e.get("sport_hint", ""))
        if deporte == "descartar": continue
        e["torneo"] = torneo_final
        e.pop("sport_hint", None)
        clasificados[deporte].append(e)
    return clasificados

def subir_github(token: str, user: str, repo: str, path_repo: str, contenido: str) -> bool:
    api = f"https://api.github.com/repos/{user}/{repo}/contents/{path_repo}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    r = requests.get(api, headers=headers, timeout=20)
    sha = r.json().get("sha") if r.status_code == 200 else None
    data = {"message": f"chore: update {path_repo}", "content": base64.b64encode(contenido.encode()).decode(), "branch": "main"}
    if sha: data["sha"] = sha
    resp = requests.put(api, headers=headers, json=data, timeout=30)
    return resp.status_code in (200, 201)

async def main():
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token: sys.exit(1)
    
    eventos_crudos = await obtener_eventos()
    if not eventos_crudos:
        LOG.error("❌ No se extrajeron eventos válidos.")
        return 1

    eventos = deduplicar_eventos(eventos_crudos)
    clasificados = clasificar_eventos(eventos)
    ahora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    LOG.info(f"Clasificación → Fútbol={len(clasificados['futbol'])} NBA={len(clasificados['nba'])} F1={len(clasificados['f1'])}")

    for deporte, lista in clasificados.items():
        payload = {"fecha_actualizacion": ahora, "total": len(lista), "eventos": lista}
        subir_github(token, "adrunreal24", "isoposports", f"output/agenda_{deporte}.json", json.dumps(payload, ensure_ascii=False, indent=2))
    
    return 0

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
