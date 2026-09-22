# -*- coding: utf-8 -*-
"""Genera loterias/datos.json. Replica el cron del VPS:

    0 */2 * * * /var/www/html/loterias/backend/venv/bin/python worker.py

Los calculos usan el horario de Salta, no el del runner.
"""
import csv
import datetime
import json
import random
import time
from collections import Counter, defaultdict
from pathlib import Path

import requests

# Salta no usa horario de verano: UTC-3 todo el año.
ZONA = datetime.timezone(datetime.timedelta(hours=-3))
BASE = Path(__file__).resolve().parent
RUTA_CSV = BASE / "data" / "historico_sorteos.csv"
RUTA_JSON = BASE / "loterias" / "datos.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.loteriadesalta.com/",
}

MAPA_SORTEOS = {
    "MATUTINO": "Matutina",
    "VESPERTINO": "Vespertina",
    "EXTRA": "Extra",
    "NOCTURNO": "Nocturna",
    "NOCTURNOEXTRA": "La Revancha",
}
MAPA_JURISDICCIONES = {
    "SALTA": "SALTA",
    "JUJUY": "JUJUY",
    "NACIONAL": "CABA",
    "BSAS": "PBA",
}
JURISDICCIONES = ["SALTA", "JUJUY", "CABA", "PBA"]
SORTEOS = ["Matutina", "Vespertina", "Extra", "Nocturna", "La Revancha"]
SORTEO_RANK = {nombre: i for i, nombre in enumerate(SORTEOS)}
CAMPOS = ["fecha", "jurisdiccion", "sorteo", "numero", "posicion"]


def ahora_salta():
    return datetime.datetime.now(ZONA).replace(tzinfo=None)


def hoy_salta():
    return ahora_salta().date()


def cargar_historico():
    filas = []
    claves = set()
    if not RUTA_CSV.exists():
        return filas, claves
    with RUTA_CSV.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            row["posicion"] = int(row["posicion"])
            filas.append(row)
            claves.add((row["fecha"], row["jurisdiccion"], row["sorteo"], row["posicion"]))
    return filas, claves


def anexar_historico(nuevos):
    if not nuevos:
        return
    RUTA_CSV.parent.mkdir(parents=True, exist_ok=True)
    nuevo_archivo = not RUTA_CSV.exists() or RUTA_CSV.stat().st_size == 0
    with RUTA_CSV.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CAMPOS, lineterminator="\n")
        if nuevo_archivo:
            writer.writeheader()
        for row in nuevos:
            writer.writerow({campo: row[campo] for campo in CAMPOS})


def obtener_datos_multiples(dias_atras=3):
    datos = []
    hoy = hoy_salta()
    print(f"--- Descargando ultimos {dias_atras} dias ---")

    for i in range(dias_atras):
        fecha = hoy - datetime.timedelta(days=i)
        fecha_str_url = fecha.strftime("%d-%m-%Y")
        fecha_esperada_json = fecha.strftime("%d/%m/%Y")
        fecha_iso = fecha.strftime("%Y-%m-%d")
        url = f"https://www.loteriadesalta.com/loteria-ws/api/extracto/TOMBOLA/{fecha_str_url}/salta"
        print(f"--> Consultando: {fecha_str_url} ... ", end="", flush=True)
        time.sleep(0.5)

        try:
            response = requests.get(url, headers=HEADERS, timeout=10)
            if response.status_code != 200:
                print(f"HTTP {response.status_code}")
                continue
            try:
                data = response.json()
            except Exception:
                print("No JSON")
                continue

            fecha_json_raw = "No encontrada"
            resultados = data.get("resultados", {})
            if isinstance(resultados, dict):
                for info in resultados.values():
                    if not isinstance(info, dict) or "extractos" not in info:
                        continue
                    extractos = info.get("extractos") or {}
                    if not isinstance(extractos, dict):
                        continue
                    for provincia in extractos.values():
                        if isinstance(provincia, dict) and "fechaSorteo" in provincia:
                            fecha_json_raw = provincia["fechaSorteo"]
                            break

            if fecha_json_raw != "No encontrada" and fecha_json_raw != fecha_esperada_json:
                print(f"[SKIP] Fecha incorrecta ({fecha_json_raw}).")
                continue
            if not isinstance(resultados, dict):
                print("Sin datos.")
                continue

            contador_dia = 0
            for key_juego, info_juego in resultados.items():
                if key_juego not in MAPA_SORTEOS or not isinstance(info_juego, dict):
                    continue
                nombre_sorteo = MAPA_SORTEOS[key_juego]
                extractos_provincias = info_juego.get("extractos", {})
                if not isinstance(extractos_provincias, dict):
                    continue
                for key_prov, info_prov in extractos_provincias.items():
                    if key_prov not in MAPA_JURISDICCIONES or not isinstance(info_prov, dict):
                        continue
                    lista_numeros = info_prov.get("extracto", [])
                    if not isinstance(lista_numeros, list):
                        continue
                    for index, numero_str in enumerate(lista_numeros):
                        if not isinstance(numero_str, str):
                            continue
                        datos.append({
                            "fecha": fecha_iso,
                            "jurisdiccion": MAPA_JURISDICCIONES[key_prov],
                            "sorteo": nombre_sorteo,
                            "posicion": index + 1,
                            "numero": numero_str.strip().zfill(4),
                        })
                        contador_dia += 1

            print(f"OK ({contador_dia})" if contador_dia else "Vacio")
        except Exception as exc:
            print(f"Error: {exc}")

    return datos


def incorporar(filas, claves, nuevos):
    agregados = []
    for row in nuevos:
        clave = (row["fecha"], row["jurisdiccion"], row["sorteo"], row["posicion"])
        if clave in claves:
            continue
        claves.add(clave)
        filas.append(row)
        agregados.append(row)
    print(f"-> Guardados {len(agregados)} registros nuevos en historico.")
    return agregados


def procesar_estadisticas(filas):
    por_juris = defaultdict(list)
    for row in filas:
        if row["posicion"] == 1:
            por_juris[row["jurisdiccion"]].append(row)

    hoy = hoy_salta()
    stats = {}
    for juris in JURISDICCIONES:
        cabezas = por_juris.get(juris, [])
        if not cabezas:
            stats[juris] = {"mas_atrasados": [], "mas_salidos_cabeza": [], "top10_primeros": []}
            continue

        top10 = [
            {"numero": numero, "apariciones": cantidad}
            for numero, cantidad in Counter(row["numero"][-2:] for row in cabezas).most_common(10)
        ]

        ultima = {}
        for row in cabezas:
            term = row["numero"][-2:]
            fecha = datetime.date.fromisoformat(row["fecha"])
            if term not in ultima or fecha > ultima[term]:
                ultima[term] = fecha

        atrasados = []
        for i in range(100):
            numero = f"{i:02d}"
            if numero in ultima:
                dias = (hoy - ultima[numero]).days
                fecha_txt = ultima[numero].isoformat()
            else:
                dias = 999
                fecha_txt = "—"
            atrasados.append({"numero": numero, "dias_sin_salir": int(dias), "ultima": fecha_txt})
        atrasados.sort(key=lambda item: item["dias_sin_salir"], reverse=True)

        stats[juris] = {
            "mas_atrasados": atrasados[:10],
            "mas_salidos_cabeza": top10,
            "top10_primeros": top10,
        }
    return stats


def generar_matriz_favoritos(filas):
    por_sorteo = defaultdict(list)
    for row in filas:
        if row["jurisdiccion"] == "SALTA" and row["posicion"] == 1 and row["sorteo"] in SORTEO_RANK:
            por_sorteo[row["sorteo"]].append(str(row["numero"]).zfill(4))

    salida = []
    for nombre in SORTEOS:
        numeros = por_sorteo.get(nombre, [])
        if not numeros:
            continue
        columnas = [Counter(numero[i] for numero in numeros) for i in range(4)]
        salida.append({
            "sorteo": nombre,
            "favorito": "".join(columna.most_common(1)[0][0] for columna in columnas),
            "frecuencias": {
                "millar": {digito: int(cantidad) for digito, cantidad in columnas[0].most_common()},
                "centena": {digito: int(cantidad) for digito, cantidad in columnas[1].most_common()},
                "decena": {digito: int(cantidad) for digito, cantidad in columnas[2].most_common()},
                "unidad": {digito: int(cantidad) for digito, cantidad in columnas[3].most_common()},
            },
        })
    return salida


def generar_ultimas_buscador(filas):
    output = {}
    for juris in JURISDICCIONES:
        cabezas = [row for row in filas if row["jurisdiccion"] == juris and row["posicion"] == 1]
        cabezas.sort(key=lambda row: (row["fecha"], SORTEO_RANK.get(row["sorteo"], 0)), reverse=True)
        mapa = {}
        for row in cabezas:
            term = row["numero"][-2:]
            if term not in mapa:
                mapa[term] = {"terminacion": term, "fecha": row["fecha"], "sorteo": row["sorteo"]}
        output[juris] = [
            mapa.get(f"{i:02d}", {"terminacion": f"{i:02d}", "fecha": "—", "sorteo": ""})
            for i in range(100)
        ]
    return output


def cargar_significados():
    url = "https://www.loteriadesalta.com/loterias/significados.json"
    dicc = {}
    try:
        data = requests.get(url, timeout=5).json()
        if isinstance(data, list):
            for item in data:
                clave = str(item.get("numero") or item.get("n") or "").strip().zfill(2)
                valor = item.get("significado") or item.get("s") or item.get("nombre") or ""
                if clave and valor:
                    dicc[clave] = valor
        elif isinstance(data, dict):
            for clave, valor in data.items():
                dicc[str(clave).zfill(2)] = valor
    except Exception as exc:
        print(f"Error Significados: {exc}")

    if not dicc:
        dicc = {
            "00": "Huevos",
            "01": "Agua",
            "02": "Niño",
            "03": "San Cono",
            "07": "Revolver",
            "17": "Desgracia",
            "48": "Muerto que habla",
            "99": "Hermanos",
        }
    return dicc


def cargar_efemerides():
    url = "https://www.loteriadesalta.com/loterias/efemerides.json"
    efemerides_hoy = []
    hoy = hoy_salta()
    try:
        data = requests.get(url, timeout=5).json()
        for item in data:
            match = False
            if "mes" in item and "dia" in item:
                if int(item["mes"]) == hoy.month and int(item["dia"]) == hoy.day:
                    match = True
            elif "fecha" in item:
                suffix = f"-{hoy.month:02d}-{hoy.day:02d}"
                if str(item["fecha"]).endswith(suffix):
                    match = True
            if match:
                texto = item.get("texto") or item.get("efemeride") or item.get("titulo") or str(item)
                efemerides_hoy.append(texto)
    except Exception as exc:
        print(f"Error Efemerides: {exc}")

    if not efemerides_hoy:
        efemerides_hoy.append(f"Hoy es {hoy.strftime('%d/%m')}. ¡Mucha suerte!")
    return efemerides_hoy


def generar_fijas_random(diccionario_significados):
    random.seed(int(hoy_salta().strftime("%Y%m%d")))
    nums = random.sample(range(100), 3)
    fija, ojo, dato = (str(numero).zfill(2) for numero in nums)
    return [{
        "sorteo": "General",
        "fija": fija,
        "fija_significado": diccionario_significados.get(fija, "La Suerte"),
        "ojo": ojo,
        "ojo_significado": diccionario_significados.get(ojo, "Dinero"),
        "dato": dato,
        "dato_significado": diccionario_significados.get(dato, "Pálpito"),
    }]


def obtener_poceados():
    poceados = []
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.loteriadesalta.com/"}

    def normalizar_fecha(fecha):
        if not fecha:
            return "—"
        try:
            return datetime.datetime.strptime(fecha, "%d/%m/%Y").strftime("%Y-%m-%d")
        except Exception:
            return fecha

    def join_numeros(lista):
        if not lista:
            return ""
        return ", ".join(str(item) for item in lista)

    print("--- Descargando Poceados ---")

    try:
        data = requests.get(
            "https://www.loteriadesalta.com/loteria-ws/api/extracto/QUINI6/ultimo/salta",
            headers=headers,
            timeout=5,
        ).json()
        row = data["resultados"]["UNICO"]
        fecha = normalizar_fecha(row.get("f_sorte"))
        proximo = row.get("proximoSorteo") or {}
        prox = normalizar_fecha(proximo.get("f_sorte") if isinstance(proximo, dict) else None)
        poceados.append({"juego": "Quini 6 – Tradicional", "fecha": fecha, "resultado": join_numeros(row.get("ubicacionesTradicionales", [])), "proximo_sorteo": prox, "valor_apuesta": "$2.500"})
        poceados.append({"juego": "Quini 6 – Revancha", "fecha": fecha, "resultado": join_numeros(row.get("ubicacionesRevancha", [])), "proximo_sorteo": prox, "valor_apuesta": "$2.500"})
        poceados.append({"juego": "Quini 6 – Siempre Sale", "fecha": fecha, "resultado": join_numeros(row.get("ubicacionesSiempreSale", [])), "proximo_sorteo": prox, "valor_apuesta": "$2.500"})
    except Exception:
        pass

    try:
        data = requests.get(
            "https://www.loteriadesalta.com/loteria-ws/api/extracto/LOTO/ultimo/salta",
            headers=headers,
            timeout=5,
        ).json()
        row = data["resultados"]["UNICO"]
        fecha = normalizar_fecha(row.get("f_sorte"))
        proximo = row.get("proximoSorteo") or {}
        prox = normalizar_fecha(proximo.get("f_sorte") if isinstance(proximo, dict) else None)
        poceados.append({"juego": "Loto Plus – Tradicional", "fecha": fecha, "resultado": join_numeros(row.get("ubicacionesTradicional", [])), "proximo_sorteo": prox, "valor_apuesta": "$2.500"})
        poceados.append({"juego": "Loto Plus – Desquite", "fecha": fecha, "resultado": join_numeros(row.get("ubicacionesDesquite", [])), "proximo_sorteo": prox, "valor_apuesta": "$2.500"})
        poceados.append({"juego": "Loto Plus – Sale o Sale", "fecha": fecha, "resultado": join_numeros(row.get("ubicacionesSaleOSale", [])), "proximo_sorteo": prox, "valor_apuesta": "$2.500"})
    except Exception:
        pass

    try:
        data = requests.get(
            "https://www.loteriadesalta.com/loteria-ws/api/extracto/BRINCO/ultimo/salta",
            headers=headers,
            timeout=5,
        ).json()
        row = data["resultados"]["UNICO"]
        fecha = normalizar_fecha(row.get("f_sorte"))
        proximo = row.get("proximoSorteo") or {}
        prox = normalizar_fecha(proximo.get("f_sorte") if isinstance(proximo, dict) else None)
        poceados.append({"juego": "Brinco", "fecha": fecha, "resultado": join_numeros(row.get("extraccionesTradicional", [])), "proximo_sorteo": prox, "valor_apuesta": "$1.000"})
    except Exception:
        pass

    try:
        data = requests.get(
            "https://www.loteriadesalta.com/loteria-ws/api/extracto/TELEKINO/ultimo/salta",
            headers=headers,
            timeout=5,
        ).json()
        row = data["resultados"]["UNICO"]
        fecha = normalizar_fecha(row.get("f_sorte"))
        poceados.append({"juego": "Telekino", "fecha": fecha, "resultado": join_numeros(row.get("resultadosTelekino", [])), "proximo_sorteo": "Ver Web", "valor_apuesta": "$2.000"})
        poceados.append({"juego": "Rekino", "fecha": fecha, "resultado": join_numeros(row.get("resultadosRekino", [])), "proximo_sorteo": "Ver Web", "valor_apuesta": "$2.000"})
    except Exception:
        pass

    return poceados


def main():
    print(f"--- WORKER INICIADO: {ahora_salta()} ---")
    filas, claves = cargar_historico()
    print(f"Historico cargado: {len(filas)} filas")

    nuevos = obtener_datos_multiples(dias_atras=3)
    agregados = incorporar(filas, claves, nuevos)
    anexar_historico(agregados)

    json_data = {
        "meta": {"actualizado": str(ahora_salta())},
        "efemerides": cargar_efemerides(),
        "fijas_dos_cifras": generar_fijas_random(cargar_significados()),
        "estadisticas": procesar_estadisticas(filas),
        "ultimas_apariciones": generar_ultimas_buscador(filas),
        "favoritos_detalle": generar_matriz_favoritos(filas),
        "poceados": obtener_poceados(),
    }

    RUTA_JSON.parent.mkdir(parents=True, exist_ok=True)
    with RUTA_JSON.open("w", encoding="utf-8") as handle:
        json.dump(json_data, handle, ensure_ascii=False)
    print(f"JSON generado en: {RUTA_JSON}")


if __name__ == "__main__":
    main()
