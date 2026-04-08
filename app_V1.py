"""
╔══════════════════════════════════════════════════════════════════╗
║           COMPARADOR DE CARPETAS - DETECTOR DE DUPLICADOS        ║
║                     Versión 1.0 - Python 3.x                     ║
╚══════════════════════════════════════════════════════════════════╝

Uso:
    streamlit run app.py

Dependencias:
    pip install streamlit pandas openpyxl
"""

import os
import hashlib
import logging
import io
from datetime import datetime
from collections import defaultdict
from pathlib import Path

import pandas as pd
import streamlit as st

# ─────────────────────────────────────────────
# CONFIGURACIÓN DE LOGGING
# ─────────────────────────────────────────────

LOG_FILE = "comparador_carpetas.log"

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# FUNCIONES AUXILIARES
# ─────────────────────────────────────────────

def formatear_bytes(size_bytes: int) -> str:
    """Convierte bytes a una representación legible (KB, MB, GB)."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.2f} KB"
    elif size_bytes < 1024 ** 3:
        return f"{size_bytes / 1024**2:.2f} MB"
    else:
        return f"{size_bytes / 1024**3:.2f} GB"


def calcular_hash(filepath: str, algoritmo: str = "md5", bloque: int = 65536) -> str | None:
    """
    Calcula el hash de un archivo leyéndolo en bloques.
    Evita cargar archivos grandes completos en memoria.

    Args:
        filepath: Ruta completa al archivo.
        algoritmo: 'md5' o 'sha256'.
        bloque: Tamaño del bloque en bytes (default 64KB).

    Returns:
        Hash hexadecimal o None si ocurrió un error.
    """
    try:
        h = hashlib.md5() if algoritmo == "md5" else hashlib.sha256()
        with open(filepath, "rb") as f:
            while chunk := f.read(bloque):
                h.update(chunk)
        return h.hexdigest()
    except PermissionError:
        logger.warning(f"Sin permisos para leer: {filepath}")
        return None
    except FileNotFoundError:
        logger.warning(f"Archivo no encontrado: {filepath}")
        return None
    except OSError as e:
        logger.error(f"OSError al calcular hash de {filepath}: {e}")
        return None


def escanear_carpeta(carpeta: str) -> list[dict]:
    """
    Recorre recursivamente una carpeta y retorna información de cada archivo.

    Args:
        carpeta: Ruta de la carpeta raíz a escanear.

    Returns:
        Lista de dicts con: ruta, nombre, tamaño, carpeta_origen.
    """
    archivos = []
    for root, _, files in os.walk(carpeta):
        for nombre in files:
            ruta_completa = os.path.join(root, nombre)
            try:
                tamaño = os.path.getsize(ruta_completa)
                archivos.append({
                    "ruta": ruta_completa,
                    "nombre": nombre,
                    "tamaño": tamaño,
                    "carpeta_origen": carpeta,
                })
            except (PermissionError, FileNotFoundError, OSError) as e:
                logger.warning(f"No se pudo acceder a {ruta_completa}: {e}")
    return archivos


def encontrar_duplicados(
    archivos1: list[dict],
    archivos2: list[dict],
    usar_hash: bool = False,
    algoritmo_hash: str = "md5",
    progress_callback=None,
) -> list[dict]:
    """
    Compara dos listas de archivos y devuelve los duplicados.

    Estrategia:
      1. Agrupar archivos por (nombre, tamaño).
      2. Si usar_hash=True, confirmar con hash real.

    Args:
        archivos1: Lista de archivos de la carpeta 1.
        archivos2: Lista de archivos de la carpeta 2.
        usar_hash: Si True, calcula hash para confirmar duplicados.
        algoritmo_hash: 'md5' o 'sha256'.
        progress_callback: Función callable(actual, total) para progreso.

    Returns:
        Lista de dicts representando grupos de duplicados.
    """
    # Paso 1: Indexar carpeta 2 por (nombre, tamaño)
    indice2 = defaultdict(list)
    for archivo in archivos2:
        clave = (archivo["nombre"], archivo["tamaño"])
        indice2[clave].append(archivo)

    duplicados = []
    total = len(archivos1)
    hash_cache: dict[str, str] = {}  # Cache para evitar recálculos

    for i, arch1 in enumerate(archivos1):
        clave = (arch1["nombre"], arch1["tamaño"])

        if progress_callback:
            progress_callback(i + 1, total)

        if clave in indice2:
            candidatos = indice2[clave]

            if not usar_hash:
                # Coincidencia por nombre + tamaño (suficiente para uso general)
                for arch2 in candidatos:
                    duplicados.append({
                        "nombre": arch1["nombre"],
                        "ruta_carpeta1": arch1["ruta"],
                        "ruta_carpeta2": arch2["ruta"],
                        "tamaño_bytes": arch1["tamaño"],
                        "tamaño_legible": formatear_bytes(arch1["tamaño"]),
                        "metodo": "Nombre + Tamaño",
                    })
            else:
                # Confirmar con hash
                hash1 = hash_cache.get(arch1["ruta"])
                if hash1 is None:
                    hash1 = calcular_hash(arch1["ruta"], algoritmo_hash)
                    if hash1:
                        hash_cache[arch1["ruta"]] = hash1

                if hash1 is None:
                    logger.warning(f"No se pudo calcular hash para {arch1['ruta']}")
                    continue

                for arch2 in candidatos:
                    hash2 = hash_cache.get(arch2["ruta"])
                    if hash2 is None:
                        hash2 = calcular_hash(arch2["ruta"], algoritmo_hash)
                        if hash2:
                            hash_cache[arch2["ruta"]] = hash2

                    if hash2 and hash1 == hash2:
                        duplicados.append({
                            "nombre": arch1["nombre"],
                            "ruta_carpeta1": arch1["ruta"],
                            "ruta_carpeta2": arch2["ruta"],
                            "tamaño_bytes": arch1["tamaño"],
                            "tamaño_legible": formatear_bytes(arch1["tamaño"]),
                            "metodo": f"Hash {algoritmo_hash.upper()}",
                            "hash": hash1,
                        })

    return duplicados


def eliminar_archivos(rutas: list[str], dry_run: bool = True) -> dict:
    """
    Elimina una lista de archivos con manejo de errores.

    Args:
        rutas: Lista de rutas completas a eliminar.
        dry_run: Si True, simula la eliminación sin borrar nada.

    Returns:
        Dict con listas de 'eliminados' y 'errores'.
    """
    resultado = {"eliminados": [], "errores": []}

    for ruta in rutas:
        if dry_run:
            resultado["eliminados"].append(ruta)
            logger.info(f"[DRY RUN] Simularía eliminar: {ruta}")
        else:
            try:
                os.remove(ruta)
                resultado["eliminados"].append(ruta)
                logger.info(f"Eliminado: {ruta}")
            except PermissionError:
                msg = f"Sin permisos para eliminar: {ruta}"
                resultado["errores"].append(msg)
                logger.error(msg)
            except FileNotFoundError:
                msg = f"Archivo no encontrado: {ruta}"
                resultado["errores"].append(msg)
                logger.warning(msg)
            except OSError as e:
                msg = f"Error al eliminar {ruta}: {e}"
                resultado["errores"].append(msg)
                logger.error(msg)

    return resultado


def exportar_csv(df: pd.DataFrame) -> bytes:
    """Exporta un DataFrame a CSV en memoria."""
    return df.to_csv(index=False).encode("utf-8")


def exportar_excel(df: pd.DataFrame) -> bytes:
    """Exporta un DataFrame a Excel (.xlsx) en memoria."""
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Duplicados")
    return buffer.getvalue()


# ─────────────────────────────────────────────
# INTERFAZ STREAMLIT
# ─────────────────────────────────────────────

def configurar_pagina():
    """Configura la página de Streamlit."""
    st.set_page_config(
        page_title="Comparador de Carpetas",
        page_icon="🔍",
        layout="wide",
        initial_sidebar_state="expanded",
    )


def sidebar_opciones() -> dict:
    """Renderiza la barra lateral con opciones de configuración."""
    st.sidebar.title("⚙️ Opciones")
    st.sidebar.markdown("---")

    usar_hash = st.sidebar.checkbox(
        "🔐 Comparar por Hash (más preciso)",
        value=False,
        help="Calcula el hash de cada archivo para confirmar duplicados reales. Más lento pero más preciso.",
    )

    algoritmo = "md5"
    if usar_hash:
        algoritmo = st.sidebar.radio(
            "Algoritmo de Hash",
            options=["md5", "sha256"],
            format_func=lambda x: x.upper(),
            help="MD5 es más rápido. SHA256 es más seguro pero más lento.",
        )

    dry_run = st.sidebar.checkbox(
        "🛡️ Modo Dry Run (simulación)",
        value=True,
        help="Activo: simula las eliminaciones sin borrar nada. RECOMENDADO para primera revisión.",
    )

    st.sidebar.markdown("---")
    st.sidebar.markdown("### 📋 Información")
    st.sidebar.info(
        "**Dry Run activo:** Ningún archivo será eliminado.\n\n"
        "Desactívalo solo cuando estés seguro de los resultados."
        if dry_run else
        "⚠️ **Dry Run desactivado.** Los archivos SÍ serán eliminados."
    )

    return {
        "usar_hash": usar_hash,
        "algoritmo": algoritmo,
        "dry_run": dry_run,
    }


def mostrar_metricas(duplicados: list[dict]):
    """Muestra métricas resumidas de los duplicados encontrados."""
    total = len(duplicados)
    espacio_total = sum(d["tamaño_bytes"] for d in duplicados)

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("📂 Duplicados encontrados", total)
    with col2:
        st.metric("💾 Espacio recuperable", formatear_bytes(espacio_total))
    with col3:
        nombres_unicos = len(set(d["nombre"] for d in duplicados))
        st.metric("📄 Nombres únicos duplicados", nombres_unicos)


def mostrar_tabla(duplicados: list[dict]) -> pd.DataFrame:
    """Construye y muestra el DataFrame de duplicados."""
    columnas = ["nombre", "ruta_carpeta1", "ruta_carpeta2", "tamaño_legible", "metodo"]
    if duplicados and "hash" in duplicados[0]:
        columnas.append("hash")

    df = pd.DataFrame(duplicados)[columnas]
    df.columns = ["Nombre", "Ruta Carpeta 1", "Ruta Carpeta 2", "Tamaño", "Método"] + (
        ["Hash"] if "hash" in columnas else []
    )

    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Nombre": st.column_config.TextColumn("📄 Nombre", width="medium"),
            "Ruta Carpeta 1": st.column_config.TextColumn("📁 Ruta Carpeta 1", width="large"),
            "Ruta Carpeta 2": st.column_config.TextColumn("📁 Ruta Carpeta 2", width="large"),
            "Tamaño": st.column_config.TextColumn("💾 Tamaño", width="small"),
            "Método": st.column_config.TextColumn("🔍 Método", width="small"),
        },
    )
    return df


def seccion_eliminacion(duplicados: list[dict], dry_run: bool):
    """Renderiza los botones de eliminación con confirmación."""
    st.markdown("---")
    st.subheader("🗑️ Acciones de Eliminación")

    if dry_run:
        st.warning("🛡️ **Modo Dry Run activo** — Las acciones simularán la eliminación sin borrar archivos reales.")
    else:
        st.error("⚠️ **Atención:** El modo Dry Run está desactivado. Los archivos SÍ serán eliminados.")

    rutas1 = [d["ruta_carpeta1"] for d in duplicados]
    rutas2 = [d["ruta_carpeta2"] for d in duplicados]
    espacio = formatear_bytes(sum(d["tamaño_bytes"] for d in duplicados))

    col1, col2, col3 = st.columns(3)

    with col1:
        if st.button("🗑️ Eliminar duplicados en Carpeta 1", type="primary", use_container_width=True):
            st.session_state["confirmar_accion"] = ("carpeta1", rutas1)

    with col2:
        if st.button("🗑️ Eliminar duplicados en Carpeta 2", type="primary", use_container_width=True):
            st.session_state["confirmar_accion"] = ("carpeta2", rutas2)

    with col3:
        if st.button("✅ No eliminar nada", use_container_width=True):
            st.session_state.pop("confirmar_accion", None)
            st.info("No se realizará ninguna acción.")

    # Confirmación de eliminación
    if "confirmar_accion" in st.session_state:
        carpeta_label, rutas = st.session_state["confirmar_accion"]
        label_display = "Carpeta 1" if carpeta_label == "carpeta1" else "Carpeta 2"

        st.markdown(f"#### ⚠️ Confirmación requerida")
        st.write(f"Se {'simularán' if dry_run else '**eliminarán**'} **{len(rutas)} archivos** de **{label_display}**.")
        st.write(f"Espacio {'simulado' if dry_run else 'liberado'}: **{espacio}**")

        with st.expander("Ver archivos que serán afectados"):
            for r in rutas[:50]:
                st.code(r)
            if len(rutas) > 50:
                st.caption(f"... y {len(rutas) - 50} más.")

        col_si, col_no = st.columns(2)
        with col_si:
            if st.button("✔️ Confirmar", type="primary"):
                resultado = eliminar_archivos(rutas, dry_run=dry_run)

                if resultado["eliminados"]:
                    accion = "simuló eliminar" if dry_run else "eliminó"
                    st.success(f"✅ Se {accion} exitosamente **{len(resultado['eliminados'])} archivos**.")

                if resultado["errores"]:
                    st.error(f"❌ {len(resultado['errores'])} errores:")
                    for err in resultado["errores"]:
                        st.warning(err)

                st.session_state.pop("confirmar_accion", None)

        with col_no:
            if st.button("✖️ Cancelar"):
                st.session_state.pop("confirmar_accion", None)
                st.info("Operación cancelada.")


def seccion_exportar(df: pd.DataFrame):
    """Botones de exportación a CSV y Excel."""
    st.markdown("---")
    st.subheader("📤 Exportar Resultados")

    col1, col2 = st.columns(2)

    with col1:
        csv_data = exportar_csv(df)
        st.download_button(
            label="⬇️ Descargar CSV",
            data=csv_data,
            file_name=f"duplicados_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv",
            use_container_width=True,
        )

    with col2:
        try:
            excel_data = exportar_excel(df)
            st.download_button(
                label="⬇️ Descargar Excel (.xlsx)",
                data=excel_data,
                file_name=f"duplicados_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
        except ImportError:
            st.warning("Instala `openpyxl` para exportar a Excel: `pip install openpyxl`")


# ─────────────────────────────────────────────
# FUNCIÓN PRINCIPAL
# ─────────────────────────────────────────────

def main():
    configurar_pagina()
    opciones = sidebar_opciones()

    # ── Header ──
    st.title("🔍 Comparador de Carpetas")
    st.markdown("Detecta archivos duplicados entre dos carpetas de forma rápida y segura.")
    st.markdown("---")

    # ── Entrada de rutas ──
    col1, col2 = st.columns(2)
    with col1:
        carpeta1 = st.text_input(
            "📁 Carpeta 1",
            placeholder="Ej: C:\\Users\\Usuario\\Documentos",
            help="Ruta completa de la primera carpeta a comparar.",
        )
        if carpeta1:
            if os.path.isdir(carpeta1):
                st.success("✅ Carpeta encontrada")
            else:
                st.error("❌ La ruta no existe o no es una carpeta")

    with col2:
        carpeta2 = st.text_input(
            "📁 Carpeta 2",
            placeholder="Ej: C:\\Users\\Usuario\\Descargas",
            help="Ruta completa de la segunda carpeta a comparar.",
        )
        if carpeta2:
            if os.path.isdir(carpeta2):
                st.success("✅ Carpeta encontrada")
            else:
                st.error("❌ La ruta no existe o no es una carpeta")

    st.markdown("---")

    # ── Botón de inicio ──
    iniciar = st.button("🚀 Iniciar Comparación", type="primary", use_container_width=True)

    if iniciar:
        # Validaciones
        errores_validacion = []
        if not carpeta1:
            errores_validacion.append("Debes ingresar la ruta de la Carpeta 1.")
        elif not os.path.isdir(carpeta1):
            errores_validacion.append("La Carpeta 1 no existe o no es válida.")

        if not carpeta2:
            errores_validacion.append("Debes ingresar la ruta de la Carpeta 2.")
        elif not os.path.isdir(carpeta2):
            errores_validacion.append("La Carpeta 2 no existe o no es válida.")

        if errores_validacion:
            for e in errores_validacion:
                st.error(f"❌ {e}")
            return

        if carpeta1 == carpeta2:
            st.warning("⚠️ Las dos carpetas son iguales. Ingresa rutas diferentes.")
            return

        # ── Escaneo ──
        with st.spinner("🔎 Escaneando carpetas..."):
            archivos1 = escanear_carpeta(carpeta1)
            archivos2 = escanear_carpeta(carpeta2)

        st.info(
            f"📊 Carpeta 1: **{len(archivos1)} archivos** | "
            f"Carpeta 2: **{len(archivos2)} archivos**"
        )

        if not archivos1 or not archivos2:
            st.warning("Una o ambas carpetas están vacías o sin archivos accesibles.")
            return

        # ── Búsqueda de duplicados con progreso ──
        st.markdown("#### 🔄 Buscando duplicados...")
        barra = st.progress(0, text="Iniciando análisis...")

        def actualizar_progreso(actual, total):
            pct = int((actual / total) * 100)
            barra.progress(pct, text=f"Analizando archivos... {actual}/{total}")

        with st.spinner("Comparando archivos..."):
            duplicados = encontrar_duplicados(
                archivos1,
                archivos2,
                usar_hash=opciones["usar_hash"],
                algoritmo_hash=opciones["algoritmo"],
                progress_callback=actualizar_progreso,
            )

        barra.progress(100, text="✅ Análisis completo")

        # ── Resultados ──
        st.markdown("---")
        st.subheader("📋 Resultados")

        if not duplicados:
            st.success("🎉 No se encontraron archivos duplicados entre las carpetas.")
            return

        mostrar_metricas(duplicados)
        st.markdown("### 📄 Lista de Duplicados")
        df = mostrar_tabla(duplicados)

        # ── Exportar ──
        seccion_exportar(df)

        # ── Eliminación ──
        seccion_eliminacion(duplicados, opciones["dry_run"])

    # ── Footer ──
    st.markdown("---")
    st.caption(
        f"🗂️ Log guardado en: `{os.path.abspath(LOG_FILE)}` | "
        "Comparador de Carpetas v1.0 · Desarrollado con Python + Streamlit"
    )


if __name__ == "__main__":
    main()
