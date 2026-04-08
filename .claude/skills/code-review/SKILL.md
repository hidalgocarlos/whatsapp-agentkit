---
name: code-review
description: >
  Realiza code reviews exhaustivos nivel senior. Se activa cuando el usuario
  pide revisar código, analizar calidad, preparar un PR, o evaluar cambios
  antes de merge. También se activa con "review", "revisar", "code quality".
allowed-tools:
  - Bash
  - Read
  - Grep
  - Glob
---

# Code Review Skill

## Workflow

### Paso 1: Descubrimiento
Antes de revisar, entiende el contexto:
1. Lee el CLAUDE.md del proyecto para entender convenciones
2. Identifica el lenguaje, framework y patrones del proyecto
3. Si hay linter/formatter configurado, verifica que el código pase

### Paso 2: Análisis Automatizado
Ejecuta las herramientas disponibles:
```bash
# Para proyectos JS/TS
npx tsc --noEmit 2>&1 | head -50       # Type errors
npx eslint --quiet $FILE 2>&1           # Lint issues

# Para proyectos Python
python -m py_compile $FILE 2>&1         # Syntax check
ruff check $FILE 2>&1                   # Lint
mypy $FILE 2>&1                         # Types
```

### Paso 3: Review Manual (tu expertise)
Aplica el análisis senior sobre:
- Seguridad, bugs, performance, arquitectura, mantenibilidad

### Paso 4: Reporte
Genera un reporte estructurado con severidades y sugerencias accionables.

## Formato de Reporte

```
## Code Review: [archivo/PR]

### Resumen Ejecutivo
Score: X/10 | Veredicto: APROBAR / CAMBIOS REQUERIDOS / RECHAZAR

### Hallazgos
[Lista ordenada por severidad]

### Recomendaciones Generales
[Patrones y mejoras transversales]
```
