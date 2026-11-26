"""Lectura y alta de contactos en Google Sheets, independiente del agente.

Configuración: GOOGLE_SHEETS_ID y GOOGLE_SHEETS_SERVICE_ACCOUNT (JSON de
credenciales o ruta a su archivo). La hoja debe estar compartida como editor
con la cuenta de servicio y tener encabezados en la primera fila, incluyendo
Email, Correo, Correo electrónico o Correo empresarial. Se usa la primera
pestaña por defecto.

Los datos se reciben por nombre de columna, sin depender de su posición::

    record, created = get_or_create_record(
        'ana@example.com', {'Nombre': 'Ana', 'Teléfono': '+525512345678'},
    )

Para registrar un lead en la pestaña de su unidad de negocio::

    record, created = register_google_sheet_lead(
        'ODOO', conversation_id=178, email='ana@example.com', name='Ana',
        phone='+525512345678', enterprise='Empresa de Ana',
    )

Este registro escribe FECHA (día/mes/año), NOMBRE COMPLETO, CORREO EMPRESARIAL,
TELÉFONO, EMPRESA e ID Conversación. Solo se lee y se escribe la pestaña de la
unidad de negocio recibida, que debe ser una de BUSINESS_UNIT_SHEETS; las demás
pestañas del documento (históricos, resúmenes, hojas de trabajo) nunca se
consultan. El ID se busca únicamente en esa pestaña: si ya existe, se conserva
la fila sin modificarla. El mismo correo puede aparecer en varias
conversaciones. Las filas anteriores sin ID no se atribuyen a una conversación
por su correo.

Cambiar las dos variables permite usar otro documento/cuenta con columnas
compatibles. No hay envíos automáticos ni modificaciones de registros existentes.
La comprobación y el alta son operaciones separadas: la integración con workers
deberá serializar los envíos y reconciliar timeouts para evitar duplicados.

Las funciones públicas reutilizan un cliente por proceso e hilo. Conserva la
sesión HTTP y sus credenciales, cuyos tokens renueva AuthorizedSession. Un cambio
de documento o cuenta reconstruye el cliente. Las lecturas de Sheets son frescas.
"""

import atexit
import hashlib
import json
import os
import re
import threading
import unicodedata
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.validators import validate_email
from django.utils import timezone
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import AuthorizedSession
from google.oauth2.service_account import Credentials
from requests.exceptions import RequestException

from ..agent_configuration import DEFAULT_BUSINESS_UNIT_NAME


SCOPES = ('https://www.googleapis.com/auth/spreadsheets',)
API_URL = 'https://sheets.googleapis.com/v4/spreadsheets'
EMAIL_HEADERS = {'email', 'correo', 'correoelectronico', 'correoempresarial', 'emailaddress'}
CONVERSATION_ID_HEADER = 'ID Conversación'
LEAD_COLUMNS = (
    'FECHA', 'NOMBRE COMPLETO', 'CORREO EMPRESARIAL', 'TELÉFONO', 'EMPRESA',
    CONVERSATION_ID_HEADER,
)
# Equivalencias de nombres anteriores del documento, no un catálogo de unidades.
UNIT_SHEET_ALIASES = {'desarolloweb': 'desarrolloweb', 'desarroloweb': 'desarrolloweb'}
_client_state = threading.local()


class GoogleSheetsError(RuntimeError):
    """Falló una operación o la estructura de la hoja no es compatible."""


def _name_key(value):
    normalized = unicodedata.normalize('NFKD', str(value).casefold())
    return re.sub(r'[^a-z0-9]', '', normalized)


def _column_key(value):
    key = _name_key(value)
    return 'email' if key in EMAIL_HEADERS else key


def _unit_key(value):
    key = _name_key(value)
    return UNIT_SHEET_ALIASES.get(key, key)


# Únicas pestañas de leads, una por unidad de negocio. Cualquier otra pestaña
# del documento se ignora por completo.
BUSINESS_UNIT_SHEETS = (
    'KIOSKOS DIGITALES', 'ODOO', 'APPS', 'BPM', 'CIBERSEGURIDAD', 'DESARROLLO WEB',
    'DIGITAL SIGNAGE', 'ECOMMERCE', 'INTELIGENCIA ARTIFICIAL', 'MARKETING DIGITAL',
    DEFAULT_BUSINESS_UNIT_NAME,
)
_BUSINESS_UNIT_KEYS = frozenset(_unit_key(name) for name in BUSINESS_UNIT_SHEETS)


def _email(value):
    if not isinstance(value, str):
        raise ValueError('Se necesita un correo electrónico válido.')
    value = value.strip().casefold()
    try:
        validate_email(value)
    except ValidationError:
        raise ValueError('Se necesita un correo electrónico válido.') from None
    return value


def _conversation_id(value):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError('Se necesita un ID de conversación entero y positivo.')
    value = str(value).strip()
    if not re.fullmatch(r'[0-9]+', value) or int(value) <= 0:
        raise ValueError('Se necesita un ID de conversación entero y positivo.')
    return str(int(value))


def _stored_conversation_id(value):
    # Sheets puede devolver una celda numérica como entero o como float.
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    try:
        return _conversation_id(value)
    except ValueError:
        return None


def _credentials():
    account = getattr(settings, 'GOOGLE_SHEETS_SERVICE_ACCOUNT', None)
    if not account:
        raise ImproperlyConfigured('Falta GOOGLE_SHEETS_SERVICE_ACCOUNT.')
    try:
        if isinstance(account, dict):
            return Credentials.from_service_account_info(account, scopes=SCOPES)
        account = account.strip()
        if account.startswith('{'):
            return Credentials.from_service_account_info(json.loads(account), scopes=SCOPES)
        path = Path(account)
        if not path.is_absolute():
            path = Path(settings.BASE_DIR) / path
        return Credentials.from_service_account_file(str(path), scopes=SCOPES)
    except (OSError, ValueError, TypeError, AttributeError, GoogleAuthError):
        # No incluir el JSON, la clave privada ni el error del parser.
        raise ImproperlyConfigured(
            'GOOGLE_SHEETS_SERVICE_ACCOUNT debe contener credenciales JSON '
            'válidas o la ruta a su archivo.'
        ) from None


class GoogleSheetsService:
    def __init__(self, sheet_name=None):
        spreadsheet_id = (getattr(settings, 'GOOGLE_SHEETS_ID', None) or '').strip()
        if not spreadsheet_id:
            raise ImproperlyConfigured('Falta GOOGLE_SHEETS_ID.')
        self._url = f'{API_URL}/{quote(spreadsheet_id, safe="")}'
        self._sheet_name = sheet_name
        self._session = AuthorizedSession(_credentials())
        self._closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        if not self._closed:
            self._session.close()
            self._closed = True

    def _request(self, method, path='', **kwargs):
        try:
            response = self._session.request(
                method, f'{self._url}{path}', timeout=30, **kwargs,
            )
            response.raise_for_status()
            result = response.json()
        except (RequestException, GoogleAuthError, ValueError) as exc:
            status = getattr(getattr(exc, 'response', None), 'status_code', None)
            detail = f'HTTP {status}' if status else 'conexión o autenticación'
            raise GoogleSheetsError(
                f'Falló Google Sheets ({detail}). Revisa el ID, las credenciales '
                'y el permiso de acceso al documento.'
            ) from None
        if not isinstance(result, dict):
            raise GoogleSheetsError('Google Sheets devolvió una respuesta inválida.')
        return result

    def _sheet_properties(self):
        document = self._request('GET', params={'fields': 'sheets.properties'})
        return sorted(
            (sheet['properties'] for sheet in document.get('sheets', [])
             if sheet.get('properties', {}).get('sheetType', 'GRID') == 'GRID'),
            key=lambda sheet: sheet.get('index', 0),
        )

    def _table(self, sheet_name=None):
        sheets = self._sheet_properties()
        selected_sheet = self._sheet_name if sheet_name is None else sheet_name
        if selected_sheet is not None:
            sheets = [sheet for sheet in sheets if sheet['title'] == selected_sheet]
        if not sheets:
            raise GoogleSheetsError('No se encontró una pestaña de celdas compatible.')
        return self._read_table(sheets[0]['title'])

    def _read_table(self, title):
        # Citar el título evita confundirlo con un rango con nombre.
        a1_range = "'" + title.replace("'", "''") + "'"
        path = f'/values/{quote(a1_range, safe="")}'
        values = self._request('GET', path, params={
            'majorDimension': 'ROWS', 'valueRenderOption': 'UNFORMATTED_VALUE',
        }).get('values', [])
        if not values:
            raise GoogleSheetsError('La hoja necesita encabezados en la primera fila.')
        headers = [str(value).strip() for value in values[0]]
        if not all(headers):
            raise GoogleSheetsError('La primera fila no debe tener encabezados vacíos entre columnas.')
        keys = [_column_key(header) for header in headers]
        if len(set(keys)) != len(keys) or '' in keys:
            raise GoogleSheetsError('Los encabezados de la hoja son ambiguos o están duplicados.')
        if 'email' not in keys:
            raise GoogleSheetsError(
                'Falta una columna Email, Correo, Correo electrónico o Correo empresarial.'
            )
        return title, path, headers, values[1:]

    @staticmethod
    def _record(title, headers, row, number):
        return {
            'row_number': number,
            'sheet_name': title,
            'values': {header: row[index] if index < len(row) else ''
                       for index, header in enumerate(headers) if header},
        }

    def _find(self, table, email):
        title, _, headers, rows = table
        column = next(index for index, header in enumerate(headers)
                      if header and _column_key(header) == 'email')
        for number, row in enumerate(rows, start=2):
            if column < len(row) and str(row[column]).strip().casefold() == email:
                return self._record(title, headers, row, number)
        return None

    def find_by_email(self, email, *, sheet_name=None):
        """Devuelve la primera fila con ese correo exacto, o None."""
        email = _email(email)
        return self._find(self._table(sheet_name), email)

    def _find_conversation(self, table, conversation_id):
        title, _, headers, rows = table
        key = _column_key(CONVERSATION_ID_HEADER)
        column = next((index for index, header in enumerate(headers)
                       if _column_key(header) == key), None)
        if column is None:
            raise GoogleSheetsError(
                f'Falta la columna {CONVERSATION_ID_HEADER!r} en la pestaña {title!r}.'
            )
        for number, row in enumerate(rows, start=2):
            if column < len(row) and _stored_conversation_id(row[column]) == conversation_id:
                return self._record(title, headers, row, number)
        return None

    def find_by_conversation_id(self, conversation_id, *, sheet_name=None):
        """Devuelve la primera fila de ese ID en la pestaña seleccionada, o None."""
        conversation_id = _conversation_id(conversation_id)
        return self._find_conversation(self._table(sheet_name), conversation_id)

    def _append(self, table, data, *, skip_unset=False, allow_empty_email=False):
        title, path, headers, _ = table
        if not isinstance(data, dict):
            raise ValueError('Los datos deben ser un diccionario por nombre de columna.')
        available = {_column_key(header) for header in headers if header}
        normalized = {}
        for header, value in data.items():
            key = _column_key(header)
            if key not in available:
                raise ValueError(f'No existe la columna {header!r} en la hoja.')
            if key in normalized:
                raise ValueError(f'La columna {header!r} se proporcionó más de una vez.')
            if value is not None and not isinstance(value, (str, int, float, bool)):
                raise ValueError(f'El valor de {header!r} debe ser texto, número o booleano.')
            normalized[key] = '' if value is None else value
        normalized['email'] = (
            '' if allow_empty_email and not normalized.get('email')
            else _email(normalized.get('email'))
        )
        missing_value = None if skip_unset else ''
        row = [normalized.get(_column_key(header), missing_value) for header in headers]
        if skip_unset:
            # null omite la celda; '' la vaciaría. No tocar columnas ajenas
            # al registro común, incluidas ESTATUS, fórmulas y seguimiento.
            while row and row[-1] is None:
                row.pop()
        response = self._request('POST', f'{path}:append', params={
            'valueInputOption': 'RAW', 'insertDataOption': 'INSERT_ROWS',
        }, json={'majorDimension': 'ROWS', 'values': [row]})
        updated_range = response.get('updates', {}).get('updatedRange', '')
        match = re.search(r'![A-Z]+(\d+)(?::[A-Z]+\d+)?$', updated_range)
        if not match:
            raise GoogleSheetsError(
                'Google Sheets no confirmó la fila creada. Consulta el identificador '
                'antes de volver a intentar el alta.'
            )
        return self._record(title, headers, row, int(match.group(1)))

    def create_record(self, data, *, sheet_name=None):
        """Agrega una fila. Para comprobar antes el correo, usar get_or_create_record."""
        return self._append(self._table(sheet_name), data)

    def get_or_create_record(self, email, data=None, *, sheet_name=None):
        """Devuelve (fila, creada); conserva la fila existente sin sobrescribirla.

        No es una operación atómica entre procesos; no reintentar append a ciegas.
        """
        email = _email(email)
        if data is not None and not isinstance(data, dict):
            raise ValueError('Los datos deben ser un diccionario por nombre de columna.')
        data = dict(data or {})
        email_keys = [key for key in data if _column_key(key) == 'email']
        if any(_email(data[key]) != email for key in email_keys):
            raise ValueError('El correo de los datos no coincide con el correo consultado.')
        if not email_keys:
            data['email'] = email
        table = self._table(sheet_name)
        existing = self._find(table, email)
        if existing is not None:
            return existing, False
        return self._append(table, data), True

    def register_google_sheet_lead(self, business_unit=None, *, conversation_id, email,
                                   name='', phone='', enterprise='', received_at=None):
        """Registra una vez por conversación, en la pestaña de su unidad.

        El nombre de la unidad debe corresponder a una pestaña del documento.
        Se toleran mayúsculas, acentos, espacios y guiones bajos. Una unidad
        vacía utiliza NO_DETERMINADO. Un nombre sin pestaña produce un error.
        Si el ID ya existe en otra pestaña, devuelve esa fila sin moverla.
        Otra conversación con el mismo correo sí puede crear una fila nueva.
        El correo puede estar vacío si el canal solo proporciona teléfono.
        """
        conversation_id = _conversation_id(conversation_id)
        if email is None:
            email = ''
        if not isinstance(email, str):
            raise ValueError('El correo debe ser texto.')
        email = _email(email) if email.strip() else ''
        if business_unit is not None and not isinstance(business_unit, str):
            raise ValueError('La unidad de negocio debe ser un nombre de texto.')
        unit = (business_unit or '').strip() or DEFAULT_BUSINESS_UNIT_NAME
        for value in (name, phone, enterprise):
            if value is not None and not isinstance(value, str):
                raise ValueError('Nombre, teléfono y empresa deben ser texto.')
        received_at = timezone.now() if received_at is None else received_at
        if isinstance(received_at, datetime):
            if timezone.is_naive(received_at):
                received_at = timezone.make_aware(received_at)
            date_text = timezone.localtime(received_at).strftime('%d/%m/%Y')
        elif isinstance(received_at, date):
            date_text = received_at.strftime('%d/%m/%Y')
        else:
            raise ValueError('received_at debe ser una fecha o fecha con hora.')

        unit_key = _unit_key(unit)
        if unit_key not in _BUSINESS_UNIT_KEYS:
            raise GoogleSheetsError(f'La unidad de negocio {unit!r} no tiene una pestaña de leads.')
        sheets = self._sheet_properties()
        destinations = [sheet for sheet in sheets if _unit_key(sheet['title']) == unit_key]
        if not destinations:
            raise GoogleSheetsError(f'No existe una pestaña para la unidad de negocio {unit!r}.')
        if len(destinations) != 1:
            raise GoogleSheetsError(f'Hay varias pestañas equivalentes para la unidad {unit!r}.')
        destination = destinations[0]['title']
        target_table = self._read_table(destination)
        common_keys = {_column_key(column) for column in LEAD_COLUMNS}

        def common_record(record):
            return {**record, 'values': {
                header: value for header, value in record['values'].items()
                if _column_key(header) in common_keys
            }}

        # Solo se consulta la pestaña de la unidad; las demás no se leen.
        existing = self._find_conversation(target_table, conversation_id)
        if existing is not None:
            return common_record(existing), False

        available = {_column_key(header) for header in target_table[2]}
        missing = [column for column in LEAD_COLUMNS if _column_key(column) not in available]
        if missing:
            raise GoogleSheetsError(
                f'Faltan columnas en la pestaña {destination!r}: {", ".join(missing)}.'
            )
        record = self._append(target_table, {
            'FECHA': date_text,
            'NOMBRE COMPLETO': (name or '').strip(),
            'CORREO EMPRESARIAL': email,
            'TELÉFONO': (phone or '').strip(),
            'EMPRESA': (enterprise or '').strip(),
            CONVERSATION_ID_HEADER: conversation_id,
        }, skip_unset=True, allow_empty_email=True)
        return common_record(record), True


def _configuration_key():
    """Identifica la configuración sin guardar el JSON secreto como clave."""
    account = getattr(settings, 'GOOGLE_SHEETS_SERVICE_ACCOUNT', None)
    if isinstance(account, dict):
        try:
            source = json.dumps(account, sort_keys=True, separators=(',', ':'))
        except (TypeError, ValueError):
            raise ImproperlyConfigured('Las credenciales de Google Sheets deben ser JSON válido.') from None
    else:
        source = str(account or '').strip()
    file_key = None
    if source and not source.startswith('{'):
        path = Path(source)
        if not path.is_absolute():
            path = Path(settings.BASE_DIR) / path
        try:
            info = path.stat()
            file_key = (str(path), info.st_mtime_ns, info.st_size)
        except OSError:
            file_key = (str(path), None, None)
    return (
        (getattr(settings, 'GOOGLE_SHEETS_ID', None) or '').strip(),
        hashlib.sha256(source.encode('utf-8')).digest(), file_key,
    )


def reset_google_sheets_service():
    """Cierra y descarta el cliente del proceso/hilo actual."""
    client = getattr(_client_state, 'client', None)
    owner_pid = getattr(_client_state, 'pid', None)
    _client_state.__dict__.clear()
    if client is not None and owner_pid == os.getpid():
        client.close()


def get_google_sheets_service():
    """Cliente lazy reutilizable; nunca comparte sesiones entre hilos o forks."""
    pid = os.getpid()
    key = _configuration_key()
    client = getattr(_client_state, 'client', None)
    if (client is not None and not client._closed
            and getattr(_client_state, 'pid', None) == pid
            and getattr(_client_state, 'key', None) == key):
        return client
    reset_google_sheets_service()
    client = GoogleSheetsService()
    _client_state.client = client
    _client_state.pid = pid
    _client_state.key = key
    return client


atexit.register(reset_google_sheets_service)


def find_by_email(email, *, sheet_name=None):
    return get_google_sheets_service().find_by_email(email, sheet_name=sheet_name)


def find_by_conversation_id(conversation_id, *, sheet_name=None):
    return get_google_sheets_service().find_by_conversation_id(conversation_id, sheet_name=sheet_name)


def create_record(data, *, sheet_name=None):
    return get_google_sheets_service().create_record(data, sheet_name=sheet_name)


def get_or_create_record(email, data=None, *, sheet_name=None):
    return get_google_sheets_service().get_or_create_record(email, data, sheet_name=sheet_name)


def register_google_sheet_lead(business_unit=None, *, conversation_id, email,
                              name='', phone='', enterprise='', received_at=None):
    return get_google_sheets_service().register_google_sheet_lead(
        business_unit, conversation_id=conversation_id, email=email, name=name, phone=phone,
        enterprise=enterprise, received_at=received_at,
    )
