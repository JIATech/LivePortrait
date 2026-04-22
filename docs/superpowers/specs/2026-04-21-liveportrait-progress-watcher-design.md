# LivePortrait Progress Watcher Design

## Goal

Agregar watcher CLI standalone para pipeline largo. Usuario debe poder ver progreso local sin depender de sesión activa de asistente.

## Scope

### In scope
- Comando `status` para snapshot único.
- Comando `watch` para refresco continuo.
- Lectura de estado desde `.pipeline_work/<video_id>/manifests/manifest.json`.
- Inferencia de progreso desde manifest + archivos de trabajo.
- ETA aproximada basada en chunks terminados.
- Soporte explícito para seleccionar video por nombre.

### Out of scope
- UI Tkinter.
- Modificar pipeline principal para streaming sofisticado de eventos.
- Seguimiento por frame.
- Telemetría remota.

## Commands

### Status

```powershell
& ".venv311\Scripts\python.exe" tools/watch_pipeline_progress.py status --work-dir .pipeline_work --video RE9-part1.mp4
```

Debe imprimir una vez y salir.

### Watch

```powershell
& ".venv311\Scripts\python.exe" tools/watch_pipeline_progress.py watch --work-dir .pipeline_work --video RE9-part1.mp4 --interval 10
```

Debe refrescar cada N segundos hasta `Ctrl+C`.

## Data sources

Watcher debe usar solo fuentes locales, sin depender de memoria de sesión:

1. `.pipeline_work/<video_id>/manifests/manifest.json`
2. timestamps y tamaños de:
   - `chunks/full/`
   - `chunks/roi/`
   - `chunks/liveportrait/`
   - `chunks/composited/`
   - `output/<video_id>/final.mp4` si existe
3. opcionalmente `pipeline_re9_stdout.log` / `pipeline_re9_stderr.log` si se apunta explícitamente a logs, pero no como dependencia obligatoria de v1

## Output contract

Watcher debe mostrar explícitamente:

- `video_id`
- `input_video`
- `total_chunks`
- `done`
- `pending`
- `failed`
- `% complete`
- `current_chunk` inferido
- `elapsed_seconds`
- `eta_seconds` o `ETA: unknown`
- último artefacto tocado
- `final_output` si existe
- `last_error` si algún chunk falló

## Current chunk inference

Regla v1:

- si existe chunk `failed`, mostrar menor índice fallido como foco actual
- si no, mostrar menor índice `pending` cuyo directorio o artefactos tengan actividad más reciente
- si todos `done`, mostrar `completed`

No hace falta precisión perfecta por frame. Necesitamos señal útil, no telemetría fantasiosa.

## ETA model

Primera versión deliberadamente simple:

- `avg_seconds_per_done_chunk = elapsed / done`
- `eta = avg_seconds_per_done_chunk * pending`
- si `done == 0`, entonces `ETA: unknown`

Si luego vemos chunks con tiempos muy distintos, se mejora. V1 debe ser barata y robusta.

## UX rules

- `status` imprime snapshot compacto y sale con código 0
- `watch` limpia pantalla y reimprime estado
- `watch` termina limpio con `Ctrl+C`
- si video no existe en `.pipeline_work`, error claro
- si manifest no existe, error claro
- salida legible en terminal Windows

## Architecture

Separar en dos piezas chicas:

1. `collect_pipeline_status(...)`
   - carga manifest
   - calcula métricas
   - devuelve estructura pura serializable

2. `render_pipeline_status(...)`
   - convierte estructura en texto humano

CLI solo enruta `status` vs `watch`.

## Risks

- Si pipeline actualiza manifest solo por chunk, watcher no verá etapa interna exacta. Aceptable para v1.
- ETA al inicio será mala o desconocida. Aceptable.
- Si usuario mueve o borra `.pipeline_work`, watcher no puede ayudar. Debe decirlo explícito.

## Future extensions

- Soporte para múltiples jobs simultáneos
- Lectura opcional de logs para etapa actual más precisa
- Salida JSON para tooling externo
- UI Tkinter encima de mismo backend
