

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat&logo=python&logoColor=white)
![Streamlit](https://img.shields.io/badge/Streamlit-App-FF4B4B?style=flat&logo=streamlit&logoColor=white)
![Pandas](https://img.shields.io/badge/Pandas-Data%20Analysis-150458?style=flat&logo=pandas&logoColor=white)
![Windows](https://img.shields.io/badge/Platform-Windows-0078D6?style=flat&logo=windows&logoColor=white)
![Send2Trash](https://img.shields.io/badge/Send2Trash-Recycle%20Bin-4CAF50?style=flat)
![OpenPyXL](https://img.shields.io/badge/OpenPyXL-Excel%20Export-107C41?style=flat)
![License](https://img.shields.io/badge/License-MIT-green?style=flat)



#  Comparador de Carpetas — Guía de Instalación y Uso

##  Requisitos previos
- Python 3.10 o superior
- pip (gestor de paquetes)

---

##  1. Instalación de dependencias

Abre una terminal (o símbolo del sistema en Windows) y ejecuta:

```bash
pip install streamlit pandas openpyxl
```

### Verificar instalación
```bash
streamlit --version
python -c "import pandas; print(pandas.__version__)"
```

---

##  2. Ejecución de la aplicación

Navega hasta la carpeta donde guardaste `app.py`:

```bash
cd ruta/a/tu/carpeta
streamlit run app.py
```

El navegador se abrirá automáticamente en:
```
http://localhost:8501
```

---

##  3. Ejemplo de uso paso a paso

### Paso 1: Ingresar rutas
```
Carpeta 1: C:\Users\Juan\Documentos\Fotos2023
Carpeta 2: D:\Backup\Fotos2023
```

### Paso 2: Configurar opciones (panel lateral)
| Opción | Recomendación |
|--------|--------------|
| Comparar por Hash | Desactivado para inicio rápido |
| Modo Dry Run |  **SIEMPRE activo** la primera vez |

### Paso 3: Iniciar comparación
Clic en **" Iniciar Comparación"**

### Paso 4: Revisar resultados
- Verifica la tabla de duplicados
- Revisa el espacio recuperable
- Exporta a CSV/Excel si necesitas guardar el reporte

### Paso 5: Eliminar (opcional)
1. Desactiva **Dry Run** solo si estás seguro
2. Elige **"Eliminar duplicados en Carpeta 1"** o **Carpeta 2**
3. Confirma la operación en el resumen

---

##  Archivos del proyecto

```
proyecto/
├── app.py                          ← Aplicación Streamlit (EJECUTAR ESTE)
├── comparador_carpetas.ipynb       ← Notebook Jupyter con explicaciones
├── README.md                       ← Este archivo
└── comparador_carpetas.log         ← Log de eventos (auto-generado al correr)
```

---

##  Opciones avanzadas

### Comparación por Hash
- **MD5**: Más rápido, recomendado para uso general
- **SHA256**: Más seguro, para archivos críticos
- Útil para detectar archivos con mismo nombre pero contenido diferente

### Exportación de resultados
Los resultados se pueden descargar desde la interfaz en:
- **CSV**: Compatible con cualquier programa
- **Excel (.xlsx)**: Con formato tabular, listo para analizar

---

##  Seguridad

- **Modo Dry Run**: Simula todas las operaciones sin borrar nada → úsalo primero
- **Log de auditoría**: Cada acción queda registrada en `comparador_carpetas.log`
- **Confirmación explícita**: La app pide confirmación antes de eliminar
- **Resumen previo**: Muestra exactamente qué archivos serán afectados

---

##  Solución de problemas

| Problema | Solución |
|----------|---------|
| `ModuleNotFoundError: streamlit` | `pip install streamlit` |
| `ModuleNotFoundError: openpyxl` | `pip install openpyxl` |
| La app no abre el navegador | Abre `http://localhost:8501` manualmente |
| Error de permisos en carpeta | Ejecuta el terminal como Administrador |
| Escaneo muy lento | Desactiva "Comparar por Hash" |

---

##  Notas finales

- Compatible con **Windows, macOS y Linux**
- Probado con **Python 3.10, 3.11 y 3.12**
- Para reportar problemas, revisa el archivo `comparador_carpetas.log`
