# Actualización del VPS

## Despliegue automático desde GitHub Actions

`.github/workflows/deploy.yml` se ejecuta al hacer `push` a `main` o desde **Actions → Construir y desplegar al VPS → Run workflow** en esa rama. Primero verifica el script de despliegue con pruebas que simulan Docker, sin red ni datos reales. Construye una imagen Linux AMD64 y publica `ghcr.io/ricardorobledo/leadmanagerpuntoreactivo:production` y una etiqueta por commit. El VPS recibe la referencia exacta por digest y los archivos de Compose, Redis y despliegue del mismo commit. No necesita clonar el repositorio ni descargar código con Git para este flujo.

Configurar en **Settings → Secrets and variables → Actions → Secrets** los cinco secretos:

- `VPS_HOST`: IP o nombre del VPS, sin protocolo ni puerto.
- `VPS_USER`: usuario SSH con acceso a Docker y permiso de escritura en `/var/www/LeadManagerPuntoReactivo`.
- `VPS_SSH_KEY`: clave privada SSH sin contraseña cuya clave pública esté autorizada para ese usuario.
- `GHCR_USERNAME`: cuenta con acceso de lectura al paquete de GHCR.
- `GHCR_PULL_TOKEN`: token con `read:packages` y acceso al paquete. La publicación usa el `GITHUB_TOKEN` automático con `packages: write`.

El workflow obtiene automáticamente la clave pública del servidor con `ssh-keyscan` para las conexiones de esa ejecución, sin compararla con una huella configurada previamente. Se autentica con `VPS_SSH_KEY`.

Opcionalmente, en la pestaña **Variables**, `VPS_SSH_PORT` cambia el puerto SSH (por defecto `22`) y `GHCR_IMAGE_NAME` cambia el nombre de la imagen dentro de tu cuenta (por defecto, el nombre del repositorio en minúsculas; sin registro ni etiqueta). Ninguna de estas variables es obligatoria.

Preparar una sola vez en el VPS:

1. Instalar Docker Engine y el plugin Compose. `VPS_USER` debe poder ejecutar `docker info` sin solicitar contraseña.
2. Crear `/var/www/LeadManagerPuntoReactivo` y dar acceso de lectura/escritura a ese mismo usuario. Usa el mismo usuario en los despliegues posteriores.
3. Crear allí `.env.docker`, completar `SECRET_KEY`, `OPENAI_API_KEY`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `REDIS_PASSWORD` y las integraciones que utilices, y restringir sus permisos con `chmod 600 .env.docker`. Mantener valores numéricos válidos, como `ODOO_SELLER_ID`, `CHAT_HISTORY_LIMIT`, `IMAP_PORT` y `EMAIL_PORT`. El archivo debe ser legible por `VPS_USER`.

El workflow copia los archivos necesarios aunque el directorio solo contenga `.env.docker`. Conserva el `.env` existente. `docker/deploy.sh` crea los cuatro volúmenes externos durante la primera preparación y los reutiliza. Después de un despliegue correcto registra `.last-deploy`; si un volumen falta en un despliegue posterior, se detiene para evitar inicializar una base vacía.

El script descarga la imagen, levanta PostgreSQL y Redis, comprueba Django y detiene web, worker y Beat antes de aplicar las migraciones pendientes. Los inicia después de que terminen las migraciones y espera a que la web esté saludable. Habrá una breve interrupción durante esa actualización. Si un paso falla, el workflow informa el error; no revierte automáticamente migraciones.

Al completar el despliegue, actualiza únicamente `APP_IMAGE` en `.env.docker` con el digest verificado. El login de GHCR se guarda en un directorio temporal que se elimina al terminar. Los volúmenes y los usuarios existentes se conservan; el workflow no ejecuta `createsuperuser` ni `seed_agents`.

Para crear el primer administrador después del primer despliegue, desde el directorio del VPS:

```bash
docker compose --env-file .env.docker exec web python manage.py createsuperuser
```

La web se publica en `127.0.0.1:8777`, definido directamente en `compose.yaml`; el puerto interno del contenedor sigue siendo `8000`. Para acceder mediante un dominio, configurar por separado el proxy HTTPS hacia `127.0.0.1:8777` y los ajustes de dominio/CSRF de Django.

## Actualización vigente: retirada de Google Sheets

La integración de Google Sheets se ha eliminado. El prospector pasa de generar la respuesta a resumir el historial y continuar con Odoo y el envío al prospecto. Ya no se importa ni se encola la tarea de Sheets y `google-auth` deja de ser una dependencia del proyecto. No se requieren variables `GOOGLE_SHEETS_*` ni migraciones por esta retirada.

Para actualizar una instalación existente, subir las versiones actuales de `prospector.py`, `prospector_steps.py`, `tasks.py`, `config/settings/base.py` y `requirements.txt`, y retirar `google_sheets_tasks.py` y `services/google_sheets_service.py` si todavía existen en el servidor. Comprobar el arranque de Django y reiniciar Django y los workers de Celery.

Todas las referencias a Google Sheets en los inventarios siguientes son históricas: no se deben volver a subir los módulos ni las pruebas eliminadas, ni configurar sus credenciales. Los inventarios y recuentos antiguos tampoco confirman el estado actual del VPS.

## Histórico: Google Sheets y búsqueda de conversaciones (13 de noviembre de 2026)

### Último ajuste: corrección de la importación circular

Si ya subiste la integración de Google Sheets anterior, para este último ajuste sube estos tres archivos juntos:

```text
leadmanager/apps/automations/google_sheets_tasks.py
leadmanager/apps/automations/agents/prospector_agent/prospector_steps.py
leadmanager/apps/automations/tasks.py
```

`google_sheets_tasks.py` es nuevo. El step importa la tarea desde este módulo en la cabecera; `tasks.py` la reexporta y conserva su nombre registrado en Celery. Después de copiar los tres, ejecuta `python manage.py check` y reinicia Django/Gunicorn y el worker de Celery. Este ajuste no requiere migraciones ni cambios en la hoja. Si todavía no has subido la integración, utiliza la lista completa de nueve archivos de abajo.

### Archivos de ejecución que hay que subir o actualizar
 
| Archivo | Cambio incluido |
|---|---|
| `requirements.txt` | Añade `google-auth==2.62.2`; instalar las dependencias en el entorno virtual del VPS. |
| `config/settings/base.py` | Lee `GOOGLE_SHEETS_ID` y `GOOGLE_SHEETS_SERVICE_ACCOUNT`. |
| `leadmanager/apps/automations/services/google_sheets_service.py` | **Nuevo.** Busca y crea filas, selecciona la pestaña por unidad de negocio y verifica el ID de conversación en todas las pestañas. Reutiliza el cliente, la sesión HTTP y las credenciales por proceso/hilo. |
| `leadmanager/apps/automations/agents/prospector_agent/prospector.py` | Coloca `register_google_sheet_step` inmediatamente después de `generate_prospect_response_step`. |
| `leadmanager/apps/automations/agents/prospector_agent/prospector_steps.py` | Prepara los datos originales y el CRM actual, encola el envío y conserva el ID al reintentar. Importa la tarea desde el módulo independiente `google_sheets_tasks.py`, sin pasar por `tasks.py`. |
| `leadmanager/apps/automations/google_sheets_tasks.py` | **Nuevo.** Contiene `register_google_sheet_lead_task`, con bloqueo por conversación y hasta tres reintentos adicionales de Sheets, sin repetir IA/Odoo. |
| `leadmanager/apps/automations/tasks.py` | Reexporta la tarea de Sheets para el descubrimiento de Celery; la implementación de esta tarea vive en `google_sheets_tasks.py`. Conserva el nombre de tarea ya utilizado por los mensajes encolados. |
| `leadmanager/apps/prospects/views.py` | La búsqueda de un correo también contempla los mensajes y evita filas duplicadas. |
| `leadmanager/apps/prospects/templates/prospects/conversations.html` | Aclara los campos de búsqueda y que se respetan el periodo y los demás filtros. |

Los nueve archivos anteriores son la entrega actual y deben subirse juntos. El servicio y el módulo de tareas nuevos utilizan módulos del proyecto ya existentes (`agent_configuration.py`, modelos de conversaciones y catálogo de agentes); este bloque presupone que los despliegues anteriores ya están instalados.

### Configuración del VPS y de la hoja

Conserva el `.env` del servidor y configura allí las dos variables. Ejemplo sin credenciales reales:

```dotenv
GOOGLE_SHEETS_ID=ID_DEL_DOCUMENTO
GOOGLE_SHEETS_SERVICE_ACCOUNT=/var/www/LeadManagerNueva/secrets/google-sheets.json
```

La cuenta de servicio también puede configurarse como JSON completo en la variable. Si se utiliza una ruta, el archivo debe existir en el VPS y ser legible por el worker. Comparte el documento con la cuenta de servicio como **Editor** y habilita Google Sheets API en su proyecto. No copies el `.env` local ni guardes claves privadas en este repositorio.

Todas las pestañas de datos deben tener una primera fila con encabezados completos y estas seis columnas; su posición puede variar:

```text
FECHA
NOMBRE COMPLETO
CORREO EMPRESARIAL
TELÉFONO
EMPRESA
ID Conversación
```

- Se selecciona la pestaña por `crm_update.unidad_negocio_detectada`. Si no hay unidad, se utiliza `NO_DETERMINADO`. Una unidad indicada sin pestaña correspondiente produce un error visible.
- Se reconocen mayúsculas, acentos y separadores. Hay equivalencias para los nombres anteriores `DESAROLLO WEB` y `DESARROLO_WEB` respecto de `DESARROLLO WEB`.
- `ID Conversación` recibe `conversation.pk`, no `session_id`. El mismo ID conserva su fila, incluso si cambia de unidad; un ID distinto permite otra fila con el mismo correo. Las filas antiguas sin ID no se asignan por correo ni se rellenan automáticamente.
- `FECHA` se guarda como día/mes/año, sin hora, tomando la fecha de llegada de la conversación en el horario configurado. No se envía ni actualiza `ESTATUS`.
- El step corre solo en producción y sin condicionar el envío a la calificación o al escalamiento. Se ejecuta después de generar la respuesta, de modo que un fallo anterior a ese punto no llega a encolar Sheets.
- La tarea usa la cola habitual de Celery. No se añade una programación a Beat. El cliente reutilizable es local a cada proceso/hilo; reinicia los procesos para cargar cambios en el `.env` o en el código.
- La búsqueda de conversaciones continúa respetando fechas, canal y semáforo; el periodo «Hoy» no muestra conversaciones creadas en días anteriores.

### Orden de actualización

1. Sube los nueve archivos juntos, manteniendo las rutas y conservando la configuración, medios y entorno virtual del VPS. Incluye el nuevo `google_sheets_tasks.py` antes de reiniciar los procesos.
2. Con el entorno virtual del servidor activo, desde la raíz del proyecto, ejecuta:

```bash
python -m pip install -r requirements.txt
python manage.py check
```

3. Reinicia Django/Gunicorn y el worker de Celery usando los nombres reales de sus servicios. El worker debe registrar:

```text
leadmanager.apps.automations.tasks.register_google_sheet_lead_task
```

El nombre registrado no cambia, aunque el código de la tarea se haya trasladado a `google_sheets_tasks.py`. La reexportación desde `tasks.py` mantiene el descubrimiento de Celery y la compatibilidad con tareas que ya estaban encoladas. El prospector importa directamente el módulo independiente, de modo que ya no necesita importar `tasks.py` durante su inicialización.

4. Comprueba en la ejecución del agente el evento de registro encolado y en el worker el resultado con pestaña, fila y `created`. Para una conversación nueva debe crearse una fila; otro mensaje con el mismo ID debe reutilizarla.

**Este bloque no añade modelos ni migraciones.** No requiere `makemigrations`, `migrate`, `seed_agents` ni `collectstatic` por sus cambios Python/HTML. Si despliegas otros bloques antiguos junto con este, revisa sus requisitos por separado y compara siempre el historial real de migraciones del VPS; no reemplaces ni elimines sus migraciones basándote en los inventarios históricos.

No copies `venv/`, `__pycache__/`, `*.pyc` ni `celerybeat-schedule*`. Esta guía y `archivos_deploy_vps.txt` son referencias locales, no archivos de ejecución del servidor.

### Pruebas opcionales y validación actual

Estos archivos son opcionales para ejecutar la aplicación:

```text
leadmanager/apps/automations/test_google_sheet_step.py
leadmanager/apps/automations/test_google_sheets_service.py
leadmanager/apps/automations/test_google_sheets_routing.py
leadmanager/apps/automations/test_google_sheets_singleton.py
leadmanager/apps/prospects/test_conversation_search.py
diagnostico/verificar_integracion.py
```

Después de extraer la tarea de Sheets a `google_sheets_tasks.py`, el chequeo de Django y **126 pruebas** pasaron con SQLite en memoria, red bloqueada e integraciones simuladas. Se verificaron también la compatibilidad del nombre de tarea, los módulos existentes de identidad y agentes de pruebas y la búsqueda de conversaciones. Esta validación es local: no se ejecutó un despliegue, no se modificó la base del VPS ni se enviaron datos a Google Sheets.

Para repetir las pruebas de esta entrega, si subes los archivos opcionales:

```bash
python -B diagnostico/verificar_integracion.py leadmanager.apps.automations.test_google_sheet_step leadmanager.apps.automations.test_google_sheets_service leadmanager.apps.automations.test_google_sheets_routing leadmanager.apps.automations.test_google_sheets_singleton leadmanager.apps.prospects.test_conversation_search
```

## Archivos creados o modificados con Codex (10 de noviembre de 2026)

Este bloque añade lotes semanales por agente, resultados individuales guardados y una consolidación final de `observaciones` y `sugerencias`. Si ya subiste los cambios de Claude, añade o actualiza estos archivos. Copia siempre la versión actual de cada archivo para incorporar los cambios de ambas sesiones.

**Archivos existentes que hay que actualizar**

| Archivo | Qué cambió |
|---|---|
| `config/settings/base.py` | Beat programa `analyze_conversations` cada lunes a las 06:00 en `America/Mexico_City` y `retry_conversation_analyses` cada 15 minutos. |
| `leadmanager/apps/automations/models.py` | `WeeklyAnalysisBatchModel` conserva un lote por agente y semana. `ConversationAnalysisModel` guarda la posición, el resultado individual y su seguimiento. `LearningAnotations` usa `observaciones` y `sugerencias`. |
| `leadmanager/apps/automations/admin.py` | Consulta de lotes, resultados individuales, intentos y errores. Búsqueda de anotaciones por ambos campos. |
| `leadmanager/apps/automations/tasks.py` | `analyze_conversations` prepara los lotes semanales. `retry_conversation_analyses` retoma los lotes pendientes o con reintento vencido. |
| `leadmanager/apps/automations/agents/conversation_analyzer_agent/conversation_analyzer_steps.py` | Prepara el historial y el prompt vigente, valida el agente destinatario y guarda el resultado individual en el seguimiento del lote. |
| `leadmanager/apps/automations/agents/conversation_analyzer_agent/prompts/analyze_conversation_prompt.txt` | Evalúa conversación e instrucciones del prospector y devuelve observaciones y sugerencias. Distingue la configuración vigente de una versión histórica no disponible. |
| `leadmanager/apps/automations/agents/conversation_analyzer_agent/schemas/conversation_analysis_schema.py` | Cada propuesta exige las dos claves de texto `observaciones` y `sugerencias`. |
| `leadmanager/apps/automations/agents/conversation_analyzer_agent/schemas/__init__.py` | Exporta el esquema del resultado final junto con el esquema individual. |
| `leadmanager/apps/prospects/templates/prospects/agent_analyses.html` | Muestra observaciones y sugerencias por separado; las notas antiguas pueden no tener observaciones. |

**Archivos nuevos que hay que subir**

```text
leadmanager/apps/automations/services/conversation_analysis.py
leadmanager/apps/automations/services/weekly_analysis.py
leadmanager/apps/automations/agents/conversation_analyzer_agent/prompts/consolidate_weekly_analysis_prompt.txt
leadmanager/apps/automations/agents/conversation_analyzer_agent/schemas/weekly_analysis_schema.py
leadmanager/apps/automations/migrations/0011_learning_annotations_observaciones_sugerencias.py
leadmanager/apps/automations/migrations/0012_learning_sugerencias_label.py
leadmanager/apps/automations/migrations/0013_conversation_analysis_tracking.py
leadmanager/apps/automations/migrations/0014_weekly_analysis_batches.py
```

`conversation_analysis.py` registra el resultado y los intentos de cada conversación. `weekly_analysis.py` fija la lista del lote, controla el orden de ejecución y consolida los resultados completos. Las cuatro migraciones fueron generadas con `makemigrations`.

**Dependencias del analizador: comprobar que ya estén en el VPS**

Estos tres archivos ya existían al comenzar estos cambios. Si falta el paquete del analizador en el servidor, súbelos junto con los archivos anteriores, conservando sus directorios.

```text
leadmanager/apps/automations/agents/conversation_analyzer_agent/__init__.py
leadmanager/apps/automations/agents/conversation_analyzer_agent/conversation_analyzer.py
leadmanager/apps/automations/agents/conversation_analyzer_agent/conversation_analyzer_resource_handler.py
```

También se reutilizan `agent_base.py`, `agent_catalog.py`, `agent_configuration.py`, `resource_handler.py`, el handler del prospector y las plantillas `generate_prospect_response_web_prompt.txt` y `generate_prospect_response_prompt.txt`. Deben acompañar el código actual si el VPS todavía tiene una versión anterior. El inventario general al final incluye los cambios locales anteriores. Este bloque no añade paquetes a `requirements.txt` ni exige variables de entorno nuevas.

**Solo pruebas: opcionales para ejecutar la aplicación**

```text
leadmanager/apps/automations/test_analyze_conversations.py
leadmanager/apps/automations/test_conversation_analysis.py
leadmanager/apps/automations/test_weekly_analysis.py
leadmanager/apps/automations/agents/conversation_analyzer_agent/tests.py
```

Pasaron 52 pruebas con SQLite en memoria e inferencia simulada. Cubren la lista fija, la pausa ante el primer fallo, la recuperación de reservas vencidas y la reanudación de la consolidación desde el bloque que falló.

**Funcionamiento que debe quedar después del despliegue**

- La revisión general se ejecuta los lunes a las 06:00, hora de Ciudad de México. Selecciona conversaciones con mensajes creados o actualizados en la semana anterior, de lunes a lunes con fin exclusivo, y analiza su historial completo, incluyendo conversaciones abiertas y cerradas.
- El analizador debe estar activo y en producción. El destinatario debe ser un único agente de prospección activo, en producción y con «Puede aprender» habilitado. Las conversaciones con varios autores de atención se omiten.
- Cada lote corresponde a un agente y una semana. Su lista queda fija desde la primera preparación. El worker procesa las conversaciones en orden y registra sus resultados internos en `ConversationAnalysisModel.result`, incluso si el análisis devuelve cero hallazgos. Para analizarlas usa el historial y el prompt formateado vigente del agente, con su canal y unidades de negocio.
- El primer fallo individual pausa ese lote. El barrido de recuperación se ejecuta cada 15 minutos y retoma el lote desde la primera conversación incompleta cuando vence el plazo. Los resultados completos se reutilizan. Cada conversación admite hasta tres intentos totales. Si los agota, el lote queda fallido y la consolidación requiere que todas las conversaciones estén completas.
- La consolidación final admite hasta tres intentos totales. Si falla, el siguiente intento reutiliza los resultados individuales y los bloques de consolidación ya guardados. El barrido también puede recuperar lotes pendientes y reservas vencidas de workers interrumpidos, incluso fuera de la semana original. Las reservas vencen a los 30 minutos.
- Solo el resultado final genera una propuesta en `LearningAnotations` para el lote. Cada campo admite hasta 4000 caracteres. Las propuestas nuevas quedan inactivas y pendientes de revisión. Si el mismo agente ya tiene una propuesta idéntica, el lote enlaza esa anotación.
- En Django admin (`/panel-control/`), «Lotes de análisis semanal» muestra la etapa, el estado, el próximo intento y el último error del lote. «Seguimiento de análisis» muestra la posición, el resultado y los intentos de cada conversación. La propuesta final aparece en el botón de análisis del agente.

**Orden de despliegue para este bloque**

1. Respalda la base y compara el historial de migraciones del servidor con los archivos entregados.
2. Detén los servicios de Django, el worker y Beat durante la actualización para evitar que convivan código y esquema anteriores con los nuevos.
3. Sube los archivos de ejecución, sus dependencias faltantes y las migraciones generadas. Conserva el `.env`, los medios y el entorno virtual del VPS.
4. Desde la raíz del proyecto, con el entorno virtual del servidor activo, ejecuta:

```bash
python manage.py showmigrations automations prospects
python manage.py migrate --plan
python manage.py migrate --noinput
python manage.py migrate --check
python manage.py check
```

5. Si subiste cambios de archivos estáticos de los bloques anteriores, ejecuta `python manage.py collectstatic --noinput`.
6. Inicia Django, el worker de Celery y Celery Beat con los nombres reales de sus servicios. El worker debe registrar `analyze_conversations` y `retry_conversation_analyses`, además de las tareas existentes.

Si el VPS ya tiene `automations.0010_test_conversations`, el plan mostrará las migraciones pendientes entre `0011` y `0014`. Las cuatro ya se aplicaron a la base configurada desde este equipo. Si el VPS comparte esa base, aparecerán con `[X]`. Comprueba el estado desde el servidor antes de iniciar los servicios con el código nuevo.

## Archivos creados o modificados con Claude (9 y 10 de noviembre de 2026)

Lista exacta de los archivos que se crearon o modificaron en la sesión con Claude. La tabla de anotaciones de la configuración del agente (`agent_configuration.html`, `AgentConfigurationView`, `AgentAnnotationStatusView`) la hizo Codex; Claude le agregó pruebas, quitó el borde negro de foco de la sección y rediseñó el modal de detalle con editar y eliminar. Los cambios hechos después con Codex no están aquí y se documentan por separado. Si Codex también tocó alguno de estos archivos, copia al VPS la versión actual del archivo, que ya incluye ambos cambios.

**Nuevos**

| Archivo | Qué contiene |
|---|---|
| `leadmanager/apps/automations/migrations/0010_test_conversations.py` | Crea las tablas `TestConversationModel` y `TestMessageModel`. Solo crea; no modifica tablas existentes. Hay que aplicarla con `migrate`. |
| `leadmanager/apps/prospects/test_conversations.py` | Lecturas de las conversaciones de pruebas: semáforo, filas de la tabla y contenido del modal. |
| `leadmanager/apps/automations/migrations/0015_rename_learning_annotation_model.py` | Renombra el modelo `LearningAnotations` a `LearningAnnotationModel` y sus campos `observaciones` → `observation` y `sugerencias` → `suggestion`. Solo renombra (tabla `automations_learninganotations` → `automations_learningannotationmodel` y dos columnas); no borra datos. Depende de `0014_weekly_analysis_batches`. |

**Modificados**

| Archivo | Qué cambió |
|---|---|
| `config/settings/base.py` | `CELERY_SHADOW_QUEUE` toma por defecto la cola `leadmanager`: el worker de siempre atiende las pruebas sin `-Q`. |
| `leadmanager/apps/automations/models.py` | Modelos `TestConversationModel` y `TestMessageModel`; `LearningAnotations` renombrado a `LearningAnnotationModel`, con campos `observation` y `suggestion`. |
| `leadmanager/apps/automations/admin.py` | Admin de `LearningAnnotationModel` (búsqueda por `observation` y `suggestion`). |
| `leadmanager/apps/automations/agents/conversation_analyzer_agent/conversation_analyzer_steps.py` | Guarda las anotaciones con los campos nuevos. La respuesta de la IA conserva sus llaves en español (`observaciones`, `sugerencias`) y se mapea al modelo. |
| `leadmanager/apps/automations/services/weekly_analysis.py` | Igual que el anterior para el análisis semanal; reemplaza el `**value` por un mapeo explícito de las llaves de la IA a los campos del modelo. **13 de noviembre:** se quitó el modo de prueba (`restart_trial_batch` y la anotación «Prueba {run}» de los lotes de domingo). Cada lote crea una sola anotación inactiva y un lote completado no vuelve a analizarse. |
| `leadmanager/apps/automations/tasks.py` | **13 de noviembre:** `analyze_conversations` ya no acepta `prueba`. Siempre analiza la semana anterior, de lunes a lunes (fin exclusivo, hora de Ciudad de México). Ejecutarla otra vez reutiliza el mismo lote y continúa desde el primer pendiente. |
| `leadmanager/apps/automations/services/agent_execution.py` | La copia del turno guarda `source_message_id`, el mensaje real que origina cada respuesta de prueba. |
| `leadmanager/apps/automations/agents/prospector_agent/shadow_steps.py` | Flujo completo del agente de pruebas y paso `save_test_conversation_step`. |
| `leadmanager/apps/automations/views.py` | Ejecuciones muestran la categoría del agente en lugar de su identificador. |
| `leadmanager/apps/automations/templates/automations/runs.html` | Ancho completo con margen pequeño, categoría del agente y colores de modo («Atención real» morado, «Pruebas» gris). |
| `leadmanager/apps/automations/templates/automations/run_detail.html` | Lo mismo que `runs.html` en el detalle de una ejecución. |
| `leadmanager/apps/automations/templates/automations/email_deliveries.html` | Ancho completo con margen pequeño. |
| `leadmanager/apps/automations/templates/automations/email_delivery_detail.html` | Ancho completo con margen pequeño. |
| `leadmanager/apps/prospects/views.py` | Conversaciones en modo Producción o Pruebas; vista `TestConversationView` del modal de pruebas; vista `AgentDuplicateView`; los agentes nuevos reciben las unidades de negocio base; la página de Análisis filtra por anotaciones activas (`?annotation_status=active`); vistas `AgentAnnotationUpdateView` (edita solo `suggestion`, de 1 a 4000 caracteres; el formulario envía el campo `suggestion`) y `AgentAnnotationDeleteView` (borra la anotación), que regresan a la pestaña Anotaciones conservando página y filtro. |
| `leadmanager/apps/automations/services/google_sheets_service.py` | **13 de noviembre:** el registro de leads solo lee y escribe la pestaña de la unidad de negocio recibida, que debe ser una de las 11 de `BUSINESS_UNIT_SHEETS` (KIOSKOS DIGITALES, ODOO, APPS, BPM, CIBERSEGURIDAD, DESARROLLO WEB, DIGITAL SIGNAGE, ECOMMERCE, INTELIGENCIA ARTIFICIAL, MARKETING DIGITAL, NO_DETERMINADO). Las demás pestañas del documento («Hoja 2», «Leads anteriores», etc.) ya no se consultan ni bloquean el registro. El ID de conversación se busca solo en esa pestaña: si la conversación cambia de unidad, se crea otra fila en la nueva. Esto reemplaza lo descrito arriba sobre revisar todas las pestañas. Reiniciar el worker de Celery. |
| `leadmanager/apps/automations/test_google_sheets_routing.py` | **13 de noviembre:** pruebas ajustadas al registro por pestaña de unidad. Solo pruebas. |
| `leadmanager/apps/prospects/admin.py` | **13 de noviembre:** en el admin de Django, las conversaciones, los mensajes (Chat Histories) y las escalaciones se buscan por `session_id`; los mensajes y las escalaciones también por texto dentro de su JSON (por ejemplo, un correo). Las relaciones se muestran como id en lugar de listas desplegables. No requiere migración. |
| `leadmanager/apps/prospects/urls.py` | Rutas `conversations/test/<id>/`, `agents/<key>/duplicate/`, `agents/<key>/annotations/<id>/edit/` y `agents/<key>/annotations/<id>/delete/`. |
| `leadmanager/apps/prospects/templates/prospects/conversations.html` | Selector «Producción \| Pruebas» debajo de «Filtrar», filas grises en Pruebas y badge «Prueba» centrado en el modal. |
| `leadmanager/apps/prospects/templates/prospects/agent_configuration.html` | La tabla de anotaciones muestra dos columnas de fecha, «Creada» y «Actualizada», en formato de 12 horas. Modal de detalle de anotación rediseñado: resumen con estado, agente y fechas (el id ya no se muestra en el modal, solo abreviado en la tabla); observaciones de solo lectura; sugerencias editables con contador de caracteres; Activar/Desactivar y Eliminar con confirmación dentro del modal. La sección de anotaciones ya no muestra el borde negro de foco. |
| `leadmanager/apps/prospects/templates/static/agents/agent-prompts.js` | Comportamiento del modal: abrir/cancelar la edición, contador, confirmación de borrado y evitar doble envío. Es archivo estático: en el VPS hay que correr `collectstatic`. |
| `leadmanager/apps/prospects/templates/prospects/agent_analyses.html` | Página de Análisis: id de cada anotación, fecha en formato de 12 horas y filtro «Todas / Solo activas»; la paginación conserva el filtro. |
| `leadmanager/apps/prospects/templates/prospects/agents.html` | Botones «Duplicar agente» y «Conversaciones» (agentes de pruebas), más chicos y en cuadrícula de dos columnas. |
| `leadmanager/apps/automations/test_shadow_execution.py` | Pruebas del flujo de pruebas y de su guardado aislado. Solo pruebas. |
| `leadmanager/apps/automations/agents/conversation_analyzer_agent/tests.py`, `leadmanager/apps/automations/test_weekly_analysis.py`, `leadmanager/apps/automations/test_conversation_analysis.py` | Ajustadas al nuevo nombre del modelo y de sus campos. En `test_weekly_analysis.py` se quitaron las pruebas del reinicio de prueba y se agregaron dos: un lote completado se reutiliza, y la tarea toma la semana anterior y no repite análisis al ejecutarse dos veces. Solo pruebas. |
| `leadmanager/apps/prospects/tests.py` | Pruebas de los modos de Conversaciones y de sus modales. Solo pruebas. |
| `leadmanager/apps/prospects/test_agent_management.py` | Pruebas de duplicar, de unidades base y del botón Conversaciones; las pruebas de imágenes ya no cierran la conexión a la base; pruebas del formato de 12 horas, el id y el filtro de anotaciones (en la configuración del agente y en la página de Análisis) y de editar y eliminar anotaciones; las 41 pasan juntas. Solo pruebas. |
| `DESPLIEGUE_VPS.md` | Este documento. No se copia al VPS. |

Los archivos marcados «Solo pruebas» no son necesarios para que la aplicación funcione en el VPS.

**Orden del bloque de Claude:** respaldo de la base → copiar estos archivos → `python manage.py migrate --plan` (deben aparecer `automations.0010_test_conversations` y, después de las de Codex, `automations.0015_rename_learning_annotation_model` si aún está pendiente, revisando antes el historial de migraciones como se indica más abajo) → `python manage.py migrate --noinput` → `python manage.py collectstatic --noinput` → reiniciar Django y el worker de Celery. Al arrancar, la lista `[tasks]` del worker debe incluir `process_shadow_run`. Para desplegar también los cambios posteriores de Codex, usa el orden completo del bloque anterior, incluyendo las migraciones `0011`–`0014` y el reinicio de Beat.

## Cambios acumulados de agentes y canales

Estos archivos implementan el historial de análisis, la administración de agentes y la selección de canales:

```text
leadmanager/apps/automations/admin.py
leadmanager/apps/automations/agent_catalog.py
leadmanager/apps/automations/agent_channels.py
leadmanager/apps/automations/forms.py
leadmanager/apps/automations/agent_preview.py
leadmanager/apps/automations/services/conversation_reminders.py
leadmanager/apps/automations/services/agent_selection.py
leadmanager/apps/automations/services/agent_execution.py
leadmanager/apps/automations/tasks.py
leadmanager/apps/automations/management/commands/import_agent_avatars.py
leadmanager/apps/automations/models.py
leadmanager/apps/automations/agents/agent_base.py
leadmanager/apps/automations/agents/error_policy.py
leadmanager/apps/automations/agents/prospector_agent/prospector.py
leadmanager/apps/automations/agents/prospector_agent/prospector_steps.py
leadmanager/apps/automations/agents/prospector_agent/shadow_steps.py
leadmanager/apps/automations/agents/conversation_analyzer_agent/conversation_analyzer_steps.py
leadmanager/apps/automations/test_shadow_execution.py
leadmanager/apps/automations/views.py
leadmanager/apps/automations/templates/automations/runs.html
leadmanager/apps/automations/templates/automations/run_detail.html
leadmanager/apps/automations/templates/automations/email_deliveries.html
leadmanager/apps/automations/templates/automations/email_delivery_detail.html
leadmanager/apps/automations/migrations/0006_agent_channel_assignments.py
leadmanager/apps/automations/migrations/0007_agent_category_choices.py
leadmanager/apps/automations/migrations/0008_agent_mode.py
leadmanager/apps/automations/migrations/0009_agent_run_shadow_tracking.py
leadmanager/apps/automations/migrations/0010_test_conversations.py
leadmanager/apps/api/agents/views.py
config/settings/base.py
leadmanager/apps/prospects/urls.py
leadmanager/apps/prospects/views.py
leadmanager/apps/prospects/test_conversations.py
leadmanager/apps/prospects/tests.py
leadmanager/apps/prospects/templates/prospects/conversations.html
leadmanager/apps/prospects/templates/prospects/agents.html
leadmanager/apps/prospects/templates/prospects/_channel_conflict_modal.html
leadmanager/apps/prospects/templates/prospects/agent_analyses.html
leadmanager/apps/prospects/templates/prospects/agent_channels.html
leadmanager/apps/prospects/templates/prospects/agent_configuration.html
leadmanager/apps/prospects/templates/prospects/agent_steps/test.html
leadmanager/apps/prospects/templates/prospects/agent_create.html
leadmanager/apps/prospects/templates/prospects/agent_delete.html
leadmanager/apps/prospects/templates/static/agents/agent-prompts.js
leadmanager/apps/prospects/test_agent_management.py
leadmanager/apps/pages/views.py
leadmanager/apps/pages/urls.py
leadmanager/apps/pages/static/chat-widget/chat-widget.js
leadmanager/apps/pages/templates/webchat/chat.html
```

El archivo `test_agent_management.py` contiene pruebas; no es necesario para ejecutar la aplicación. Sus pruebas de imágenes de agente ya no cierran la conexión a la base al terminar (antes, al correr el archivo completo, las pruebas siguientes fallaban con «connection already closed»); las 28 pasan juntas. Los archivos nuevos deben copiarse también si el despliegue se hace manualmente. Estos cambios dependen de código y migraciones anteriores de agentes y análisis; no se garantiza que copiar solo esta lista baste en un VPS que no los tenga.

Los canales seleccionan ahora el agente de prospección de producción que atiende cada mensaje web, formulario o correo. Los agentes con `mode=shadow_testing` y ese mismo canal seleccionado reciben una copia del turno mediante tareas independientes. El seguimiento de cada lote semanal conserva los resultados individuales, los intentos y la consolidación final. Las anotaciones muestran esa consolidación en dos campos. Las ejecuciones y sus pasos quedan registrados.

La tabla de ejecuciones, su filtro de agentes y el detalle muestran el nombre actual del agente configurado en el catálogo, con su imagen subida o una inicial si no tiene imagen. Debajo del nombre aparece el modo («Atención real» en morado, «Pruebas» en gris) y la categoría del agente (por ejemplo, «Prospección»); el identificador interno (`prospector` o `agent-<código>`) ya no se muestra. Si el agente ya no existe en el catálogo, el nombre cae al identificador y la categoría queda vacía. Las pantallas de ejecuciones, detalle de ejecución, correos y detalle de correo ocupan todo el ancho junto al sidebar, con un margen pequeño (`p-3 md:p-4`). Este cambio no requiere migraciones.

En Conversaciones, dentro del formulario de filtros y debajo del botón «Filtrar», hay un selector «Producción | Pruebas» de todo el ancho (`?mode=production`, por defecto, o `?mode=shadow_testing`). Elegir un lado no recarga la tabla: se aplica al pulsar «Filtrar», junto con los demás filtros. Producción lista las conversaciones reales, como siempre. Pruebas lista las conversaciones de los agentes de pruebas (`TestConversationModel`), con los mismos filtros: periodo, semáforo (el que asignó el agente de pruebas), canal, UTM, origen, búsqueda de prospecto y de mensajes; canal, atribución y prospecto se toman de la conversación real que acompaña. En Pruebas las filas se muestran en gris claro. Al abrir una conversación de pruebas, el modal muestra un badge gris «Prueba» centrado en la cabecera, los mensajes del prospecto con las respuestas de ese agente (con su nombre en las burbujas) y su análisis CRM; el modal de una conversación real no muestra nada de las pruebas. La exportación a Excel sigue cubriendo solo conversaciones reales.

## Migraciones: revisar antes de desplegar

**Renombre de anotaciones (0015).** `automations.0015_rename_learning_annotation_model` renombra `LearningAnotations` → `LearningAnnotationModel` y las columnas `observaciones` → `observation` y `sugerencias` → `suggestion`. Es un renombre: los datos se conservan y la llave foránea de `WeeklyAnalysisBatchModel.annotation` se reapunta sola. Se aplicó en la base de desarrollo el 10 de noviembre de 2026 (2 anotaciones antes y después). El código nuevo no funciona sin ella, y el código viejo no funciona con ella: copia el código y migra en el mismo despliegue, y reinicia Django y el worker. Cualquier referencia escrita fuera del repositorio a los nombres viejos (consultas SQL, reportes) debe actualizarse.

**No reemplaces ni elimines automáticamente las migraciones del VPS.** El inventario anterior del proyecto indicaba que ese servidor tenía `automations` hasta `0004_emaildeliverylogmodel` y `prospects` hasta `0013_conversationmodel_web_message_count_and_more`, pero no se ha consultado el servidor en esta revisión. Además, Git local muestra migraciones históricas modificadas o eliminadas y otras con nombres diferentes. Hay que comparar nombres, contenido y estado reales antes de decidir qué archivos subir y ejecutar `migrate`.

La migración local nueva `automations.0006_agent_channel_assignments` depende de `0005_agent_learning_and_annotations`. Crea la tabla de asignaciones y copia los canales anteriores. Si hay canales desconocidos o un mismo canal ya aparece en varios agentes activos, la migración falla: revisa y resuelve los datos antes de continuar. No se ha aplicado en el VPS.

La migración `automations.0007_agent_category_choices` limita las opciones de categoría a prospección y análisis y normaliza la categoría anterior «Asistente». Si encuentra otra categoría, detiene la migración para que se revise.

La migración `automations.0008_agent_mode`, generada con `makemigrations`, añade `AgentModel.mode` con las opciones `production` y `shadow_testing`. Los registros existentes y los nuevos quedan en producción por defecto. El modo se puede editar y filtrar desde Django admin.

La migración `automations.0009_agent_run_shadow_tracking`, generada con `makemigrations`, añade a cada ejecución su modo, relación con la ejecución real, copia de entrada y resultado, además del estado «En cola». La restricción `unique_shadow_agent_run` evita repetir un agente de pruebas dentro de la misma ejecución real. Los runs anteriores quedan clasificados como producción. Es necesario aplicarla antes de usar este código.

La migración `automations.0010_test_conversations`, generada con `makemigrations`, solo crea dos tablas nuevas y vacías: `TestConversationModel` (una por agente de pruebas y conversación real, con la restricción `unique_test_conversation_per_agent`) y `TestMessageModel` (los mensajes de esa conversación de pruebas, ligados a su ejecución y al mensaje real que los originó). No modifica tablas existentes. Depende de `automations.0009_agent_run_shadow_tracking` y `prospects.0013_conversationmodel_web_message_count_and_more`. Es necesario aplicarla antes de usar este código: sin ella, las ejecuciones de pruebas fallan al guardar su turno (la atención real no se afecta).

Las migraciones nuevas del análisis semanal fueron generadas con `python manage.py makemigrations`; las migraciones futuras también deben generarse con ese comando, no escribirse manualmente. En el VPS se aplican los archivos entregados con `migrate`, sin regenerarlos allí:

- `automations.0011_learning_annotations_observaciones_sugerencias`: renombra `learning_content` a `sugerencias`, conservando texto, identificador, agente, fechas y estado de aprobación; añade `observaciones`, vacío en las notas anteriores.
- `automations.0012_learning_sugerencias_label`: cambia la etiqueta del campo a «Sugerencias».
- `automations.0013_conversation_analysis_tracking`: crea `ConversationAnalysisModel`, único por conversación y agente, con huellas, estado, intentos, próximo reintento, reserva, error y enlace a la ejecución. No elimina conversaciones ni mensajes.
- `automations.0014_weekly_analysis_batches` crea `WeeklyAnalysisBatchModel`, único por agente y semana. Añade lote, posición y resultado al seguimiento de conversación, y admite una fila por conversación dentro de cada lote. Conserva los registros históricos de seguimiento sin lote y sus restricciones de unicidad anteriores, además de conversaciones, mensajes y anotaciones.

`0011`, `0012` y `0013` se aplicaron correctamente en la base configurada desde este equipo el 10 de noviembre de 2026; `migrate --check` terminó sin pendientes. Esto no confirma que los archivos del VPS estén actualizados ni que su `.env` apunte a esa misma base.

La `0014` se generó con `makemigrations` y se aplicó correctamente a la base configurada desde este equipo. `migrate --check` terminó sin pendientes. Incluye el archivo al desplegar y aplica la migración si aparece pendiente en el VPS.

`0009` y `0010` se aplicaron correctamente en la base configurada desde este equipo el 9 de noviembre de 2026. No se ha verificado si el VPS usa esa misma base; si usa otra, debe aplicar su migración correspondiente.

En la pestaña Prueba de la configuración del agente se puede seleccionar el modo mediante un interruptor con modal explicativo. Se aplica al pulsar «Guardar cambios» y queda fuera del JSON del prompt. Las respuestas de pruebas se consultan en Ejecuciones; no se calcula una puntuación automática de comparación.

El modo de pruebas se limita a agentes de prospección que no estén atendiendo en producción. El agente integrado `prospector` permanece protegido. El editor y Django admin validan la restricción al guardar; los agentes de producción protegidos y los de análisis no muestran la tarjeta ni el interruptor del modo de pruebas. Un shadow no necesita `is_active=True`: ese campo reserva canales para atención real; el modo habilita las pruebas y se toman sus canales seleccionados aunque sus asignaciones estén inactivas.

Desde la raíz del proyecto en el VPS, con su entorno virtual activo:

```bash
python manage.py showmigrations automations prospects
python manage.py migrate --plan
```

Haz un respaldo de la base antes de cambiar el código o esquema. Solo después de confirmar que el historial y el plan son compatibles, sube los archivos aprobados, aplica las migraciones pendientes con `python manage.py migrate --noinput` y verifica con `python manage.py migrate --check`.

## Reinicio y comprobación

Las tareas reales, las de pruebas (shadow) y las de análisis semanal usan por defecto la misma cola, `leadmanager`: `CELERY_SHADOW_QUEUE` toma por defecto el valor de `CELERY_TASK_DEFAULT_QUEUE`. Los mismos comandos de siempre atienden esas tareas. Después de subir el código y aplicar las migraciones, reinicia Django, el worker y Beat; el worker debe cargar los pasos nuevos y Beat, la programación semanal y de recuperación:

```bash
python -m celery -A config worker --loglevel=info --pool=solo
python -m celery -A config beat --loglevel=info
```

En el VPS usa los nombres reales de sus servicios. Al arrancar, la lista `[tasks]` del worker debe incluir `leadmanager.apps.automations.tasks.process_shadow_run`, `analyze_conversations` y `retry_conversation_analyses`; si faltan, el worker sigue con código anterior. Mantén una única instancia de Beat para esta programación.

La tarea semanal y el barrido de recuperación procesan cada lote en orden. Un fallo pausa ese lote hasta el siguiente intento previsto. Con `--pool=solo`, el análisis y la consolidación ocupan el worker y pueden retrasar mensajes reales mientras se ejecutan.

Con `--pool=solo` el worker procesa una tarea a la vez: la prueba corre justo después de la atención real, no al mismo tiempo. Producción no la espera y la respuesta al prospecto no se retrasa. Cada prueba tarda lo que sus llamadas a OpenAI (unos 30 s), así que puede retrasar el siguiente mensaje real que llegue en ese lapso. Para separarlas, define `CELERY_SHADOW_QUEUE=leadmanager-shadow` en el `.env` y lanza un worker aparte con `-Q leadmanager-shadow`. Si se separa la cola y no hay worker para ella, los runs de pruebas quedarán en «En cola» y producción continuará.

No se usa `.get()` ni se espera el resultado de las pruebas. El encolado sí requiere una publicación breve al broker; se deshabilitan sus reintentos en esta ruta. No configures `CELERY_TASK_ALWAYS_EAGER=True`, porque ejecutaría las tareas dentro del proceso que las publica.

**Flujo de pruebas y aislamiento.** Cada agente de pruebas ejecuta `SHADOW_STEPS` en `shadow_steps.py`: carga la base de conocimiento, busca información, genera su respuesta con su propia configuración, la registra en su ejecución y guarda el turno en `TestConversationModel` / `TestMessageModel`. Es comparación por turno: recibe el historial real, con las respuestas de producción, y guarda lo que habría contestado con ese mismo contexto. Esas tablas solo las lee el lado «Pruebas» de Conversaciones; tableros, estadísticas, exportación, recordatorios, Odoo y el chat del prospecto siguen leyendo únicamente `conversations` y su historial, así que las pruebas no cuentan como conversaciones ni llegan al cliente. Cada turno de pruebas hace sus propias llamadas a OpenAI (búsqueda, validación y respuesta), con su costo correspondiente.

Cada copia se toma después de preparar prospecto/conversación y antes de búsqueda/respuesta real. Si producción falla antes de ese punto, no se genera el shadow. Las pruebas pueden continuar aunque producción falle después. Los shadows solo guardan sus propios runs, pasos y eventos: no escriben historial público, prospectos ni Odoo y no envían mensajes. Desactivar el modo o quitar el canal antes de que el worker tome la tarea la marca fallida sin ejecutarla. No hay reintento automático de shadows fallidos.

Para comprobarlo, selecciona `web` en un agente de pruebas inactivo, habilita «Está en pruebas» y guarda. Mantén un agente de prospección activo con `mode=production` y la asignación `web` activa. Envía un mensaje nuevo desde el widget: debe aparecer un run «Atención real» y uno «Pruebas» por agente configurado. Cada detalle de pruebas enlaza su ejecución real. El run de pruebas guarda su respuesta interna en el resultado y en eventos, y en Conversaciones → Pruebas aparece la conversación del agente de pruebas con esa respuesta. El widget no espera a los runs de pruebas para aceptar otro mensaje. Si no hay producción asignada al canal, se registra un error en el pendiente y no se sustituye por un shadow.

Conserva el `.env`, el entorno virtual y los datos del servidor. No copies `__pycache__`, `*.pyc`, `celerybeat-schedule` ni el entorno virtual de Windows. Si se actualizan archivos estáticos ejecuta `python manage.py collectstatic --noinput`. Reinicia los servicios Django y Celery con los nombres reales del VPS después de actualizar el código y, cuando corresponda, migrar la base.

Las imágenes personalizadas de agentes se guardan en el almacenamiento configurado para medios, bajo `agent-avatars/`. El proceso de Django debe poder escribir en ese directorio y su contenido debe conservarse entre despliegues; incluye esos archivos en los respaldos.

Sofía y María fueron registradas en la base configurada desde este equipo. Para que el VPS las muestre con almacenamiento local independiente, copia los siguientes archivos a su `MEDIA_ROOT`, conservando las mismas rutas:

```text
media/agent-avatars/12f65468ebf545258429f3d46cd28284.png
media/agent-avatars/0bd54e3a03ad4fc5b599a28a28d02ba6.jpg
```

Si ambos entornos usan el mismo almacenamiento compartido, no hace falta copiarlos. `python manage.py import_agent_avatars` importa los archivos originales únicamente para agentes sin imagen subida; no vuelve a copiar archivos si ya existe una ruta de imagen subida. Si las rutas ya están registradas en la base pero los archivos faltan en el servidor, copia los archivos anteriores: el comando no los recreará.

Un agente creado con «Crear agente» nace con su propia copia de las unidades de negocio base del proyecto (`agent_prompt_defaults.json`), no de las que tenga Sofía en ese momento. Las copias son independientes: editarlas no cambia las de ningún otro agente. Los agentes que ya existían no se modifican; si uno se creó solo con NO_DETERMINADO, sus unidades se agregan desde su configuración.

Cada agente de prospección tiene un botón «Duplicar agente» (`agents/<key>/duplicate/`, POST). La copia se llama «Copia de <nombre>», nace inactiva y en modo producción (no atiende ni corre como agente de pruebas), y lleva su propia copia del prompt, las unidades de negocio, los canales seleccionados sin reservar, la opción de aprendizaje y un archivo de imagen propio. No copia ejecuciones ni anotaciones de aprendizaje. Los agentes de análisis no se pueden duplicar. Los agentes en modo de pruebas tienen además un botón «Conversaciones» que abre Conversaciones en el lado Pruebas (`?mode=shadow_testing`); muestra las conversaciones de prueba de todos los agentes de pruebas, no solo las de ese agente. Los botones de cada tarjeta de agente quedaron más chicos y en una cuadrícula de dos columnas; con cinco botones, «Eliminar agente» ocupa la fila completa. Estos cambios no requieren migraciones.

Comprueba en Agentes que puedes crear un agente (con las unidades base), duplicarlo, seleccionar varios canales, bloquear un conflicto entre agentes activos y volver a activar uno tras liberar el canal. Comprueba también el botón de análisis y la configuración de un agente inactivo.

## Inventario local de aplicación frente a Git

> Inventario contrastado con Git el 10 de noviembre de 2026. Incluye 110 archivos nuevos o modificados, entre ellos los del lote semanal. La sección de Codex al principio identifica qué subir para esos cambios.

La lista siguiente incluye archivos nuevos y modificados de `config/`, `leadmanager/`, `manage.py`, `build.sh` y `requirements.txt`, también cambios ajenos a esta conversación. Los archivos de migración se listan para revisión, no como autorización para reemplazar el historial del VPS. Se excluyen cachés y archivos eliminados; las eliminaciones locales deben evaluarse por separado y no replicarse automáticamente.

- **Modificado** `config/settings/base.py`
- **Modificado** `leadmanager/apps/api/agents/models.py`
- **Modificado** `leadmanager/apps/api/agents/serializers.py`
- **Modificado** `leadmanager/apps/api/agents/tests.py`
- **Modificado** `leadmanager/apps/api/agents/urls.py`
- **Modificado** `leadmanager/apps/api/agents/views.py`
- **Modificado** `leadmanager/apps/api/prospects/views.py`
- **Modificado** `leadmanager/apps/authentication/templates/authentication/login.html`
- **Modificado** `leadmanager/apps/automations/admin.py`
- **Modificado** `leadmanager/apps/automations/agent_catalog.py`
- **Modificado** `leadmanager/apps/automations/agent_preview.py`
- **Modificado** `leadmanager/apps/automations/agents/__init__.py`
- **Modificado** `leadmanager/apps/automations/agents/agent_base.py`
- **Modificado** `leadmanager/apps/automations/agents/error_policy.py`
- **Modificado** `leadmanager/apps/automations/agents/general_schemas.py`
- **Modificado** `leadmanager/apps/automations/agents/prospector_agent/prompts/generate_history_summary_prompt.txt`
- **Modificado** `leadmanager/apps/automations/agents/prospector_agent/prompts/generate_prospect_response_prompt.txt`
- **Modificado** `leadmanager/apps/automations/agents/prospector_agent/prompts/generate_prospect_response_web_prompt.txt`
- **Modificado** `leadmanager/apps/automations/agents/prospector_agent/prompts/search_information_email_form_prompt.txt`
- **Modificado** `leadmanager/apps/automations/agents/prospector_agent/prompts/search_information_validator_prompt.txt`
- **Modificado** `leadmanager/apps/automations/agents/prospector_agent/prompts/search_information_web_prompt.txt`
- **Modificado** `leadmanager/apps/automations/agents/prospector_agent/prospector.py`
- **Modificado** `leadmanager/apps/automations/agents/prospector_agent/prospector_steps.py`
- **Modificado** `leadmanager/apps/automations/agents/prospector_agent/schemas/answer_schema.py`
- **Modificado** `leadmanager/apps/automations/agents/prospector_agent/schemas/prospect_schema.py`
- **Modificado** `leadmanager/apps/automations/forms.py`
- **Modificado** `leadmanager/apps/automations/management/commands/seed_agents.py`
- **Modificado** `leadmanager/apps/automations/migrations/0002_pendingrunmodel.py`
- **Modificado** `leadmanager/apps/automations/models.py`
- **Modificado** `leadmanager/apps/automations/services/conversation_reminders.py`
- **Modificado** `leadmanager/apps/automations/services/imap_service.py`
- **Modificado** `leadmanager/apps/automations/tasks.py`
- **Modificado** `leadmanager/apps/automations/templates/automations/email_deliveries.html`
- **Modificado** `leadmanager/apps/automations/templates/automations/email_delivery_detail.html`
- **Modificado** `leadmanager/apps/automations/templates/automations/run_detail.html`
- **Modificado** `leadmanager/apps/automations/templates/automations/runs.html`
- **Modificado** `leadmanager/apps/automations/test1.py`
- **Modificado** `leadmanager/apps/automations/tests.py`
- **Modificado** `leadmanager/apps/automations/views.py`
- **Modificado** `leadmanager/apps/pages/static/chat-widget/chat-widget.js`
- **Modificado** `leadmanager/apps/pages/templates/pages/widget_demo.html`
- **Modificado** `leadmanager/apps/pages/templates/webchat/chat.html`
- **Modificado** `leadmanager/apps/pages/urls.py`
- **Modificado** `leadmanager/apps/pages/views.py`
- **Modificado** `leadmanager/apps/prospects/migrations/0010_prospectmodel_enterprise_summary.py`
- **Modificado** `leadmanager/apps/prospects/migrations/0011_emailmessagemodel_attempts_and_more.py`
- **Modificado** `leadmanager/apps/prospects/models.py`
- **Modificado** `leadmanager/apps/prospects/templates/prospects/agent_configuration.html`
- **Modificado** `leadmanager/apps/prospects/templates/prospects/agent_steps/conversation_flow.html`
- **Modificado** `leadmanager/apps/prospects/templates/prospects/agent_steps/test.html`
- **Modificado** `leadmanager/apps/prospects/templates/prospects/agents.html`
- **Modificado** `leadmanager/apps/prospects/templates/prospects/base_dashboard.html`
- **Modificado** `leadmanager/apps/prospects/templates/prospects/chatbot_analyzer.html`
- **Modificado** `leadmanager/apps/prospects/templates/prospects/conversations.html`
- **Modificado** `leadmanager/apps/prospects/templates/prospects/dashboard.html`
- **Modificado** `leadmanager/apps/prospects/templates/prospects/knowledge.html`
- **Modificado** `leadmanager/apps/prospects/templates/prospects/sidebar.html`
- **Modificado** `leadmanager/apps/prospects/templates/prospects/statistics.html`
- **Modificado** `leadmanager/apps/prospects/templates/static/agents/agent-prompts.js`
- **Modificado** `leadmanager/apps/prospects/tests.py`
- **Modificado** `leadmanager/apps/prospects/urls.py`
- **Modificado** `leadmanager/apps/prospects/views.py`
- **Modificado** `leadmanager/apps/urls.py`
- **Modificado** `manage.py`
- **Modificado** `requirements.txt`
- **Nuevo** `leadmanager/apps/automations/agent_channels.py`
- **Nuevo** `leadmanager/apps/automations/agents/conversation_analyzer_agent/__init__.py`
- **Nuevo** `leadmanager/apps/automations/agents/conversation_analyzer_agent/conversation_analyzer.py`
- **Nuevo** `leadmanager/apps/automations/agents/conversation_analyzer_agent/conversation_analyzer_resource_handler.py`
- **Nuevo** `leadmanager/apps/automations/agents/conversation_analyzer_agent/conversation_analyzer_steps.py`
- **Nuevo** `leadmanager/apps/automations/agents/conversation_analyzer_agent/prompts/analyze_conversation_prompt.txt`
- **Nuevo** `leadmanager/apps/automations/agents/conversation_analyzer_agent/prompts/consolidate_weekly_analysis_prompt.txt`
- **Nuevo** `leadmanager/apps/automations/agents/conversation_analyzer_agent/schemas/__init__.py`
- **Nuevo** `leadmanager/apps/automations/agents/conversation_analyzer_agent/schemas/conversation_analysis_schema.py`
- **Nuevo** `leadmanager/apps/automations/agents/conversation_analyzer_agent/schemas/weekly_analysis_schema.py`
- **Nuevo** `leadmanager/apps/automations/agents/conversation_analyzer_agent/tests.py`
- **Nuevo** `leadmanager/apps/automations/agents/prospector_agent/shadow_steps.py`
- **Nuevo** `leadmanager/apps/automations/management/commands/import_agent_avatars.py`
- **Nuevo** `leadmanager/apps/automations/migrations/0003_agentmodel_agentbusinessunitmodel.py`
- **Nuevo** `leadmanager/apps/automations/migrations/0004_emaildeliverylogmodel.py`
- **Nuevo** `leadmanager/apps/automations/migrations/0005_agent_learning_and_annotations.py`
- **Nuevo** `leadmanager/apps/automations/migrations/0006_agent_channel_assignments.py`
- **Nuevo** `leadmanager/apps/automations/migrations/0007_agent_category_choices.py`
- **Nuevo** `leadmanager/apps/automations/migrations/0008_agent_mode.py`
- **Nuevo** `leadmanager/apps/automations/migrations/0009_agent_run_shadow_tracking.py`
- **Nuevo** `leadmanager/apps/automations/migrations/0010_test_conversations.py`
- **Nuevo** `leadmanager/apps/automations/migrations/0011_learning_annotations_observaciones_sugerencias.py`
- **Nuevo** `leadmanager/apps/automations/migrations/0012_learning_sugerencias_label.py`
- **Nuevo** `leadmanager/apps/automations/migrations/0013_conversation_analysis_tracking.py`
- **Nuevo** `leadmanager/apps/automations/migrations/0014_weekly_analysis_batches.py`
- **Nuevo** `leadmanager/apps/automations/services/conversation_analysis.py`
- **Nuevo** `leadmanager/apps/automations/services/agent_execution.py`
- **Nuevo** `leadmanager/apps/automations/services/agent_selection.py`
- **Nuevo** `leadmanager/apps/automations/services/weekly_analysis.py`
- **Nuevo** `leadmanager/apps/automations/test_analyze_conversations.py`
- **Nuevo** `leadmanager/apps/automations/test_conversation_analysis.py`
- **Nuevo** `leadmanager/apps/automations/test_shadow_execution.py`
- **Nuevo** `leadmanager/apps/automations/test_weekly_analysis.py`
- **Nuevo** `leadmanager/apps/pages/static/imgs/analizer.jpg`
- **Nuevo** `leadmanager/apps/pages/static/imgs/prospector.png`
- **Nuevo** `leadmanager/apps/prospects/migrations/0012_conversationmodel_traffic_origin_and_more.py`
- **Nuevo** `leadmanager/apps/prospects/migrations/0013_conversationmodel_web_message_count_and_more.py`
- **Nuevo** `leadmanager/apps/prospects/static/agents/agent-test.js`
- **Nuevo** `leadmanager/apps/prospects/templates/prospects/_channel_conflict_modal.html`
- **Nuevo** `leadmanager/apps/prospects/templates/prospects/agent_analyses.html`
- **Nuevo** `leadmanager/apps/prospects/templates/prospects/agent_channels.html`
- **Nuevo** `leadmanager/apps/prospects/templates/prospects/agent_create.html`
- **Nuevo** `leadmanager/apps/prospects/templates/prospects/agent_delete.html`
- **Nuevo** `leadmanager/apps/prospects/test_agent_management.py`
- **Nuevo** `leadmanager/apps/prospects/test_conversations.py`
