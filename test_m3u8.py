#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test rápido: prueba 5 streams EN VIVO ahora mismo.
No depende de la portada. Prueba extracción + manifest + segmento.
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import requests
from playwright.async_api import async_playwright

OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "output"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
TIMEOUT_MS = 40000

LOG = logging.getLogger("test")
LOG.setLevel(logging.INFO)
fh = logging.FileHandler(OUTPUT_DIR / "test_m3u8.log.txt", mode="w", encoding="utf-8")
fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
LOG.addHandler(fh)
sh = logging.StreamHandler()
sh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
LOG.addHandler(sh)

# 5 streams que están EN VIVO según tu captura de hoy
STREAMS_A_PROBAR = ["disney3", "espn5", "telemundo", "disney15", "disney7"]


async def extract_m3u8(stream: str) -> dict:
    url = f"https://streamxhd.sbs/live1.php?stream={stream}"
    LOG.info(f"[{stream}] Abriendo {url}")
    captured_m3u8: list[str] = []
    network_log: list[dict] = []
    html_snippet = ""

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = await browser.new_context(user_agent=UA, locale="es-ES")
        page = await context.new_page()

        def on_request(req):
            u = req.url
            if ".m3u8" in u.lower():
                captured_m3u8.append(u)
                LOG.info(f"[{stream}] 🎯 m3u8: {u[:140]}")
            network_log.append({"method": req.method, "url": u[:250], "type": req.resource_type})

        page.on("request", on_request)

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
            await page.wait_for_timeout(8000)
            try:
                await page.evaluate("""
                    () => {
                        const sels = ['video', '.jw-icon-display', '.vjs-big-play-button',
                                      'button[aria-label*="play" i]', '.play-button'];
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
            try:
                content = await page.content()
                html_snippet = content[:2000]
            except Exception:
                pass
        except Exception as e:
            LOG.warning(f"[{stream}] Error navegando: {e}")
        finally:
            await context.close()
            await browser.close()

    return {
        "stream": stream,
        "live1_url": url,
        "m3u8_encontrado": captured_m3u8[-1] if captured_m3u8 else None,
        "m3u8_candidatos": captured_m3u8,
        "peticiones_red_total": len(network_log),
        "peticiones_red_muestra": network_log[-20:],
        "html_snippet": html_snippet,
    }


def probar_manifest(m3u8: str) -> dict:
    if not m3u8:
        return {"ok": False, "motivo": "sin_m3u8"}

    headers = {
        "User-Agent": UA,
        "Referer": "https://streamxhd.sbs/",
        "Origin": "https://streamxhd.sbs",
        "Accept": "*/*",
        "Accept-Language": "es-ES,es;q=0.9",
    }
    s = requests.Session()
    try:
        r = s.get(m3u8, headers=headers, timeout=20, allow_redirects=True)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    status = r.status_code
    content_type = r.headers.get("content-type", "")
    body = r.text
    es_extm3u = body.strip().startswith("#EXTM3U")
    segments = re.findall(r"^(?!#)(https?://\S+|\S+\.(?:ts|m4s|mp4|aac))", body, re.MULTILINE)
    segments = [x for x in segments if not x.startswith("#")][:3]

    seg_result = {"probado": False}
    if segments:
        seg_url = segments[0]
        if not seg_url.startswith("http"):
            base = m3u8.rsplit("/", 1)[0]
            seg_url = base + "/" + seg_url.lstrip("/")
        try:
            rs = s.get(seg_url, headers=headers, timeout=20, stream=True)
            first_bytes = next(rs.iter_content(chunk_size=4096), b"")
            seg_result = {
                "probado": True,
                "status": rs.status_code,
                "content_type": rs.headers.get("content-type", ""),
                "primeros_bytes": len(first_bytes),
                "ok": rs.status_code == 200 and len(first_bytes) > 0,
            }
        except Exception as e:
            seg_result = {"probado": True, "error": str(e), "ok": False}

    return {
        "status": status,
        "content_type": content_type,
        "tamano": len(body),
        "es_extm3u": es_extm3u,
        "num_segmentos": len(segments),
        "segmento_prueba": seg_result,
        "ok": status == 200 and es_extm3u and seg_result.get("ok", False),
    }


async def main():
    LOG.info("=== TEST RÁPIDO (5 streams EN VIVO) ===")
    resultados = []
    for s in STREAMS_A_PROBAR:
        LOG.info(f"--- Probando {s} ---")
        info = await extract_m3u8(s)
        manifest = probar_manifest(info.get("m3u8_encontrado"))
        info["test_manifest"] = manifest
        resultados.append(info)
        LOG.info(f"[{s}] m3u8={bool(info.get('m3u8_encontrado'))} playable={manifest.get('ok')}")

    resumen = {
        "fecha_test": datetime.now(timezone.utc).isoformat(),
        "total_probados": len(resultados),
        "con_m3u8": sum(1 for r in resultados if r.get("m3u8_encontrado")),
        "reproducibles": sum(1 for r in resultados if r.get("test_manifest", {}).get("ok")),
        "detalle": resultados,
    }
    out = OUTPUT_DIR / "test_m3u8.json"
    out.write_text(json.dumps(resumen, ensure_ascii=False, indent=2), encoding="utf-8")
    LOG.info("=== FIN ===")
    LOG.info(f"Total: {resumen['total_probados']} | con m3u8: {resumen['con_m3u8']} | reproducibles: {resumen['reproducibles']}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(asyncio.run(main()))
