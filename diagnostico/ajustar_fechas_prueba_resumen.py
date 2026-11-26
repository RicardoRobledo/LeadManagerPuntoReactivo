"""Aplica o restaura las tres fechas respaldadas para probar el resumen diario."""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--restaurar', action='store_true')
    args = parser.parse_args()

    raiz = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(raiz))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.base')
    import django
    django.setup()

    from django.db import connection, transaction
    from django.utils import timezone
    from leadmanager.apps.prospects.models import ConversationModel
    from leadmanager.apps.automations.services.daily_summary import build_daily_report

    logging.basicConfig(level=logging.INFO, format='%(message)s')
    connection.settings_dict.setdefault('OPTIONS', {})['connect_timeout'] = 10
    ruta = raiz / 'diagnostico' / 'resumir_final_dia_fechas_originales_2026-10-22.json'
    respaldo = json.loads(ruta.read_text(encoding='utf-8'))
    if not args.restaurar:
        ayer = timezone.now().astimezone(ZoneInfo(respaldo['zona_horaria'])).date() - timedelta(days=1)
        if ayer.isoformat() != respaldo['fecha_prueba']:
            raise RuntimeError('El respaldo ya no corresponde a ayer; no se modificaron datos.')

    origen, destino = 'created_at_original', 'created_at_prueba'
    if args.restaurar:
        origen, destino = destino, origen

    with transaction.atomic():
        for fila in respaldo['conversaciones']:
            cambiadas = ConversationModel.objects.filter(
                pk=fila['id'], created_at=datetime.fromisoformat(fila[origen]),
            ).update(created_at=datetime.fromisoformat(fila[destino]))
            if cambiadas != 1:
                raise RuntimeError(
                    'La conversacion {} cambio; se revierte la transaccion.'.format(fila['id'])
                )

    print('Fechas {}: {}'.format(
        'restauradas' if args.restaurar else 'actualizadas',
        [fila['id'] for fila in respaldo['conversaciones']],
    ))
    # Verificar el filtro sin llamar al LLM ni enviar correos al ajustar fechas.
    zona = ZoneInfo(respaldo['zona_horaria'])
    limite = timezone.now().astimezone(zona).replace(hour=0, minute=0, second=0, microsecond=0)
    inicio = limite - timedelta(days=1)
    conversaciones = ConversationModel.objects.filter(
        created_at__gte=inicio, created_at__lt=limite,
    ).order_by('created_at', 'id')
    informe = build_daily_report(conversaciones, inicio, limite - timedelta(microseconds=1), zona)
    print('Conversaciones de ayer:', [fila['id'] for fila in informe['conversaciones']])


if __name__ == '__main__':
    main()
