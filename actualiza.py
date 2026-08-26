#!/usr/bin/env python3
"""
Actualización diaria de la estación ICEREZ2.

Hace tres cosas, en este orden:
  1. Mira qué día es el último del CSV y pide a la API solo lo que falta.
  2. Añade los días nuevos a icerez2_diario.csv.
  3. Reconstruye index.html metiendo los datos dentro de plantilla.html.

Pensado para correr solo cada noche desde GitHub Actions, pero funciona
igual si lo lanzas a mano:

    export WU_API_KEY="tu_clave"
    python3 actualiza.py

Ficheros que espera encontrar en la misma carpeta:
    plantilla.html        la página vacía (estacion-cerezo.html renombrada)
    icerez2_diario.csv    el histórico ya descargado
"""

import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

ESTACION = "ICEREZ2"
CSV_DATOS = "icerez2_diario.csv"
PLANTILLA = "plantilla.html"
SALIDA = "index.html"
PRIMER_DIA = date(2020, 7, 15)
RE_CHECK_DIAS = 2  # días recientes que se vuelven a pedir cada vez, por si llegaron incompletos

BASE = "https://api.weather.com/v2/pws/history/daily"
MARCADOR = '<script id="datos-incrustados" type="application/json">null</script>'

API_KEY = os.environ.get("WU_API_KEY", "").strip()
if not API_KEY:
    sys.exit("ERROR: falta la variable de entorno WU_API_KEY.")


# ---------------------------------------------------------------------------

def pide(dia, intentos=3):
    url = BASE + "?" + urllib.parse.urlencode({
        "stationId": ESTACION, "format": "json", "units": "m",
        "date": dia.strftime("%Y%m%d"), "apiKey": API_KEY,
    })
    espera = 6
    for _ in range(intentos):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                if r.status == 204:
                    return []
                return json.loads(r.read().decode("utf-8")).get("observations", [])
        except urllib.error.HTTPError as e:
            if e.code == 204:
                return []
            if e.code in (401, 403):
                sys.exit(f"ERROR {e.code}: la clave no vale o ha caducado.")
            if e.code == 429 or e.code >= 500:
                time.sleep(espera); espera *= 2; continue
            return []
        except Exception:
            time.sleep(espera); espera *= 2
    return []


def aplana(obs):
    plano = {}
    for k, v in obs.items():
        if isinstance(v, dict):
            plano.update(v)
        elif not isinstance(v, list):
            plano[k] = v
    return plano


# ---------------------------------------------------------------------------

def carga_csv():
    if not os.path.exists(CSV_DATOS):
        return [], []
    with open(CSV_DATOS, encoding="utf-8") as f:
        lector = csv.DictReader(f)
        return lector.fieldnames or [], list(lector)


def ultimo_dia(filas):
    fechas = [f.get("obsTimeLocal", "")[:10] for f in filas]
    fechas = [x for x in fechas if len(x) == 10]
    if not fechas:
        return PRIMER_DIA - timedelta(days=1)
    return date.fromisoformat(max(fechas))


def descarga_pendientes(desde, hasta):
    nuevas = []
    d = desde
    while d <= hasta:
        obs = pide(d)
        if obs:
            nuevas.extend(aplana(o) for o in obs)
            print(f"  {d}: {len(obs)} observación(es)")
        else:
            print(f"  {d}: sin datos")
        d += timedelta(days=1)
        time.sleep(2.5)
    return nuevas


def guarda_csv(columnas, filas, nuevas):
    for fila in nuevas:
        for c in fila:
            if c not in columnas:
                columnas.append(c)
    # Un mismo día puede llegar dos veces (la re-comprobación de los últimos
    # días, ver RE_CHECK_DIAS en main()): si Weather Underground aún no había
    # cerrado el día la primera vez, el registro nuevo debe pisar al viejo,
    # nunca al revés.
    por_fecha = {}
    for fila in filas:
        clave = fila.get("obsTimeLocal", "")[:10]
        if clave:
            por_fecha[clave] = fila
    for fila in nuevas:
        clave = fila.get("obsTimeLocal", "")[:10]
        if clave:
            por_fecha[clave] = {c: fila.get(c, "") for c in columnas}
    unicas = [por_fecha[clave] for clave in sorted(por_fecha)]
    with open(CSV_DATOS, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columnas)
        w.writeheader()
        w.writerows(unicas)
    return unicas


# ---------------------------------------------------------------------------

def num(v):
    if v in (None, "", "None", "null", "--"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def construye_html(filas):
    if not os.path.exists(PLANTILLA):
        sys.exit(f"ERROR: no encuentro {PLANTILLA} en esta carpeta.")
    plantilla = open(PLANTILLA, encoding="utf-8").read()
    if MARCADOR not in plantilla:
        sys.exit("ERROR: la plantilla no lleva el hueco para los datos. "
                 "¿Has renombrado por error la página que ya tenía datos dentro?")

    compacto = []
    for fila in filas:
        f = fila.get("obsTimeLocal", "")[:10]
        if len(f) != 10:
            continue
        compacto.append([f, num(fila.get("tempHigh")), num(fila.get("tempLow")),
                         num(fila.get("tempAvg")), num(fila.get("precipTotal"))])

    relleno = ('<script id="datos-incrustados" type="application/json">'
               + json.dumps(compacto, separators=(",", ":")) + "</script>")
    html = plantilla.replace(MARCADOR, relleno)
    if not html.lstrip().startswith("<!DOCTYPE"):
        html = "<!DOCTYPE html>\n" + html

    with open(SALIDA, "w", encoding="utf-8") as f:
        f.write(html)
    return len(compacto), len(html)


# ---------------------------------------------------------------------------

def main():
    columnas, filas = carga_csv()
    ayer = date.today() - timedelta(days=1)
    ultimo = ultimo_dia(filas)

    # Weather Underground a veces no ha cerrado del todo el día cuando este
    # script lo pide a primera hora (se guarda un total parcial, ej. solo
    # hasta las 10 de la mañana). Para que eso se corrija solo, cada vez
    # volvemos a pedir también los RE_CHECK_DIAS días más recientes que ya
    # teníamos guardados, no solo los que faltan.
    desde = max(PRIMER_DIA, min(ultimo + timedelta(days=1), ayer) - timedelta(days=RE_CHECK_DIAS - 1))

    if desde > ayer:
        print("El CSV ya está al día. Reconstruyo la página por si acaso.")
        nuevas = []
    else:
        # red de seguridad: si algo se quedó atrás, no pedimos medio año de golpe
        if (ayer - desde).days > 45:
            print(f"Aviso: faltan {(ayer - desde).days} días. Bajo los 45 más "
                  f"recientes; vuelve a lanzarlo para seguir.")
            desde = ayer - timedelta(days=45)
        print(f"Pidiendo del {desde} al {ayer} "
              f"(los últimos {RE_CHECK_DIAS} días ya guardados se vuelven a comprobar):")
        nuevas = descarga_pendientes(desde, ayer)

    filas = guarda_csv(columnas, filas, nuevas) if nuevas else filas
    dias, peso = construye_html(filas)
    print(f"\n{SALIDA}: {dias} días · {peso // 1024} KB")
    print(f"Días añadidos hoy: {len(nuevas)}")


if __name__ == "__main__":
    main()
