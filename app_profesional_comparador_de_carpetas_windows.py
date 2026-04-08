# app.py
# Aplicación profesional para comparar carpetas en Windows
# Interfaz moderna con Streamlit

import os
import hashlib
import pandas as pd
import streamlit as st
from pathlib import Path

st.set_page_config(page_title="Comparador de Carpetas PRO", layout="wide")

# -------------------------
# FUNCIONES CORE
# -------------------------

def obtener_archivos(ruta):
    archivos = []
    for root, _, files in os.walk(ruta):
        for f in files:
            full_path = os.path.join(root, f)
            try:
                size = os.path.getsize(full_path)
                archivos.append((f, full_path, size))
            except Exception:
                continue
    return archivos


def calcular_hash(path, chunk_size=8192):
    hash_md5 = hashlib.md5()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(chunk_size), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    except Exception:
        return None


def encontrar_duplicados(dir1, dir2, usar_hash=False):
    archivos1 = obtener_archivos(dir1)
    archivos2 = obtener_archivos(dir2)

    mapa = {}
    duplicados = []

    for nombre, path, size in archivos1:
        clave = (nombre, size)
        mapa.setdefault(clave, []).append(("carpeta1", path))

    for nombre, path, size in archivos2:
        clave = (nombre, size)
        if clave in mapa:
            for origen, path1 in mapa[clave]:
                if usar_hash:
                    if calcular_hash(path1) == calcular_hash(path):
                        duplicados.append((path1, path, size))
                else:
                    duplicados.append((path1, path, size))

    return duplicados


def eliminar_archivos(lista, carpeta_objetivo, dry_run=True):
    eliminados = []
    errores = []

    for p1, p2, _ in lista:
        target = p1 if carpeta_objetivo == 1 else p2
        try:
            if not dry_run:
                os.remove(target)
            eliminados.append(target)
        except Exception as e:
            errores.append((target, str(e)))

    return eliminados, errores

# -------------------------
# UI
# -------------------------

st.title("📂 Comparador de Carpetas PRO")

col1, col2 = st.columns(2)

with col1:
    dir1 = st.text_input("Ruta Carpeta 1")

with col2:
    dir2 = st.text_input("Ruta Carpeta 2")

usar_hash = st.checkbox("Comparar usando hash (más preciso, más lento)")
dry_run = st.checkbox("Modo seguro (no borrar archivos)", value=True)

if st.button("🔍 Comparar"):
    if not os.path.exists(dir1) or not os.path.exists(dir2):
        st.error("Una o ambas rutas no existen")
    else:
        with st.spinner("Analizando archivos..."):
            duplicados = encontrar_duplicados(dir1, dir2, usar_hash)

        if duplicados:
            df = pd.DataFrame(duplicados, columns=["Carpeta1", "Carpeta2", "Tamaño"])
            st.success(f"Duplicados encontrados: {len(df)}")
            st.dataframe(df, use_container_width=True)

            st.download_button("Descargar CSV", df.to_csv(index=False), "duplicados.csv")

            colA, colB = st.columns(2)

            with colA:
                if st.button("Eliminar en carpeta 1"):
                    elim, err = eliminar_archivos(duplicados, 1, dry_run)
                    st.warning(f"Eliminados: {len(elim)}")
                    if err:
                        st.error(err)

            with colB:
                if st.button("Eliminar en carpeta 2"):
                    elim, err = eliminar_archivos(duplicados, 2, dry_run)
                    st.warning(f"Eliminados: {len(elim)}")
                    if err:
                        st.error(err)
        else:
            st.info("No se encontraron duplicados")

# -------------------------
# INSTRUCCIONES
# -------------------------

# Ejecutar:
# pip install streamlit pandas
# streamlit run app.py

# Para convertir a .exe (Windows):
# pip install pyinstaller
# pyinstaller --onefile app.py
