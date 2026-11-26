# Investigación de respuestas por correo que no llegan

Fecha de revisión: 22 de octubre de 2026.

## Estado después de integrar GitHub

`origin/main` en `e39ace4` corrige los dos fallos reproducidos: un error del LLM
al resumir permite continuar sin compactar y un resultado SMTP `False` hace
fallar el paso, sin registrar envío correcto ni marcar el correo como procesado.
También limita el PDF adjunto y usa el dominio del remitente para Message-ID/HELO.
Esto no confirma la entrega final de correos concretos del VPS.

La reproducción se actualizó para verificar el comportamiento corregido.
La investigación original queda documentada abajo; el respaldo y los stashes
conservan las versiones anteriores de los archivos.

## Hallazgos previos a la actualización (commit 10bda3e)

1. `CHAT_HISTORY_LIMIT` dispara un resumen; no cancela el envío por sí mismo.
   `generate_history_summary_step` está antes de los pasos de Odoo y del envío.
   Si la llamada de resumen, su respuesta estructurada o el guardado producen
   una excepción, `Agent._run_step` marca la ejecución fallida y detiene el flujo.
   En ese caso el paso de envío no se ejecuta.

2. El umbral se compara con `len(ctx['history'])`, que contiene mensajes del
   cliente, respuestas del asistente y mensajes internos de búsqueda/contexto.
   No representa un número fijo de correos. No puede atribuirse el fallo al
   tercer o cuarto correo sin el límite efectivo y los registros de ese caso.

3. `send_email()` devuelve `False` cuando falla SMTP. Antes de la actualización,
   `send_response_to_prospect_step` ignora ese resultado tanto para `email` como
   para `form`. Aun así registra «Correo enviado» y el run queda en éxito.
   En el canal correo también marca el mensaje entrante como `processed`.
   Esto permite un falso éxito sin envío aceptado por SMTP y evita que se
   active el tratamiento de errores de la tarea.

4. La respuesta se guarda en `ChatHistoryModel` antes de resumir y enviar.
   Ver una respuesta en Conversations no demuestra que se haya enviado.

5. Los errores reales del pipeline usan colas de reintentos en base de datos.
   Si sus tareas de recuperación no están programadas o no se ejecutan,
   los mensajes que quedaron pendientes no se recuperan automáticamente.

## Reproducción controlada previa a la actualización

Se ejecutaron cinco comprobaciones en SQLite en memoria con LLM y SMTP
simulados. Todas confirmaron el comportamiento anterior:

| Caso | Se llama al envío | Resultado del run |
| --- | --- | --- |
| Historial menor al límite | Sí | Éxito |
| Límite alcanzado y resumen correcto | Sí, con la respuesta original | Éxito |
| Límite alcanzado y resumen falla | No | Fallido en `generate_history_summary_step` |
| SMTP devuelve `False` en correo | Sí, devuelve fallo | Éxito incorrecto y mensaje `processed` |
| SMTP devuelve `False` en formulario | Sí, devuelve fallo | Éxito incorrecto |

La versión actual de `diagnostico/reproducir_entrega_correos.py` comprueba
que el fallo del resumen continúa hasta SMTP y que el rechazo SMTP falla
sin producir un falso éxito. Puede ejecutarse sin PostgreSQL ni red con:

```powershell
.\venv\Scripts\python.exe -B diagnostico/verificar_integracion.py
```

## Consulta de los registros disponibles

Se consultó de solo lectura la base configurada en este entorno, para el periodo
del 8 al 22 de octubre. El límite efectivo fue 15 y los reintentos de correo y
formulario/web no estaban programados en esa configuración local.

No se encontraron ejecuciones de correo/formulario en el periodo consultado.
Esta consulta no permite confirmar la causa de los casos del VPS: aquí se
observó una conversación de canal correo, mientras el diagnóstico del VPS
mostró 13. Hay que consultar los registros del servidor correspondiente.

## Auditoría en el VPS

Subir únicamente `diagnostico/auditar_entrega_correos.py` conservando su ruta.
Desde la raíz del proyecto y con el entorno virtual activo:

```bash
python diagnostico/auditar_entrega_correos.py --days 14
```

Para acotar a un caso, reemplazar la dirección del ejemplo:

```bash
python diagnostico/auditar_entrega_correos.py --days 14 --email direccion@dominio.com
```

El script no ejecuta agentes, no llama al LLM, no envía correos ni altera filas.
En PostgreSQL sus consultas se realizan en una transacción de solo lectura.
El informe evita cuerpos e historiales completos y oculta correos/teléfonos
en los detalles de error.

Revisar `pasos_fallidos` y `fallos_recientes` para distinguir fallos de resumen,
generación de respuesta u Odoo, además de `colas` para comprobar reintentos.
Los fallos SMTP que la versión anterior ignoraba pueden no figurar como pasos fallidos:
se buscan también en el journal del worker:

```bash
sudo journalctl --no-pager | grep -E 'generate_history_summary_step|Error sending email to|Email sent to|NO se reintenta'
```

La aceptación SMTP y la entrega en la bandeja del destinatario son resultados
distintos. Si SMTP aceptó un correo concreto, hace falta revisar su entrega con
el proveedor de correo y el destinatario para completar esa parte del análisis.

## Correcciones que se desprenden de la investigación

- Comprobar el resultado de `send_email` antes de registrar éxito o `processed`.
- Separar la compactación del historial del envío para que su fallo no impida
  entregar una respuesta ya generada, conservando el historial si el resumen falla.
- Verificar en el VPS que los barridos de recuperación necesarios estén activos.

No se modificó el flujo de producción durante esta investigación ni se
confirmó todavía qué fallo afectó a los correos citados en la conversación.
