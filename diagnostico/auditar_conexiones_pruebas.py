"""Identifica sesiones de una base de pruebas PostgreSQL sin cerrarlas ni alterar datos."""

import argparse
import json
import os
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', default='test_postgres')
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.base')

    import django
    django.setup()
    from django.db import connection, transaction

    if connection.vendor != 'postgresql':
        parser.error('La base configurada no es PostgreSQL.')
    connection.settings_dict.setdefault('OPTIONS', {})['connect_timeout'] = 10

    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION READ ONLY')
            cursor.execute('SET LOCAL statement_timeout = 10000')
            cursor.execute('SELECT current_database(), inet_server_addr()::text, inet_server_port()')
            database, server, port = cursor.fetchone()
            cursor.execute('''
                SELECT pid, application_name, client_addr::text, client_port, state,
                       backend_start, state_change, xact_start, query_start,
                       wait_event_type, wait_event,
                       split_part(ltrim(query), ' ', 1) AS sql_command
                FROM pg_stat_activity
                WHERE datname = %s
                ORDER BY backend_start
            ''', [args.database])
            columns = [column[0] for column in cursor.description]
            report = {
                'solo_lectura': True,
                'base_de_auditoria': database,
                'servidor': server,
                'puerto': port,
                'base_consultada': args.database,
                'sesiones': [dict(zip(columns, row)) for row in cursor.fetchall()],
            }
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    finally:
        # Se cierra únicamente la conexión abierta por este script.
        connection.close()


if __name__ == '__main__':
    main()
