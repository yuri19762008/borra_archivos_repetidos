import os
import io
import hashlib
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st

APP_TITLE = "Comparador de carpetas y duplicados"
LOG_FILE = "folder_compare.log"
HASH_BLOCK_SIZE = 1024 * 1024


def setup_logger() -> logging.Logger:
    logger = logging.getLogger("folder_compare")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger


LOGGER = setup_logger()

st.set_page_config(page_title=APP_TITLE, page_icon="📁", layout="wide")


def normalize_path(path: str) -> str:
    return os.path.abspath(os.path.expandvars(os.path.expanduser(path.strip().strip('"'))))


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



def format_size(size_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size_bytes)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{size_bytes} B"



def validate_folder(path: str) -> Tuple[bool, str]:
    if not path.strip():
        return False, "La ruta está vacía."
    normalized = normalize_path(path)
    if not os.path.exists(normalized):
        return False, f"La ruta no existe: {normalized}"
    if not os.path.isdir(normalized):
        return False, f"La ruta no es una carpeta válida: {normalized}"
    return True, normalized



def collect_files(folder_path: str, progress_bar=None, status_text=None) -> Tuple[List[Dict], List[str]]:
    file_records: List[Dict] = []
    errors: List[str] = []
    all_dirs = []

    for root, dirs, _ in os.walk(folder_path):
        all_dirs.append(root)
        for directory in dirs:
            all_dirs.append(os.path.join(root, directory))

    total_dirs = max(len(all_dirs), 1)
    processed_dirs = 0

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

    for record in files_folder_2:
        key = (record["file_name"], record["size_bytes"])
        index_folder_2.setdefault(key, []).append(record)

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



def summarize_duplicates(df: pd.DataFrame) -> Tuple[int, int]:
    if df.empty:
        return 0, 0
    groups = df["group_id"].nunique()
    recoverable = df.groupby("group_id").apply(lambda group: group["size_bytes"].iloc[0]).sum()
    return int(groups), int(recoverable)



def plan_deletions(df: pd.DataFrame, target_folder_source: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    return df[df["folder_source"] == target_folder_source].copy().reset_index(drop=True)



def remove_empty_parent_folders(paths: List[str], dry_run: bool = True) -> Tuple[List[str], List[str]]:
    success_messages: List[str] = []
    error_messages: List[str] = []
    candidate_dirs = set()

    for file_path in paths:
        parent = os.path.dirname(file_path)
        while parent and os.path.isdir(parent):
            candidate_dirs.add(parent)
            next_parent = os.path.dirname(parent)
            if next_parent == parent:
                break
            parent = next_parent

    sorted_dirs = sorted(candidate_dirs, key=lambda p: len(p), reverse=True)

    for folder_path in sorted_dirs:
        try:
            if not os.path.isdir(folder_path):
                continue
            if os.listdir(folder_path):
                continue
            if dry_run:
                success_messages.append(f"[DRY RUN] Se simula la eliminación de carpeta vacía: {folder_path}")
            else:
                os.rmdir(folder_path)
                success_messages.append(f"Carpeta vacía eliminada: {folder_path}")
        except PermissionError as exc:
            msg = f"Sin permisos para eliminar carpeta vacía: {folder_path} | {exc}"
            LOGGER.error(msg)
            error_messages.append(msg)
        except FileNotFoundError as exc:
            msg = f"Carpeta no encontrada al eliminar: {folder_path} | {exc}"
            LOGGER.error(msg)
            error_messages.append(msg)
        except OSError as exc:
            msg = f"No se pudo eliminar la carpeta vacía: {folder_path} | {exc}"
            LOGGER.error(msg)
            error_messages.append(msg)

    return success_messages, error_messages



def delete_files(delete_df: pd.DataFrame, dry_run: bool = True) -> Tuple[List[str], List[str]]:
    success_messages: List[str] = []
    error_messages: List[str] = []
    processed_paths: List[str] = []

    for _, row in delete_df.iterrows():
        file_path = row["full_path"]
        try:
            if dry_run:
                success_messages.append(f"[DRY RUN] Se simula la eliminación de: {file_path}")
                processed_paths.append(file_path)
            else:
                os.remove(file_path)
                success_messages.append(f"Eliminado correctamente: {file_path}")
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

    folder_success, folder_errors = remove_empty_parent_folders(processed_paths, dry_run=dry_run)
    success_messages.extend(folder_success)
    error_messages.extend(folder_errors)
    return success_messages, error_messages



def dataframe_to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8-sig")



def dataframe_to_excel_bytes(df: pd.DataFrame) -> bytes:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="duplicados")
    buffer.seek(0)
    return buffer.getvalue()



def initialize_state() -> None:
    default_keys = {
        "duplicates_df": pd.DataFrame(),
        "scan_errors": [],
        "delete_plan": pd.DataFrame(),
        "delete_target": "",
        "last_scan_info": {},
    }
    for key, value in default_keys.items():
        if key not in st.session_state:
            st.session_state[key] = value



def render_header() -> None:
    st.title("📁 Comparador de carpetas en Windows")
    st.markdown(
        """
        Esta aplicación compara dos carpetas, detecta archivos duplicados y permite revisar o eliminar duplicados de forma segura.
        Puedes comparar por **nombre y tamaño** o aumentar la precisión usando **hash MD5 o SHA256**.
        """
    )



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



def render_metrics(df: pd.DataFrame) -> None:
    groups, recoverable = summarize_duplicates(df)
    rows = len(df)
    col1, col2, col3 = st.columns(3)
    col1.metric("Grupos duplicados", groups)
    col2.metric("Registros encontrados", rows)
    col3.metric("Tamaño recuperable", format_size(recoverable))



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



def render_delete_section(df: pd.DataFrame, dry_run: bool) -> None:
    st.subheader("Acciones sobre duplicados")
    st.write("Selecciona qué carpeta debe conservarse y cuál debe limpiarse.")

    col1, col2, col3 = st.columns(3)

    if col1.button("Eliminar duplicados en carpeta 1", use_container_width=True):
        st.session_state["delete_target"] = "carpeta_1"
        st.session_state["delete_plan"] = plan_deletions(df, "carpeta_1")

    if col2.button("Eliminar duplicados en carpeta 2", use_container_width=True):
        st.session_state["delete_target"] = "carpeta_2"
        st.session_state["delete_plan"] = plan_deletions(df, "carpeta_2")

    if col3.button("No eliminar nada", use_container_width=True):
        st.session_state["delete_target"] = ""
        st.session_state["delete_plan"] = pd.DataFrame()
        st.info("No se ejecutará ninguna eliminación.")

    delete_plan = st.session_state.get("delete_plan", pd.DataFrame())
    delete_target = st.session_state.get("delete_target", "")

    if not delete_plan.empty and delete_target:
        st.warning(
            f"Se prepara la eliminación de {len(delete_plan)} archivo(s) en {delete_target}. "
            f"Además, la app intentará eliminar carpetas internas que queden vacías. "
            f"Modo actual: {'DRY RUN' if dry_run else 'ELIMINACIÓN REAL'}"
        )
        st.dataframe(
            delete_plan[["group_id", "file_name", "size_readable", "full_path"]],
            use_container_width=True,
            hide_index=True,
        )
        confirm = st.checkbox("Confirmo que revisé el resumen y deseo continuar")
        if st.button("Confirmar acción", type="primary", use_container_width=True, disabled=not confirm):
            success_messages, error_messages = delete_files(delete_plan, dry_run=dry_run)
            for msg in success_messages[:30]:
                st.success(msg)
            if len(success_messages) > 30:
                st.info(f"Se generaron {len(success_messages)} mensajes de éxito. Se muestran solo los primeros 30.")
            for msg in error_messages:
                st.error(msg)
            if not error_messages:
                st.success("Proceso finalizado sin errores críticos.")



def main() -> None:
    initialize_state()
    render_header()

    st.sidebar.header("Configuración")
    use_hash = st.sidebar.checkbox("Comparar también por hash", value=True)
    hash_algorithm = st.sidebar.selectbox("Algoritmo hash", options=["md5", "sha256"], index=0, disabled=not use_hash)
    dry_run = st.sidebar.checkbox("Modo dry run (simular sin borrar)", value=True)
    st.sidebar.markdown("---")
    st.sidebar.caption("Recomendación: usa hash para mayor precisión cuando existan archivos con mismo nombre y tamaño.")

    col_left, col_right = st.columns(2)
    with col_left:
        folder_1_input = st.text_input("Ruta carpeta 1", placeholder=r"C:\\carpeta1")
        valid_1, folder_1 = render_path_validation("Carpeta 1", folder_1_input)
    with col_right:
        folder_2_input = st.text_input("Ruta carpeta 2", placeholder=r"C:\\carpeta2")
        valid_2, folder_2 = render_path_validation("Carpeta 2", folder_2_input)

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

    if scan_info:
        st.subheader("Resumen del análisis")
        st.write(f"Carpeta 1: **{scan_info['folder_1']}** | Archivos encontrados: **{scan_info['files_folder_1']}**")
        st.write(f"Carpeta 2: **{scan_info['folder_2']}** | Archivos encontrados: **{scan_info['files_folder_2']}**")
        st.write(
            f"Modo de comparación: **{'nombre+tamaño+' + scan_info['hash_algorithm'].upper() if scan_info['use_hash'] else 'nombre+tamaño'}**"
        )

    if scan_errors:
        st.subheader("Advertencias y errores")
        for err in scan_errors[:20]:
            st.warning(err)
        if len(scan_errors) > 20:
            st.info(f"Se registraron {len(scan_errors)} incidencias. Revisa también el archivo {LOG_FILE}.")

    if not duplicates_df.empty:
        st.subheader("Duplicados encontrados")
        render_metrics(duplicates_df)
        st.dataframe(duplicates_df, use_container_width=True, hide_index=True)
        render_export_buttons(duplicates_df)
        render_delete_section(duplicates_df, dry_run=dry_run)
    elif scan_info:
        st.success("No se encontraron duplicados entre las carpetas analizadas con la configuración seleccionada.")

    with st.expander("Ver recomendaciones de uso"):
        st.markdown(
            """
            - Usa rutas absolutas de Windows, por ejemplo: `C:\\Usuarios\\Yuri\\Documents\\carpeta1`.
            - Si tienes miles de archivos, primero prueba con comparación por nombre y tamaño.
            - Luego activa hash para confirmar duplicados reales.
            - Mantén activado **dry run** antes de usar eliminación real.
            - Si al borrar duplicados quedan subcarpetas vacías, la app intentará eliminarlas automáticamente.
            - Revisa el archivo `folder_compare.log` si necesitas auditar errores o acciones.
            """
        )


if __name__ == "__main__":
    main()
