# Integración de GitHub y cambios locales

Validación del 22 de octubre de 2026, desde `D:\ProyectosTemporales\LeadManagerNueva`.

## Resultado de la validación inicial en `prueba-pull`

- Rama actual: `prueba-pull`, basada en `origin/main` (`e39ace4`).
- En esa primera validación, `main` permaneció en `10bda3e`, sin commit ni push.
- Los dos stashes se conservaron y no se prepararon archivos para commit en esa fase.
- El fetch confirmó que GitHub sigue en `e39ace4`.
- Los cambios locales de `tasks.py`, `tests.py` y `conversations.html` coinciden
  con los respaldados en `stash@{1}`. Las correcciones remotas de
  `prospector_steps.py`, `imap_service.py` y sus pruebas permanecen intactas.
- `git diff --check` pasó y el índice no contiene conflictos sin resolver.
- Pasaron 108 pruebas: automatizaciones, clasificación de Conversations y los
  cinco escenarios de entrega. Se usó SQLite en memoria, sin conexiones de red.
  No se utilizaron PostgreSQL ni servicios de correo, Odoo o LLM para las pruebas.
- La comprobación de Django pasó. La advertencia sobre `staticfiles` ausente
  permanece; no impidió las pruebas.
- La consulta de solo lectura no encontró sesiones activas en `test_postgres`.
  No se cerraron procesos ni conexiones ajenas y no se eliminó esa base.

Además de los stashes, el respaldo de los archivos originales y el parche están en:

`C:\Users\RSSpe\AppData\Local\Temp\LeadManagerNueva-integracion-20261022`

## Integración posterior autorizada en `main`

Se amplió la revisión a las aplicaciones activas antes de integrar. La ejecución
en SQLite en memoria encontró 280 casos: 279 aprobaron y hubo un error de importación
en `leadmanager.apps.api.prospects.tests`, que referencia `EmailReminderModel`.
Ese modelo ya no existe. Los modelos y ese módulo de pruebas son idénticos en
`main` original, `origin/main` y el árbol integrado: es un fallo previo a esta
integración. No se alteró ni se ocultó esa prueba para obtener un resultado verde.
La comprobación de Django pasó; la red permaneció bloqueada durante la suite.

El commit de integración reúne los cambios locales y las correcciones de GitHub
en `e39ace4`. La `main` anterior se conserva en
`respaldo/main-antes-integracion-20261022` (`10bda3e`). El avance de `main` se realiza
solo si sigue en ese commit y es ancestro del commit validado. Después se cambia
a `main` en este mismo directorio, sin descartar los archivos de ejecución.

Se conserva la configuración local de Celery descrita abajo, incluidas sus cuatro
entradas comentadas. No se ha hecho push ni se ha desplegado al VPS. Las pruebas
en memoria no verifican SQL específico de PostgreSQL ni entrega final de correos.

Para consultar el resultado actual:

```powershell
git branch --show-current
git log -1 --oneline
git rev-parse main prueba-pull
git status --short
git stash list
```

Los procedimientos de guardado e integración que aparecen más abajo se conservan
como referencia para futuras integraciones; no es necesario repetirlos para esta.

## Ajustes realizados para validar

`test1.py` es un script manual de Odoo, no una prueba automatizada. El mismo
archivo defectuoso existía tanto en `main` como en `origin/main`. Sus operaciones
ahora están dentro de `main()`, con un import de paquete correcto, y solo se
ejecutan directamente. El descubrimiento de Django ya no consulta ni escribe en
Odoo. Una prueba comprueba esta condición. No se ejecutó el caso manual.

Las cinco comprobaciones de `reproducir_entrega_correos.py` ahora esperan las
correcciones de GitHub. El informe de investigación conserva los hallazgos previos
identificados como históricos.

## Configuración que hay que revisar antes de producción

En `config/settings/base.py` se conservan comentadas estas cuatro entradas locales:

- `reintentar-formulario-y-web`
- `revisar-correos-cada-minuto`
- `reintentar-correos-fallidos`
- `rescatar-runs-colgados`

No son eliminaciones producidas por un conflicto de merge. La integración conserva
esta configuración local: al desplegarla, esas entradas también quedarían
desactivadas. Antes de actualizar el servidor, revisar si allí deben habilitarse.

El resumen diario sigue activo a las 00:05 de `America/Mexico_City`; la zona global
de Celery sigue en UTC. Los recordatorios mantienen su intervalo de 12 horas y
el filtro local por prospectos amarillos/en proceso.

## Pasos seguros en PowerShell

### 1. Consultar el estado sin cambiarlo

```powershell
Set-Location D:\ProyectosTemporales\LeadManagerNueva
git status --short --branch
git diff --cached --stat
git stash list
git diff -- config/settings/base.py
```

El último comando permite revisar la decisión de programación pendiente.
Los `.pyc` y archivos de Celery siguen como estaban; no deben añadirse al commit
de integración. Si Git indica propiedad dudosa, anteponer a cada comando Git
`-c safe.directory=D:/ProyectosTemporales/LeadManagerNueva`, sin cambiar la
configuración global.

### 2. Repetir la validación aislada

```powershell
.\venv\Scripts\python.exe -B diagnostico/verificar_integracion.py
```

El script fuerza SQLite en memoria antes de iniciar Django y bloquea conexiones
TCP. No arranca Celery ni modifica la configuración persistente de la aplicación.
Esta validación no sustituye una prueba con PostgreSQL aislado ni comprueba
entrega final en buzones reales.

Para volver a consultar las conexiones, sin terminar ninguna:

```powershell
.\venv\Scripts\python.exe -B diagnostico/auditar_conexiones_pruebas.py
```

### 3. Guardar solo fuentes revisadas en `prueba-pull`

Después de resolver la decisión de configuración del paso 1:

```powershell
$archivos = @(
    'config/settings/base.py'
    'leadmanager/apps/automations/tasks.py'
    'leadmanager/apps/automations/tests.py'
    'leadmanager/apps/automations/test1.py'
    'leadmanager/apps/automations/test_manual_script.py'
    'leadmanager/apps/prospects/templates/prospects/conversations.html'
    'diagnostico/auditar_entrega_correos.py'
    'diagnostico/auditar_conexiones_pruebas.py'
    'diagnostico/reproducir_entrega_correos.py'
    'diagnostico/verificar_integracion.py'
    'diagnostico/investigacion_entrega_correos.md'
    'diagnostico/integracion_segura.md'
)
git add -- $archivos
git diff --cached --check
git diff --cached --stat
git diff --cached
```

Revisar esa salida y, si contiene los cambios esperados:

```powershell
git commit -m "Integra correcciones de correo con ajustes locales"
```

No usar `git add .`: incluiría archivos de ejecución ajenos a esta integración.
Si se cambia código o configuración después de las pruebas, repetir el paso 2.

### 4. Llevar el commit validado a `main` sin tocar el directorio actual

Este procedimiento alternativo es una referencia para futuras integraciones.
Se utiliza otro directorio para no tener que descartar o mover los archivos de
Celery y `.pyc` que siguen modificados aquí.

```powershell
git merge-base --is-ancestor main prueba-pull
```

Continuar únicamente si `$LASTEXITCODE` es `0`. Elegir una ruta nueva que no
exista y crear un directorio de trabajo de `main`:

```powershell
$principal = 'D:\ProyectosTemporales\LeadManagerNueva-main-validada'
git worktree add $principal main
git -C $principal merge --ff-only prueba-pull
git -C $principal log -1 --oneline
git worktree list
git stash list
```

El avance `--ff-only` incluye los commits de GitHub y tu commit local. Si Git
no puede avanzar directamente, se detiene: no forzar la operación.
`prueba-pull` seguirá abierta en el directorio original con sus archivos de
ejecución intactos. El nuevo directorio no copia el `.env` ignorado; no es una
instrucción para arrancar servicios ni desplegar automáticamente.

No se ha hecho push. Publicar en GitHub o actualizar el VPS es un paso posterior.
