#!/usr/bin/env python3
"""
Registro de heladas de Cerezo de Abajo.

No avisa a nadie todavía: solo apunta, cada noche, lo que se predijo y, a la
mañana siguiente, lo que de verdad marcó la estación. Con dos meses de esto se
puede decir con números si merece la pena montar un aviso.

Guarda TODOS los ingredientes de la noche (temperatura, punto de rocío, viento,
humedad, presión al anochecer), no solo una estimación. Así, más adelante, se
puede ajustar cualquier fórmula con datos de Cerezo en vez de fiarse de la mía.

Órdenes:
    python3 heladas.py buscar      busca el código INE de tu municipio
    python3 heladas.py noche       al anochecer: apunta las predicciones
    python3 heladas.py manana      por la mañana: apunta lo que pasó de verdad
    python3 heladas.py resumen     saca el balance de aciertos y fallos

Variables de entorno necesarias:
    WU_API_KEY      la de Weather Underground
    AEMET_API_KEY   la de AEMET OpenData
    MUNICIPIO       código INE (5 cifras). Sáltatelo para la orden 'buscar'.
"""

import csv
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

ESTACION = "ICEREZ2"
REGISTRO = "heladas_registro.csv"

WU = os.environ.get("WU_API_KEY", "").strip()
AEMET = os.environ.get("AEMET_API_KEY", "").strip()
MUNICIPIO = os.environ.get("MUNICIPIO", "").strip()

COLUMNAS = [
    "noche",              # fecha de la tarde; la mínima cae en la madrugada siguiente
    "aemet_min",          # mínima que predice AEMET para el día siguiente
    "aemet_max",
    "aemet_cielo",        # descripción del cielo previsto
    "t_anochecer",        # lo que marcaba TU estación al apuntar
    "rocio_anochecer",
    "viento_anochecer",
    "humedad_anochecer",
    "presion_anochecer",
    "base_rocio",         # estimación provisional 1: la mínima tiende al rocío
    "base_media",         # estimación provisional 2: media de temperatura y rocío
    "noche_despejada",    # 1 si viento flojo (condición de helada por irradiación)
    "min_real",           # lo que marcó de verdad la estación
    "error_aemet",
    "error_rocio",
    "error_media",
]


# ---------------------------------------------------------------------------
# Peticiones
# ---------------------------------------------------------------------------

def trae(url, intentos=3):
    contexto = ssl.create_default_context()
    espera = 5
    for _ in range(intentos):
        try:
            pet = urllib.request.Request(url, headers={"User-Agent": "estacion-cerezo/1.0"})
            with urllib.request.urlopen(pet, timeout=45, context=contexto) as r:
                if r.status == 204:
                    return None
                return json.loads(r.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                print(f"  ERROR {e.code}: clave rechazada. Si es la de AEMET, "
                      f"recuerda que caducan; pide una nueva.")
                return None
            if e.code == 429 or e.code >= 500:
                time.sleep(espera); espera *= 2; continue
            print(f"  HTTP {e.code}")
            return None
        except Exception as e:
            print(f"  red: {e}")
            time.sleep(espera); espera *= 2
    return None


def aemet(ruta):
    """AEMET responde con un JSON que apunta a otro JSON. Dos saltos."""
    if not AEMET:
        print("  falta AEMET_API_KEY")
        return None
    puerta = trae(f"https://opendata.aemet.es/opendata/api{ruta}"
                  f"?api_key={urllib.parse.quote(AEMET)}")
    if not puerta or "datos" not in puerta:
        if puerta:
            print(f"  AEMET dice: {puerta.get('descripcion', puerta)}")
        return None
    time.sleep(1)
    return trae(puerta["datos"])


def observacion_actual():
    if not WU:
        print("  falta WU_API_KEY")
        return {}
    d = trae("https://api.weather.com/v2/pws/observations/current?"
             + urllib.parse.urlencode({"stationId": ESTACION, "format": "json",
                                       "units": "m", "apiKey": WU}))
    if not d or not d.get("observations"):
        return {}
    o = d["observations"][0]
    m = o.get("metric", {})
    return {"t": m.get("temp"), "rocio": m.get("dewpt"), "viento": m.get("windSpeed"),
            "humedad": o.get("humidity"), "presion": m.get("pressure")}


def minima_del_dia(dia):
    d = trae("https://api.weather.com/v2/pws/history/daily?"
             + urllib.parse.urlencode({"stationId": ESTACION, "format": "json",
                                       "units": "m", "date": dia.strftime("%Y%m%d"),
                                       "apiKey": WU}))
    if not d or not d.get("observations"):
        return None
    return d["observations"][0].get("metric", {}).get("tempLow")


# ---------------------------------------------------------------------------
# El registro
# ---------------------------------------------------------------------------

def lee_registro():
    if not os.path.exists(REGISTRO):
        return []
    with open(REGISTRO, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def escribe_registro(filas):
    with open(REGISTRO, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNAS)
        w.writeheader()
        for fila in filas:
            w.writerow({c: fila.get(c, "") for c in COLUMNAS})


def num(v):
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Órdenes
# ---------------------------------------------------------------------------

def cmd_buscar():
    print("Buscando municipios que empiecen por 'Cerezo'...\n")
    lista = aemet("/maestro/municipios")
    if not lista:
        sys.exit("No he podido bajar el listado. ¿Está bien AEMET_API_KEY?")
    for m in lista:
        nombre = m.get("nombre", "")
        if "cerezo" in nombre.lower():
            # el id viene como 'id28012'; el código INE son las 5 cifras
            codigo = str(m.get("id", "")).replace("id", "")
            print(f"  {codigo}  {nombre}  ({m.get('nombre_provincia') or m.get('provincia','')})")
    print("\nCoge el de Segovia y guárdalo como variable MUNICIPIO.")


def cmd_noche():
    hoy = date.today()
    manana = hoy + timedelta(days=1)
    print(f"Apuntando la noche del {hoy}:")

    obs = observacion_actual()
    print(f"  estación: {obs.get('t')} °C, rocío {obs.get('rocio')} °C, "
          f"viento {obs.get('viento')} km/h")

    amin = amax = acielo = None
    if MUNICIPIO:
        pred = aemet(f"/prediccion/especifica/municipio/diaria/{MUNICIPIO}")
        if pred:
            dias = (pred[0].get("prediccion", {}) or {}).get("dia", [])
            for d in dias:
                if str(d.get("fecha", ""))[:10] == manana.isoformat():
                    amin = (d.get("temperatura") or {}).get("minima")
                    amax = (d.get("temperatura") or {}).get("maxima")
                    cielos = [c.get("descripcion") for c in d.get("estadoCielo", [])
                              if c.get("descripcion")]
                    acielo = cielos[0] if cielos else None
                    break
            print(f"  AEMET para mañana: mínima {amin} °C, {acielo or 'sin dato de cielo'}")
    else:
        print("  (sin MUNICIPIO no pido nada a AEMET)")

    t, rocio, viento = obs.get("t"), obs.get("rocio"), obs.get("viento")
    base_rocio = rocio
    base_media = None if (t is None or rocio is None) else round((t + rocio) / 2, 1)
    despejada = 1 if (viento is not None and viento < 6) else 0

    filas = [f for f in lee_registro() if f.get("noche") != hoy.isoformat()]
    filas.append({
        "noche": hoy.isoformat(),
        "aemet_min": amin, "aemet_max": amax, "aemet_cielo": acielo,
        "t_anochecer": t, "rocio_anochecer": rocio, "viento_anochecer": viento,
        "humedad_anochecer": obs.get("humedad"), "presion_anochecer": obs.get("presion"),
        "base_rocio": base_rocio, "base_media": base_media,
        "noche_despejada": despejada,
    })
    escribe_registro(sorted(filas, key=lambda x: x["noche"]))
    print(f"\nApuntado. Estimaciones provisionales: rocío {base_rocio} °C, "
          f"media {base_media} °C.")


def cmd_manana():
    ayer = date.today() - timedelta(days=1)
    print(f"Buscando la mínima real de la noche del {ayer}:")
    real = minima_del_dia(date.today())
    if real is None:
        print("  la estación aún no da la mínima de hoy. Lo intento mañana.")
        return
    print(f"  mínima real: {real} °C")

    filas = lee_registro()
    tocada = False
    for f in filas:
        if f.get("noche") == ayer.isoformat():
            f["min_real"] = real
            for campo, origen in (("error_aemet", "aemet_min"),
                                  ("error_rocio", "base_rocio"),
                                  ("error_media", "base_media")):
                v = num(f.get(origen))
                f[campo] = "" if v is None else round(real - v, 1)
            tocada = True
    if tocada:
        escribe_registro(filas)
        print("  anotada.")
    else:
        print("  no hay ninguna predicción apuntada para esa noche.")


def cmd_resumen():
    filas = [f for f in lee_registro() if num(f.get("min_real")) is not None]
    if not filas:
        sys.exit("Todavía no hay ninguna noche completa. Paciencia.")

    print(f"\n{len(filas)} noches con predicción y realidad.\n")
    print(f"{'método':<22}{'error medio':>13}{'error absoluto':>16}{'peor fallo':>13}")
    for nombre, campo in (("AEMET", "error_aemet"),
                          ("Punto de rocío", "error_rocio"),
                          ("Media T y rocío", "error_media")):
        e = [num(f.get(campo)) for f in filas]
        e = [x for x in e if x is not None]
        if not e:
            continue
        print(f"{nombre:<22}{sum(e)/len(e):>+12.1f}°"
              f"{sum(abs(x) for x in e)/len(e):>15.1f}°"
              f"{max(e, key=abs):>+12.1f}°")

    print("\nAciertos de helada (mínima real bajo cero):")
    print(f"{'método':<22}{'avisó bien':>12}{'se le escapó':>14}{'falsa alarma':>14}")
    for nombre, campo in (("AEMET", "aemet_min"),
                          ("Punto de rocío", "base_rocio"),
                          ("Media T y rocío", "base_media")):
        acierto = escape = falsa = 0
        for f in filas:
            p, r = num(f.get(campo)), num(f.get("min_real"))
            if p is None:
                continue
            if r < 0 and p < 0: acierto += 1
            elif r < 0 and p >= 0: escape += 1
            elif r >= 0 and p < 0: falsa += 1
        print(f"{nombre:<22}{acierto:>12}{escape:>14}{falsa:>14}")

    heladas = sum(1 for f in filas if num(f["min_real"]) < 0)
    print(f"\nDe las {len(filas)} noches registradas, heló en {heladas}.")
    if heladas < 10:
        print("Con menos de diez heladas los porcentajes engañan. Sigue acumulando.")


# ---------------------------------------------------------------------------

ORDENES = {"buscar": cmd_buscar, "noche": cmd_noche,
           "manana": cmd_manana, "resumen": cmd_resumen}

if __name__ == "__main__":
    orden = sys.argv[1] if len(sys.argv) > 1 else ""
    if orden not in ORDENES:
        sys.exit(f"Uso: python3 heladas.py [{' | '.join(ORDENES)}]")
    ORDENES[orden]()
