"""Reutilización del cliente de Sheets, aislada por hilo/proceso y configuración."""

import json
import os
from copy import deepcopy
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
from threading import Thread
from unittest.mock import Mock, patch
from urllib.parse import unquote

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from .services import google_sheets_service as service
from .test_google_sheets_service import SERVICE_ACCOUNT


@override_settings(
    GOOGLE_SHEETS_ID='singleton-documento',
    GOOGLE_SHEETS_SERVICE_ACCOUNT=json.dumps(SERVICE_ACCOUNT),
)
class GoogleSheetsSingletonTests(SimpleTestCase):
    def setUp(self):
        service.reset_google_sheets_service()
        self.sessions = []
        credentials_patch = patch.object(service, '_credentials', side_effect=lambda: Mock(name='credentials'))
        session_patch = patch.object(service, 'AuthorizedSession', side_effect=self.new_session)
        self.credentials_loader = credentials_patch.start()
        self.session_factory = session_patch.start()
        self.addCleanup(credentials_patch.stop)
        self.addCleanup(session_patch.stop)
        # Limpiar antes de desmontar los mocks; cada test posee sus sesiones.
        self.addCleanup(service.reset_google_sheets_service)
        self.email = 'ana@example.test'

    def new_session(self, credentials):
        session = Mock(name=f'session-{len(self.sessions)}')
        session.credentials = credentials
        self.sessions.append(session)
        return session

    @staticmethod
    def response(payload):
        response = Mock()
        response.json.return_value = payload
        response.raise_for_status.return_value = None
        return response

    def install_sheet_reads(self, session, tables):
        def request(method, url, **kwargs):
            self.assertEqual(method, 'GET')
            url = unquote(url)
            if '/values/' not in url:
                return self.response({'sheets': [
                    {'properties': {'title': title, 'index': index, 'sheetType': 'GRID'}}
                    for index, title in enumerate(tables)
                ]})
            title = url.split('/values/', 1)[1][1:-1].replace("''", "'")
            return self.response({'values': deepcopy(tables[title])})
        session.request.side_effect = request

    def test_factory_is_lazy_and_preserves_client_session_and_credentials(self):
        self.credentials_loader.assert_not_called()
        self.session_factory.assert_not_called()
        first = service.get_google_sheets_service()
        first._session.credentials.token = 'token-en-memoria'
        repeated = service.get_google_sheets_service()
        self.assertIs(first, repeated)
        self.assertIs(first._session, repeated._session)
        self.assertEqual(repeated._session.credentials.token, 'token-en-memoria')
        self.credentials_loader.assert_called_once()
        self.session_factory.assert_called_once()
        first._session.close.assert_not_called()

    def test_public_helpers_reuse_client_and_do_not_close_between_operations(self):
        client = service.get_google_sheets_service()
        with patch.object(client, 'find_by_email', return_value=None) as find_email, \
                patch.object(client, 'find_by_conversation_id', return_value=None) as find_id, \
                patch.object(client, 'create_record', return_value={'created': True}) as create, \
                patch.object(client, 'get_or_create_record', return_value=({}, False)) as get_create, \
                patch.object(client, 'register_google_sheet_lead', return_value=({}, True)) as register:
            self.assertIsNone(service.find_by_email(self.email, sheet_name='ODOO'))
            self.assertIsNone(service.find_by_conversation_id(178, sheet_name='APPS'))
            self.assertEqual(service.create_record({'Correo': self.email}, sheet_name='BPM'), {'created': True})
            self.assertEqual(service.get_or_create_record(self.email, {'Nombre': 'Ana'}, sheet_name='ODOO'), ({}, False))
            self.assertEqual(service.register_google_sheet_lead(
                'APPS', conversation_id=178, email=self.email, name='Ana',
            ), ({}, True))
        find_email.assert_called_once_with(self.email, sheet_name='ODOO')
        find_id.assert_called_once_with(178, sheet_name='APPS')
        create.assert_called_once_with({'Correo': self.email}, sheet_name='BPM')
        get_create.assert_called_once_with(self.email, {'Nombre': 'Ana'}, sheet_name='ODOO')
        self.assertEqual(register.call_args.kwargs['conversation_id'], 178)
        self.assertIsNone(client._sheet_name)
        client._session.close.assert_not_called()
        self.session_factory.assert_called_once()

    def test_tab_selection_belongs_to_operation_and_does_not_change_client(self):
        client = service.get_google_sheets_service()
        self.install_sheet_reads(client._session, {
            'ODOO': [['Correo'], [self.email]],
            'APPS': [['Correo'], [self.email]],
        })
        self.assertEqual(service.find_by_email(self.email, sheet_name='ODOO')['sheet_name'], 'ODOO')
        self.assertEqual(service.find_by_email(self.email, sheet_name='APPS')['sheet_name'], 'APPS')
        self.assertEqual(service.find_by_email(self.email)['sheet_name'], 'ODOO')
        self.assertIsNone(client._sheet_name)
        self.assertIs(service.get_google_sheets_service(), client)
        self.session_factory.assert_called_once()

    def test_changing_document_closes_previous_client_and_rebuilds(self):
        first = service.get_google_sheets_service()
        with override_settings(GOOGLE_SHEETS_ID='otro-documento'):
            second = service.get_google_sheets_service()
            self.assertIsNot(second, first)
            self.assertTrue(second._url.endswith('/otro-documento'))
            self.assertIs(second, service.get_google_sheets_service())
        first._session.close.assert_called_once()
        self.assertTrue(first._closed)
        self.assertEqual(self.credentials_loader.call_count, 2)

    def test_changing_account_closes_previous_client_and_rebuilds(self):
        first = service.get_google_sheets_service()
        changed = {**SERVICE_ACCOUNT, 'client_email': 'otra-cuenta@example.test'}
        with override_settings(GOOGLE_SHEETS_SERVICE_ACCOUNT=json.dumps(changed)):
            second = service.get_google_sheets_service()
            self.assertIsNot(first, second)
        first._session.close.assert_called_once()
        self.assertEqual(self.credentials_loader.call_count, 2)

    def test_mutating_dictionary_credentials_is_detected(self):
        account = deepcopy(SERVICE_ACCOUNT)
        with override_settings(GOOGLE_SHEETS_SERVICE_ACCOUNT=account):
            first = service.get_google_sheets_service()
            account['private_key_id'] = 'otra-clave-simulada'
            second = service.get_google_sheets_service()
            self.assertIsNot(first, second)
            first._session.close.assert_called_once()

    def test_relative_credentials_path_uses_base_dir_in_configuration_identity(self):
        with TemporaryDirectory() as folder:
            for name in ('primero', 'segundo'):
                path = Path(folder) / name
                path.mkdir()
                (path / 'credenciales.json').write_text('{}', encoding='utf-8')
            with override_settings(BASE_DIR=Path(folder) / 'primero', GOOGLE_SHEETS_SERVICE_ACCOUNT='credenciales.json'):
                first = service.get_google_sheets_service()
            with override_settings(BASE_DIR=Path(folder) / 'segundo', GOOGLE_SHEETS_SERVICE_ACCOUNT='credenciales.json'):
                second = service.get_google_sheets_service()
                self.assertIsNot(first, second)
                first._session.close.assert_called_once()

    def test_rotating_credentials_file_at_same_path_rebuilds_client(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'credenciales.json'
            path.write_text('{}', encoding='utf-8')
            with override_settings(GOOGLE_SHEETS_SERVICE_ACCOUNT=str(path)):
                first = service.get_google_sheets_service()
                stamp = path.stat().st_mtime_ns
                path.write_text('{"rotada":true}', encoding='utf-8')
                os.utime(path, ns=(stamp + 2_000_000_000, stamp + 2_000_000_000))
                second = service.get_google_sheets_service()
                self.assertIsNot(first, second)
                first._session.close.assert_called_once()
                self.assertIs(second, service.get_google_sheets_service())

    def test_configuration_cache_does_not_retain_plain_credentials_as_key(self):
        secret = 'CLAVE SIMULADA QUE NO DEBE SER PARTE DE LA CLAVE DE CACHE'
        with override_settings(GOOGLE_SHEETS_SERVICE_ACCOUNT={**SERVICE_ACCOUNT, 'private_key': secret}):
            service.get_google_sheets_service()
            self.assertNotIn(secret, repr(service._client_state.key))

    def test_changed_process_never_reuses_or_closes_parent_session(self):
        with patch.object(service.os, 'getpid', return_value=101):
            first = service.get_google_sheets_service()
        with patch.object(service.os, 'getpid', return_value=202):
            second = service.get_google_sheets_service()
            self.assertIsNot(first, second)
            first._session.close.assert_not_called()
            service.reset_google_sheets_service()
        second._session.close.assert_called_once()
        first._session.close.assert_not_called()

    def test_different_threads_have_different_clients_and_reset_is_local(self):
        main = service.get_google_sheets_service()
        result = Queue()

        def worker():
            try:
                first = service.get_google_sheets_service()
                repeated = service.get_google_sheets_service()
                service.reset_google_sheets_service()
                result.put((first, repeated, None))
            except BaseException as exc:
                result.put((None, None, exc))

        thread = Thread(target=worker)
        thread.start()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive(), 'El hilo de prueba no terminó.')
        child, repeated, error = result.get_nowait()
        if error:
            raise error
        self.assertIs(child, repeated)
        self.assertIsNot(main, child)
        self.assertIsNot(main._session, child._session)
        child._session.close.assert_called_once()
        main._session.close.assert_not_called()
        self.assertIs(main, service.get_google_sheets_service())

    def test_reset_and_close_are_idempotent_and_next_lookup_recreates(self):
        first = service.get_google_sheets_service()
        first.close()
        first.close()
        first._session.close.assert_called_once()
        second = service.get_google_sheets_service()
        self.assertIsNot(first, second)
        service.reset_google_sheets_service()
        service.reset_google_sheets_service()
        second._session.close.assert_called_once()
        self.assertIsNot(second, service.get_google_sheets_service())

    def test_explicit_context_manager_is_independent_from_singleton(self):
        shared = service.get_google_sheets_service()
        with service.GoogleSheetsService(sheet_name='APPS') as explicit:
            self.assertIsNot(explicit, shared)
            self.assertIsNot(explicit._session, shared._session)
            self.assertEqual(explicit._sheet_name, 'APPS')
        explicit._session.close.assert_called_once()
        self.assertTrue(explicit._closed)
        shared._session.close.assert_not_called()
        self.assertIs(shared, service.get_google_sheets_service())

    def test_reads_see_external_changes_while_connection_is_reused(self):
        client = service.get_google_sheets_service()
        tables = {'ODOO': [['Correo', 'ID Conversación']]}
        self.install_sheet_reads(client._session, tables)
        self.assertIsNone(service.find_by_conversation_id(178, sheet_name='ODOO'))
        # Otra tarea/usuario escribió entre consultas. No reutilizar filas antiguas.
        tables['ODOO'].append([self.email, 178])
        record = service.find_by_conversation_id(178, sheet_name='ODOO')
        self.assertEqual(record['row_number'], 2)
        self.assertEqual(record['values']['ID Conversación'], 178)
        self.assertEqual(client._session.request.call_count, 4)
        self.session_factory.assert_called_once()
        client._session.close.assert_not_called()

    def test_invalid_new_document_cannot_fall_back_to_old_cached_client(self):
        previous = service.get_google_sheets_service()
        with override_settings(GOOGLE_SHEETS_ID=''):
            with self.assertRaises(ImproperlyConfigured):
                service.find_by_email(self.email)
        previous._session.request.assert_not_called()
