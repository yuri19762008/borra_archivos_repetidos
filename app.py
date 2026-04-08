import os
import io
import hashlib
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st

# Intentamos importar send2trash para poder enviar archivos a la
# Papelera de reciclaje real de Windows. Si no está instalada,
# la app seguirá funcionando, pero avisará que esa opción no está disponible.
try:
    from send2trash import send2trash
    SEND2TRASH_AVAILABLE = True
except ImportError:
    SEND2TRASH_AVAILABLE = False
    send2trash = None

# Constantes generales de la aplicación.
APP_TITLE = "Comparador de carpetas y duplicados"
LOG_FILE = "folder_compare.log"
HASH_BLOCK_SIZE = 1024 * 1024
DELETE_CONFIRM_WORD = "CONFIRMAR"


# -------------------------------------------------------------------
# CONFIGURACIÓN DE LOGS
# -------------------------------------------------------------------
# Esta función crea y devuelve un logger que escribe en un archivo .log.
# El objetivo es guardar errores, advertencias y eventos importantes
# para poder revisar lo ocurrido más tarde.
def setup_logger() -> logging.Logger:
    logger = logging.getLogger("folder_compare")

    # Si el logger ya tiene handlers, lo devolvemos tal cual para evitar
    # duplicar mensajes en el archivo de log.
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")

    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


LOGGER = setup_logger()

# Configuración visual principal de Streamlit.
st.set_page_config(page_title=APP_TITLE, page_icon="📁", layout="wide")


# -------------------------------------------------------------------
# FUNCIONES AUXILIARES
# -------------------------------------------------------------------
# Normaliza una ruta de Windows o del sistema actual:
# - quita comillas sobrantes,
# - expande variables de entorno,
# - expande ~ si existe,
# - la convierte en ruta absoluta.
def normalize_path(path: str) -> str:
    return os.path.abspath(os.path.expandvars(os.path.expanduser(path.strip().strip('"'))))


# Calcula el hash de un archivo en bloques para evitar consumir demasiada memoria.
# Esto es importante cuando el archivo es grande.
# Se usa cache de Streamlit para no recalcular hashes innecesariamente.
@st.cache_data(show_spinner=False)
def compute_file_hash(file_path: str, algorithm: str = "md5") -> Optional[str]:
    hasher = hashlib.md5() if algorithm.lower() == "md5" else hashlib.sha256()

    try:
        with open(file_path, "rb") as file_obj:
            while True:
                chunk = file_obj.read(HASH_BLOCK_SIZE)
                if not chunk:
                    break
                hasher.update(chunk)
        return hasher.hexdigest()
    except (PermissionError, FileNotFoundError, OSError) as exc:
        LOGGER.error("Error calculando hash para %s: %s", file_path, exc)
        return None


# Convierte tamaño en bytes a un texto legible, por ejemplo:
# 1024 -> 1.00 KB
def format_size(size_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size_bytes)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{size_bytes} B"


# Verifica que la ruta ingresada exista y que realmente sea una carpeta.
# Devuelve una tupla: (es_valida, mensaje_o_ruta_normalizada)
def validate_folder(path: str) -> Tuple[bool, str]:
    if not path.strip():
        return False, "La ruta está vacía."

    normalized = normalize_path(path)

    if not os.path.exists(normalized):
        return False, f"La ruta no existe: {normalized}"
    if not os.path.isdir(normalized):
        return False, f"La ruta no es una carpeta válida: {normalized}"

    return True, normalized


# -------------------------------------------------------------------
# ESCANEO DE CARPETAS
# -------------------------------------------------------------------
# Recorre una carpeta y todas sus subcarpetas para registrar sus archivos.
# Para cada archivo guarda:
# - nombre,
# - ruta completa,
# - ruta relativa,
# - tamaño,
# - fecha de modificación.
def collect_files(folder_path: str, progress_bar=None, status_text=None) -> Tuple[List[Dict], List[str]]:
    file_records: List[Dict] = []
    errors: List[str] = []
    all_dirs = []

    # Primer recorrido: contar directorios para estimar progreso.
    for root, dirs, _ in os.walk(folder_path):
        all_dirs.append(root)
        for directory in dirs:
            all_dirs.append(os.path.join(root, directory))

    total_dirs = max(len(all_dirs), 1)
    processed_dirs = 0

    # Segundo recorrido: registrar archivos reales.
    for root, _, files in os.walk(folder_path):
        processed_dirs += 1

        if progress_bar is not None:
            progress_bar.progress(min(processed_dirs / total_dirs, 1.0), text=f"Escaneando: {root}")

        if status_text is not None:
            status_text.caption(f"Procesando carpeta: {root}")

        for filename in files:
            full_path = os.path.join(root, filename)
            try:
                stats = os.stat(full_path)
                file_records.append(
                    {
                        "folder_root": folder_path,
                        "file_name": filename,
                        "full_path": full_path,
                        "relative_path": os.path.relpath(full_path, folder_path),
                        "size_bytes": stats.st_size,
                        "modified_time": datetime.fromtimestamp(stats.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    }
                )
            except PermissionError as exc:
                msg = f"Sin permisos para leer: {full_path} | {exc}"
                LOGGER.warning(msg)
                errors.append(msg)
            except FileNotFoundError as exc:
                msg = f"Archivo no encontrado durante el escaneo: {full_path} | {exc}"
                LOGGER.warning(msg)
                errors.append(msg)
            except OSError as exc:
                msg = f"Error del sistema al leer: {full_path} | {exc}"
                LOGGER.warning(msg)
                errors.append(msg)

    return file_records, errors


# -------------------------------------------------------------------
# DETECCIÓN DE DUPLICADOS
# -------------------------------------------------------------------
# Compara dos listas de archivos.
# La lógica es:
# 1. Encontrar candidatos por nombre + tamaño.
# 2. Si el usuario activó hash, confirmar además con MD5 o SHA256.
# 3. Crear un DataFrame agrupado por group_id.
def find_duplicates(
    files_folder_1: List[Dict],
    files_folder_2: List[Dict],
    use_hash: bool = False,
    hash_algorithm: str = "md5",
    hash_progress_bar=None,
    status_text=None,
) -> Tuple[pd.DataFrame, List[str]]:
    errors: List[str] = []
    index_folder_2: Dict[Tuple[str, int], List[Dict]] = {}

    # Indexamos carpeta 2 por (nombre, tamaño) para acelerar la búsqueda.
    for record in files_folder_2:
        key = (record["file_name"], record["size_bytes"])
        index_folder_2.setdefault(key, []).append(record)

    # Generamos pares candidatos a duplicado.
    candidate_pairs = []
    for left in files_folder_1:
        key = (left["file_name"], left["size_bytes"])
        matches = index_folder_2.get(key, [])
        for right in matches:
            candidate_pairs.append((left, right))

    total_pairs = max(len(candidate_pairs), 1)
    result_rows = []
    group_id = 0

    for idx, (left, right) in enumerate(candidate_pairs, start=1):
        if status_text is not None:
            status_text.caption(f"Comparando duplicados candidatos: {idx}/{len(candidate_pairs)}")

        if hash_progress_bar is not None:
            hash_progress_bar.progress(min(idx / total_pairs, 1.0), text="Validando duplicados")

        left_hash = None
        right_hash = None
        is_duplicate = True

        # Si se activa hash, se verifica el contenido con más precisión.
        if use_hash:
            left_hash = compute_file_hash(left["full_path"], hash_algorithm)
            right_hash = compute_file_hash(right["full_path"], hash_algorithm)

            if left_hash is None or right_hash is None:
                msg = f"No fue posible calcular hash para comparar: {left['full_path']} <-> {right['full_path']}"
                errors.append(msg)
                LOGGER.warning(msg)
                is_duplicate = False
            else:
                is_duplicate = left_hash == right_hash

        # Si se confirma duplicado, guardamos ambos registros dentro del mismo grupo.
        if is_duplicate:
            group_id += 1
            result_rows.append(
                {
                    "group_id": group_id,
                    "folder_source": "carpeta_1",
                    "file_name": left["file_name"],
                    "size_bytes": left["size_bytes"],
                    "size_readable": format_size(left["size_bytes"]),
                    "full_path": left["full_path"],
                    "relative_path": left["relative_path"],
                    "modified_time": left["modified_time"],
                    "hash": left_hash,
                    "match_basis": f"nombre+tamaño+{hash_algorithm.upper()}" if use_hash else "nombre+tamaño",
                }
            )
            result_rows.append(
                {
                    "group_id": group_id,
                    "folder_source": "carpeta_2",
                    "file_name": right["file_name"],
                    "size_bytes": right["size_bytes"],
                    "size_readable": format_size(right["size_bytes"]),
                    "full_path": right["full_path"],
                    "relative_path": right["relative_path"],
                    "modified_time": right["modified_time"],
                    "hash": right_hash,
                    "match_basis": f"nombre+tamaño+{hash_algorithm.upper()}" if use_hash else "nombre+tamaño",
                }
            )

    df = pd.DataFrame(result_rows)
    if not df.empty:
        df = df.sort_values(by=["group_id", "folder_source", "file_name"]).reset_index(drop=True)

    return df, errors


# Resume cuántos grupos duplicados existen y cuánto tamaño se podría recuperar.
def summarize_duplicates(df: pd.DataFrame) -> Tuple[int, int]:
    if df.empty:
        return 0, 0

    groups = df["group_id"].nunique()
    recoverable = df.groupby("group_id").apply(lambda group: group["size_bytes"].iloc[0]).sum()
    return int(groups), int(recoverable)


# Filtra qué archivos se van a procesar según la carpeta elegida por el usuario.
def plan_deletions(df: pd.DataFrame, target_folder_source: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    return df[df["folder_source"] == target_folder_source].copy().reset_index(drop=True)


# -------------------------------------------------------------------
# LIMPIEZA DE SUBCARPETAS VACÍAS
# -------------------------------------------------------------------
# Después de mover o eliminar archivos duplicados, algunas subcarpetas pueden
# quedar vacías. Esta función intenta eliminarlas, pero nunca elimina las
# carpetas raíz ingresadas por el usuario.
def remove_empty_parent_folders(paths: List[str], protected_roots: List[str], dry_run: bool = True) -> Tuple[List[str], List[str]]:
    success_messages: List[str] = []
    error_messages: List[str] = []
    candidate_dirs = set()
    protected_roots = [normalize_path(root) for root in protected_roots]

    # Recolectamos carpetas padre candidatas, subiendo desde el archivo
    # hasta antes de llegar a una raíz protegida.
    for file_path in paths:
        parent = normalize_path(os.path.dirname(file_path))
        while parent and os.path.isdir(parent):
            if parent in protected_roots:
                break
            if any(os.path.commonpath([parent, root]) == root for root in protected_roots):
                candidate_dirs.add(parent)
            next_parent = os.path.dirname(parent)
            if next_parent == parent:
                break
            parent = next_parent

    # Ordenamos desde la carpeta más profunda hacia arriba.
    sorted_dirs = sorted(candidate_dirs, key=lambda p: len(p), reverse=True)

    for folder_path in sorted_dirs:
        try:
            if folder_path in protected_roots:
                continue
            if not os.path.isdir(folder_path):
                continue
            if os.listdir(folder_path):
                continue

            if dry_run:
                success_messages.append(f"[DRY RUN] Se simula la eliminación de subcarpeta vacía: {folder_path}")
            else:
                os.rmdir(folder_path)
                success_messages.append(f"Subcarpeta vacía eliminada: {folder_path}")
        except PermissionError as exc:
            msg = f"Sin permisos para eliminar subcarpeta vacía: {folder_path} | {exc}"
            LOGGER.error(msg)
            error_messages.append(msg)
        except FileNotFoundError as exc:
            msg = f"Subcarpeta no encontrada al eliminar: {folder_path} | {exc}"
            LOGGER.error(msg)
            error_messages.append(msg)
        except OSError as exc:
            msg = f"No se pudo eliminar la subcarpeta vacía: {folder_path} | {exc}"
            LOGGER.error(msg)
            error_messages.append(msg)

    return success_messages, error_messages


# -------------------------------------------------------------------
# ENVÍO A PAPELERA DE RECICLAJE
# -------------------------------------------------------------------
# Envía un archivo a la Papelera de reciclaje real, si send2trash está disponible.
def send_file_to_recycle_bin(file_path: str, dry_run: bool = True) -> Tuple[Optional[str], Optional[str]]:
    if not SEND2TRASH_AVAILABLE:
        return "La librería send2trash no está instalada. Instálala con: pip install send2trash", None

    try:
        if dry_run:
            return f"[DRY RUN] Se simula mover a la Papelera de reciclaje: {file_path}", file_path
        send2trash(file_path)
        return f"Movido a la Papelera de reciclaje: {file_path}", file_path
    except PermissionError as exc:
        msg = f"Sin permisos o archivo en uso al mover a la Papelera: {file_path} | {exc}"
        LOGGER.error(msg)
        return msg, None
    except FileNotFoundError as exc:
        msg = f"Archivo no encontrado al mover a la Papelera: {file_path} | {exc}"
        LOGGER.error(msg)
        return msg, None
    except OSError as exc:
        msg = f"Error del sistema al mover a la Papelera: {file_path} | {exc}"
        LOGGER.error(msg)
        return msg, None


# -------------------------------------------------------------------
# PROCESAMIENTO FINAL DE DUPLICADOS
# -------------------------------------------------------------------
# Esta función decide qué hacer con los archivos duplicados:
# - enviarlos a Papelera,
# - o eliminarlos definitivamente.
# Luego intenta limpiar subcarpetas vacías internas.
def process_duplicates(
    delete_df: pd.DataFrame,
    protected_roots: List[str],
    action_mode: str,
    dry_run: bool = True,
) -> Tuple[List[str], List[str]]:
    success_messages: List[str] = []
    error_messages: List[str] = []
    processed_paths: List[str] = []

    for _, row in delete_df.iterrows():
        file_path = row["full_path"]

        if action_mode == "recycle_bin":
            message, original_path = send_file_to_recycle_bin(file_path, dry_run=dry_run)
            if original_path:
                processed_paths.append(original_path)
                success_messages.append(message)
            else:
                error_messages.append(message)
            continue

        try:
            if dry_run:
                success_messages.append(f"[DRY RUN] Se simula la eliminación definitiva de: {file_path}")
                processed_paths.append(file_path)
            else:
                os.remove(file_path)
                success_messages.append(f"Eliminado definitivamente: {file_path}")
                processed_paths.append(file_path)
        except PermissionError as exc:
            msg = f"Sin permisos o archivo en uso: {file_path} | {exc}"
            LOGGER.error(msg)
            error_messages.append(msg)
        except FileNotFoundError as exc:
            msg = f"Archivo no encontrado al eliminar: {file_path} | {exc}"
            LOGGER.error(msg)
            error_messages.append(msg)
        except OSError as exc:
            msg = f"Error del sistema al eliminar: {file_path} | {exc}"
            LOGGER.error(msg)
            error_messages.append(msg)

    folder_success, folder_errors = remove_empty_parent_folders(processed_paths, protected_roots=protected_roots, dry_run=dry_run)
    success_messages.extend(folder_success)
    error_messages.extend(folder_errors)

    return success_messages, error_messages


# -------------------------------------------------------------------
# EXPORTACIÓN
# -------------------------------------------------------------------
# Convierte el DataFrame a CSV en memoria para descargarlo desde Streamlit.
def dataframe_to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8-sig")


# Convierte el DataFrame a Excel en memoria para descargarlo desde Streamlit.
def dataframe_to_excel_bytes(df: pd.DataFrame) -> bytes:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="duplicados")
    buffer.seek(0)
    return buffer.getvalue()


# -------------------------------------------------------------------
# ESTADO DE SESIÓN
# -------------------------------------------------------------------
# Inicializa variables persistentes de Streamlit.
# Esto permite recordar resultados entre interacciones.
def initialize_state() -> None:
    default_keys = {
        "duplicates_df": pd.DataFrame(),
        "scan_errors": [],
        "delete_plan": pd.DataFrame(),
        "delete_target": "",
        "last_scan_info": {},
        "action_mode": "recycle_bin",
        "danger_confirm_text": "",
    }

    for key, value in default_keys.items():
        if key not in st.session_state:
            st.session_state[key] = value


# -------------------------------------------------------------------
# FUNCIONES DE INTERFAZ
# -------------------------------------------------------------------
# Encabezado principal de la app.
def render_header() -> None:
    st.title("📁 Comparador de carpetas en Windows")
    st.markdown(
        """
        Esta aplicación compara dos carpetas, detecta archivos duplicados y permite limpiar duplicados de forma segura.
        La opción recomendada es mover archivos a la **Papelera de reciclaje** en vez de eliminarlos definitivamente.
        """
    )


# Muestra validación visual de la ruta ingresada.
def render_path_validation(label: str, path_input: str) -> Tuple[bool, str]:
    valid, message = validate_folder(path_input)

    if path_input.strip():
        if valid:
            st.success(f"{label} válida: {message}")
        else:
            st.error(f"{label} inválida: {message}")
    else:
        st.info(f"Ingresa la ruta de {label.lower()}.")

    return valid, message


# Muestra métricas resumen sobre duplicados encontrados.
def render_metrics(df: pd.DataFrame) -> None:
    groups, recoverable = summarize_duplicates(df)
    rows = len(df)

    col1, col2, col3 = st.columns(3)
    col1.metric("Grupos duplicados", groups)
    col2.metric("Registros encontrados", rows)
    col3.metric("Tamaño recuperable", format_size(recoverable))


# Muestra botones de descarga para CSV y Excel.
def render_export_buttons(df: pd.DataFrame) -> None:
    if df.empty:
        return

    st.subheader("Exportar resultados")
    col1, col2 = st.columns(2)

    col1.download_button(
        label="Descargar CSV",
        data=dataframe_to_csv_bytes(df),
        file_name="duplicados_carpetas.csv",
        mime="text/csv",
        use_container_width=True,
    )

    col2.download_button(
        label="Descargar Excel",
        data=dataframe_to_excel_bytes(df),
        file_name="duplicados_carpetas.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )


# Traduce el identificador del modo de acción a una etiqueta legible.
def get_action_label(action_mode: str) -> str:
    return "Mover a la Papelera de reciclaje" if action_mode == "recycle_bin" else "Eliminar definitivamente"


# Sección de limpieza de duplicados con doble candado de seguridad.
def render_delete_section(df: pd.DataFrame, dry_run: bool, protected_roots: List[str]) -> None:
    st.subheader("Acciones sobre duplicados")
    st.write("Selecciona qué carpeta debe conservarse y cuál debe limpiarse.")

    st.markdown("### Modo de limpieza")

    # El usuario elige entre mover a papelera o eliminar definitivamente.
    action_mode_ui = st.radio(
        "¿Qué deseas hacer con los duplicados seleccionados?",
        options=["recycle_bin", "delete"],
        format_func=get_action_label,
        key="action_mode",
        horizontal=True,
    )

    st.info(f"Modo activo: **{get_action_label(action_mode_ui)}**")

    # Si el usuario eligió borrado definitivo, activamos una confirmación extra.
    if action_mode_ui == "delete":
        st.error("Atención: este modo elimina archivos de forma definitiva.")
        st.warning(
            f"Doble candado activo: para habilitar el borrado definitivo debes escribir exactamente **{DELETE_CONFIRM_WORD}**."
        )
        st.text_input(
            "Escribe la palabra de seguridad para habilitar el borrado definitivo",
            key="danger_confirm_text",
            placeholder=DELETE_CONFIRM_WORD,
        )

        if st.session_state.get("danger_confirm_text", "").strip() == DELETE_CONFIRM_WORD:
            st.success("Palabra de seguridad correcta. El borrado definitivo quedó habilitado.")
        else:
            st.info("El borrado definitivo seguirá bloqueado hasta escribir la palabra exacta.")
    elif not SEND2TRASH_AVAILABLE:
        st.warning("Para usar la Papelera de reciclaje real debes instalar `send2trash` con `pip install send2trash`.")

    # Botones para elegir qué carpeta se quiere limpiar.
    col1, col2, col3 = st.columns(3)

    if col1.button("Limpiar duplicados en carpeta 1", use_container_width=True):
        st.session_state["delete_target"] = "carpeta_1"
        st.session_state["delete_plan"] = plan_deletions(df, "carpeta_1")

    if col2.button("Limpiar duplicados en carpeta 2", use_container_width=True):
        st.session_state["delete_target"] = "carpeta_2"
        st.session_state["delete_plan"] = plan_deletions(df, "carpeta_2")

    if col3.button("No limpiar nada", use_container_width=True):
        st.session_state["delete_target"] = ""
        st.session_state["delete_plan"] = pd.DataFrame()
        st.info("No se ejecutará ninguna acción de limpieza.")

    delete_plan = st.session_state.get("delete_plan", pd.DataFrame())
    delete_target = st.session_state.get("delete_target", "")
    action_mode = st.session_state.get("action_mode", "recycle_bin")
    action_label = get_action_label(action_mode)

    # Solo se habilita borrado definitivo si el usuario escribió la palabra correcta.
    allow_danger_action = action_mode != "delete" or st.session_state.get("danger_confirm_text", "").strip() == DELETE_CONFIRM_WORD

    if not delete_plan.empty and delete_target:
        st.warning(
            f"Se prepara la acción sobre {len(delete_plan)} archivo(s) en {delete_target}. "
            f"Modo seleccionado: {action_label}. Las carpetas raíz están protegidas y solo se eliminarán subcarpetas internas vacías. "
            f"Modo actual: {'DRY RUN' if dry_run else 'EJECUCIÓN REAL'}"
        )

        st.dataframe(
            delete_plan[["group_id", "file_name", "size_readable", "full_path"]],
            use_container_width=True,
            hide_index=True,
        )

        review_check = st.checkbox("Confirmo que revisé el resumen y deseo continuar")

        disabled_reason = False
        if action_mode == "delete" and not allow_danger_action:
            disabled_reason = True
            st.error(f"No puedes ejecutar borrado definitivo hasta escribir exactamente {DELETE_CONFIRM_WORD}.")

        if st.button(
            "Confirmar acción",
            type="primary",
            use_container_width=True,
            disabled=(not review_check) or disabled_reason,
        ):
            success_messages, error_messages = process_duplicates(
                delete_plan,
                protected_roots=protected_roots,
                action_mode=action_mode,
                dry_run=dry_run,
            )

            for msg in success_messages[:40]:
                st.success(msg)

            if len(success_messages) > 40:
                st.info(f"Se generaron {len(success_messages)} mensajes de éxito. Se muestran solo los primeros 40.")

            for msg in error_messages:
                st.error(msg)

            if not error_messages:
                st.success("Proceso finalizado sin errores críticos.")

            # Limpiamos la palabra de seguridad después del uso.
            if action_mode == "delete":
                st.session_state["danger_confirm_text"] = ""


# -------------------------------------------------------------------
# FUNCIÓN PRINCIPAL
# -------------------------------------------------------------------
# Orquesta toda la aplicación:
# - inicializa estado,
# - muestra configuración,
# - valida rutas,
# - escanea carpetas,
# - detecta duplicados,
# - presenta resultados,
# - y permite ejecutar acciones seguras.
def main() -> None:
    initialize_state()
    render_header()

    # Panel lateral con configuración de análisis.
    st.sidebar.header("Configuración")
    use_hash = st.sidebar.checkbox("Comparar también por hash", value=True)
    hash_algorithm = st.sidebar.selectbox("Algoritmo hash", options=["md5", "sha256"], index=0, disabled=not use_hash)
    dry_run = st.sidebar.checkbox("Modo dry run (simular sin ejecutar cambios)", value=True)
    st.sidebar.markdown("---")
    st.sidebar.caption("Recomendación: usa hash para mayor precisión cuando existan archivos con mismo nombre y tamaño.")
    st.sidebar.info("Instala `send2trash` para usar la Papelera de reciclaje real de Windows.")

    # Entradas de rutas.
    col_left, col_right = st.columns(2)
    with col_left:
        folder_1_input = st.text_input("Ruta carpeta 1", placeholder=r"C:\\carpeta1")
        valid_1, folder_1 = render_path_validation("Carpeta 1", folder_1_input)

    with col_right:
        folder_2_input = st.text_input("Ruta carpeta 2", placeholder=r"C:\\carpeta2")
        valid_2, folder_2 = render_path_validation("Carpeta 2", folder_2_input)

    # Botón principal para ejecutar la comparación.
    if st.button("Iniciar comparación", type="primary", use_container_width=True):
        if not (valid_1 and valid_2):
            st.error("Debes ingresar dos rutas válidas antes de iniciar la comparación.")
        else:
            if folder_1 == folder_2:
                st.warning("Las rutas apuntan a la misma carpeta. Usa carpetas distintas para una comparación útil.")
            else:
                progress_scan_1 = st.progress(0.0, text="Preparando escaneo de carpeta 1")
                status_text = st.empty()

                with st.spinner("Escaneando carpeta 1..."):
                    files_1, errors_1 = collect_files(folder_1, progress_bar=progress_scan_1, status_text=status_text)

                progress_scan_2 = st.progress(0.0, text="Preparando escaneo de carpeta 2")
                with st.spinner("Escaneando carpeta 2..."):
                    files_2, errors_2 = collect_files(folder_2, progress_bar=progress_scan_2, status_text=status_text)

                progress_hash = st.progress(0.0, text="Preparando comparación de duplicados")
                with st.spinner("Buscando archivos duplicados..."):
                    duplicates_df, compare_errors = find_duplicates(
                        files_folder_1=files_1,
                        files_folder_2=files_2,
                        use_hash=use_hash,
                        hash_algorithm=hash_algorithm,
                        hash_progress_bar=progress_hash,
                        status_text=status_text,
                    )

                all_errors = errors_1 + errors_2 + compare_errors

                # Guardamos resultados en sesión.
                st.session_state["duplicates_df"] = duplicates_df
                st.session_state["scan_errors"] = all_errors
                st.session_state["last_scan_info"] = {
                    "folder_1": folder_1,
                    "folder_2": folder_2,
                    "files_folder_1": len(files_1),
                    "files_folder_2": len(files_2),
                    "use_hash": use_hash,
                    "hash_algorithm": hash_algorithm if use_hash else "no_aplica",
                }
                st.session_state["delete_plan"] = pd.DataFrame()
                st.session_state["delete_target"] = ""
                status_text.empty()

    duplicates_df = st.session_state.get("duplicates_df", pd.DataFrame())
    scan_errors = st.session_state.get("scan_errors", [])
    scan_info = st.session_state.get("last_scan_info", {})

    protected_roots = []
    if scan_info:
        protected_roots = [scan_info["folder_1"], scan_info["folder_2"]]

        st.subheader("Resumen del análisis")
        st.write(f"Carpeta 1: **{scan_info['folder_1']}** | Archivos encontrados: **{scan_info['files_folder_1']}**")
        st.write(f"Carpeta 2: **{scan_info['folder_2']}** | Archivos encontrados: **{scan_info['files_folder_2']}**")
        st.write(
            f"Modo de comparación: **{'nombre+tamaño+' + scan_info['hash_algorithm'].upper() if scan_info['use_hash'] else 'nombre+tamaño'}**"
        )
        st.info("Las carpetas raíz ingresadas están protegidas. Solo se eliminarán subcarpetas internas vacías.")

    # Mostramos advertencias o errores detectados durante el proceso.
    if scan_errors:
        st.subheader("Advertencias y errores")
        for err in scan_errors[:20]:
            st.warning(err)
        if len(scan_errors) > 20:
            st.info(f"Se registraron {len(scan_errors)} incidencias. Revisa también el archivo {LOG_FILE}.")

    # Si hubo duplicados, mostramos resultados y acciones.
    if not duplicates_df.empty:
        st.subheader("Duplicados encontrados")
        render_metrics(duplicates_df)
        st.dataframe(duplicates_df, use_container_width=True, hide_index=True)
        render_export_buttons(duplicates_df)
        render_delete_section(duplicates_df, dry_run=dry_run, protected_roots=protected_roots)
    elif scan_info:
        st.success("No se encontraron duplicados entre las carpetas analizadas con la configuración seleccionada.")

    # Panel con recomendaciones de uso seguro.
    with st.expander("Ver recomendaciones de uso"):
        st.markdown(
            """
            - Usa rutas absolutas de Windows, por ejemplo: `C:\\Usuarios\\Yuri\\Documents\\carpeta1`.
            - Si tienes miles de archivos, primero prueba con comparación por nombre y tamaño.
            - Luego activa hash para confirmar duplicados reales.
            - Mantén activado **dry run** antes de ejecutar cambios reales.
            - Para mover a la Papelera de reciclaje real de Windows, instala: `pip install send2trash`.
            - El borrado definitivo requiere escribir la palabra `CONFIRMAR` como doble candado de seguridad.
            - La carpeta raíz que ingresas está protegida y no se eliminará.
            - Revisa el archivo `folder_compare.log` si necesitas auditar errores o acciones.
            """
        )


# Punto de entrada principal del programa.
if __name__ == "__main__":
    main()
