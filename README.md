cd 'C:\Users\PRIDE BLACK WOLF\Downloads\Proyectos\LeadManagerNuevaTest' opencode

cd 'C:\Users\PRIDE BLACK WOLF\Downloads\Proyectos\LeadManagerNuevaTest\venv\Scripts'
./Activate.ps1
cd ..
cd ..
python manage.py runserver

cd 'C:\Users\PRIDE BLACK WOLF\Downloads\Proyectos\LeadManagerNuevaTest\venv\Scripts'
./Activate.ps1
cd ..
cd ..
celery -A config worker --loglevel=info --pool=solo

cd 'C:\Users\PRIDE BLACK WOLF\Downloads\Proyectos\LeadManagerNuevaTest\venv\Scripts'
./Activate.ps1
cd ..
cd ..
celery -A config beat --loglevel=info

formulario: acoplar datos y directo

mover info, afinar prompts, hacer pruebas, montar en servidor.

limitar tamaño de entrada de correo a 8000 o x caracteres

python.exe manage.py seed_agents

1.- que me den las credenciales del bot: correo, api key y etiqueta de Sofia


-actualizar archivos
-aplicar migraciones
-reiniciar servicios
-borrar angel murillo