#!/bin/sh
set -eu

# Crear un volumen con un nombre existente conserva su contenido.
for volume_name in leadmanager_pg_data leadmanager_redis_data leadmanager_media leadmanager_beat_data; do
    docker volume create "$volume_name"
done
