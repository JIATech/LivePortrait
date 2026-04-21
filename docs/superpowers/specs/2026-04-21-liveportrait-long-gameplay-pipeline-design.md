# LivePortrait Long Gameplay Pipeline Design

## Goal

Automatizar el procesamiento de gameplays largos para producir un video final listo para postproducción usando el pipeline visual ganador refinado en esta sesión, sin intervención manual durante la ejecución.

## Scope

### In scope
- Procesar gameplays crudos ubicados en `gameplays_crudos/`.
- Usar un único source maestro validado: `john/john_video_45deg_ver4.mp4`.
- Procesar visualmente el recuadro/cámara del speaker.
- Trabajar en chunks fijos de 60 segundos.
- Permitir reanudación sin recomenzar desde cero.
- Conservar el audio original intacto.
- Entregar `output/<video_id>/final.mp4` listo para postproducción.

### Out of scope (v1)
- Generalización a otros layouts o tipos de contenido.
- Selección automática de múltiples source assets según pose/estado.
- Fallback al footage original en tramos dudosos.
- Postproducción fina automática.
- Relighting avanzado o corrección fotométrica compleja.

## Constraints and assumptions

- El pipeline es **monoespecífico** para este tipo de gameplay y este layout.
- El recuadro del speaker se asume estable en v1.
- Política de operación: **best effort siempre**. Si un tramo queda feo, se conserva para revisión y corte en post.
- Los recursos de hardware son limitados; el diseño debe favorecer robustez y reanudación antes que velocidad.
- El audio del gameplay original debe preservarse sin modificación.

## Winning visual stack (baseline v1)

El pipeline ganador actual para esta utilidad es:

- Source maestro: `john_video_45deg_ver4`
- LivePortrait con `--flag-eye-retargeting`
- Compositor con:
  - key fuerte del avatar
  - jaw-preserving handoff
  - suppression media
- Sin relighting automático como parte obligatoria de v1, porque la prueba conservadora no produjo una mejora perceptible suficiente.

### Current calibrated compositor profile

Tomando como base el mejor resultado actual del pipeline compuesto (patch con `eye-retargeting` de `test8` + composición refinada de `test12`), el perfil de composición a portar a v1 es:

- `alpha_cutoff = 0.48`
- `alpha_erode = 5`
- `alpha_post_blur = 0.45`
- `handoff_start = 0.72`
- `handoff_end = 0.94`
- `suppress_strength = 0.60`
- `suppress_dilate = 13`
- `suppress_blur = 3.0`
- `suppress_roi_blur_ksize = 19`

Estos valores deben quedar en un perfil/configuración editable, no enterrados rígidamente dentro del pipeline.

## Input / output contract

### Inputs
- Carpeta de entrada: `gameplays_crudos/`
- Source maestro base: `john/john_video_45deg_ver4.mp4`
- Calibración del recuadro del speaker para v1:
  - `x = 6`
  - `y = 811`
  - `w = 259`
  - `h = 268`

### Outputs
- Carpeta final: `output/<video_id>/`
- Archivo final: `output/<video_id>/final.mp4`
- Reporte: `output/<video_id>/report.json`

## Working directory layout

Toda la ejecución intermedia debe vivir fuera de `output/`, en una carpeta técnica separada:

- `.pipeline_work/<video_id>/`

Estructura propuesta:

```text
.pipeline_work/<video_id>/
├── source/
├── chunks/
│   ├── full/
│   ├── roi/
│   ├── liveportrait/
│   └── composited/
├── templates/
├── logs/
└── manifests/
```

### Responsibilities
- `source/` — versionado de trabajo del source maestro (ej. 12 fps, sin audio).
- `chunks/full/` — chunks full-frame del gameplay.
- `chunks/roi/` — ROI del speaker por chunk.
- `chunks/liveportrait/` — parches generados por LivePortrait.
- `chunks/composited/` — chunks full-frame ya recompuestos.
- `templates/` — motion templates `.pkl` reutilizables por chunk.
- `logs/` — logs por ejecución/chunk y errores.
- `manifests/` — estado del job y metadatos para reanudación.

## Processing flow

### 1. Job discovery
- Escanear `gameplays_crudos/`.
- Crear un `video_id` determinista por input.
- Crear manifest inicial con metadatos del archivo.

### 2. Source preparation
- Convertir `john_video_45deg_ver4.mp4` a versión de trabajo estable.
- Formato de trabajo recomendado para v1:
  - 12 fps
  - sin audio
- Guardar en `.pipeline_work/<video_id>/source/`.
- Como el source maestro dura ~10 s y los chunks duran 60 s, cada chunk debe usar una versión **looped/extendida** del source preparada automáticamente hasta cubrir la duración completa del chunk. El pipeline no puede asumir que LivePortrait extenderá el source por sí solo.

### 3. Chunking
- Dividir el gameplay completo en bloques de 60 s.
- Cada chunk se guarda como archivo full-frame independiente.

### 4. ROI extraction per chunk
- Recortar la ROI del speaker usando la calibración fija.
- Guardar una ROI por chunk.

### 5. ROI normalization
- Convertir la ROI al formato de trabajo del pipeline.
- Eliminar audio del chunk ROI.
- Generar template `.pkl` cuando corresponda.

### 6. LivePortrait generation
- Correr LivePortrait chunk por chunk usando:
  - source maestro preparado
  - driving ROI del chunk
  - `--flag-eye-retargeting`
- Guardar el parche resultante.

### 7. Composition
- Reinsertar el parche en el chunk full original.
- Aplicar el compositor ganador actual.
- Guardar el chunk full ya compuesto.

### 8. Final assembly
- Concatenar los chunks compuestos en orden.
- Remuxear el audio original del gameplay completo.
- Escribir `output/<video_id>/final.mp4`.
- Escribir `report.json`.

## Resumability model

La reanudación es requisito de arquitectura, no un extra.

### Manifest state per chunk
Cada chunk debe tener estado explícito, por ejemplo:
- `pending`
- `done`
- `failed`

### Resume behavior
Al iniciar una ejecución:
- chunks `done` se respetan
- chunks `pending` se procesan
- chunks `failed` se pueden reintentar

El sistema no debe borrar automáticamente outputs válidos ya generados.

### Failure policy
Hay dos tipos de fallo:

1. **Fallos visuales / baja calidad**
   - Se conservan como parte del modo best effort.

2. **Fallos técnicos del pipeline**
   - Deben marcar el chunk como `failed`.
   - El job no debe recomenzar desde cero.
   - La reanudación debe continuar desde el primer chunk no completado.

## Final report

`report.json` debe incluir como mínimo:
- input original
- duración total
- cantidad de chunks
- chunks procesados
- chunks fallidos
- tiempo total de procesamiento
- configuración usada
- source maestro usado
- fecha/hora de ejecución

## User experience

### User action
El usuario solo debe:
1. copiar gameplays en `gameplays_crudos/`
2. ejecutar un comando único
3. esperar
4. recoger `output/<video_id>/final.mp4`

### System action
El sistema debe:
- descubrir inputs
- crear jobs
- chunkear
- procesar visualmente
- recomponer
- unir chunks
- remuxear audio
- entregar salida final

## Design decisions

- **Chunking de 60 s** elegido por balance entre overhead, seguridad y reanudación.
- **Best effort siempre** elegido porque los errores visibles se corrigen en postproducción y no conviene abandonar o hacer fallback al footage original.
- **Layout fijo v1** elegido para llegar antes a una pipeline robusta.
- **Source maestro único** elegido para evitar complejidad prematura.
- **Audio original intacto** elegido para separar claramente automatización visual de postproducción sonora.

## Risks

- Cambios fuertes de pose, salida de cámara o comportamiento extremo seguirán produciendo fallos visibles: esto es aceptado por diseño.
- Cambios de iluminación no uniforme pueden quedar imperfectos: no son el foco principal de v1.
- Si el layout cambia entre sesiones futuras, v1 requerirá recalibración.

## Future extensions

- perfiles múltiples de layout por sesión/video
- selección automática de source según pose/estado
- relighting localizado más agresivo en neck/jawline
- estrategia de retries automáticos por chunk fallido
- limpieza automática selectiva de artefactos para casos conocidos
