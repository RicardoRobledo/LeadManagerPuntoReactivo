# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Los comentarios y docstrings del repo están en español. Conserva ese idioma al escribir código nuevo.

## Comandos

El intérprete vive en `venv\Scripts\python.exe` (Windows). Todos los comandos se corren desde la raíz del repo.

```bash
venv/Scripts/python.exe manage.py runserver 127.0.0.1:8000
```

```bash
venv/Scripts/python.exe manage.py test
```

Una sola clase o un solo test:

```bash
venv/Scripts/python.exe manage.py test leadmanager.apps.prospects.tests.ConversationsViewTests.test_escalated_lee_el_contacto_dentro_de_crm_update
```

Los tests viven en `tests.py` por app (`prospects`, `api.prospects`, `documents`); no hay pytest ni linter configurados.

`build.sh` es el build de Render: instala requirements y corre `collectstatic --no-input`.

### Variables de entorno

Se cargan desde `.env` en la raíz (único archivo en `.gitignore`). `SECRET_KEY` lanza excepción si falta.
Requeridas: `SECRET_KEY`, `ENVIRONMENT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`, `OPENAI_API_KEY`.

`ENVIRONMENT` (`DEVELOPMENT` | `PRODUCTION`, por omisión `PRODUCTION`) decide qué módulo importa `config/settings/base.py` en su línea 37. De ahí salen `DEBUG`, `ALLOWED_HOSTS` y `DATABASES`. **Ambos entornos usan PostgreSQL** — no hay fallback a SQLite, así que sin base no arranca ni corren los tests.

## Arquitectura

### Dos mundos de base de datos

Es lo primero que hay que entender antes de tocar modelos. El esquema de negocio **no lo controla Django**: vive en Supabase y lo creó n8n.

- **Tablas externas (`managed = False`)** en `leadmanager/apps/prospects/models.py`: `prospects`, `conversations`, `sellers`, `conversation_escalations`, `n8n_chat_histories`. Django solo las lee y escribe; `makemigrations` no genera DDL para ellas.
- **Tablas gestionadas por Django**: `ChatConversationEndedModel`, `MessengerReminderModel`, `NotQualifiedCategoryModel` (misma app), toda la app `documents` y el auth de Django.

`README.md` **no es un readme**: es el script SQL que crea y parcha el esquema externo — tablas, FKs, el trigger `trg_ensure_conversation` (que inserta la fila en `conversations` automáticamente al insertar en `n8n_chat_histories`) y los índices GIN sobre `ai_data`. Si cambia una tabla externa, se cambia ahí y a mano en Supabase, no con una migración.

`leadmanager/test_runner.py` (`ManagedModelTestRunner`) existe por esto: marca los modelos como gestionados y anula las migraciones de sus apps para que Django cree las tablas en la base de pruebas. Sin él, cualquier test que toque `prospects` falla con "relation does not exist".

### Herencia de n8n

El chatbot corre fuera de Django. n8n escribe los mensajes en `n8n_chat_histories` y el análisis de la IA en `conversations.ai_data`; Django los lee para pintar el panel. `documentos/workflow_prospectos_no_escalados_ayer.json` es un export de uno de esos workflows.

**El JSON de `ai_data` no tiene forma fija.** Puede traer los campos en la raíz o anidados bajo `crm_update`, y a veces llega guardado como string JSON en vez de objeto. Por eso `prospects/views.py` tiene helpers defensivos (`extract_contact`, `extract_escalation_data`) que parsean, mezclan `crm_update` como base y dejan que la raíz gane en caso de repetirse. Cualquier lectura nueva de `ai_data` o de `conversation_escalations.conversation_content` debe pasar por esos helpers, no asumir estructura.

### Árboles de URLs

| Prefijo | Contenido | Auth |
|---|---|---|
| `/` | `apps.pages` — landing y `handler404` | pública |
| `/panel-control/` | admin de Django | staff |
| `/authentication/` | login/logout | pública |
| `/leadmanager-admin/` | panel interno: vistas HTML de `prospects` + endpoints JSON de `documents/` | `LoginRequiredMixin` / `@login_required` |
| `/api/prospects/` | DRF para consumo externo (n8n) | `TokenAuthentication` + `IsAuthenticated` global |

El panel y la API son dos capas distintas con estilos distintos: el panel usa vistas basadas en clase de Django que devuelven `render()` o `JsonResponse`; la API usa funciones con `@api_view` de DRF.

### Base de conocimiento (`apps/documents`)

`knowledge_structure.py` es la **única fuente de verdad** de la estructura: define las secciones (`TopicModel`) y los documentos fijos que cuelgan de cada una (`DocumentModel`), con sus descripciones, iconos y clases de color. Tanto las migraciones de datos como `KnowledgeView` leen de esa constante.

Para agregar una sección o un documento: editar la lista y crear una migración de datos nueva que llame a `sync_knowledge_structure(TopicModel, DocumentModel)`. Ojo con que las migraciones existentes **importan el módulo vivo**, no una copia congelada — cambiar `KNOWLEDGE_STRUCTURE` cambia lo que siembran las migraciones viejas al correr desde cero.

Cada sección y cada documento tienen su propio `is_active`, y un documento solo llega a la IA si ambos están encendidos. Por eso ninguna descripción puede enumerar lo que contienen sus vecinos: prometería algo que quizá esté apagado.

`MAX_TEXT_LENGTH = 57618` no es un límite de Postgres (que no tiene) sino del prompt: lo que se guarda ahí viaja dentro del contexto de la IA. `pdf.py` extrae el texto del PDF subido y **descarta el archivo**; solo se persiste el texto.

### Chatbot analyzer (asíncrono)

`ChatbotAnalyzerView` en `prospects/views.py` es una vista async completa: `dispatch`, `get` y `post` son `async def`, y todo acceso al ORM va envuelto en `sync_to_async`. Llama a `create_response` en `prospects/chatbot.py`, que usa `AsyncOpenAI` con la Responses API (`gpt-5-mini`, `reasoning.effort='low'`).

El system prompt se carga **una sola vez** en `ProspectsConfig.ready()` desde `prospects/prompts/system_prompt.txt` hacia el atributo de clase `PromptHandler.system_prompt`. Es una ruta relativa, así que el proceso debe arrancar desde la raíz del repo o el `ready()` truena. El prompt es una `string.Template` con un placeholder `$data` que `create_response` rellena con las estadísticas de `NotQualifiedCategoryModel` de los últimos 30 días.

### Máquina de estados de recordatorios

`PHASE_SEQUENCE` y `PHASE_DELAYS` en `api/prospects/views.py` definen el avance `FIRST → SECOND → THIRD → FOURTH` (1 día para el primero, 3 para los demás). `MessengerReminderModel` es el único canal implementado: `CHANNEL_CONFIG` tiene el de SMS comentado, y todos los `create_sms_reminder` son cascarones que devuelven 201 sin escribir nada.

## Trampas conocidas

- **Funciones duplicadas en `leadmanager/apps/api/prospects/views.py`**: `create_sms_reminder` está definida en las líneas 301 y 429, `create_messenger_reminder` en 331 y 448, `create_email_reminder` en 366 y 478. Gana la segunda definición. Editar la primera no tiene efecto alguno.
- **Vistas importadas pero no ruteadas**: `urls.py` de `api/prospects` importa `create_message`, `close_conversation`, `get_conversation_state`, `email_exists`, `get_conversation_history`, `create_form_conversation`, `close_form_conversation`, `get_next_owner`, `mark_email_as_quoted` y `mark_messenger_as_quoted`, pero no registra ninguna. Existen en el código y no son alcanzables por HTTP.
- **`.gitignore` solo ignora `/.env`**: los `__pycache__/*.pyc`, `staticfiles/` y `venv/` están versionados, así que `git status` y los diffs salen ruidosos con binarios. No confundir esos `.pyc` modificados con cambios reales.
- `from cmath import phase` al inicio de `api/prospects/views.py` es un import accidental que no se usa.
- Las migraciones de `prospects` y `documents` están *squashed* (`0001_squashed_...`), y las de `documents` incluyen migraciones de datos posteriores (`0002`–`0004`, `0015`).
