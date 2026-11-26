"""Servicio de Sheets con credenciales y HTTP simulados, sin red ni base de datos."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
from urllib.parse import unquote

import requests
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from .services import google_sheets_service as service


SERVICE_ACCOUNT = {
    'type': 'service_account',
    'project_id': 'proyecto-de-prueba',
    'private_key_id': 'clave-de-prueba',
    'private_key': 'PRIVATE KEY SIMULADA; NO ES UNA CREDENCIAL',
    'client_email': 'pruebas@proyecto-de-prueba.iam.gserviceaccount.com',
    'token_uri': 'https://oauth2.googleapis.com/token',
}

KIOSKOS_HEADERS = [
    'FECHA', 'NOMBRE COMPLETO', 'CORREO EMPRESARIAL', 'TELÉFONO',
    'Industria', 'Cantidad Estimada de Kioscos', 'Tipo de Instalción deseada',
    'Ubicación del proyecto', 'Plazo estimado para arranque', 'EMPRESA',
    'ASUNTO', 'MENSAJE', 'MQL / SQL', 'ESTATUS', 'COMENTARIOS',
]


@override_settings(
    GOOGLE_SHEETS_ID='sheet-de-prueba',
    GOOGLE_SHEETS_SERVICE_ACCOUNT=json.dumps(SERVICE_ACCOUNT),
)
class GoogleSheetsServiceTests(SimpleTestCase):
    def setUp(self):
        service.reset_google_sheets_service()
        info_patch = patch(
            'google.oauth2.service_account.Credentials.from_service_account_info',
            return_value=Mock(name='credentials'),
        )
        file_patch = patch(
            'google.oauth2.service_account.Credentials.from_service_account_file',
            return_value=Mock(name='file_credentials'),
        )
        session_patch = patch.object(service, 'AuthorizedSession')
        self.info_loader = info_patch.start()
        self.file_loader = file_patch.start()
        self.file_loader.side_effect = self.load_test_file
        self.session_factory = session_patch.start()
        for mocked_patch in (info_patch, file_patch, session_patch):
            self.addCleanup(mocked_patch.stop)
        self.session = self.session_factory.return_value
        self.email = 'ana@example.test'
        self.addCleanup(service.reset_google_sheets_service)

    @staticmethod
    def load_test_file(path, **kwargs):
        if not Path(path).is_file():
            raise FileNotFoundError('No existe el archivo de prueba.')
        return Mock(name='file_credentials')

    @staticmethod
    def response(payload):
        response = Mock()
        response.json.return_value = payload
        response.raise_for_status.return_value = None
        return response

    def table(self, rows, *, sheets=None, append_row=None, append_range=None):
        if sheets is None:
            sheets = [{'properties': {
                'sheetId': 9, 'title': 'Contactos', 'index': 0, 'sheetType': 'GRID',
            }}]
        responses = [
            self.response({'sheets': sheets}),
            self.response({'values': rows}),
        ]
        if append_row is not None:
            responses.append(self.response({'updates': {
                'updatedRange': append_range or f"'Contactos'!A{append_row}:D{append_row}",
                'updatedRows': 1,
            }}))
        self.session.request.side_effect = responses

    def requests(self):
        result = []
        for call in self.session.request.call_args_list:
            method = call.kwargs.get('method') or call.args[0]
            url = call.kwargs.get('url') or call.args[1]
            result.append((method.upper(), unquote(url), call.kwargs))
        return result

    def test_authenticates_from_json_and_uses_sheets_scope(self):
        self.table([['Correo']])
        self.assertIsNone(service.find_by_email(self.email))
        self.assertEqual(self.info_loader.call_args.args[0], SERVICE_ACCOUNT)
        self.assertIn(
            'https://www.googleapis.com/auth/spreadsheets',
            self.info_loader.call_args.kwargs['scopes'],
        )
        self.file_loader.assert_not_called()

    def test_accepts_credentials_dictionary(self):
        with override_settings(GOOGLE_SHEETS_SERVICE_ACCOUNT=SERVICE_ACCOUNT):
            self.table([['Correo']])
            self.assertIsNone(service.find_by_email(self.email))
        self.assertEqual(self.info_loader.call_args.args[0], SERVICE_ACCOUNT)

    def test_accepts_absolute_and_base_dir_relative_credentials_paths(self):
        with TemporaryDirectory() as folder:
            credentials_file = Path(folder) / 'credenciales.json'
            credentials_file.write_text(json.dumps(SERVICE_ACCOUNT), encoding='utf-8')
            for configured_path in (str(credentials_file), credentials_file.name):
                with self.subTest(path_type='absolute' if Path(configured_path).is_absolute() else 'relative'):
                    self.file_loader.reset_mock()
                    with override_settings(
                        BASE_DIR=Path(folder), GOOGLE_SHEETS_SERVICE_ACCOUNT=configured_path,
                    ):
                        self.table([['Correo']])
                        self.assertIsNone(service.find_by_email(self.email))
                    self.assertEqual(
                        Path(self.file_loader.call_args.args[0]), credentials_file,
                    )

    def test_missing_or_malformed_configuration_fails_before_http(self):
        configurations = (
            {'GOOGLE_SHEETS_ID': ''},
            {'GOOGLE_SHEETS_SERVICE_ACCOUNT': ''},
            {'GOOGLE_SHEETS_SERVICE_ACCOUNT': '{json-roto'},
            {'GOOGLE_SHEETS_SERVICE_ACCOUNT': '[]'},
            {'GOOGLE_SHEETS_SERVICE_ACCOUNT': 'no-existe-credencial.json'},
        )
        for configuration in configurations:
            with self.subTest(keys=tuple(configuration)):
                with override_settings(**configuration):
                    with self.assertRaises(ImproperlyConfigured):
                        service.GoogleSheetsService()
        self.session.request.assert_not_called()

    def test_credential_errors_do_not_expose_json_or_parser_details(self):
        secret = 'CLAVE SIMULADA QUE NO DEBE MOSTRARSE'
        with override_settings(GOOGLE_SHEETS_SERVICE_ACCOUNT='{"private_key":"' + secret):
            with self.assertRaises(ImproperlyConfigured) as error:
                service.GoogleSheetsService()
            self.assertNotIn(secret, str(error.exception))
            self.assertIsNone(error.exception.__cause__)
        self.info_loader.side_effect = ValueError(secret)
        with self.assertRaises(ImproperlyConfigured) as error:
            service.GoogleSheetsService()
        self.assertNotIn(secret, str(error.exception))
        self.assertIsNone(error.exception.__cause__)
        self.session.request.assert_not_called()

    def test_new_clients_use_changed_spreadsheet_and_account_settings(self):
        metadata_urls = []
        for index in (1, 2):
            with override_settings(
                GOOGLE_SHEETS_ID=f'documento-{index}',
                GOOGLE_SHEETS_SERVICE_ACCOUNT={
                    **SERVICE_ACCOUNT, 'client_email': f'cuenta-{index}@example.test',
                },
            ):
                self.table([['Correo'], [self.email]])
                self.assertEqual(service.find_by_email(self.email)['row_number'], 2)
                metadata_urls.append(self.requests()[-2][1])
                self.assertEqual(
                    self.info_loader.call_args.args[0]['client_email'], f'cuenta-{index}@example.test',
                )
        self.assertTrue(metadata_urls[0].endswith('/documento-1'))
        self.assertTrue(metadata_urls[1].endswith('/documento-2'))

    def test_finds_exact_normalized_email_and_preserves_row_number(self):
        self.table([
            ['Nombre', 'Correo electrónico', 'Teléfono'],
            ['Otra persona', 'otra-ana@example.test', '111'],
            [],
            ['Ana', '  ANA@EXAMPLE.TEST  ', '222'],
        ])
        result = service.find_by_email('  Ana@Example.Test  ')
        self.assertEqual(result, {
            'row_number': 4,
            'sheet_name': 'Contactos',
            'values': {
                'Nombre': 'Ana',
                'Correo electrónico': '  ANA@EXAMPLE.TEST  ',
                'Teléfono': '222',
            },
        })

    def test_does_not_match_email_substrings(self):
        self.table([['Correo'], ['otra-ana@example.test'], ['ana@example.test.mx']])
        self.assertIsNone(service.find_by_email(self.email))

    def test_invalid_email_is_rejected_before_reading_sheet(self):
        for email in (None, '', '   ', 'sin-correo', '@example.test'):
            with self.subTest(email=email):
                with self.assertRaises(ValueError):
                    service.find_by_email(email)
        self.session.request.assert_not_called()

    def test_email_header_aliases(self):
        for header in (
            'email', 'Correo', '  CORREO ELECTRÓNICO  ', 'E-mail', 'Email Address',
            'CORREO EMPRESARIAL',
        ):
            with self.subTest(header=header):
                self.table([[header], [self.email]])
                self.assertEqual(service.find_by_email(self.email)['row_number'], 2)

    def test_first_grid_sheet_is_chosen_by_index_and_title_is_escaped(self):
        self.table([['Correo'], [self.email]], sheets=[
            {'properties': {'title': 'Segunda', 'index': 2, 'sheetType': 'GRID'}},
            {'properties': {'title': 'Vista', 'index': 0, 'sheetType': 'OBJECT'}},
            {'properties': {'title': "Datos O'Brien", 'index': 1, 'sheetType': 'GRID'}},
        ])
        result = service.find_by_email(self.email)
        self.assertEqual(result['sheet_name'], "Datos O'Brien")
        read = self.requests()[1]
        self.assertIn("/values/'Datos O''Brien'", read[1])
        self.assertEqual(read[2]['params']['majorDimension'], 'ROWS')
        self.assertEqual(read[2]['params']['valueRenderOption'], 'UNFORMATTED_VALUE')

    def test_explicit_sheet_name_selects_requested_tab(self):
        self.table([['Correo'], [self.email]], sheets=[
            {'properties': {'title': 'Primera', 'index': 0, 'sheetType': 'GRID'}},
            {'properties': {'title': 'Elegida', 'index': 1, 'sheetType': 'GRID'}},
        ])
        self.assertEqual(
            service.find_by_email(self.email, sheet_name='Elegida')['sheet_name'], 'Elegida',
        )

    def test_missing_requested_sheet_or_grid_sheet_fails(self):
        for sheets, name in (([], None), ([{'properties': {
            'title': 'Otra', 'index': 0, 'sheetType': 'GRID',
        }}], 'Inexistente')):
            with self.subTest(name=name):
                self.table([['Correo']], sheets=sheets)
                with self.assertRaises(service.GoogleSheetsError):
                    service.find_by_email(self.email, sheet_name=name)

    def test_invalid_headers_are_rejected_without_appending(self):
        for headers in (
            [], ['Nombre'], ['Correo', 'Correo'], ['Correo', 'email'],
            ['', 'Correo'], ['Nombre', '', 'Correo'],
        ):
            with self.subTest(headers=headers):
                self.session.request.reset_mock()
                self.table([headers])
                with self.assertRaises(service.GoogleSheetsError):
                    service.create_record({'correo': self.email})
                self.assertFalse(any(method == 'POST' for method, _, _ in self.requests()))

    def test_partial_rows_are_padded_with_empty_values(self):
        self.table([['Correo', 'Nombre', 'Teléfono'], [self.email, 'Ana']])
        record = service.find_by_email(self.email)
        self.assertEqual(record['values'], {
            'Correo': self.email, 'Nombre': 'Ana', 'Teléfono': '',
        })

    def test_create_maps_headers_and_uses_raw_values(self):
        self.table([['Nombre', 'Correo electrónico', 'Teléfono', 'Empresa']], append_row=8)
        result = service.create_record({
            'correo': '  ANA@EXAMPLE.TEST ',
            'nombre': '=CONTENIDO RECIBIDO',
            'teléfono': '+525500001234',
            'empresa': None,
        })
        self.assertEqual(result, {
            'row_number': 8,
            'sheet_name': 'Contactos',
            'values': {
                'Nombre': '=CONTENIDO RECIBIDO',
                'Correo electrónico': self.email,
                'Teléfono': '+525500001234',
                'Empresa': '',
            },
        })
        append = self.requests()[-1]
        self.assertEqual(append[0], 'POST')
        self.assertIn(':append', append[1])
        self.assertEqual(append[2]['params']['valueInputOption'], 'RAW')
        self.assertEqual(append[2]['params']['insertDataOption'], 'INSERT_ROWS')
        self.assertEqual(append[2]['json']['values'], [[
            '=CONTENIDO RECIBIDO', self.email, '+525500001234', '',
        ]])
        self.assertTrue(all(kwargs['timeout'] == 30 for _, _, kwargs in self.requests()))

    def test_unknown_or_ambiguous_input_columns_do_not_append(self):
        for data in (
            {'correo': self.email, 'columna inexistente': 'no perder este dato'},
            {'correo': self.email, 'EMAIL': self.email},
        ):
            with self.subTest(keys=tuple(data)):
                self.session.request.reset_mock()
                self.table([['Correo', 'Nombre']])
                with self.assertRaises(ValueError):
                    service.create_record(data)
                self.assertFalse(any(method == 'POST' for method, _, _ in self.requests()))

    def test_get_or_create_existing_record_does_not_overwrite_or_append(self):
        self.table([['Nombre', 'Correo'], ['Ana original', self.email]])
        record, created = service.get_or_create_record(self.email, {'nombre': 'Nombre nuevo'})
        self.assertFalse(created)
        self.assertEqual(record['values']['Nombre'], 'Ana original')
        self.assertFalse(any(method == 'POST' for method, _, _ in self.requests()))

    def test_get_or_create_inserts_missing_email_without_extra_data(self):
        self.table([['Nombre', 'Correo']], append_row=2)
        record, created = service.get_or_create_record('  ANA@EXAMPLE.TEST  ')
        self.assertTrue(created)
        self.assertEqual(record['values']['Correo'], self.email)
        self.assertEqual(record['row_number'], 2)
        self.assertEqual(sum(method == 'POST' for method, _, _ in self.requests()), 1)

    def test_real_kioskos_headers_create_and_find_email_in_third_column(self):
        sheets = [{'properties': {
            'title': 'KIOSKOS DIGITALES', 'index': 0, 'sheetType': 'GRID',
        }}]
        self.table(
            [KIOSKOS_HEADERS], sheets=sheets, append_row=2,
            append_range="'KIOSKOS DIGITALES'!A2:O2",
        )
        record, created = service.get_or_create_record(
            '  ANA@EXAMPLE.TEST  ', {
                'FECHA': '2026-11-12',
                'NOMBRE COMPLETO': 'Ana de prueba',
                'TELÉFONO': '+525500001234',
                'Industria': 'Comercio',
                'Cantidad Estimada de Kioscos': 2,
                'Tipo de Instalción deseada': 'Interior',
                'ESTATUS': 'Recibido',
            }, sheet_name='KIOSKOS DIGITALES',
        )
        self.assertTrue(created)
        self.assertEqual(record['sheet_name'], 'KIOSKOS DIGITALES')
        self.assertEqual(record['row_number'], 2)
        self.assertEqual(record['values']['CORREO EMPRESARIAL'], self.email)
        appended = self.requests()[-1][2]['json']['values'][0]
        self.assertEqual(len(appended), len(KIOSKOS_HEADERS))
        self.assertEqual(appended[:4], [
            '2026-11-12', 'Ana de prueba', self.email, '+525500001234',
        ])
        self.assertEqual(appended[5], 2)
        self.assertEqual(appended[13], 'Recibido')
        self.assertEqual(appended[14], '')

        self.table([KIOSKOS_HEADERS, appended], sheets=sheets)
        found = service.find_by_email(self.email.upper(), sheet_name='KIOSKOS DIGITALES')
        self.assertEqual(found, record)

    def test_real_kioskos_headers_existing_email_is_not_overwritten_or_duplicated(self):
        original = [
            '2026-11-11', 'Nombre original', 'ANA@EXAMPLE.TEST', '+525500009999',
            'Industria original', 3, 'Interior', 'Guadalajara', 'Diciembre',
            'Empresa original', 'Asunto original', 'Mensaje original', 'MQL',
            'En seguimiento', 'Comentario original',
        ]
        self.table([KIOSKOS_HEADERS, original], sheets=[{'properties': {
            'title': 'KIOSKOS DIGITALES', 'index': 0, 'sheetType': 'GRID',
        }}])
        record, created = service.get_or_create_record(
            self.email, {'NOMBRE COMPLETO': 'Nombre nuevo', 'ESTATUS': 'Nuevo'},
            sheet_name='KIOSKOS DIGITALES',
        )
        self.assertFalse(created)
        self.assertEqual(record['values'], dict(zip(KIOSKOS_HEADERS, original)))
        self.assertEqual(record['row_number'], 2)
        self.assertFalse(any(method == 'POST' for method, _, _ in self.requests()))

    def test_http_error_is_reported_without_leaking_transport_details(self):
        response = self.response({})
        response.status_code = 403
        response.raise_for_status.side_effect = requests.HTTPError(
            'DETALLE SECRETO que no debe mostrarse', response=response,
        )
        self.session.request.side_effect = [response]
        with self.assertRaises(service.GoogleSheetsError) as error:
            service.find_by_email(self.email)
        self.assertNotIn('DETALLE SECRETO', str(error.exception))
        self.assertEqual(self.session.request.call_count, 1)

    def test_network_error_does_not_retry_automatically(self):
        self.session.request.side_effect = requests.Timeout('DETALLE SECRETO')
        with self.assertRaises(service.GoogleSheetsError) as error:
            service.find_by_email(self.email)
        self.assertNotIn('DETALLE SECRETO', str(error.exception))
        self.assertEqual(self.session.request.call_count, 1)

    def test_timeout_during_append_does_not_retry_or_leak_transport_details(self):
        self.table([['Nombre', 'Correo']], append_row=2)
        responses = list(self.session.request.side_effect)
        responses[-1] = requests.Timeout('DETALLE SECRETO')
        self.session.request.side_effect = responses
        with self.assertRaises(service.GoogleSheetsError) as error:
            service.get_or_create_record(self.email)
        self.assertNotIn('DETALLE SECRETO', str(error.exception))
        self.assertEqual(sum(method == 'POST' for method, _, _ in self.requests()), 1)
        self.assertEqual(self.session.request.call_count, 3)
