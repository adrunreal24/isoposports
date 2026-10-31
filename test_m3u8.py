#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test v3: en vez de hacer fetch manual (que falla por CORS/WAF),
medimos el estado REAL del reproductor: currentSrc, readyState,
buffered, red cargada de segmentos .ts/.m4s.
Si el video carga datos, ¡el stream es reproducible!
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import async_playwright

OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "output"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
TIMEOUT_MS = 45000

LOG = logging.getLogger("test")
LOG.setLevel(logging.INFO)
fh = logging.FileHandler(OUTPUT_DIR / "test_m3u8.log.txt", mode="w", encoding="utf-8")
fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
LOG.addHandler(fh)
sh = logging.StreamHandler()
sh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
LOG.addHandler(sh)

STREAMS_A_PROBAR = ["disney3", "espn5", "telemundo", "disney15", "disney7"]


async def probar_stream(stream: str) -> dict:
    url = f"https://streamxhd.sbs/live1.php?stream={stream}"
    LOG.info(f"[{stream}] Abriendo {url}")

    # Recopilar red: m3u8, segmentos .ts/.m4s, respuestas 200
    red = {
        "m3u8": [],
        "segmentos": [],
        "respuestas_200": 0,
        "respuestas_error": 0,
        "tamanio_total_descargado": 0,
    }

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--mute-audio",
                  "--autoplay-policy=no-user-gesture-required"],
        )
        context = await browser.new_context(
            user_agent=UA,
            locale="es-ES",
            viewport={"width": 1366, "height": 900},
        )
        page = await context.new_page()

        def on_response(resp):
            u = resp.url
            try:
                status = resp.status
                if status == 200:
                    red["respuestas_200"] += 1
                elif status >= 400:
                    red["respuestas_error"] += 1
                cl = resp.headers.get("content-length")
                if cl and cl.isdigit():
                    red["tamanio_total_descargado"] += int(cl)
            except Exception:
                pass
            ulow = u.lower()
            if ".m3u8" in ulow and u not in red["m3u8"]:
                red["m3u8"].append(u)
                LOG.info(f"[{stream}] 🎯 m3u8: {u[:140]}")
            elif any(ext in ulow for ext in (".ts", ".m4s", ".mp4", ".aac")):
                red["segmentos"].append(u)

        page.on("response", on_response)

        estado_player = {}
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
            await page.wait_for_timeout(5000)
            # click en play si hay botón
            try:
                await page.evaluate("""
                    () => {
                        const sels = ['.jw-icon-display', '.vjs-big-play-button',
                                      'button[aria-label*="play" i]', '.play-button', '.vjs-play-control'];
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

            # esperar hasta 20s a que el video tenga buffer
            for i in range(20):
                await page.wait_for_timeout(1000)
                estado_player = await page.evaluate("""
                    () => {
                        const v = document.querySelector('video');
                        if (!v) return { has_video: false };
                        let buffered_end = 0;
                        try { if (v.buffered.length > 0) buffered_end = v.buffered.end(v.buffered.length - 1); } catch(e){}
                        let hls_url = null;
                        if (window.Hls && window.Hls.instances && Object.keys(window.Hls.instances).length) {
                            try {
                                const inst = window.Hls.instances[Object.keys(window.Hls.instances)[0]];
                                hls_url = inst.url || null;
                            } catch(e){}
                        }
                        return {
                            has_video: true,
                            currentSrc: v.currentSrc || "",
                            src: v.src || "",
                            readyState: v.readyState,
                            networkState: v.networkState,
                            paused: v.paused,
                            currentTime: v.currentTime,
                            duration: isFinite(v.duration) ? v.duration : null,
                            buffered_end: buffered_end,
                            videoWidth: v.videoWidth,
                            videoHeight: v.videoHeight,
                            error: v.error ? { code: v.error.code, message: v.error.message } : null,
                            hls_url: hls_url,
                        };
                    }
                """)
                # si ya tiene buffer o error, paramos
                if estado_player.get("buffered_end", 0) > 0:
                    break
                if estado_player.get("error"):
                    break
        except Exception as e:
            LOG.warning(f"[{stream}] Error: {e}")
        finally:
            try:
                await context.close()
            except Exception:
                pass
            await browser.close()

    # Determinar si es reproducible
    reproduciendo = (
        estado_player.get("has_video", False)
        and (estado_player.get("buffered_end", 0) > 0 or estado_player.get("currentTime", 0) > 0)
        and (estado_player.get("videoWidth", 0) > 0)
        and not estado_player.get("error")
    )

    return {
        "stream": stream,
        "live1_url": url,
        "m3u8_detectados": red["m3u8"],
        "num_segmentos_descargados": len(red["segmentos"]),
        "primeros_segmentos": red["segmentos"][:3],
        "respuestas_200": red["respuestas_200"],
        "respuestas_error": red["respuestas_error"],
        "tamanio_total_red": red["tamanio_total_descargado"],
        "estado_reproductor": estado_player,
        "playable": reproduciendo,
    }


async def main():
    LOG.info("=== TEST v3 (mide estado real del reproductor) ===")
    resultados = []
    for s in STREAMS_A_PROBAR:
        LOG.info(f"--- Probando {s} ---")
        try:
            r = await probar_stream(s)
            resultados.append(r)
            LOG.info(f"[{s}] PLAYABLE={r['playable']} · segmentos={r['num_segmentos_descargados']} · "
                     f"buffer={r['estado_reproductor'].get('buffered_end', 0)}s · "
                     f"resolución={r['estado_reproductor'].get('videoWidth', 0)}x{r['estado_reproductor'].get('videoHeight', 0)}")
        except Exception as e:
            LOG.error(f"Error en {s}: {e}")
            resultados.append({"stream": s, "error": str(e), "playable": False})

    resumen = {
        "fecha_test": datetime.now(timezone.utc).isoformat(),
        "total_probados": len(resultados),
        "con_m3u8": sum(1 for r in resultados if r.get("m3u8_detectados")),
        "con_segmentos": sum(1 for r in resultados if r.get("num_segmentos_descargados", 0) > 0),
        "reproducibles": sum(1 for r in resultados if r.get("playable")),
        "detalle": resultados,
    }
    out = OUTPUT_DIR / "test_m3u8.json"
    out.write_text(json.dumps(resumen, ensure_ascii=False, indent=2), encoding="utf-8")
    LOG.info("=== FIN ===")
    LOG.info(f"Total: {resumen['total_probados']} | con m3u8: {resumen['con_m3u8']} | "
             f"con segmentos: {resumen['con_segmentos']} | reproducibles: {resumen['reproducibles']}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(asyncio.run(main()))
