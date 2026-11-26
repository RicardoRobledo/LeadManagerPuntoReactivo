"""Registro común por unidad con Sheets simulado, sin red ni base de datos."""

from copy import deepcopy
from datetime import date, datetime, timezone as datetime_timezone
from unittest.mock import Mock, patch
from urllib.parse import unquote

import requests
from django.test import SimpleTestCase, override_settings

from .services import google_sheets_service as service
from .test_google_sheets_service import KIOSKOS_HEADERS as ORIGINAL_KIOSKOS_HEADERS, SERVICE_ACCOUNT


COMMON_HEADERS = [
    'FECHA', 'NOMBRE COMPLETO', 'CORREO EMPRESARIAL', 'TELÉFONO', 'EMPRESA',
    'ID Conversación',
]
KIOSKOS_HEADERS = [*ORIGINAL_KIOSKOS_HEADERS, 'ID Conversación']
BUSINESS_UNITS = (
    'KIOSKOS DIGITALES', 'ODOO', 'APPS', 'BPM', 'CIBERSEGURIDAD',
    'DESARROLLO WEB', 'DIGITAL SIGNAGE', 'ECOMMERCE',
    'INTELIGENCIA ARTIFICIAL', 'MARKETING DIGITAL', 'NO_DETERMINADO',
)


@override_settings(
    GOOGLE_SHEETS_ID='documento-pruebas-rutas',
    GOOGLE_SHEETS_SERVICE_ACCOUNT=SERVICE_ACCOUNT,
    TIME_ZONE='America/Mexico_City',
)
class GoogleSheetsRoutingTests(SimpleTestCase):
    def setUp(self):
        service.reset_google_sheets_service()
        credentials_patch = patch.object(service.Credentials, 'from_service_account_info')
        session_patch = patch.object(service, 'AuthorizedSession')
        credentials_patch.start()
        self.session = session_patch.start().return_value
        self.addCleanup(credentials_patch.stop)
        self.addCleanup(session_patch.stop)
        self.email = 'ana@example.test'
        self.conversation_id = 178
        self.configure({'ODOO': [COMMON_HEADERS]})
        self.addCleanup(service.reset_google_sheets_service)

    def configure(self, tables, *, errors=None, sheet_types=None):
        self.tables = deepcopy(tables)
        self.errors = errors or {}
        self.sheet_types = sheet_types or {}
        self.posts = []
        self.reads = []
        self.metadata_reads = 0
        self.session.request.reset_mock()
        self.session.request.side_effect = self.http

    @staticmethod
    def response(payload):
        response = Mock()
        response.json.return_value = payload
        response.raise_for_status.return_value = None
        return response

    def http(self, method, url, **kwargs):
        """Servidor mínimo con filas persistentes para comprobar dos altas seguidas."""
        url = unquote(url)
        if '/values/' not in url:
            self.assertEqual(method, 'GET')
            self.metadata_reads += 1
            return self.response({'sheets': [
                {'properties': {
                    'title': title, 'index': index, 'sheetId': index,
                    'sheetType': self.sheet_types.get(title, 'GRID'),
                }}
                for index, title in enumerate(self.tables)
            ]})
        cell_range = url.split('/values/', 1)[1]
        if cell_range.endswith(':append'):
            cell_range = cell_range[:-len(':append')]
        title = cell_range.split('!', 1)[0][1:-1].replace("''", "'")
        if method == 'GET':
            self.reads.append(title)
            if title in self.errors:
                raise self.errors[title]
            return self.response({'values': deepcopy(self.tables[title])})
        self.assertEqual(method, 'POST')
        self.assertTrue(url.endswith(':append'))
        row = deepcopy(kwargs['json']['values'][0])
        self.posts.append((title, row, kwargs))
        self.tables[title].append(['' if value is None else value for value in row])
        number = len(self.tables[title])
        return self.response({'updates': {'updatedRange': f"'{title}'!A{number}:ZZ{number}"}})

    def register(self, unit='ODOO', **data):
        values = {
            'conversation_id': self.conversation_id,
            'email': self.email,
            'name': 'Ana de prueba',
            'phone': '+525500001234',
            'enterprise': 'Empresa de prueba',
            'received_at': date(2026, 11, 12),
        }
        values.update(data)
        return service.register_google_sheet_lead(unit, **values)

    def test_each_configured_business_unit_selects_its_own_tab(self):
        self.configure({unit: [COMMON_HEADERS] for unit in BUSINESS_UNITS})
        for index, unit in enumerate(BUSINESS_UNITS):
            with self.subTest(unit=unit):
                record, created = self.register(
                    unit, conversation_id=index + 1, email=f'persona{index}@example.test',
                )
                self.assertTrue(created)
                self.assertEqual(record['sheet_name'], unit)
                self.assertEqual(record['row_number'], 2)
                self.assertEqual(set(record['values']), set(COMMON_HEADERS))
        self.assertEqual(len(self.posts), len(BUSINESS_UNITS))
        self.assertEqual(self.metadata_reads, len(BUSINESS_UNITS))

    def test_unit_outside_business_unit_tabs_is_rejected_without_http(self):
        for unit in ('SERVICIO NUEVO', 'Hoja 2', 'Leads anteriores'):
            with self.subTest(unit=unit):
                self.configure({unit: [COMMON_HEADERS]})
                with self.assertRaises(service.GoogleSheetsError):
                    self.register(unit)
                self.assertEqual(self.posts, [])
                self.session.request.assert_not_called()

    def test_other_tabs_of_the_document_are_never_read(self):
        self.configure({
            'Hoja 2': [['Notas']],
            'Leads anteriores': [['Correo'], [self.email]],
            'ODOO': [COMMON_HEADERS],
        }, errors={'Leads anteriores': requests.Timeout('No se debe consultar esta pestaña')})
        record, created = self.register('ODOO')
        self.assertTrue(created)
        self.assertEqual(record['sheet_name'], 'ODOO')
        self.assertEqual(self.reads, ['ODOO'])
        self.assertEqual([title for title, _, _ in self.posts], ['ODOO'])

    def test_normalizes_case_accents_spaces_and_underscores_in_unit(self):
        self.configure({'DESARROLLO WEB': [COMMON_HEADERS]})
        record, created = self.register('  desarrolló_web  ')
        self.assertTrue(created)
        self.assertEqual(record['sheet_name'], 'DESARROLLO WEB')

    def test_development_unit_accepts_existing_sheet_spelling(self):
        self.configure({'DESAROLLO WEB': [COMMON_HEADERS]})
        record, created = self.register('DESARROLLO WEB')
        self.assertTrue(created)
        self.assertEqual(record['sheet_name'], 'DESAROLLO WEB')

    def test_blank_unit_uses_no_determinado_instead_of_first_tab(self):
        for unit in (None, '', '  '):
            with self.subTest(unit=unit):
                self.configure({'ODOO': [COMMON_HEADERS], 'NO_DETERMINADO': [COMMON_HEADERS]})
                record, created = self.register(unit)
                self.assertTrue(created)
                self.assertEqual(record['sheet_name'], 'NO_DETERMINADO')

    def test_unknown_or_ambiguous_destination_never_appends(self):
        scenarios = (
            ({'ODOO': [COMMON_HEADERS]}, 'APPS'),
            ({'ODOO': [COMMON_HEADERS]}, None),
            ({'DESARROLLO WEB': [COMMON_HEADERS], 'DESARROLLO_WEB': [COMMON_HEADERS]}, 'DESARROLLO WEB'),
            ({'DESARROLLO WEB': [COMMON_HEADERS], 'DESAROLLO WEB': [COMMON_HEADERS]}, 'DESARROLLO WEB'),
        )
        for tables, unit in scenarios:
            with self.subTest(unit=unit):
                self.configure(tables)
                with self.assertRaises(service.GoogleSheetsError):
                    self.register(unit)
                self.assertEqual(self.posts, [])

    def test_existing_contact_in_other_unit_is_not_read_moved_or_overwritten(self):
        original = [
            '11/11/2026', 'Nombre original', '  ANA@EXAMPLE.TEST ',
            '+525500009999', 'Empresa original', self.conversation_id, 'Estatus manual',
        ]
        self.configure({
            'ODOO': [COMMON_HEADERS],
            'NO_DETERMINADO': [COMMON_HEADERS + ['ESTATUS'], original],
        })
        record, created = self.register('ODOO', name='Nombre diferente', email='otro@example.test')
        # Solo se consulta la pestaña de la unidad: la fila de otra unidad no se lee.
        self.assertTrue(created)
        self.assertEqual(record['sheet_name'], 'ODOO')
        self.assertEqual(record['values']['NOMBRE COMPLETO'], 'Nombre diferente')
        self.assertEqual(self.tables['NO_DETERMINADO'][1], original)
        self.assertEqual(self.reads, ['ODOO'])
        self.assertEqual(self.metadata_reads, 1)

    def test_existing_contact_in_target_returns_before_reading_other_tabs(self):
        self.configure({
            'APPS': [COMMON_HEADERS],
            'ODOO': [COMMON_HEADERS, [
                '11/11/2026', 'Ana', self.email, '', 'Empresa', self.conversation_id,
            ]],
        }, errors={'APPS': requests.Timeout('No se debe consultar esta pestaña')})
        record, created = self.register('ODOO')
        self.assertFalse(created)
        self.assertEqual(record['sheet_name'], 'ODOO')
        self.assertEqual(self.reads, ['ODOO'])
        self.assertEqual(self.posts, [])

    def test_same_conversation_repeated_in_its_unit_only_appends_once(self):
        self.configure({'ODOO': [COMMON_HEADERS], 'APPS': [COMMON_HEADERS]})
        first, first_created = self.register('ODOO')
        second, second_created = self.register('ODOO', email='otro@example.test')
        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first, second)
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(len(self.tables['APPS']), 1)
        self.assertNotIn('APPS', self.reads)

    def test_same_conversation_in_another_unit_gets_a_row_in_that_unit(self):
        self.configure({'ODOO': [COMMON_HEADERS], 'APPS': [COMMON_HEADERS]})
        first, first_created = self.register('ODOO')
        second, second_created = self.register('APPS')
        self.assertTrue(first_created)
        self.assertTrue(second_created)
        self.assertEqual([title for title, _, _ in self.posts], ['ODOO', 'APPS'])

    def test_same_email_in_different_conversations_creates_distinct_rows(self):
        first, first_created = self.register(conversation_id=178)
        second, second_created = self.register(conversation_id=179)
        self.assertTrue(first_created)
        self.assertTrue(second_created)
        self.assertEqual(first['values']['CORREO EMPRESARIAL'], second['values']['CORREO EMPRESARIAL'])
        self.assertNotEqual(first['row_number'], second['row_number'])
        self.assertEqual(len(self.posts), 2)
        self.assertEqual(int(first['values']['ID Conversación']), 178)
        self.assertEqual(int(second['values']['ID Conversación']), 179)

    def test_historical_same_email_without_id_is_not_adopted(self):
        historical = [
            ['11/11/2026', 'Sin ID', self.email, '', 'Empresa'],
            ['11/11/2026', 'ID vacío', self.email, '', 'Empresa', ''],
        ]
        self.configure({'ODOO': [COMMON_HEADERS, *historical]})
        record, created = self.register()
        self.assertTrue(created)
        self.assertEqual(record['row_number'], 4)
        self.assertEqual(self.tables['ODOO'][1:3], historical)
        self.assertEqual(len(self.posts), 1)

    def test_existing_id_matches_integer_float_or_normalized_string(self):
        for stored in (178, 178.0, '178', ' 00178 '):
            for identifier in (178, '178', ' 00178 '):
                with self.subTest(stored=stored, identifier=identifier):
                    self.configure({'ODOO': [COMMON_HEADERS, [
                        '11/11/2026', 'Original', 'original@example.test', '', 'Empresa', stored,
                    ]]})
                    record, created = self.register(conversation_id=identifier)
                    self.assertFalse(created)
                    self.assertEqual(record['row_number'], 2)
                    self.assertEqual(record['values']['CORREO EMPRESARIAL'], 'original@example.test')
                    self.assertEqual(self.posts, [])

    def test_malformed_or_different_ids_do_not_match_by_email(self):
        invalid_or_different = ('', None, True, False, 0, -178, 178.5, '178.0', 'no-id', 1178, 1780)
        historical = [
            ['11/11/2026', 'Histórico', self.email, '', 'Empresa', value]
            for value in invalid_or_different
        ]
        self.configure({'ODOO': [COMMON_HEADERS, *historical]})
        record, created = self.register()
        self.assertTrue(created)
        self.assertEqual(record['row_number'], len(historical) + 2)
        self.assertEqual(self.tables['ODOO'][1:-1], historical)
        self.assertEqual(len(self.posts), 1)

    def test_missing_id_column_in_destination_aborts_instead_of_email_fallback(self):
        self.configure({'ODOO': [['Correo'], [self.email]]})
        with self.assertRaises(service.GoogleSheetsError):
            self.register()
        self.assertEqual(self.posts, [])

    def test_find_by_conversation_id_is_exact_and_preserves_row_number(self):
        self.configure({
            'APPS': [COMMON_HEADERS],
            'ODOO': [['Correo', 'ID Conversación'], ['otra@example.test', 1178], [], [self.email, '00178']],
        })
        record = service.find_by_conversation_id(' 178 ', sheet_name='ODOO')
        self.assertEqual(record['sheet_name'], 'ODOO')
        self.assertEqual(record['row_number'], 4)
        self.assertEqual(record['values']['Correo'], self.email)
        self.assertEqual(self.reads, ['ODOO'])
        self.assertEqual(self.posts, [])

    def test_find_by_conversation_id_returns_none_when_missing(self):
        self.configure({'ODOO': [['Correo', 'ID Conversación'], [self.email, 179]]})
        self.assertIsNone(service.find_by_conversation_id(178))
        self.assertEqual(self.posts, [])

    def test_id_header_accepts_case_accents_and_underscores(self):
        for header in ('ID Conversación', 'ID_CONVERSACION', '  id conversación  '):
            with self.subTest(header=header):
                self.configure({'ODOO': [['Correo', header], [self.email, 178]]})
                record = service.find_by_conversation_id('00178')
                self.assertEqual(record['row_number'], 2)
                existing, created = self.register()
                self.assertFalse(created)
                self.assertEqual(existing['row_number'], 2)
                self.assertEqual(self.posts, [])

    def test_invalid_conversation_ids_are_rejected_before_http(self):
        for identifier in (None, True, False, 178.0, 178.5, 0, -1, '', ' ', '0', '-178', '178.0', 'abc'):
            with self.subTest(identifier=identifier):
                with self.assertRaises(ValueError):
                    self.register(conversation_id=identifier)
                with self.assertRaises(ValueError):
                    service.find_by_conversation_id(identifier)
        self.session.request.assert_not_called()

    def test_unreadable_destination_aborts_without_creating_contact(self):
        self.configure({'ODOO': [COMMON_HEADERS]}, errors={
            'ODOO': requests.Timeout('Fallo de lectura simulado'),
        })
        with self.assertRaises(service.GoogleSheetsError):
            self.register('ODOO')
        self.assertEqual(self.posts, [])

    def test_unreadable_or_invalid_other_tabs_do_not_block_the_unit(self):
        self.configure({
            'ODOO': [COMMON_HEADERS], 'APPS': [COMMON_HEADERS], 'Hoja 2': [['Nombre']],
        }, errors={'APPS': requests.Timeout('Fallo de lectura simulado')})
        record, created = self.register('ODOO')
        self.assertTrue(created)
        self.assertEqual(record['sheet_name'], 'ODOO')
        self.assertEqual(self.reads, ['ODOO'])

    def test_destination_requires_all_six_columns_for_new_conversation(self):
        for missing in COMMON_HEADERS:
            with self.subTest(missing=missing):
                self.configure({'ODOO': [[header for header in COMMON_HEADERS if header != missing]]})
                with self.assertRaises(service.GoogleSheetsError):
                    self.register('ODOO')
                self.assertEqual(self.posts, [])

    def test_only_common_columns_are_written_and_status_is_skipped(self):
        self.configure({'KIOSKOS DIGITALES': [KIOSKOS_HEADERS]})
        record, created = self.register('KIOSKOS DIGITALES')
        self.assertTrue(created)
        title, row, kwargs = self.posts[0]
        self.assertEqual(title, 'KIOSKOS DIGITALES')
        self.assertEqual(len(row), 16)  # El ID está después de las 15 columnas originales.
        self.assertEqual(row[:4], ['12/11/2026', 'Ana de prueba', self.email, '+525500001234'])
        self.assertEqual(row[4:9], [None] * 5)
        self.assertEqual(row[9], 'Empresa de prueba')
        self.assertEqual(row[10:15], [None] * 5)
        self.assertEqual(int(row[15]), self.conversation_id)
        self.assertEqual(kwargs['params']['valueInputOption'], 'RAW')
        self.assertEqual(kwargs['params']['insertDataOption'], 'INSERT_ROWS')
        self.assertEqual(set(record['values']), set(COMMON_HEADERS))
        self.assertNotIn('ESTATUS', record['values'])

    def test_status_between_common_columns_is_null_not_empty_string(self):
        self.configure({'ODOO': [[
            'FECHA', 'NOMBRE COMPLETO', 'ESTATUS', 'CORREO EMPRESARIAL',
            'TELÉFONO', 'EMPRESA', 'ID Conversación', 'COMENTARIOS',
        ]]})
        self.register('ODOO')
        row = self.posts[0][1]
        self.assertEqual(len(row), 7)
        self.assertIsNone(row[2])
        self.assertEqual(row[3], self.email)
        self.assertEqual(int(row[6]), self.conversation_id)

    def test_dates_use_mexico_local_time_and_date_inputs_have_no_time(self):
        moments = (
            (date(2026, 11, 12), '12/11/2026'),
            (datetime(2026, 11, 13, 2, 30, tzinfo=datetime_timezone.utc), '12/11/2026'),
            (datetime(2026, 11, 12, 9, 10), '12/11/2026'),
        )
        for moment, expected in moments:
            with self.subTest(moment=moment):
                self.configure({'ODOO': [COMMON_HEADERS]})
                record, _ = self.register(received_at=moment)
                self.assertEqual(record['values']['FECHA'], expected)
        self.configure({'ODOO': [COMMON_HEADERS]})
        now = datetime(2026, 11, 13, 2, 30, tzinfo=datetime_timezone.utc)
        with patch.object(service.timezone, 'now', return_value=now):
            record, _ = self.register(received_at=None)
        self.assertEqual(record['values']['FECHA'], '12/11/2026')

    def test_object_sheets_are_not_searched_as_cell_tables(self):
        self.configure(
            {'ODOO': [COMMON_HEADERS], 'Grafico': []},
            sheet_types={'Grafico': 'OBJECT'},
        )
        _, created = self.register('ODOO')
        self.assertTrue(created)
        self.assertEqual(self.reads, ['ODOO'])

    def test_contact_without_email_is_registered_by_conversation_id(self):
        self.configure({'ODOO': [COMMON_HEADERS]})
        record, created = self.register(email='', phone='+525500001234')
        self.assertTrue(created)
        self.assertEqual(record['values']['CORREO EMPRESARIAL'], '')
        self.assertEqual(record['values']['TELÉFONO'], '+525500001234')
        repeated, created_again = self.register(email='correo-nuevo@example.test')
        self.assertFalse(created_again)
        self.assertEqual(repeated, record)
        self.assertEqual(len(self.posts), 1)

    def test_invalid_arguments_fail_before_network_calls(self):
        cases = (
            {'email': 'correo-invalido'}, {'name': 1}, {'phone': 123}, {'enterprise': []},
            {'received_at': '2026-11-12'},
        )
        for invalid in cases:
            with self.subTest(argument=next(iter(invalid))):
                with self.assertRaises(ValueError):
                    self.register(**invalid)
        with self.assertRaises(ValueError):
            self.register(unit=123)
        self.session.request.assert_not_called()
