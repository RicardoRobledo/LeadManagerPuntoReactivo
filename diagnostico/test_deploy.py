"""Prueba el despliegue con Docker simulado, sin red ni datos del proyecto."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent
BASH = shutil.which('bash')
if os.name == 'nt':
    git_bash = Path('C:/Program Files/Git/bin/bash.exe')
    if git_bash.is_file():
        BASH = str(git_bash)

IMAGE = 'ghcr.io/example/leadmanager@sha256:' + 'a' * 64
NEXT_IMAGE = 'ghcr.io/example/leadmanager@sha256:' + 'b' * 64
TOKEN = 'token-simulado-no-publicar-en-logs'
VOLUMES = ('leadmanager_pg_data', 'leadmanager_redis_data', 'leadmanager_media', 'leadmanager_beat_data')


def shell_path(path):
    """Usa rutas de Git Bash en Windows y rutas nativas en Linux."""
    value = path.as_posix()
    return '/' + value[0].lower() + value[2:] if os.name == 'nt' else value


DOCKER_STUB = r'''#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$DEPLOY_TEST_LOG"
case "$1" in
    info) exit 0 ;;
    volume)
        case "$2" in
            create) mkdir -p "$DEPLOY_TEST_VOLUMES/$3" ;;
            inspect) [[ -d "$DEPLOY_TEST_VOLUMES/$3" ]] ;;
            *) exit 90 ;;
        esac
        ;;
    login)
        IFS= read -r token
        [[ "$token" == "$DEPLOY_TEST_TOKEN" ]]
        printf '%s' "$token" > "$DOCKER_CONFIG/test-auth"
        printf '%s' "$DOCKER_CONFIG" > "$DEPLOY_TEST_AUTH_DIR"
        [[ "${DEPLOY_TEST_FAILURE:-}" != login ]]
        ;;
    compose)
        shift
        if [[ "$1" == version ]]; then exit 0; fi
        [[ "$1" == --env-file && "$2" == .env.docker ]]
        shift 2
        case "$1" in
            config) [[ "${DEPLOY_TEST_FAILURE:-}" != config ]] ;;
            pull) [[ "${DEPLOY_TEST_FAILURE:-}" != pull ]] ;;
            run) [[ "${DEPLOY_TEST_FAILURE:-}" != preflight ]] ;;
            exec) [[ "${DEPLOY_TEST_FAILURE:-}" != verification ]] ;;
            up)
                if [[ "$*" != *'postgres redis' ]]; then
                    [[ "${DEPLOY_TEST_FAILURE:-}" != health ]]
                fi
                ;;
            stop|ps) ;;
            *) exit 91 ;;
        esac
        ;;
    *) exit 92 ;;
esac
'''


@unittest.skipUnless(BASH, 'Se necesita Bash para probar el script del VPS.')
class DeployTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='leadmanager-deploy-test-')
        self.root = Path(temporary.name).resolve()
        assert self.root.parent == Path(tempfile.gettempdir()).resolve()
        self.addCleanup(temporary.cleanup)
        (self.root / 'docker').mkdir()
        for name in ('deploy.sh', 'create-volumes.sh'):
            shutil.copy2(ROOT / 'docker' / name, self.root / 'docker' / name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        for name, content in (
            ('docker', DOCKER_STUB),
            ('flock', '#!/usr/bin/env bash\n[[ "${DEPLOY_TEST_LOCKED:-}" != 1 ]]\n'),
        ):
            target = self.bin / name
            target.write_text(content, encoding='utf-8', newline='\n')
            target.chmod(0o755)

        self.env_file = self.root / '.env.docker'
        self.original_env = "# Configuracion privada\nAPP_IMAGE=local:old\nDB_PASSWORD='clave $@/# con espacios'\nSECRET_KEY=clave-local\n"
        self.env_file.write_text(self.original_env, encoding='utf-8', newline='\n')
        (self.root / '.env').write_text('ARCHIVO_EXISTENTE=conservar\n', encoding='utf-8')
        self.log = self.root / 'docker.log'
        self.volumes = self.root / 'volumes'
        self.auth_dir = self.root / 'auth-dir'
        self.environment = os.environ.copy()
        self.environment.update({
            'PATH': str(self.bin) + os.pathsep + os.environ['PATH'],
            'DOCKER_CONFIG': shell_path(self.root / 'original-docker-config'),
            'DEPLOY_TEST_LOG': shell_path(self.log),
            'DEPLOY_TEST_VOLUMES': shell_path(self.volumes),
            'DEPLOY_TEST_AUTH_DIR': shell_path(self.auth_dir),
            'DEPLOY_TEST_TOKEN': TOKEN,
        })

    def deploy(self, image=IMAGE, **environment):
        result = subprocess.run(
            [BASH, 'docker/deploy.sh', image], cwd=self.root,
            env={**self.environment, **environment},
            input=('cuenta-de-prueba\n' + TOKEN + '\n').encode('utf-8'),
            capture_output=True, timeout=30,
        )
        result.stdout = result.stdout.decode('utf-8')
        result.stderr = result.stderr.decode('utf-8')
        self.assertNotIn(TOKEN, result.stdout + result.stderr)
        if self.auth_dir.exists():
            path = self.auth_dir.read_text(encoding='utf-8')
            check = subprocess.run([BASH, '-c', '[[ ! -e "$1" ]]', '--', path])
            self.assertEqual(check.returncode, 0, 'Quedaron credenciales temporales en disco.')
        return result

    def commands(self):
        return self.log.read_text(encoding='utf-8') if self.log.exists() else ''

    def test_success_keeps_credentials_and_records_the_verified_image(self):
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        saved = self.env_file.read_text(encoding='utf-8')
        self.assertEqual(saved, self.original_env.replace('APP_IMAGE=local:old', 'APP_IMAGE=' + IMAGE))
        self.assertEqual((self.root / '.last-deploy').read_text().strip(), IMAGE)
        self.assertEqual((self.root / '.env').read_text(), 'ARCHIVO_EXISTENTE=conservar\n')
        self.assertTrue(all((self.volumes / name).is_dir() for name in VOLUMES))
        if os.name != 'nt':
            self.assertEqual(self.env_file.stat().st_mode & 0o777, 0o600)

    def test_repeated_deployment_reuses_volumes_and_preserves_data(self):
        self.assertEqual(self.deploy().returncode, 0)
        records = self.volumes / 'leadmanager_pg_data' / 'records.txt'
        records.write_text('datos-que-deben-conservarse', encoding='utf-8')
        self.log.write_text('', encoding='utf-8')

        result = self.deploy(NEXT_IMAGE)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(records.read_text(), 'datos-que-deben-conservarse')
        self.assertNotIn('volume create', self.commands())
        self.assertIn('APP_IMAGE=' + NEXT_IMAGE, self.env_file.read_text())

    def test_missing_volume_after_deployment_stops_before_starting_services(self):
        (self.root / '.last-deploy').write_text(IMAGE, encoding='utf-8')
        for name in VOLUMES[1:]:
            (self.volumes / name).mkdir(parents=True)

        result = self.deploy()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Falta el volumen persistente leadmanager_pg_data', result.stderr)
        self.assertNotIn('volume create', self.commands())
        self.assertNotIn(' up ', self.commands())
        self.assertEqual(self.env_file.read_text(), self.original_env)

    def test_missing_env_stops_without_calling_docker(self):
        self.env_file.unlink()

        result = self.deploy()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Falta .env.docker', result.stderr)
        self.assertEqual(self.commands(), '')

    def test_failed_steps_do_not_advance_the_saved_image(self):
        phases = {
            'config': 'config --quiet',
            'login': 'login ghcr.io',
            'pull': 'pull migrate',
            'preflight': 'manage.py check',
            'health': 'wait-timeout 300',
            'verification': 'manage.py migrate --check',
        }
        for phase, command in phases.items():
            with self.subTest(phase=phase):
                self.log.write_text('', encoding='utf-8')
                result = self.deploy(DEPLOY_TEST_FAILURE=phase)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(command, self.commands(), result.stderr)
                self.assertEqual(self.env_file.read_text(), self.original_env)
                self.assertFalse((self.root / '.last-deploy').exists())
                if phase in ('config', 'login', 'pull', 'preflight'):
                    self.assertNotIn(' stop ', self.commands())

    def test_image_without_digest_is_rejected(self):
        result = self.deploy('ghcr.io/example/leadmanager:production')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.commands(), '')

    def test_concurrent_deployment_is_rejected(self):
        result = self.deploy(DEPLOY_TEST_LOCKED='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Ya hay un despliegue', result.stderr)
        self.assertNotIn(' pull ', self.commands())


if __name__ == '__main__':
    unittest.main()
