# Pipeline local — uso práctico

## Qué hace

Pipeline toma gameplays desde `gameplays_crudos/`, usa source maestro `john/john_video_45deg_ver4.mp4`, procesa chunks de 60s, recompone speaker/avatar, conserva audio original, deja resultado en `output/`.

## Requisitos

- Estar en raíz repo: `C:\proyectos\LivePortrait`
- Tener `.venv311` creado
- Tener FFmpeg disponible en PATH
- Tener pesos en `pretrained_weights/`

## Comando principal

Procesar un gameplay específico:

```powershell
& ".venv311\Scripts\python.exe" tools/run_long_gameplay_pipeline.py --input-dir gameplays_crudos --video RE9-part1.mp4 --output-dir output --work-dir .pipeline_work
```

Procesar todos los `.mp4` de carpeta:

```powershell
& ".venv311\Scripts\python.exe" tools/run_long_gameplay_pipeline.py --input-dir gameplays_crudos --output-dir output --work-dir .pipeline_work
```

## Cómo monitorear progreso

### Snapshot una vez

```powershell
& ".venv311\Scripts\python.exe" tools/watch_pipeline_progress.py status --work-dir .pipeline_work --video RE9-part1.mp4
```

### Watch continuo

```powershell
& ".venv311\Scripts\python.exe" tools/watch_pipeline_progress.py watch --work-dir .pipeline_work --video RE9-part1.mp4 --interval 10
```

Parar watch:
- `Ctrl + C`

## Dónde mirar archivos

### Trabajo intermedio

- `.pipeline_work/<video_id>/manifests/manifest.json`
- `.pipeline_work/<video_id>/chunks/full/`
- `.pipeline_work/<video_id>/chunks/roi/`
- `.pipeline_work/<video_id>/chunks/source/`
- `.pipeline_work/<video_id>/chunks/liveportrait/`
- `.pipeline_work/<video_id>/chunks/composited/`

### Resultado final

- `output/<video_id>/final.mp4`
- `output/<video_id>/visual_full.mp4`
- `output/<video_id>/report.json`

## Cómo reanudar

Pipeline es reanudable.

Si se corta:
- NO borrar `.pipeline_work/<video_id>/`
- volver a correr mismo comando principal
- retomará desde primer chunk no `done`

## Cómo reiniciar desde cero un video

Si quieres forzar reproceso total de un video:

1. borrar job intermedio
2. borrar output final viejo
3. correr comando otra vez

Ejemplo:

```powershell
Remove-Item -Recurse -Force ".pipeline_work\RE9-part1-3153aeea"
Remove-Item -Recurse -Force "output\RE9-part1-3153aeea"
& ".venv311\Scripts\python.exe" tools/run_long_gameplay_pipeline.py --input-dir gameplays_crudos --video RE9-part1.mp4 --output-dir output --work-dir .pipeline_work
```

## Cómo encontrar `video_id`

Forma simple:

```powershell
Get-ChildItem .pipeline_work
Get-ChildItem output
```

Watcher también lo muestra en primera línea.

## Logs útiles

Si lanzas proceso manualmente en consola, verás salida ahí mismo.

Si quieres logs a archivo:

```powershell
$stdout = Join-Path (Get-Location) "pipeline_run_stdout.log"
$stderr = Join-Path (Get-Location) "pipeline_run_stderr.log"
& ".venv311\Scripts\python.exe" tools/run_long_gameplay_pipeline.py --input-dir gameplays_crudos --video RE9-part1.mp4 --output-dir output --work-dir .pipeline_work 1> $stdout 2> $stderr
```

Luego mirar:

```powershell
Get-Content .\pipeline_run_stdout.log -Tail 50
Get-Content .\pipeline_run_stderr.log -Tail 50
```

## Lectura rápida de `report.json`

Campos importantes:

- `input_video`
- `source_master`
- `total_duration_seconds`
- `chunk_count`
- `processed_chunks`
- `failed_chunks`
- `total_processing_seconds`
- `config_path`
- `execution_timestamp`

## Notas reales

- Pipeline corre en modo **best effort**. No hace fallback a webcam original.
- Si hay tramos feos, se conservan. Cortes/fixes van en post.
- Audio original se remuxea intacto.
- ETA de watcher al principio puede ser mala o `unknown`.
- Watcher CLI sirve para seguimiento local. No depende de sesión de asistente.

## Flujo recomendado

### Terminal 1 — procesar

```powershell
& ".venv311\Scripts\python.exe" tools/run_long_gameplay_pipeline.py --input-dir gameplays_crudos --video RE9-part1.mp4 --output-dir output --work-dir .pipeline_work
```

### Terminal 2 — vigilar

```powershell
& ".venv311\Scripts\python.exe" tools/watch_pipeline_progress.py watch --work-dir .pipeline_work --video RE9-part1.mp4 --interval 10
```

## Si algo falla

1. correr snapshot:

```powershell
& ".venv311\Scripts\python.exe" tools/watch_pipeline_progress.py status --work-dir .pipeline_work --video RE9-part1.mp4
```

2. mirar `last_error`
3. mirar `report.json`
4. revisar chunk que falló en `.pipeline_work/<video_id>/chunks/...`
5. volver a lanzar mismo comando principal para reanudar
