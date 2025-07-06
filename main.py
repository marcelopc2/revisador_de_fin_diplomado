import streamlit as st
import requests
import pandas as pd
import re
from decouple import config
from datetime import datetime, timedelta
import pytz

# ————— Layout en wide mode —————
st.set_page_config(
    layout="wide",
    page_title="Revisador de diplomados terminados y finiquitados".upper(),
    page_icon="🐸"
)

# ————— Configuración de zonas horarias —————
UTC = pytz.UTC
SANTIAGO = pytz.timezone('America/Santiago')

# ————— Configuración de Canvas —————
CANVAS_URL = config("URL")
API_TOKEN  = config("TOKEN")
HEADERS    = {"Authorization": f"Bearer {API_TOKEN}"}
session = requests.Session()
session.headers.update(HEADERS)

# ————— Funciones auxiliares —————
def parse_input(text: str) -> list[str]:
    return [i for i in re.split(r"[,\s]+", text.strip()) if i]

def parse_canvas_datetime(dt_str: str) -> datetime | None:
    if not dt_str:
        return None
    dt_obj = datetime.fromisoformat(dt_str)
    if dt_obj.tzinfo is None:
        dt_obj = UTC.localize(dt_obj)
    return dt_obj.astimezone(SANTIAGO)

def canvas_request(method: str, endpoint: str, payload=None, paginated=False):
    if not CANVAS_URL:
        st.error("⚠️ La variable URL no está configurada.")
        st.stop()
    url = f"{CANVAS_URL}{endpoint}"
    results = []
    try:
        while url:
            resp = session.request(method, url, json=payload)
            if not resp.ok:
                st.error(f"Error {resp.status_code} en {url}: {resp.text}")
                return None
            data = resp.json()
            if paginated:
                results.extend(data)
                url = resp.links.get("next", {}).get("url")
            else:
                return data
        return results
    except requests.RequestException as e:
        st.error(f"Excepción en petición a Canvas: {e}")
        return None

# ————— Interfaz Streamlit —————
st.title("🐸 REVISADOR DE DIPLOMADOS TERMINADOS Y FINIQUITADOS 🐸")
st.info(
    "### Información adicional\n"
    "- El cierre aproximado se basa en el inicio de **Curso 1** de cada diplomado + 171 días.\n"
    "- El cierre oficial se basa en el término de **Curso 1** de cada diplomado + 21 días de gracia."
)

input_text = st.text_area(
    "Ingresa los IDs de los cursos (separados por comas o espacios):",
    height=100
)

if st.button("Revisar cursos"):
    st.divider()
    course_ids = parse_input(input_text)
    if not course_ids:
        st.warning("Ingresa al menos un ID de curso.")
        st.stop()

    # — Construir filas con dt_start y dt_end —
    rows = []
    for cid in course_ids:
        c = canvas_request("get", f"/courses/{cid}")
        if c is None:
            rows.append({
                "Diplomado":        "Error al cargar",
                "SIS ID":           "",
                "Course Code":      "",
                "Nombre del curso": f"ID {cid}",
                "F. Inicio":        "Error",
                "F. Término":       "Error",
                "dt_start":         None,
                "dt_end":           None
            })
            continue

        name   = c.get("name", "Sin nombre")
        sis_id = c.get("sis_course_id", "")
        code   = c.get("course_code", "")

        acc_id = c.get("account_id")
        if acc_id:
            acc    = canvas_request("get", f"/accounts/{acc_id}")
            diplom = acc.get("name", "Sin Diplomado") if acc else "Error al cargar"
        else:
            diplom = "No disponible"

        dt_start = parse_canvas_datetime(c.get("start_at"))
        dt_end   = parse_canvas_datetime(c.get("end_at"))
        start_str = dt_start.strftime("%d-%m-%Y") if dt_start else "No configurada"
        end_str   = dt_end.strftime("%d-%m-%Y")   if dt_end   else "No configurada"

        rows.append({
            "Diplomado":        diplom,
            "SIS ID":           sis_id,
            "Course Code":      code,
            "Nombre del curso": name,
            "F. Inicio":        start_str,
            "F. Término":       end_str,
            "dt_start":         dt_start,
            "dt_end":           dt_end,
        })

    df = pd.DataFrame(rows)

    # — Extraer número de curso para validación y agrupamiento —
    def extract_num(s: str):
        m = re.search(r'-C(\d+)-', s)
        if m: return int(m.group(1))
        m2 = re.search(r'Curso\s*(\d+)', s)
        return int(m2.group(1)) if m2 else None

    df["Curso_Num"] = df["SIS ID"].fillna("").map(extract_num)
    df["Curso_Num"] = df["Curso_Num"].fillna(df["Course Code"].map(extract_num))

    # — Calcular Cierre Aprox. por diplomado (dt_start de Curso 1 + 171d) —
    dt1_start = (
        df[df["Curso_Num"] == 1]
        .groupby("Diplomado")["dt_start"]
        .first()
        .to_dict()
    )
    cierres_aprox = {
        d: (dt + timedelta(days=171)).strftime("%d-%m-%Y")
        for d, dt in dt1_start.items() if dt is not None
    }
    df["Cierre Aprox."] = df["Diplomado"].map(lambda d: cierres_aprox.get(d, "No aplica"))

    # — Calcular Cierre Oficial por diplomado (dt_end de Curso 1 + 21d) —
    dt1_end = (
        df[df["Curso_Num"] == 1]
        .groupby("Diplomado")["dt_end"]
        .first()
        .to_dict()
    )
    cierres_oficial = {
        d: (dt + timedelta(days=21)).strftime("%d-%m-%Y")
        for d, dt in dt1_end.items() if dt is not None
    }
    df["Cierre Oficial"] = df["Diplomado"].map(lambda d: cierres_oficial.get(d, "No aplica"))

    # — Validar que cada diplomado tenga al menos un Curso 1 —
    faltan = [d for d, g in df.groupby("Diplomado") if not (g["Curso_Num"] == 1).any()]
    if faltan:
        st.error("🚨 Faltan Curso 1 para: " + ", ".join(faltan))

    # — Añadir estados —
    today = datetime.now(SANTIAGO).date()
    def estado(x, na="Error"):
        try:
            return "Terminado" if today > datetime.strptime(x, "%d-%m-%Y").date() else "En curso"
        except:
            return na

    df["Estado Aprox."]  = df["Cierre Aprox."].apply(lambda x: estado(x, na="Error"))
    df["Estado Oficial"] = df["Cierre Oficial"].apply(lambda x: estado(x, na="No aplica"))

    # — Ordenar por Diplomado y Curso_Num, luego elegir columnas finales —
    final_cols = [
        "Diplomado", "SIS ID", "Course Code", "Nombre del curso",
        "F. Inicio", "F. Término",
        "Cierre Aprox.", "Estado Aprox.",
        "Cierre Oficial", "Estado Oficial"
    ]
    df = df.sort_values(["Diplomado", "Curso_Num"], na_position="last")[final_cols]

    # — Estilos de color —
    def style_states(data: pd.DataFrame) -> pd.DataFrame:
        styles = pd.DataFrame("", index=data.index, columns=data.columns)
        for c in ["Estado Aprox.", "Estado Oficial"]:
            styles[c] = data[c].map(
                lambda v: "background-color: green" if v == "Terminado" else "background-color: red"
            )
        styles["F. Término"] = data["F. Término"].map(
            lambda v: "background-color: yellow" if v == "No configurada" else ""
        )
        return styles

    styled = df.style.apply(style_states, axis=None)
    st.dataframe(styled, use_container_width=True, hide_index=True)
