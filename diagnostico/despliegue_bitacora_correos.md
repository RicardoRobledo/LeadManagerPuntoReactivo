# Bitácora de correos

La opción **Correos** aparece como pantalla independiente debajo de **Conversaciones**.
Registra respuestas, recordatorios de cualquier canal y resúmenes diarios. Cada intento
tiene destinatario, asunto, tipo, canal, conversación cuando aplica, Message-ID, tarea,
fecha y resultado. Los reintentos conservan los registros anteriores.

- **En proceso:** comenzó y aún no tiene resultado guardado. Si el proceso se interrumpe,
  puede permanecer así y requerir revisión en los logs; no debe asumirse que falló.
- **Aceptado por SMTP:** el servidor de envío aceptó el mensaje. No confirma la llegada
  a la bandeja del destinatario. Un fallo de cierre después de la aceptación no genera
  un falso fallo que provoque un reenvío.
- **Fallido:** ocurrió un error de preparación, conexión, autenticación o envío.
- **Omitido:** no había un destinatario válido.

La bitácora comienza con esta actualización; no reconstruye envíos históricos sin evidencia.
No almacena el cuerpo del correo. La contraseña SMTP se oculta en el detalle guardado.
Si no se puede crear el registro inicial, no se inicia SMTP y se deja el error en logs.
Si falla el guardado final después de la aceptación, se registra el problema en logs
y se conserva el resultado de SMTP para evitar un reenvío por ese motivo.

## Archivos de producción

Actualizar juntos, conservando sus rutas:

```text
leadmanager/apps/automations/models.py
leadmanager/apps/automations/migrations/0009_emaildeliverylogmodel.py
leadmanager/apps/automations/services/email_delivery.py
leadmanager/apps/automations/services/imap_service.py
leadmanager/apps/automations/services/conversation_reminders.py
leadmanager/apps/automations/services/daily_summary.py
leadmanager/apps/automations/agents/prospector_agent/prospector_steps.py
leadmanager/apps/automations/tasks.py
leadmanager/apps/automations/views.py
leadmanager/apps/automations/urls.py
leadmanager/apps/automations/templates/automations/_email_status.html
leadmanager/apps/automations/templates/automations/email_deliveries.html
leadmanager/apps/automations/templates/automations/email_delivery_detail.html
leadmanager/apps/prospects/views.py
leadmanager/apps/prospects/templates/prospects/sidebar.html
leadmanager/apps/prospects/templates/prospects/conversations.html
```

`conversation_reminders.py` incluye el cambio previo para obtener el correo del prospecto
independientemente del canal. Debe existir al copiar la versión actual de `tasks.py`.
No hace falta cambiar `config/settings/base.py` para esta bitácora ni incorporar security.
Conservar la programación que ya funciona en el VPS. No subir `.pyc`, `__pycache__`,
`celerybeat-schedule-*`, registros de diagnóstico ni la base local.

## En el VPS

Durante el despliegue, coordinar la recarga del servidor web y del worker con la migración:
la tabla debe existir antes de que los procesos comiencen a utilizar el código nuevo.
Los comandos siguientes se ejecutan en la terminal Linux del VPS, con su entorno activado:

```bash
cd /var/www/LeadManagerNueva
python manage.py check
python manage.py migrate automations 0009 --plan
python manage.py migrate automations 0009 --noinput
python manage.py showmigrations automations
```

Revisar el plan antes de aplicar. Debe crear `EmailDeliveryLogModel`; si faltan migraciones
anteriores aparecerán también y deben revisarse. La migración 0009 solo agrega la tabla
de bitácora y sus índices. En el proyecto local ya se aplicó correctamente.

Recargar el servidor web y el worker con sus nombres reales de servicio. Para identificarlos:

```bash
systemctl list-units --type=service --all | grep -Ei 'leadmanager|celery|gunicorn'
```

Abrir **Correos** en el menú. Al principio puede estar vacío. Los siguientes envíos quedarán
registrados cuando los procesos estén usando el código actualizado. Consultar el detalle
para revisar errores y otros intentos relacionados; el enlace de conversación abre su modal.
Esta actualización no ejecuta envíos de prueba ni cambia el destinatario del resumen diario.

## Validación local sin envíos

En PowerShell:

```powershell
.\venv\Scripts\python.exe -B diagnostico/verificar_integracion.py leadmanager.apps.automations leadmanager.apps.prospects.test_state_consistency diagnostico.reproducir_entrega_correos diagnostico.test_preparar_recordatorios
node --test diagnostico/conversation_email_link.test.cjs
```

La suite Python usa SQLite en memoria, bloquea la red y simula SMTP. No toca PostgreSQL
ni envía correos reales. La prueba JavaScript verifica la apertura de conversaciones
desde Correos, incluso cuando no hay una fila visible con ese identificador.

## Historial y vista previa de las pruebas

El listado de Conversaciones muestra el texto del último mensaje visible. Extrae
la respuesta de los objetos o textos JSON y omite las actualizaciones internas
`crm_update`, conservándolas para obtener el contacto y el semáforo.
Los recordatorios con el prefijo `Envío de recordatorio:` se muestran como burbuja
verde con reloj al abrir el historial de la conversación.

El script `diagnostico/probar_recordatorios.py` revierte los cambios del historial
al terminar, incluso en el modo de envío real. En ese modo conserva los intentos
SMTP en Correos, pero no deja mensajes nuevos ni fases de recordatorio en las
conversaciones de prueba. Por eso esos envíos no generan una burbuja permanente
en su historial. La tarea de producción sí guarda el mensaje tras aceptar SMTP
el envío.
