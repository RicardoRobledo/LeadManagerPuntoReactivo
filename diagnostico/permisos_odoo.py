"""
Revisa qué permisos tiene en Odoo el usuario con el que se conecta la
integración (``ODOO_UID`` del .env), sobre los modelos y operaciones que el
agente usa de verdad.

No escribe nada: solo pregunta. Correrlo es seguro en producción.

    venv/Scripts/python.exe test.py          (Windows)
    venv/bin/python test.py                  (servidor)

Revisa el Odoo al que apunte el .env de donde se corra. El del servidor y el
local pueden ser instancias distintas, así que para verificar producción hay
que correrlo EN el servidor.

Todo vive dentro de ``if __name__ == "__main__"`` a propósito: este archivo
empieza con "test", así que ``manage.py test`` lo recoge en su descubrimiento.
Al importarlo no debe pasar absolutamente nada, o las pruebas terminarían
pegándole a Odoo.

Sin acentos en lo que se imprime: la consola de Windows usa otra codificación
y los deja ilegibles.
"""

import os
import sys

import django


# El script vive en una subcarpeta: sin esto Python no encuentra config.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# Lo que el agente necesita poder hacer, sacado de odoo_service.py.
OPERACIONES = [
    ('res.partner', 'read', 'buscar contactos y empresas'),
    ('res.partner', 'create', 'crear el contacto o la empresa'),
    ('res.partner', 'write', 'actualizar un contacto existente'),
    ('crm.lead', 'read', 'buscar el lead activo del prospecto'),
    ('crm.lead', 'create', 'crear el lead u oportunidad'),
    ('crm.lead', 'write', 'actualizar el lead existente'),
    ('crm.business.unit', 'read', 'resolver la unidad de negocio'),
]


def permiso(call, model, operation):
    """
    Devuelve True, False o None (no se pudo determinar).

    Odoo renombró este método: ``has_access`` en las versiones nuevas,
    ``check_access_rights`` en las anteriores. Se intentan los dos para que
    el script sirva sin importar contra cuál Odoo se corra.
    """
    try:
        return bool(call(model, 'has_access', [operation]))
    except RuntimeError:
        pass

    try:
        return bool(call(model, 'check_access_rights', [operation],
                         {'raise_exception': False}))
    except RuntimeError:
        return None


if __name__ == '__main__':
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.base')
    django.setup()

    from django.conf import settings

    from leadmanager.apps.automations.services.odoo_service import call

    print()
    print('Odoo:    %s' % settings.ODOO_URL)
    print('Base:    %s' % settings.ODOO_DB)
    print('Usuario: uid %s' % settings.ODOO_UID)
    print()

    # Primero, que las credenciales sirvan. Si no, todo lo de abajo devuelve
    # errores que parecen falta de permisos y no lo son.
    try:
        usuario = call('res.users', 'read',
                       [[int(settings.ODOO_UID)], ['name', 'login']])
        print('Conectado como: %s (%s)' % (usuario[0].get('name'),
                                           usuario[0].get('login')))
        print()
    except Exception as exc:
        detalle = str(exc)
        print('NO SE PUDO AUTENTICAR.')
        print()

        if 'AccessDenied' in detalle:
            print('Odoo respondio "Access Denied": el ODOO_UID y el ODOO_API_KEY')
            print('del .env no corresponden a un usuario valido en esta base.')
            print('Suele pasar cuando la instancia se recreo y cambio la llave,')
            print('o cuando el .env apunta a otra base de la que uno cree.')
        else:
            print(detalle)

        print()
        print('Sin resolver esto no tiene caso revisar permisos.')
        raise SystemExit(1)

    dados = []
    faltantes = []
    sin_determinar = []

    for model, operation, para_que in OPERACIONES:
        tiene = permiso(call, model, operation)

        if tiene is True:
            estado, destino = 'OK        ', dados
        elif tiene is False:
            estado, destino = 'FALTA     ', faltantes
        else:
            estado, destino = 'SE IGNORA ', sin_determinar

        destino.append((model, operation, para_que))
        print('%s %-20s %-7s  %s' % (estado, model, operation, para_que))

    print()

    if faltantes:
        print('Faltan %d permiso(s). Sin ellos el agente falla al:' % len(faltantes))
        for model, operation, para_que in faltantes:
            print('  - %s (%s.%s)' % (para_que, model, operation))
        print()
        print('Se dan en Odoo: Ajustes -> Usuarios -> ese usuario -> Ventas/CRM.')
    elif sin_determinar:
        print('No se pudo determinar %d permiso(s): este Odoo no expone ni' % len(sin_determinar))
        print('has_access ni check_access_rights por RPC. Esto NO significa que')
        print('esten dados; hay que revisarlos a mano en la ficha del usuario.')
    else:
        print('Todos los permisos que usa el agente estan dados.')

    print()
