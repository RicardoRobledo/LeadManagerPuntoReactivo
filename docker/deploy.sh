#!/usr/bin/env bash
set -euo pipefail
umask 077

fail() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
}

image="${1:-}"
[[ "$image" =~ ^ghcr\.io/[a-z0-9/._-]+@sha256:[0-9a-f]{64}$ ]] ||
    fail 'Se requiere la imagen publicada en GHCR con su digest sha256.'

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
deploy_dir="$(cd -- "$script_dir/.." && pwd)"
cd -- "$deploy_dir"

for dependency in docker flock awk mktemp; do
    command -v "$dependency" >/dev/null || fail "Falta instalar ${dependency} en el VPS."
done
[[ -r .env.docker ]] || fail 'Falta .env.docker o el usuario SSH no puede leerlo. Completa la plantilla en el VPS.'
[[ -w . ]] || fail 'El usuario SSH necesita permiso de escritura en el directorio del proyecto.'
docker info >/dev/null
docker compose version >/dev/null

# Tambien protege frente a ejecuciones manuales simultaneas en el VPS.
exec 9>.deploy.lock
flock -n 9 || fail 'Ya hay un despliegue ejecutandose en este directorio.'

docker_auth_dir=''
env_temp=''
cleanup() {
    local status=$?
    trap - EXIT
    # Solo se eliminan archivos temporales creados por esta ejecucion.
    if [[ -n "$docker_auth_dir" && "$docker_auth_dir" == /tmp/leadmanager-ghcr.* ]]; then
        rm -rf -- "$docker_auth_dir"
    fi
    if [[ -n "$env_temp" && "$env_temp" == "$deploy_dir"/.env.docker.tmp.* ]]; then
        rm -f -- "$env_temp"
    fi
    if (( status != 0 )); then
        printf 'El despliegue no termino. Revisa el error anterior.\n' >&2
    fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# La referencia del commit tiene prioridad sobre APP_IMAGE del archivo del VPS.
export APP_IMAGE="$image"
compose() {
    docker compose --env-file .env.docker "$@"
}
compose config --quiet

volumes=(leadmanager_pg_data leadmanager_redis_data leadmanager_media leadmanager_beat_data)
if [[ -f .last-deploy ]]; then
    # Si un volumen desaparece tras un despliegue, detenerse antes de crear datos vacios.
    for volume in "${volumes[@]}"; do
        docker volume inspect "$volume" >/dev/null || fail "Falta el volumen persistente ${volume}. Revisa el almacenamiento antes de continuar."
    done
else
    sh docker/create-volumes.sh
fi

# GitHub envia estas dos lineas por stdin; el token no aparece en los argumentos SSH.
IFS= read -r registry_username || fail 'Falta el usuario de GHCR por stdin.'
IFS= read -r registry_token || fail 'Falta el token de GHCR por stdin.'
[[ -n "$registry_username" && -n "$registry_token" ]] || fail 'Las credenciales de GHCR estan vacias.'
original_docker_config="${DOCKER_CONFIG:-${HOME}/.docker}"
docker_auth_dir="$(mktemp -d /tmp/leadmanager-ghcr.XXXXXX)"
# Conservar el acceso al plugin Compose si se instalo para este usuario.
if [[ -d "$original_docker_config/cli-plugins" ]]; then
    ln -s "$original_docker_config/cli-plugins" "$docker_auth_dir/cli-plugins"
fi
export DOCKER_CONFIG="$docker_auth_dir"
printf '%s\n' "$registry_token" | docker login ghcr.io --username "$registry_username" --password-stdin
unset registry_token

# Todos los procesos de la app comparten esta misma imagen; PostgreSQL y Redis
# conservan sus imagenes locales y solo se descargan cuando todavia no existen.
compose pull migrate
compose up -d --no-build --pull missing --wait --wait-timeout 180 postgres redis
compose run --rm --no-deps --pull never -T migrate python manage.py check

# Terminar los trabajos activos antes de cambiar el esquema de la base.
compose stop web worker beat
# El servicio migrate aplica las pendientes antes de iniciar web, worker y Beat.
compose up -d --no-build --pull never --wait --wait-timeout 300
compose exec -T web python manage.py migrate --check

# Guardar solo la referencia de imagen tras comprobar el despliegue. El resto
# de las variables y credenciales del archivo del VPS conserva su contenido.
env_temp="$(mktemp "$deploy_dir/.env.docker.tmp.XXXXXX")"
awk -v image="$image" '
    /^[[:space:]]*(export[[:space:]]+)?APP_IMAGE[[:space:]]*=/ {
        if (!found) print "APP_IMAGE=" image
        found = 1
        next
    }
    { print }
    END { if (!found) print "APP_IMAGE=" image }
' .env.docker > "$env_temp"
chmod 600 "$env_temp"
mv -f -- "$env_temp" .env.docker
env_temp=''
printf '%s\n' "$image" > .last-deploy
compose ps -a
printf 'Despliegue completado: %s\n' "$image"
