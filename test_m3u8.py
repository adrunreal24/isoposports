#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test v2: descarga m3u8 y segmento DESDE DENTRO del navegador Playwright.
Así el CDN ve un Chrome real y no bloquea por fingerprint TLS.
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

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

STREAMS_A_PROBAR = ["disney3", "espn5", "telemundo", "disney15", "disney7"]


async def probar_desde_navegador(stream: str) -> dict:
    url = f"https://streamxhd.sbs/live1.php?stream={stream}"
    LOG.info(f"[{stream}] Abriendo {url}")

    captured_m3u8 = []
    m3u8_status = None
    m3u8_content_type = None
    m3u8_body = ""
    segment_status = None
    segment_bytes = 0
    segment_url = ""
    segment_content_type = ""

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = await browser.new_context(user_agent=UA, locale="es-ES")
        page = await context.new_page()

        def on_request(req):
            u = req.url
            if ".m3u8" in u.lower() and u not in captured_m3u8:
                captured_m3u8.append(u)
                LOG.info(f"[{stream}] 🎯 m3u8: {u[:140]}")

        page.on("request", on_request)

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
            await page.wait_for_timeout(6000)
            try:
                await page.evaluate("""
                    () => {
                        const v = document.querySelector('video');
                        if (v) { try { v.muted = true; v.play(); } catch(e){} }
                        const sels = ['.jw-icon-display', '.vjs-big-play-button', 'button[aria-label*="play" i]'];
                        for (const s of sels) { const el = document.querySelector(s); if (el) { try { el.click(); } catch(e){} } }
                    }
                """)
            except Exception:
                pass
            await page.wait_for_timeout(7000)

            # Descargar el m3u8 DESDE DENTRO del navegador
            if captured_m3u8:
                m3u8_final = captured_m3u8[-1]
                fetch_js = """
                    async (m3u8_url) => {
                        try {
                            const r = await fetch(m3u8_url, { credentials: 'include' });
                            const text = await r.text();
                            return { status: r.status, content_type: r.headers.get('content-type') || '', body: text };
                        } catch (e) {
                            return { status: 0, error: String(e), body: '' };
                        }
                    }
                """
                try:
                    res = await page.evaluate(fetch_js, m3u8_final)
                    m3u8_status = res.get("status")
                    m3u8_content_type = res.get("content_type")
                    m3u8_body = res.get("body", "")
                    LOG.info(f"[{stream}] m3u8 status={m3u8_status} ct={m3u8_content_type} len={len(m3u8_body)}")
                except Exception as e:
                    LOG.warning(f"[{stream}] error fetch m3u8: {e}")

                # Extraer primer segmento
                if m3u8_body:
                    segments = re.findall(r"^(?!#)(https?://\S+|\S+\.(?:ts|m4s|mp4|aac))", m3u8_body, re.MULTILINE)
                    segments = [x for x in segments if not x.startswith("#")]
                    if segments:
                        seg = segments[0]
                        if not seg.startswith("http"):
                            base = m3u8_final.rsplit("/", 1)[0]
                            seg = base + "/" + seg.lstrip("/")
                        segment_url = seg
                        # Descargar primer chunk del segmento desde el navegador
                        seg_js = """
                            async (seg_url) => {
                                try {
                                    const r = await fetch(seg_url, { credentials: 'include' });
                                    if (!r.ok) return { status: r.status, bytes: 0, content_type: r.headers.get('content-type') || '' };
                                    const reader = r.body.getReader();
                                    let total = 0;
                                    while (true) {
                                        const { done, value } = await reader.read();
                                        if (done) break;
                                        total += value.length;
                                        if (total >= 32768) { try { reader.cancel(); } catch(e){} break; }
                                    }
                                    return { status: r.status, bytes: total, content_type: r.headers.get('content-type') || '' };
                                } catch (e) {
                                    return { status: 0, bytes: 0, error: String(e) };
                                }
                            }
                        """
                        try:
                            res2 = await page.evaluate(seg_js, seg)
                            segment_status = res2.get("status")
                            segment_bytes = res2.get("bytes", 0)
                            segment_content_type = res2.get("content_type", "")
                            LOG.info(f"[{stream}] segmento status={segment_status} bytes={segment_bytes} ct={segment_content_type}")
                        except Exception as e:
                            LOG.warning(f"[{stream}] error fetch segmento: {e}")
        except Exception as e:
            LOG.warning(f"[{stream}] Error general: {e}")
        finally:
            await context.close()
            await browser.close()

    es_extm3u = m3u8_body.strip().startswith("#EXTM3U")
    playable = (
        m3u8_status == 200
        and es_extm3u
        and segment_status == 200
        and segment_bytes > 1000
    )

    return {
        "stream": stream,
        "live1_url": url,
        "m3u8_encontrado": captured_m3u8[-1] if captured_m3u8 else None,
        "m3u8_candidatos": captured_m3u8,
        "m3u8_test": {
            "status": m3u8_status,
            "content_type": m3u8_content_type,
            "tamano": len(m3u8_body),
            "es_extm3u": es_extm3u,
            "primeras_lineas": m3u8_body.split("\n")[:6],
        },
        "segmento_test": {
            "url": segment_url[:160],
            "status": segment_status,
            "content_type": segment_content_type,
            "primeros_bytes": segment_bytes,
        },
        "playable": playable,
    }


async def main():
    LOG.info("=== TEST v2 (fetch desde dentro del navegador) ===")
    resultados = []
    for s in STREAMS_A_PROBAR:
        LOG.info(f"--- Probando {s} ---")
        try:
            r = await probar_desde_navegador(s)
            resultados.append(r)
            LOG.info(f"[{s}] PLAYABLE={r['playable']}")
        except Exception as e:
            LOG.error(f"Error en {s}: {e}")
            resultados.append({"stream": s, "error": str(e), "playable": False})

    resumen = {
        "fecha_test": datetime.now(timezone.utc).isoformat(),
        "total_probados": len(resultados),
        "con_m3u8": sum(1 for r in resultados if r.get("m3u8_encontrado")),
        "reproducibles": sum(1 for r in resultados if r.get("playable")),
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
