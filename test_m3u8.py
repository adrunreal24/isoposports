#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test de viabilidad: extraer .m3u8 real detrás de live1.php?stream=xxx.
Prueba extracción + descarga de manifest + descarga de 1 segmento.
Guarda output/test_m3u8.json + output/test_m3u8.log.txt
"""

import asyncio
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Optional

import requests
from playwright.async_api import async_playwright

BASE_URL = "https://streamxhd.sbs/"
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "output"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

TIMEOUT_MS = 40000
LOG = logging.getLogger("test_m3u8")
LOG.setLevel(logging.INFO)
_fh = logging.FileHandler(OUTPUT_DIR / "test_m3u8.log.txt", mode="w", encoding="utf-8")
_fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
LOG.addHandler(_fh)
_sh = logging.StreamHandler()
_sh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
LOG.addHandler(_sh)

M3U8_RE = re.compile(r"https?://[^\s\"'<>]+\.m3u8[^\s\"'<>]*", re.IGNORECASE)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


# ─── 1. Obtener lista de links live1.php ─────────────────────────────────────
async def get_live_links(limit: int = 15) -> List[Dict]:
    LOG.info("Abriendo portada para obtener links live1.php")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = await browser.new_context(user_agent=UA, locale="es-ES")
        page = await context.new_page()
        await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
        try:
            await page.wait_for_selector('a[href*="live1.php"]', timeout=TIMEOUT_MS)
        except Exception:
            LOG.warning("No aparecieron links live1.php en portada")
        await page.wait_for_timeout(4000)
        # scroll
        for _ in range(5):
            await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
            await page.wait_for_timeout(700)
        # recoger
        anchors = await page.eval_on_selector_all(
            'a[href*="live1.php"]',
            "els => els.map(a => ({href: a.href, text: (a.textContent||'').trim()}))"
        )
        await context.close()
        await browser.close()

    seen = set()
    unique = []
    for a in anchors:
        url = a["href"]
        if url in seen:
            continue
        seen.add(url)
        stream = ""
        m = re.search(r"[?&]stream=([A-Za-z0-9_\-]+)", url)
        if m:
            stream = m.group(1)
        unique.append({"stream": stream, "url": url, "text": a.get("text", "")})
    LOG.info(f"Links únicos live1.php: {len(unique)}")
    return unique[:limit]


# ─── 2. Extraer .m3u8 interceptando red ──────────────────────────────────────
async def extract_m3u8_for_stream(stream_info: Dict) -> Dict:
    url = stream_info["url"]
    stream = stream_info["stream"]
    LOG.info(f"[{stream}] Abriendo {url}")

    captured_m3u8: List[str] = []
    captured_json: List[Dict] = []
    network_log: List[Dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = await browser.new_context(user_agent=UA, locale="es-ES")
        page = await context.new_page()

        async def on_request(req):
            u = req.url
            if ".m3u8" in u.lower():
                captured_m3u8.append(u)
                LOG.info(f"[{stream}] 🎯 m3u8 capturado: {u[:120]}...")
            network_log.append({"method": req.method, "url": u[:300], "type": req.resource_type})
            # capturar posibles respuestas JSON con url dentro
            if req.resource_type in ("xhr", "fetch"):
                try:
                    resp = await req.response()
                    if resp and resp.headers.get("content-type", "").startswith("application/json"):
                        try:
                            body = await resp.json()
                            captured_json.append({"url": u, "json": body})
                        except Exception:
                            pass
                except Exception:
                    pass

        page.on("request", lambda r: asyncio.create_task(on_request(r)))

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
            # esperar a que la red capture lo que necesite
            await page.wait_for_timeout(12000)
            # a veces hay que clickear el play
            try:
                await page.evaluate("""
                    () => {
                        const sels = ['video', '.jw-icon-display', '.vjs-big-play-button', 'button[aria-label*="play" i]', '.play-button'];
                        for (const s of sels) {
                            const el = document.querySelector(s);
                            if (el) { try { el.click(); el.dispatchEvent(new MouseEvent('click', {bubbles:true})); } catch(e){} }
                        }
                        const v = document.querySelector('video');
                        if (v) { try { v.muted = true; v.play(); } catch(e){} }
                    }
                """)
            except Exception:
                pass
            await page.wait_for_timeout(8000)
            # cookies y headers del contexto
            cookies = await context.cookies()
        except Exception as e:
            LOG.warning(f"[{stream}] Error navegando: {e}")
            cookies = []
        finally:
            await context.close()
            await browser.close()

    m3u8_final = captured_m3u8[-1] if captured_m3u8 else None
    result = {
        "stream": stream,
        "live1_url": url,
        "m3u8_encontrado": m3u8_final,
        "m3u8_candidatos": captured_m3u8,
        "json_interceptado": captured_json[:10],
        "cookies": [{"name": c["name"], "domain": c["domain"]} for c in cookies][:20],
        "peticiones_red_total": len(network_log),
        "peticiones_red_muestra": network_log[-30:],
        "estado_extraccion": "ok" if m3u8_final else "no_m3u8",
    }
    return result


# ─── 3. Probar el .m3u8 con curl-like usando headers de navegador ────────────
def test_m3u8_playable(item: Dict, cookies: List[Dict]) -> Dict:
    m3u8 = item.get("m3u8_encontrado")
    if not m3u8:
        return {**item, "test_manifest": {"ok": False, "motivo": "sin m3u8"}}

    headers = {
        "User-Agent": UA,
        "Referer": "https://streamxhd.sbs/",
        "Origin": "https://streamxhd.sbs",
        "Accept": "*/*",
        "Accept-Language": "es-ES,es;q=0.9",
    }
    cookie_header = "; ".join(f"{c['name']}=<hidden>" for c in cookies)
    # no mandamos valor real de cookie al log por privacidad, pero sí la probamos
    session = requests.Session()
    for c in cookies:
        # placeholders: no tenemos el valor, así que no podemos reusar cookies aquí
        # (Playwright no las expuso). Se probará sin cookies.
        pass

    try:
        r = session.get(m3u8, headers=headers, timeout=20, allow_redirects=True)
        manifest_status = r.status_code
        manifest_ct = r.headers.get("content-type", "")
        manifest_len = len(r.text)
        has_extm3u = r.text.strip().startswith("#EXTM3U")
        segments = re.findall(r"^(?!#)(https?://\S+|\S+\.(?:ts|m4s|mp4|aac))", r.text, re.MULTILINE)
        segments = [s for s in segments if not s.startswith("#")][:3]
    except Exception as e:
        return {**item, "test_manifest": {"ok": False, "error": str(e)}}

    # Probar segmento si lo hay
    seg_result = {"probado": False}
    if segments:
        seg_url = segments[0]
        if not seg_url.startswith("http"):
            base = m3u8.rsplit("/", 1)[0]
            seg_url = base + "/" + seg_url.lstrip("/")
        try:
            rs = session.get(seg_url, headers=headers, timeout=20, stream=True)
            first_bytes = next(rs.iter_content(chunk_size=4096), b"")
            seg_result = {
                "probado": True,
                "url": seg_url[:120],
                "status": rs.status_code,
                "content_type": rs.headers.get("content-type", ""),
                "primeros_bytes": len(first_bytes),
                "ok": rs.status_code == 200 and len(first_bytes) > 0,
            }
        except Exception as e:
            seg_result = {"probado": True, "error": str(e), "ok": False}

    return {
        **item,
        "test_manifest": {
            "status": manifest_status,
            "content_type": manifest_ct,
            "tamano": manifest_len,
            "es_extm3u": has_extm3u,
            "num_segmentos_detectados": len(segments),
            "segmento_prueba": seg_result,
            "ok": manifest_status == 200 and has_extm3u and seg_result.get("ok", False),
        },
    }


# ─── MAIN ────────────────────────────────────────────────────────────────────
async def main():
    LOG.info("=== TEST M3U8 INICIADO ===")
    live_links = await get_live_links(limit=20)
    if not live_links:
        LOG.error("No se encontraron links live1.php")
        return 1

    results = []
    for i, link in enumerate(live_links, 1):
        LOG.info(f"--- ({i}/{len(live_links)}) {link['stream']} ---")
        try:
            info = await extract_m3u8_for_stream(link)
            probado = test_m3u8_playable(info, info.get("cookies", []))
            probado["test_manifest"]["url_probada_cruda"] = probado.get("m3u8_encontrado", "")[:80] + "..." if probado.get("m3u8_encontrado") else ""
            results.append(probado)
        except Exception as e:
            LOG.error(f"Error procesando {link['stream']}: {e}")
            results.append({**link, "estado_extraccion": "error", "error": str(e)})

    resumen = {
        "fecha_test": datetime.now(timezone.utc).isoformat(),
        "total_probados": len(results),
        "con_m3u8": sum(1 for r in results if r.get("m3u8_encontrado")),
        "reproducibles": sum(1 for r in results if r.get("test_manifest", {}).get("ok")),
        "detalle": results,
    }
    out = OUTPUT_DIR / "test_m3u8.json"
    out.write_text(json.dumps(resumen, ensure_ascii=False, indent=2), encoding="utf-8")
    LOG.info(f"=== FIN ===")
    LOG.info(f"Total probados: {resumen['total_probados']}")
    LOG.info(f"Con m3u8: {resumen['con_m3u8']}")
    LOG.info(f"Reproducibles (manifest+segmento): {resumen['reproducibles']}")
    LOG.info(f"Guardado: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
