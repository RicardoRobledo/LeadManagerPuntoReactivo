"""Envía una prueba del resumen a yupaglu sin cambiar la configuración de producción."""

import json
import logging
import os
import sys
from pathlib import Path
from unittest.mock import patch


def main():
    raiz = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(raiz))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.base')
    import django
    django.setup()

    from django.conf import settings
    from django.db import connection
    from django.test.utils import override_settings
    from leadmanager.apps.automations.services import imap_service
    from leadmanager.apps.automations.tasks import resumir_final_dia

    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s: %(message)s')
    connection.settings_dict.setdefault('OPTIONS', {})['connect_timeout'] = 10
    destino = 'yupaglu@gmail.com'
    destino_produccion = settings.DAILY_SUMMARY_RECIPIENT
    enviar_original = imap_service.send_email
    intentos = []

    def enviar_prueba(*, to, subject, **kwargs):
        if to != destino or intentos:
            raise RuntimeError('La prueba solo permite un envío al destinatario de prueba.')
        subject = '[Prueba - tarjetas] ' + subject
        intentos.append(subject)
        return enviar_original(to=to, subject=subject, **kwargs)

    # El reemplazo solo agrega el prefijo al asunto; SMTP y el análisis son reales.
    with override_settings(DAILY_SUMMARY_RECIPIENT=destino), patch.object(
        imap_service, 'send_email', side_effect=enviar_prueba,
    ):
        resultado = resumir_final_dia.run()

    if settings.DAILY_SUMMARY_RECIPIENT != destino_produccion:
        raise RuntimeError('La configuración del destinatario no se restauró.')
    print(json.dumps({
        'enviado': resultado['correo_enviado'], 'destinatario': resultado['destinatario'],
        'fecha': resultado['fecha'], 'conversaciones': resultado['total_conversaciones'],
        'asunto': intentos[0], 'destinatario_produccion': destino_produccion,
    }, ensure_ascii=False))


if __name__ == '__main__':
    main()
